"""Durable workspace state for the loop: progress detection, logs, backlog.

The loop must survive the model process dying, the machine sleeping, and the
next session starting with no conversation history. That means every decision
it takes is grounded in files it writes here, in the checkout:

``.local/dsh-loop/``  (private, git-ignored)
    loop-state.json       consecutive failures, budget spent, current phase
    passes.jsonl          one objective record per pass
    BACKLOG.md            the loop's running task queue it edits itself
    prompt-overrides.md   human guidance injected into every pass
    logs/pass-*.log       raw event streams (bounded)
    live/<session>.json   per-pass live status, readable by the dashboard
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

STATE_VERSION = 1


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class Repo:
    """Git view of the checkout the loop works in."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.is_git = (root / ".git").exists()

    def _git(self, *args: str, timeout: float = 120) -> str:
        if not self.is_git:
            return ""
        cp = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True,
                            text=True, timeout=timeout, check=False)
        return cp.stdout if cp.returncode == 0 else ""

    def branch(self) -> str:
        name = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        return name or "unknown"

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def status_lines(self) -> list[str]:
        return [line for line in self._git("status", "--porcelain=v1", "-uall").splitlines() if line.strip()]

    def dirty_paths(self) -> list[str]:
        paths = []
        for line in self.status_lines():
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path.strip('"'))
        return paths

    def patch_paths(self) -> list[str]:
        out = self._git("diff", "--name-only", "HEAD")
        return [p for p in out.splitlines() if p.strip()]

    def commit(self, message: str) -> str | None:
        """Commit tracked changes (not ignored files). Returns the new HEAD."""
        if not self.is_git:
            return None
        if not self.patch_paths():
            return None
        cp = subprocess.run(["git", "add", "-u"], cwd=str(self.root), capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            return None
        cp = subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(self.root),
                            capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            return None
        return self.head()

    def push(self, remote: str = "origin", branch: str | None = None) -> tuple[bool, str]:
        """Push the current branch. Returns (ok, message)."""
        if not self.is_git:
            return False, "not a git checkout"
        branch = branch or self.branch()
        cp = subprocess.run(["git", "push", "-q", remote, branch], cwd=str(self.root),
                            capture_output=True, text=True, timeout=600, check=False)
        return cp.returncode == 0, (cp.stderr or cp.stdout or "").strip()[-500:]


@dataclass(frozen=True)
class Signature:
    head: str
    branch: str
    status: str       # digest of tracked+untracked state
    count: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(f"{self.head}|{self.branch}|{self.status}".encode()).hexdigest()[:16]


def signature(repo: Repo, ignore_prefixes: tuple[str, ...] = (".local/",)) -> Signature:
    lines = [ln for ln in repo.status_lines()
             if not any(ln[3:].strip().startswith(p) for p in ignore_prefixes)]
    joined = "\n".join(sorted(lines))
    return Signature(repo.head(), repo.branch(), joined, len(lines))


class LoopState:
    """The loop's own durable bookkeeping, separate from project STATUS.md."""

    def __init__(self, root: Path) -> None:
        self.dir = root / ".local" / "dsh-loop"
        self.root = root
        self.path = self.dir / "loop-state.json"
        self.repo = Repo(root)
        data = read_json(self.path, {})
        if data.get("version") != STATE_VERSION:
            data = {"version": STATE_VERSION}
        self.data = data
        self.data.setdefault("created", _now())
        self.data.setdefault("passes", 0)
        self.data.setdefault("consecutive_failures", 0)
        self.data.setdefault("consecutive_no_progress", 0)
        self.data.setdefault("stopped_reason", None)
        self.data.setdefault("last_session", None)
        self.data.setdefault("last_commit", self.repo.head())
        self.data.setdefault("pushed_commit", None)
        self.data.setdefault("notes", [])

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for child in ("logs", "live"):
            (self.dir / child).mkdir(parents=True, exist_ok=True)
        gitignore = self.root / ".local" / ".gitignore"
        if not gitignore.exists():
            gitignore.parent.mkdir(parents=True, exist_ok=True)
            gitignore.write_text(
                "# Local, machine-specific state. Never commit this tree.\n"
                ".dsh-loop/\nautomation/\n", encoding="utf-8")

    def save(self) -> None:
        self.data["updated"] = _now()
        write_json(self.path, self.data)

    # -- records -----------------------------------------------------------
    def record_pass(self, record: dict) -> None:
        self.data["passes"] = int(self.data.get("passes", 0)) + 1
        record = {"pass": self.data["passes"], "at": _now(), **record}
        with (self.dir / "passes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def recent_passes(self, limit: int = 3) -> list[dict]:
        path = self.dir / "passes.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # -- loop control --------------------------------------------------------
    def stop(self, reason: str) -> None:
        self.data["stopped_reason"] = reason
        self.data["stopped_at"] = _now()
        self.save()

    def clear_stop(self) -> None:
        self.data["stopped_reason"] = None
        self.data.pop("stopped_at", None)

    def set_live(self, session: str, payload: dict) -> None:
        if not session:
            return
        write_json(self.dir / "live" / f"{session}.json", {"updated": _now(), **payload})

    def clear_live(self, session: str) -> None:
        if not session:
            return
        try:
            (self.dir / "live" / f"{session}.json").unlink()
        except OSError:
            pass

    def prompt_overrides(self) -> str:
        try:
            return (self.dir / "prompt-overrides.md").read_text(encoding="utf-8")[:6000]
        except OSError:
            return ""

    def backlog(self) -> str:
        try:
            return (self.dir / "BACKLOG.md").read_text(encoding="utf-8")[:8000]
        except OSError:
            return ""

    def pending_tasks(self, limit: int = 6) -> list[str]:
        text = self.backlog()
        tasks = re.findall(r"^\s*(\d+)[.)]\s+\[ \]\s+(.+)$", text, flags=re.M)
        if not tasks:
            tasks = re.findall(r"^\s*(\d+)[.)]\s+(?!\[x\])(.+)$", text, flags=re.M)
        return [f"{num}. {title.strip()}" for num, title in tasks[:limit]]
