#!/usr/bin/env python3
"""Start a NOVA3 reconstruction worker in its own checkout, and keep it going.

A *worker* is an independent Git worktree (``.local/worktrees/<name>``) plus its
own ``.local/dsh`` runtime/profile/config, so several workers can run at once
without sharing state, changing files under each other, or fighting over one
model session.  Each worker:

1. gets a worktree branched from ``main`` (created on first start, reused
   afterwards and fast-forwarded to ``main`` when it has no local work);
2. gets a task backlog generated from the project's own ``NEXT.md`` (the
   project's priority list) plus the worker mission, into
   ``.local/dsh-loop/BACKLOG.md`` -- so the loop always has a *next* task
   instead of stopping when a mission ends;
3. runs the DeepSeek Harness loop (``tools/dsh_loop/loop.py``) on that backlog.

Usage::

    python -m tools.dsh_loop.worker start  <name> [--mission FILE] [--max-minutes N]
    python -m tools.dsh_loop.worker status
    python -m tools.dsh_loop.worker stop   <name>

Nothing here pushes, merges or deletes.  Worker commits stay on their own
branch until a human (or the owner agent) reviews and merges them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import installer, launch, loop, platforms as plat, resource
from .workspace import LoopState, Repo

ROOT = plat.checkout_root()
WORKTREES = ROOT / ".local" / "worktrees"
MISSION_FILE = "WORKER-MISSION.md"
# A standing owner mission in the checkout is the default for every worker.
DEFAULT_MISSION = ROOT / MISSION_FILE


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    cp = subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True, text=True, check=False)
    if check and cp.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
    return cp


def branch_name(name: str) -> str:
    return f"automation/worker-{name}"


def worktree_path(name: str) -> Path:
    return WORKTREES / name


def ensure_worktree(name: str) -> Path:
    path = worktree_path(name)
    branch = branch_name(name)
    existing = _git("worktree", "list", "--porcelain").stdout
    if f"worktree {path}" in existing:
        repo = Repo(path)
        if repo.is_git and not repo.dirty_paths() and _git("rev-parse", branch, cwd=path).returncode == 0:
            # Fast-forward only when the branch has nothing of its own.
            behind = _git("rev-list", "--count", f"{branch}..main", cwd=path).stdout.strip()
            if behind and behind != "0":
                parents = _git("rev-list", "--count", "main.." + branch, cwd=path).stdout.strip()
                if parents == "0":
                    _git("reset", "--hard", "main", cwd=path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if _git("rev-parse", "--verify", branch).returncode == 0:
        _git("worktree", "add", str(path), branch, check=True)
    else:
        _git("worktree", "add", "-b", branch, str(path), "main", check=True)
    return path


# ---------------------------------------------------------------------------
# task generation
# ---------------------------------------------------------------------------

NEXT_ITEM = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
# Priorities that tell the reader what *not* to do are guidance, not tasks.
NOT_A_TASK = ("do not ", "don't ", "avoid ", "never ")


def tasks_from_next(next_md: Path, limit: int = 8) -> list[str]:
    """Turn the numbered priorities in NEXT.md into backlog items."""
    try:
        text = next_md.read_text(encoding="utf-8")
    except OSError:
        return []
    items: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") or line.startswith(">") or len(line) > 600:
            continue
        match = NEXT_ITEM.match(line)
        if not match:
            continue
        body = re.sub(r"[`*]", "", match.group(2)).strip()
        body = re.sub(r"\s+", " ", body)
        if len(body) < 12 or body.lower().startswith(NOT_A_TASK):
            continue
        items.append(body)
        if len(items) >= limit:
            break
    return items


def seed_backlog(path: Path, tasks: list[str], mission: str = "") -> None:
    """Write the backlog only when it is absent or empty of open tasks."""
    state_backlog = path
    if state_backlog.exists():
        text = state_backlog.read_text(encoding="utf-8")
        if re.search(r"^\s*\d+[.)]\s+\[ \]", text, flags=re.M):
            return
    lines = ["# Worker backlog (loop-owned; the worker edits this file)", ""]
    if mission:
        lines += ["> Mission for this worker:", "> " + mission.strip().replace("\n", "\n> "), ""]
    for index, task in enumerate(tasks, start=1):
        lines.append(f"{index}. [ ] {task}")
    if not tasks:
        lines.append("1. [ ] Read docs/progress/{STATUS,NEXT,HANDOFF}.md and derive the next task.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# start / status / stop
# ---------------------------------------------------------------------------

# Instruction documents a worker must never run with a stale copy of.  Git can
# fast-forward the *worktree* only, so these are copied from the checkout that
# owns the worker before every start.
POLICY_FILES = (
    "AGENTS.md",
    "docs/progress/STATUS.md",
    "docs/progress/NEXT.md",
    "docs/progress/HANDOFF.md",
    "docs/progress/SESSION_LOG.md",
)


def sync_policy(worktree: Path) -> list[str]:
    """Refresh instruction docs in a worker worktree from this checkout.

    Without this, a long-lived worker branch keeps whatever AGENTS.md it was
    created with, and an unattended worker would silently follow outdated
    rules.  Only files that differ are written.  The copy is not committed
    automatically (the worker commits it with its next change); a dirty copy is
    reported so a human can see it was refreshed.
    """
    updated = []
    for relative in POLICY_FILES:
        source = ROOT / relative
        target = worktree / relative
        if not source.is_file():
            continue
        try:
            if target.is_file() and target.read_bytes() == source.read_bytes():
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        updated.append(relative)
    return updated


def worker_config(name: str) -> tuple[Path, dict]:
    """Create (or reuse) this worker's own config file."""
    path = worktree_path(name)
    config_path = path / ".local" / "dsh" / "worker.json"
    base = launch.config_for_checkout(ROOT)
    state = installer.load_state(base)
    if state.get("marker"):
        state["install_root"] = str(path / ".local" / "dsh")
        state["workspace"] = str(path)
        from .loop import dsh_home_for
        state["dsh_home"] = str(dsh_home_for(state))
        plat.write_json_private(config_path, state)
    return config_path, state


def start(name: str, mission: Path | None, max_minutes: int, pass_timeout: int,
          max_stuck: int) -> int:
    checkout = ROOT
    state = installer.load_state(launch.config_for_checkout(checkout))
    if not state.get("marker"):
        print("this checkout is not set up yet; run: nova3-dsh setup", file=sys.stderr)
        return 2
    path = ensure_worktree(name)
    updated = sync_policy(path)
    if updated:
        print(f"worker '{name}': refreshed policy from main: {', '.join(updated)}")
    mission_text = ""
    chosen_mission = Path(mission) if mission else (
        DEFAULT_MISSION if DEFAULT_MISSION.is_file() else None)
    if chosen_mission:
        mission_text = chosen_mission.read_text(encoding="utf-8")
        target = path / MISSION_FILE
        if not target.is_file() or target.read_text(encoding="utf-8") != mission_text:
            target.write_text(mission_text, encoding="utf-8")
    elif (path / MISSION_FILE).is_file():
        mission_text = (path / MISSION_FILE).read_text(encoding="utf-8")
    tasks = tasks_from_next(ROOT / "docs" / "progress" / "NEXT.md")
    if mission_text:
        tasks = ["Execute the worker mission in " + MISSION_FILE] + tasks
    seed_backlog(path / ".local" / "dsh-loop" / "BACKLOG.md", tasks)
    config_path, worker_state = worker_config(name)
    cfg = loop.LoopConfig(
        workspace=path,
        config_path=config_path,
        max_passes=0,
        max_minutes=max_minutes,
        pass_timeout=pass_timeout,
        max_failures=3,
        max_stuck=max_stuck,
        github="N-O-V-A-3-RE-Contributors/nova3-reconstruction",
        seed_goal="Recover N.O.V.A. 3 as independently compilable source (AGENTS.md is the contract).",
    )
    print(f"worker '{name}': {path} on branch {branch_name(name)}; {len(tasks)} generated tasks")
    return loop.Loop(cfg, install_state=worker_state).run()


def status() -> int:
    rows = []
    for entry in sorted(WORKTREES.glob("*")):
        state = LoopState(entry)
        data = state.data
        recent = state.recent_passes(1)
        live = (state.dir / "live" / "current.json").is_file()
        rows.append({
            "worker": entry.name,
            "branch": state.repo.branch(),
            "head": state.repo.head()[:12],
            "passes": data.get("passes", 0),
            "running": live,
            "last": (recent[0].get("summary", "")[:90] if recent else ""),
            "stop": data.get("stopped_reason") or "",
            "backlog": len(state.pending_tasks(50)),
        })
    if not rows:
        print("no workers yet; start one with: nova3-dsh-worker start <name>")
        return 0
    width = max(len(r["worker"]) for r in rows)
    for row in rows:
        print(f"{row['worker']:<{width}}  {row['branch']:<32} {row['head']:<14} "
              f"passes={row['passes']:<4} open={row['backlog']:<3} "
              f"{'RUNNING' if row['running'] else 'idle':<8} {row['last']}")
        if row["stop"]:
            print(f"{'':<{width}}  stopped: {row['stop'][:140]}")
    return 0


def stop(name: str) -> int:
    path = worktree_path(name)
    if not path.is_dir():
        print(f"no worker named {name}", file=sys.stderr)
        return 1
    target = LoopState(path).dir / "STOP"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stop requested\n", encoding="utf-8")
    print(f"worker {name} stops after its current pass ({target})")
    return 0


def web(name: str, *, port: int, profile: str, open_browser: bool = True) -> int:
    """Full interactive view of one worker in the DeepSeek Harness Web UI.

    The web profile is this checkout's own ``web`` profile (so it inherits the
    model route from the same credential reference) and the workspace is the
    worker's worktree, so the browser shows the worker's files, sessions and
    task progress with the regular DSH interface.  Bound to loopback only.
    """
    from .loop import dsh_home_for, node_and_entry
    path = worktree_path(name)
    if not path.is_dir():
        print(f"no worker named {name}; start it first", file=sys.stderr)
        return 1
    config_path, state = worker_config(name)
    node, entry = node_and_entry(state)
    dsh_home = dsh_home_for(state)
    env = dict(os.environ)
    env["DSH_HOME"] = str(dsh_home)
    argv = [str(node), str(entry), "--profile", profile, "web", "--host", "127.0.0.1",
            "--port", str(port)]
    if not open_browser:
        argv.append("--no-open")
    print(f"worker '{name}' web UI: workspace {path}, profile {profile} ({dsh_home})")
    print(f"loopback only: http://127.0.0.1:{port}/?token=... (the token is printed by dsh)")
    return subprocess.call(argv, cwd=str(path), env=env, stdin=None, stdout=None, stderr=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nova3-dsh-worker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("start")
    p.add_argument("name")
    p.add_argument("--mission", type=Path, default=None)
    p.add_argument("--max-minutes", type=int, default=0)
    p.add_argument("--pass-timeout", type=int, default=90 * 60)
    p.add_argument("--max-stuck", type=int, default=3)
    sub.add_parser("status")
    p = sub.add_parser("stop")
    p.add_argument("name")
    p = sub.add_parser("web")
    p.add_argument("name")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--profile", default="web")
    p.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "start":
        return start(args.name, args.mission, args.max_minutes, args.pass_timeout, args.max_stuck)
    if args.command == "stop":
        return stop(args.name)
    if args.command == "web":
        return web(args.name, port=args.port, profile=args.profile,
                   open_browser=not args.no_open)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
