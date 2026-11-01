"""The unattended loop: run bounded DSH passes until work is done.

Control flow of one loop run::

    guard (key, runtime, resources, budget, lock)
      └─ pass: prompt -> dsh headless --json -> classify -> durable record
            ├─ progress  -> reset failure counters, maybe self-commit
            ├─ no change -> count toward stop threshold
            ├─ hard stop -> credential, repeatedly failing pass, repeatedly
            │              stuck pass, wall clock, or the loop's own STOP file

Why these stop conditions exist: an unattended agent that keeps spending tokens
on the same failing action is worse than an agent that stops and says why. The
loop distinguishes *provider* failure (timeouts, quota) from *work* failure
(the pass ran, changed nothing) because the remedy for each is different.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import credentials, dshrun, prompt, resource
from . import platforms as plat
from .workspace import LoopState, Repo, signature

Echo = Callable[[str], None]


@dataclass
class LoopConfig:
    workspace: Path
    config_path: Path
    max_passes: int = 0          # 0 = unlimited
    max_minutes: int = 0         # 0 = unlimited
    pass_timeout: int = 90 * 60  # hard limit on one model pass
    max_failures: int = 3
    max_stuck: int = 3
    min_ram_mib: int = 350
    auto_commit: bool = True
    github: str | None = None
    seed_goal: str = ""


class Stop(Exception):
    def __init__(self, reason: str, code: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def log(echo: Echo, text: str) -> None:
    echo(f"[{time.strftime('%H:%M:%S')}] {text}", )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def check_state(state: dict, workspace: Path, config_path: Path) -> list[str]:
    """Return human-readable problems that make a pass impossible."""
    problems: list[str] = []
    if not state.get("marker"):
        problems.append(
            f"no installation found at {config_path}; run 'nova3-dsh setup' (or "
            "tools/dsh_loop/install.sh) first")
        return problems
    install_root = Path(str(state.get("install_root") or ""))
    entry = install_root / "runtime" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if not entry.is_file():
        problems.append(f"DeepSeek Harness runtime missing ({entry}); re-run setup")
    if not (install_root / "dsh-home" / "profiles" / str(state.get("dsh_profile"))).is_dir():
        problems.append("worker profile missing; re-run setup")
    provider = state.get("provider")
    if provider in credentials.PROVIDERS:
        status = credentials.key_status(provider)
        if not status.present:
            problems.append(
                f"{status.env_name} is not set in this environment. Set it (a new terminal "
                "after adding it to your shell profile) or run 'nova3-dsh setup'.")
    if not workspace.is_dir():
        problems.append(f"workspace {workspace} does not exist")
    elif not (workspace / "AGENTS.md").is_file():
        problems.append(f"{workspace} does not look like a NOVA3 checkout (no AGENTS.md)")
    return problems


def node_and_entry(state: dict) -> tuple[Path, Path]:
    install_root = Path(str(state["install_root"]))
    node_dir = state.get("node_home")
    if node_dir:
        node = Path(node_dir) / ("node.exe" if plat.WINDOWS else Path("bin") / "node")
    else:
        found = plat.find_executable("node")
        if not found:
            raise Stop("node is not installed and no private runtime was recorded; re-run setup", 2)
        node = Path(found)
    entry = install_root / "runtime" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    return node, entry


def acquire_lock(state_dir: Path) -> Path:
    """Single loop per checkout. A stale lock (dead pid) is taken over."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / "loop.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = -1
        alive = False
        if pid > 0:
            if plat.WINDOWS:
                import subprocess
                cp = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False)
                alive = str(pid) in (cp.stdout or "")
            else:
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False
        if alive:
            raise Stop(f"another NOVA3 worker loop is already running (pid {pid})", 3)
    lock.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    return lock


# ---------------------------------------------------------------------------
# optional remote heartbeat
# ---------------------------------------------------------------------------

def heartbeat(url: str | None, payload: dict) -> None:
    """POST one small JSON status line. Off unless the user configured a URL."""
    if not url:
        return
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        return
    try:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "nova3-dsh-loop"})
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310 - user-provided status sink
    except OSError:
        pass


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------

class Loop:
    def __init__(self, config: LoopConfig, *, echo: Echo = print,
                 install_state: dict | None = None) -> None:
        self.cfg = config
        self.echo = echo
        self.install_state = install_state if install_state is not None else {}
        self.state = LoopState(config.workspace)
        self.started = time.time()
        self.stop_file = self.state.dir / "STOP"
        self._stopping = False

    # -- run -----------------------------------------------------------------
    def run(self) -> int:
        cfg = self.cfg
        log(self.echo, f"NOVA3 DSH loop starting in {cfg.workspace}")
        log(self.echo, "host: " + resource.describe())
        self.state.ensure()
        lock: Path | None = None
        try:
            problems = check_state(self.install_state, cfg.workspace, cfg.config_path)
            if problems:
                for p in problems:
                    log(self.echo, "BLOCKED: " + p)
                raise Stop("preflight failed: " + problems[0], 2)
            lock = acquire_lock(self.state.dir)
            previous = self.state.data.get("stopped_reason")
            if previous:
                log(self.echo, f"(clearing previous stop reason: {previous})")
                self.state.clear_stop()
            self._install_signal_handlers()
            return self._loop()
        except Stop as stop:
            log(self.echo, "STOP: " + stop.reason)
            self.state.stop(stop.reason)
            return stop.code
        finally:
            if lock is not None:
                try:
                    lock.unlink()
                except OSError:
                    pass

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):  # noqa: ANN001 - signal signature
            if self._stopping:
                os._exit(130)
            self._stopping = True
            log(self.echo, f"received signal {signum}; finishing the current pass then stopping")
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), handler)
                except (ValueError, OSError):
                    pass

    def _loop(self) -> int:
        cfg = self.cfg
        node, entry = node_and_entry(self.install_state)
        profile = str(self.install_state.get("dsh_profile") or "nova3-worker")
        dsh_home = Path(str(self.install_state.get("dsh_home")))
        log_file = self.state.dir / "logs" / f"passes-{time.strftime('%Y%m%d')}.log"
        seeded = bool(self.state.data.get("seeded"))
        pass_index = 0
        while True:
            if self.stop_file.exists():
                raise Stop("a STOP file was placed in .local/dsh-loop (developer request)")
            if cfg.max_passes and pass_index >= cfg.max_passes:
                raise Stop(f"reached --max-passes {cfg.max_passes}")
            if cfg.max_minutes and (time.time() - self.started) / 60 >= cfg.max_minutes:
                raise Stop(f"reached --max-minutes {cfg.max_minutes}")
            if self._stopping:
                raise Stop("interrupted by user")
            mem = resource.memory()
            conservative = bool(resource.pressure(mem, cfg.min_ram_mib))
            if not conservative:
                space = resource.disk(cfg.workspace)
                if resource.disk_pressure(space, 1.0):
                    log(self.echo, "disk is nearly full; pausing 5 minutes")
                    time.sleep(300)
                    continue
            pass_index += 1
            self._one_pass(node, entry, profile, dsh_home, log_file, seeded, conservative)
            seeded = True

    def _one_pass(self, node: Path, entry: Path, profile: str, dsh_home: Path,
                  log_file: Path, seeded: bool, conservative: bool) -> None:
        cfg = self.cfg
        state_data = self.state.data
        if cfg.auto_commit:
            head = self.state.repo.head()
            if head and state_data.get("last_commit") and head != state_data.get("last_commit"):
                pass  # the model committed already during the previous pass
        before = signature(self.state.repo)
        if not seeded:
            text = prompt.seed_prompt(self.state, goal=cfg.seed_goal,
                                      repo_slug=cfg.github or "(local checkout)",
                                      conservative=conservative)
        else:
            text = prompt.continuation_prompt(
                self.state, max_minutes=max(15, cfg.pass_timeout // 60 - 5),
                conservative=conservative)
        if cfg.auto_commit:
            text += ("\nCommit your changes with `git commit` when they are verified; the loop "
                     "pushes nothing and merges nothing.\n")
        number = int(state_data.get("passes", 0)) + 1
        log(self.echo, f"pass {number}: starting DSH (timeout {cfg.pass_timeout // 60}m, "
                       f"{'conservative' if conservative else 'normal'} resources)")
        runner = dshrun.DshRunner(node=node, entry=entry, profile=profile, dsh_home=dsh_home,
                                  cwd=cfg.workspace, log_path=log_file)
        started_at = time.time()
        self.state.set_live("current", {"pass": number, "started": started_at, "phase": "model"})
        result = runner.run(text, timeout=cfg.pass_timeout)
        self.state.clear_live("current")
        after = signature(self.state.repo)
        progress = before.digest != after.digest
        commits = self._commits_since(before.head)
        summary = self._summarise(result, progress, commits)
        log(self.echo, f"pass {number} finished: {summary}")
        record = {
            "ok": result.ok,
            "progress": progress,
            "summary": summary,
            "session": result.session_id,
            "tokens_in": result.input_tokens,
            "tokens_out": result.output_tokens,
            "elapsed_s": round(result.elapsed, 1),
            "head_before": before.head,
            "head_after": after.head,
            "dirty_before": before.count,
            "dirty_after": after.count,
        }
        self.state.record_pass(record)
        state_data["last_session"] = result.session_id
        if result.session_id:
            state_data["last_session_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if after.head != before.head:
            state_data["last_commit"] = after.head
        state_data["seeded"] = True
        heartbeat(cfg.github and os.environ.get("NOVA3_LOOP_HEARTBEAT"), record)

        if result.fatal_credential:
            self.state.save()
            raise Stop(f"the model provider rejected the credentials ({result.error_code}); "
                       "fix your API key, then relaunch", 2)
        if result.error_code and result.error_code in ("QUOTA", "RATE_LIMIT"):
            state_data["cooldown_until"] = time.time() + 15 * 60
            self.state.save()
            log(self.echo, "provider quota/rate limit hit; waiting 15 minutes before retrying")
            time.sleep(15 * 60)
            return
        if not result.ok and not progress:
            state_data["consecutive_failures"] = int(state_data.get("consecutive_failures", 0)) + 1
        else:
            state_data["consecutive_failures"] = 0
        if progress:
            state_data["consecutive_no_progress"] = 0
        else:
            state_data["consecutive_no_progress"] = int(state_data.get("consecutive_no_progress", 0)) + 1
        self.state.save()

        if self.cfg.auto_commit and progress and not commits:
            message = (f"worker: checkpoint pass {number}\n\n"
                       f"Unattended NOVA3 DSH loop pass. Session {result.session_id or 'unknown'}.\n")
            new_head = self.state.repo.commit(message)
            if new_head:
                state_data["last_commit"] = new_head
                log(self.echo, f"loop committed the pass as {new_head[:12]}")
                self.state.save()

        if int(state_data["consecutive_failures"]) >= cfg.max_failures:
            raise Stop(f"{cfg.max_failures} consecutive failed passes without any change; "
                       f"see {log_file}", 1)
        if int(state_data["consecutive_no_progress"]) >= cfg.max_stuck:
            raise Stop(f"{cfg.max_stuck} consecutive passes changed nothing; the loop stopped "
                       "spending tokens on a stuck task. Inspect "
                       f"{self.state.dir / 'BACKLOG.md'} and the last pass log.", 1)

    def _commits_since(self, head: str) -> list[str]:
        repo = self.state.repo
        if not repo.is_git or not head:
            return []
        out = repo._git("log", "--format=%H", f"{head}..HEAD")
        return [line for line in out.splitlines() if line.strip()]

    def _summarise(self, result: dshrun.PassResult, progress: bool, commits: list[str]) -> str:
        bits = [result.summary()]
        bits.append("progress" if progress else "no file changes")
        if commits:
            bits.append(f"{len(commits)} commit(s)")
        return "; ".join(bits)
