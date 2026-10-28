#!/usr/bin/env python3
"""NOVA3 OBSERVATORY — contributor/maintainer GitHub monitoring TUI.

Read-only until a key-driven, confirmed management action is requested.
Does NOT import, patch, or restart the NOVA3 worker just by opening the UI.
"""
from __future__ import annotations

import argparse
import collections
import copy
import curses
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any
# Load the bundled module relative to this script even when unittest imports
# observer.py by file path (and without trusting a third-party `progress`).
import importlib.util as _importlib_util
_progress_spec = _importlib_util.spec_from_file_location(
    "nova3_private_observer_progress", Path(__file__).resolve().parent / "progress.py")
if _progress_spec is None or _progress_spec.loader is None:
    raise ImportError("NOVA3 Observatory progress module is missing")
progress_map = _importlib_util.module_from_spec(_progress_spec)
_progress_spec.loader.exec_module(progress_map)
_ops_spec = _importlib_util.spec_from_file_location(
    "nova3_private_observer_ops", Path(__file__).resolve().parent / "operator_ops.py")
if _ops_spec is None or _ops_spec.loader is None:
    raise ImportError("NOVA3 Observatory operator module missing")
# The module uses dataclasses; register it to support file-based imports in tests.
import sys as _operator_sys
operator_ops = _importlib_util.module_from_spec(_ops_spec)
_operator_sys.modules[_ops_spec.name] = operator_ops
_ops_spec.loader.exec_module(operator_ops)
_presence_spec = _importlib_util.spec_from_file_location(
    "nova3_private_observer_presence", Path(__file__).resolve().parent / "presence_view.py")
if _presence_spec is None or _presence_spec.loader is None:
    raise ImportError('NOVA3 Observatory worker presence viewer missing')
presence_view = _importlib_util.module_from_spec(_presence_spec)
_presence_spec.loader.exec_module(presence_view)

VERSION = "3.0.0"
DEFAULT_REPO = "N-O-V-A-3-RE-Contributors/nova3-reconstruction"

def configured_worktree() -> Path:
    """Reuse the directory selected by the existing contributor launcher."""
    override = os.environ.get('NOVA3_WORKDIR')
    if override and (Path(override).expanduser()/'.git').exists():
        return Path(override).expanduser()
    cfg = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'nova3-team' / 'easy.json'
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
        chosen = data.get('workdir') if isinstance(data, dict) else None
        if isinstance(chosen, str) and (Path(chosen).expanduser()/'.git').exists():
            return Path(chosen).expanduser()
    except (OSError, ValueError):
        pass
    for candidate in (Path.home()/'Projects/nova3_re', Path.home()/'NOVA3-Work'):
        if (candidate/'.git').exists():
            return candidate
    return Path.home()/'Projects/nova3_re'

DEFAULT_ROOT = configured_worktree()
DEFAULT_SERVICE = "nova3-worker.service"
STATE_FOLDER = "nova3-observatory"
GH_INTERVAL = 70
LOG_INTERVAL = 4
SERVICE_INTERVAL = 6
MAX_ISSUES = 1000
MAX_PRS = 500
MAX_RUNS = 80
MINUTES_PER_TASK = {"fast": 35, "likely": 90, "slow": 210}
TABS = ["PULSE", "TASKS", "PULL REQUESTS", "ACTIONS", "SERVICE LOGS", "FORECAST", "SOURCE MAP", "APK ATLAS", "ROADMAP", "OWNER OPS", "WORKERS", "HELP"]


def dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_age(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m ago"
    return f"{seconds // 86400}d ago"


def duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    if seconds < 0:
        return "—"
    if seconds < 60:
        return f"{round(seconds)}s"
    if seconds < 3600:
        return f"{round(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def labels_of(issue: dict) -> set[str]:
    return {str(x.get("name", "")) for x in (issue.get("labels") or []) if isinstance(x, dict)}


def is_task(issue: dict) -> bool:
    labels = labels_of(issue)
    return (bool(labels.intersection({"nova:queued", "nova:claimed", "nova:review", "nova:blocked", "nova:integrated", "nova:attention", "nova:matched", "nova:verified"}))
            and "nova:dashboard" not in labels) or bool(re.match(r"^Reconstruct\b", issue.get("title", ""), re.I))


def issue_stage(issue: dict) -> str:
    labels = labels_of(issue)
    # Closed issues are never claimable, even with stale queue labels.
    if issue.get("state") == "CLOSED":
        if "nova:verified" in labels or "nova:matched" in labels:
            return "VERIFIED"
        if "nova:integrated" in labels:
            return "INTEGRATED"
        return "CLOSED*"
    if "nova:blocked" in labels:
        return "BLOCKED"
    if "nova:verified" in labels or "nova:matched" in labels:
        return "VERIFIED"
    if "nova:integrated" in labels:
        return "INTEGRATED"
    if "nova:review" in labels:
        return "REVIEW"
    if "nova:claimed" in labels:
        return "CLAIMED"
    if "nova:queued" in labels:
        return "QUEUED"
    if issue.get("state") == "CLOSED":
        return "CLOSED*"
    return "OTHER"


def function_addrs(title: str) -> set[str]:
    # Function address counts are identified boundaries, not validated matches.
    if not re.search(r"\bfunctions?\b", title or "", re.I):
        return set()
    return {x.lower() for x in re.findall(r"0x[0-9a-fA-F]{6,16}\b", title)}


def merged_issue_ids(prs: list[dict]) -> set[int]:
    result: set[int] = set()
    for pr in prs:
        if not pr.get("mergedAt"):
            continue
        match = re.match(r"^#(\d+)\s*:", pr.get("title") or "")
        if match:
            result.add(int(match.group(1)))
        for issue in pr.get("closingIssuesReferences") or []:
            if isinstance(issue, dict) and isinstance(issue.get("number"), int):
                result.add(issue["number"])
    return result


def task_counts(issues: list[dict], prs: list[dict]) -> dict[str, int]:
    counts = collections.Counter()
    merge_ids = merged_issue_ids(prs)
    for issue in issues:
        if not is_task(issue):
            continue
        stage = issue_stage(issue)
        if issue.get("number") in merge_ids and stage in ("REVIEW", "CLAIMED", "QUEUED", "CLOSED*", "OTHER"):
            stage = "INTEGRATED"
        counts[stage] += 1
    for key in ("QUEUED", "CLAIMED", "REVIEW", "BLOCKED", "INTEGRATED", "VERIFIED", "CLOSED*", "OTHER"):
        counts.setdefault(key, 0)
    counts["TOTAL"] = sum(value for key, value in counts.items() if key != "TOTAL")
    return dict(counts)


def load_inventory(root: Path) -> dict:
    """Safely read checked-in function ledger, never game binaries."""
    ledger = root / "coordination/function_ledger.json"
    if not ledger.is_file():
        return {"available": False, "count": 0, "integrated": 0, "verified": 0, "reason": "No local function ledger"}
    try:
        if ledger.stat().st_size > 50_000_000:
            raise ValueError("Ledger exceeds 50 MB read limit")
        data = json.loads(ledger.read_text(encoding="utf-8"))
        fns = data.get("functions", [])
        if not isinstance(fns, list):
            raise ValueError("'functions' is not a list")
        seen = set()
        status_by_fn = {}
        for fn in fns:
            if not isinstance(fn, dict):
                continue
            address = fn.get("address")
            ref = str(fn.get("reference_sha256", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", ref):
                continue
            if fn.get("boundary_status") not in ("tool_identified", "verified"):
                continue
            if not isinstance(address, (int, str)):
                continue
            try:
                parsed_address = int(address, 0) if isinstance(address, str) and address.startswith("0x") else int(address)
            except ValueError:
                continue
            if parsed_address < 0:
                continue
            key = (ref, parsed_address)
            seen.add(key)
            status_by_fn[key] = str(fn.get("status", "pending")).lower()
        verified = sum(s in {"matched", "verified", "byte_matched"} for s in status_by_fn.values())
        integrated = sum(s in {"integrated", "matched", "verified", "byte_matched"} for s in status_by_fn.values())
        return {"available": True, "count": len(seen), "integrated": integrated, "verified": verified,
                "reason": "Identified boundaries only; not complete game coverage"}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"available": False, "count": 0, "integrated": 0, "verified": 0, "reason": f"Cannot read ledger: {exc}"}


# Journal uses ISO timestamps in local time with offset for accurate durations.
LOG_TIME_RE = re.compile(r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:?\d\d|Z)?)\s+.*?\[\d\d:\d\d:\d\d\]\s*(.*)$")
START_RE = re.compile(r"#(\d+) AI session \d+/\d+ .*provider launched")
FINISH_RE = re.compile(r"#(\d+) Codex/AI session finished: exit=(\d+)")


def parse_log_events(lines: list[str]) -> dict:
    launches: dict[int, datetime] = {}
    sessions = []
    last_event = None
    last_launch = None
    last_task = None
    failures = []
    for line in lines:
        m = LOG_TIME_RE.search(line)
        if not m:
            continue
        when = dt(m.group(1))
        if when is None:
            continue
        msg = m.group(2)
        if last_event is None or when > last_event:
            last_event = when
        start = START_RE.search(msg)
        finish = FINISH_RE.search(msg)
        if start:
            n = int(start.group(1))
            launches[n] = when
            last_launch = when
            last_task = n
        if finish:
            n = int(finish.group(1))
            if n in launches:
                secs = (when - launches[n]).total_seconds()
                if 0 < secs < 86400:
                    sessions.append({"issue": n, "seconds": secs, "exit": int(finish.group(2))})
                del launches[n]
        if "FAILED #" in msg or "ALERT #" in msg or "ALERT WORKER STOPPED" in msg:
            failures.append({"time": when.isoformat(), "text": msg})
    return {"sessions": sessions[-100:], "last_event": last_event, "last_launch": last_launch,
            "last_task": last_task, "unfinished": sorted(launches), "failures": failures[-20:]}


def median_session_seconds(event_metrics: dict) -> float | None:
    vals = [x["seconds"] for x in event_metrics.get("sessions", []) if x.get("exit") == 0]
    return statistics.median(vals) if vals else None


def pr_stats(prs: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    merged = [p for p in prs if dt(p.get("mergedAt"))]
    durations = []
    for p in merged:
        created, closed = dt(p.get("createdAt")), dt(p.get("mergedAt"))
        if created and closed and closed >= created:
            durations.append((closed - created).total_seconds())
    return {
        "total": len(prs), "merged": len(merged), "open": sum(p.get("state") == "OPEN" for p in prs),
        "draft": sum(p.get("state") == "OPEN" and p.get("isDraft") for p in prs),
        "closed_unmerged": sum(p.get("state") == "CLOSED" and not p.get("mergedAt") for p in prs),
        "merged_24h": sum((now - dt(p["mergedAt"])) < timedelta(hours=24) for p in merged),
        "merged_7d": sum((now - dt(p["mergedAt"])) < timedelta(days=7) for p in merged),
        "median_merge_seconds": statistics.median(durations) if durations else None,
    }


def build_estimates(issues: list[dict], prs: list[dict], inventory: dict, events: dict,
                    workers: int = 1, now: datetime | None = None) -> dict:
    """Explicit planning scenarios, not promises or a claimed game completion ETA."""
    now = now or datetime.now(timezone.utc)
    workers = max(1, min(32, int(workers)))
    counts = task_counts(issues, prs)
    remaining = sum(counts.get(k, 0) for k in ("QUEUED", "CLAIMED", "REVIEW", "BLOCKED"))
    stats = pr_stats(prs, now)
    median_ai = median_session_seconds(events)
    merged_set = merged_issue_ids(prs)
    known_tasks = {x["number"]: x for x in issues if is_task(x)}
    merged_fns = set()
    discovered = set()
    for issue in known_tasks.values():
        addrs = function_addrs(issue.get("title", ""))
        discovered.update(addrs)
        if issue.get("number") in merged_set:
            merged_fns.update(addrs)
    # No proven function verification is inferred from a PR merge.
    baseline = MINUTES_PER_TASK.copy()
    # AI median helps calibrate low-end processing effort, with review/verification allowance.
    if median_ai is not None:
        observed_minutes = median_ai / 60
        baseline["fast"] = max(15, round(observed_minutes * 0.9 + 10))
        baseline["likely"] = max(30, round(observed_minutes * 1.6 + 35))
        baseline["slow"] = max(75, round(observed_minutes * 3 + 90))
    durations = {k: remaining * val * 60 / workers for k, val in baseline.items()}
    known = inventory.get("count", 0) if inventory.get("available") else 0
    if known:
        # Count verified in ledger, or merged code separately; never sum as verified.
        already_integrated = max(min(known, inventory.get("integrated", 0)), min(known, len(merged_fns)))
        remaining_fn = max(0, known - already_integrated)
        # Function clusters are 4 addresses per task in current auto-feed template.
        remaining_batches = math.ceil(remaining_fn / 4)
        scope = "local identified function inventory"
    elif discovered:
        remaining_fn = max(0, len(discovered) - len(merged_fns))
        remaining_batches = math.ceil(remaining_fn / 4)
        scope = "addressed functions in generated GitHub tasks"
    else:
        remaining_fn = None
        remaining_batches = None
        scope = "not available"
    function_projection = {k: remaining_batches * val * 60 / workers for k, val in baseline.items()} if remaining_batches is not None else {}
    # Explicitly speculative game-wide reconstruction scenarios. The known
    # ledger can be severely incomplete and need not include all libraries.
    # Coverage fractions are user-visible hypothetical assumptions, not facts.
    completeness = {"fast": 0.75, "likely": 0.30, "slow": 0.10}
    if known or discovered:
        identified = known or len(discovered)
        completed_known = min(identified, max(inventory.get("integrated", 0), len(merged_fns)))
        hypothetical_game = {}
        for mode, fraction in completeness.items():
            assumed_total = math.ceil(identified / fraction)
            hypothetical_game[mode] = math.ceil(max(0, assumed_total - completed_known) / 4) * baseline[mode] * 60 / workers
    else:
        hypothetical_game = {}
    # Throughput: don't annualize zero; no model unless merged samples exist.
    merged = [p for p in prs if dt(p.get("mergedAt"))]
    observed_rate = None
    if merged:
        earliest = min((dt(x.get("createdAt")) for x in issues if dt(x.get("createdAt"))), default=None)
        if earliest:
            elapsed_days = max(0.25, (now - earliest).total_seconds() / 86400)
            observed_rate = len(merged) / elapsed_days
    throughput_seconds = (remaining / observed_rate * 86400) if observed_rate and remaining else (0 if remaining == 0 else None)
    return {
        "workers": workers,
        "remaining_queue": remaining,
        "per_task_minutes": baseline,
        "queue_seconds": durations,
        "throughput_seconds": throughput_seconds,
        "empirical_merges_per_day": observed_rate,
        "functions_scope": scope,
        "known_functions": known or len(discovered),
        "integrated_function_addresses": len(merged_fns),
        "verified_ledger_functions": inventory.get("verified", 0) if inventory.get("available") else None,
        "function_remaining": remaining_fn,
        "function_batches": remaining_batches,
        "function_seconds": function_projection,
        "whole_game_seconds": hypothetical_game,
        "hypothetical_inventory_coverage": completeness,
        "stats": stats,
        "median_ai_seconds": median_ai,
        "assumptions": "1 task ≈ 4 identified functions; estimates include allowance for review and integration",
        "coverage_warning": "Not an ETA for the complete game; inventory completeness is unverified",
    }


def classify_health(service: dict, gh_error: str | None, counts: dict, events: dict,
                    now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    active = service.get("active") == "active"
    if not active:
        return "STOPPED", "systemd worker is not active"
    last_launch = events.get("last_launch")
    last_event = events.get("last_event")
    if last_launch and events.get("unfinished") and now - last_launch > timedelta(minutes=45):
        return "QUIET", "No logged progress for >45m since AI launch (not proof of a hang)"
    if gh_error:
        return "DEGRADED", "GitHub refresh failed; showing cached data"
    if counts.get("QUEUED", 0) == 0 and counts.get("CLAIMED", 0) == 0:
        return "IDLE", ("Worker polling; blocked tasks need maintainer review"
                        if counts.get("BLOCKED", 0) else
                        "No claimable tasks; feeder may replenish")
    if counts.get("BLOCKED", 0):
        return "RUNNING", "Worker active; separate blocked tasks need maintainer review"
    return "RUNNING", "service active; activity depends on worker log"


def simple_sparkline(samples: list[float], width: int = 24) -> str:
    if not samples:
        return "·" * min(width, 24)
    glyphs = "▁▂▃▄▅▆▇█"
    tail = samples[-width:]
    lo, hi = min(tail), max(tail)
    if hi == lo:
        return ("▃" if hi else "▁") * len(tail)
    return "".join(glyphs[min(7, round(7 * (x-lo)/(hi-lo)))] for x in tail)


def run_cmd(args: list[str], *, timeout: int = 25) -> str:
    env = dict(os.environ, GH_PAGER="cat", PAGER="cat", GIT_PAGER="cat", NO_COLOR="1")
    cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    if cp.returncode:
        detail = (cp.stderr or cp.stdout).strip()
        raise RuntimeError(f"{args[0]} {args[1] if len(args)>1 else ''}: {detail[:220] or 'exit '+str(cp.returncode)}")
    return cp.stdout


def gh_json(args: list[str], timeout: int = 30) -> list | dict:
    data = json.loads(run_cmd(args, timeout=timeout))
    if not isinstance(data, (list, dict)):
        raise ValueError("Unexpected GitHub JSON response")
    return data


def gh_collect(repo: str) -> dict:
    issue_fields = "number,title,body,state,labels,createdAt,closedAt,updatedAt,url"
    pr_fields = "number,title,state,isDraft,createdAt,mergedAt,closedAt,updatedAt,headRefName,baseRefName,url,mergeable,reviewDecision,additions,deletions,changedFiles,closingIssuesReferences"
    run_fields = "databaseId,workflowName,displayTitle,status,conclusion,createdAt,updatedAt,url,headBranch,event"
    result = {"issues": [], "prs": [], "runs": [], "errors": []}
    commands = (
        ("issues", ["gh", "issue", "list", "-R", repo, "--state", "all", "--limit", str(MAX_ISSUES), "--json", issue_fields]),
        ("prs", ["gh", "pr", "list", "-R", repo, "--state", "all", "--limit", str(MAX_PRS), "--json", pr_fields]),
        ("runs", ["gh", "run", "list", "-R", repo, "--limit", str(MAX_RUNS), "--json", run_fields]),
    )
    for name, cmd in commands:
        try:
            payload = gh_json(cmd, 35)
            if not isinstance(payload, list):
                raise ValueError(f"Expected list for {name}")
            result[name] = payload
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
            result["errors"].append(f"{name}: {e}")
    return result


def service_collect(service: str) -> dict:
    try:
        raw = run_cmd(["systemctl", "--user", "show", service, "--property=ActiveState,SubState,MainPID,NRestarts,ExecMainStatus"], timeout=6)
        d = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        return {"active": d.get("ActiveState", "unknown"), "sub": d.get("SubState", "unknown"),
                "pid": d.get("MainPID", "0"), "restarts": d.get("NRestarts", "?"),
                "exit_status": d.get("ExecMainStatus", "?"), "error": ""}
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"active": "unknown", "sub": "unknown", "pid": "0", "restarts": "?", "exit_status": "?", "error": str(exc)}


def journal_collect(service: str) -> list[str]:
    try:
        raw = run_cmd(["journalctl", "--user", "-u", service, "-n", "600", "--output=short-iso", "--no-pager"], timeout=12)
        return raw.splitlines()[-600:]
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return [f"journal unavailable: {exc}"]


def private_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def private_write(path: Path, data: dict) -> None:
    private_folder(path.parent)
    draft = path.with_name(path.name + ".tmp")
    fd = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(draft, path)
    finally:
        try:
            if draft.exists():
                draft.unlink()
        except OSError:
            pass


@dataclass
class Snapshot:
    issues: list[dict] = field(default_factory=list)
    prs: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    service: dict = field(default_factory=lambda: {"active": "checking", "sub": "", "pid": "0", "restarts": "?"})
    github_updated: str = ""
    logs_updated: str = ""
    inventory: dict = field(default_factory=dict)
    github_errors: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    last_action: str = ""
    coverage: dict = field(default_factory=dict)
    roadmap: dict = field(default_factory=dict)
    discovery: dict = field(default_factory=dict)
    git_alignment: dict = field(default_factory=dict)
    discovery_logs: list[str] = field(default_factory=list)
    operator_events: list[str] = field(default_factory=list)
    merge_summary: str = ""
    merge_status: str = "NONE"
    merge_ready: list[int] = field(default_factory=list)
    merge_rejected: dict[int, str] = field(default_factory=dict)
    worker_presence: list[dict] = field(default_factory=list)
    presence_issue: int | None = None
    presence_updated: str = ''
    presence_error: str = ''
    repo_role: str = 'UNKNOWN'


class Collector:
    def __init__(self, repo: str, root: Path, service: str, state_dir: Path,
                 *, demo: bool = False, gh_interval: int = GH_INTERVAL):
        self.repo, self.root, self.service, self.state_dir = repo, root, service, state_dir
        self.demo, self.gh_interval = demo, gh_interval
        self.lock = threading.RLock()
        self.snapshot = Snapshot()
        self.stop_event = threading.Event()
        self.force_event = threading.Event()
        self.action_in_progress = False
        self.log_thread = None
        self.gh_thread = None
        self.mgmt_thread = None
        self.merge_preview = None
        self._load_cache()
        if demo:
            self.snapshot = sample_snapshot()

    def _load_cache(self):
        fp = self.state_dir / "snapshot.json"
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            for name in ("issues", "prs", "runs", "github_updated", "github_errors", "history", "worker_presence", "presence_updated", "presence_issue", "presence_error"):
                value = data.get(name)
                if isinstance(value, type(getattr(self.snapshot, name))):
                    setattr(self.snapshot, name, value)
            self.snapshot.inventory = load_inventory(self.root)
            self.snapshot.roadmap = progress_map.load_roadmap(self.root)
            self.snapshot.coverage = progress_map.source_map(self.root, self.snapshot.issues, self.snapshot.prs)
        except (OSError, ValueError, TypeError):
            pass
        # Even when no snapshot exists, load the persistent stage plan.
        if not self.snapshot.roadmap:
            self.snapshot.roadmap = progress_map.load_roadmap(self.root)

    def view(self) -> Snapshot:
        with self.lock:
            return copy.deepcopy(self.snapshot)

    def begin(self):
        if self.demo:
            return
        self.log_thread = threading.Thread(target=self._log_loop, daemon=True, name="nova-log-poller")
        self.gh_thread = threading.Thread(target=self._gh_loop, daemon=True, name="nova-gh-poller")
        self.log_thread.start()
        self.gh_thread.start()

    def close(self):
        self.stop_event.set()
        self.force_event.set()
        # Poller threads are daemons to avoid terminal hanging during a slow gh CLI call.

    def force_refresh(self):
        if not self.demo:
            self.force_event.set()

    def _log_loop(self):
        while not self.stop_event.is_set():
            service = service_collect(self.service)
            logs = journal_collect(self.service)
            try:
                discovery = operator_ops.discovery_status(self.root)
                git_state = operator_ops.git_alignment(self.root)
                discovery_logs = operator_ops.service_error_lines(lines=6)
            except (OSError, ValueError, RuntimeError) as exc:
                discovery, git_state, discovery_logs = {'message': str(exc)}, {'note': str(exc)}, [str(exc)]
            with self.lock:
                self.snapshot.discovery = discovery
                self.snapshot.git_alignment = git_state
                self.snapshot.discovery_logs = discovery_logs
                self.snapshot.service = service
                self.snapshot.logs = logs
                self.snapshot.logs_updated = datetime.now(timezone.utc).isoformat()
            if self.stop_event.wait(LOG_INTERVAL):
                break

    def _gh_loop(self):
        while not self.stop_event.is_set():
            packet = gh_collect(self.repo)
            presence_packet = presence_view.collect(self.repo)
            try:
                repo_role = operator_ops.repo_permission(self.repo)
            except (OSError, ValueError, RuntimeError):
                repo_role = 'UNKNOWN'
            now = datetime.now(timezone.utc)
            with self.lock:
                snap = self.snapshot
                snap.repo_role = repo_role
                # Keep last good data instead of interpreting failed network access as zero tasks.
                for name in ("issues", "prs", "runs"):
                    failed = any(e.startswith(name + ":") for e in packet["errors"])
                    if not failed:
                        setattr(snap, name, packet[name])
                snap.github_errors = packet["errors"]
                if not presence_packet['error']:
                    snap.worker_presence = presence_packet['records']
                    snap.presence_issue = presence_packet['issue']
                    snap.presence_updated = now.isoformat()
                snap.presence_error = presence_packet.get('error','')
                if not packet["errors"]:
                    snap.github_updated = now.isoformat()
                snap.inventory = load_inventory(self.root)
                snap.roadmap = progress_map.load_roadmap(self.root)
                try:
                    snap.coverage = progress_map.source_map(self.root, snap.issues, snap.prs)
                except (OSError, ValueError) as exc:
                    snap.github_errors.append('source map: '+str(exc)[:130])
                if not packet["errors"]:
                    counts = task_counts(snap.issues, snap.prs)
                    record = {"at": now.isoformat(), "queued": counts["QUEUED"],
                              "merged": pr_stats(snap.prs, now)["merged"], "blocked": counts["BLOCKED"]}
                    if not snap.history or (now - dt(snap.history[-1]["at"])).total_seconds() >= 120:
                        snap.history.append(record)
                        snap.history = snap.history[-720:]
                try:
                    private_write(self.state_dir / "snapshot.json", {
                        "issues": snap.issues, "prs": snap.prs, "runs": snap.runs,
                        "history": snap.history, "github_errors": snap.github_errors,
                        "github_updated": snap.github_updated,
                        "worker_presence": snap.worker_presence,
                        "presence_issue": snap.presence_issue,
                        "presence_updated": snap.presence_updated,
                        "presence_error": snap.presence_error,
                    })
                except OSError:
                    pass
            self.force_event.wait(self.gh_interval)
            self.force_event.clear()

    def operator_event(self, message: str):
        with self.lock:
            self.snapshot.operator_events.append(datetime.now().strftime('%H:%M:%S') + ' ' + str(message)[:260])
            self.snapshot.operator_events = self.snapshot.operator_events[-75:]
            self.snapshot.last_action = str(message)[:180]

    def prepare_merges(self, selected: list[int] | None, include_others: bool = False,
                       allow_no_checks: bool = False):
        if self.demo:
            self.operator_event('DEMO: owner actions disabled')
            return
        with self.lock:
            if self.action_in_progress:
                self.operator_event('Another operation is running')
                return
            self.action_in_progress = True
            self.merge_preview = None
            self.snapshot.merge_ready = []
            self.snapshot.merge_rejected = {}
            self.snapshot.merge_status = 'SCANNING'
        def job():
            try:
                self.operator_event('Scanning selected PRs against current GitHub checks')
                preview = operator_ops.make_preview(self.repo, selected,
                   allow_others=include_others, allow_no_checks=allow_no_checks,
                   emit=self.operator_event)
                with self.lock:
                    self.merge_preview = preview
                    self.snapshot.merge_ready = preview.eligible
                    self.snapshot.merge_rejected = preview.rejected
                    self.snapshot.merge_status = 'READY' if preview.eligible else 'NONE_ELIGIBLE'
                    self.snapshot.merge_summary = (f'{len(preview.eligible)} eligible / '
                       f'{len(preview.rejected)} excluded, signed in as {preview.account}')
                self.operator_event(self.snapshot.merge_summary)
            except (OSError,ValueError,RuntimeError) as exc:
                with self.lock:
                    self.snapshot.merge_status='ERROR'
                self.operator_event('Scan failed: '+str(exc)[:240])
            finally:
                with self.lock:self.action_in_progress=False
        self.mgmt_thread=threading.Thread(target=job,name='nova-merge-preview',daemon=True)
        self.mgmt_thread.start()

    def execute_merge(self, include_others: bool = False, allow_no_checks: bool = False):
        if self.demo:
            self.operator_event('DEMO: merges disabled');return
        with self.lock:
            if self.action_in_progress or self.snapshot.merge_status!='READY' or self.merge_preview is None:
                self.operator_event('No ready/idle merge preview');return
            preview = self.merge_preview
            if time.monotonic()-preview.created > 300:
                self.snapshot.merge_status='EXPIRED'
                self.operator_event('Merge preview expired; scan again');return
            self.action_in_progress=True
            self.snapshot.merge_status='MERGING'
        def job():
            try:
                result=operator_ops.merge_batch(self.repo,preview,emit=self.operator_event,
                   allow_others=include_others,allow_no_checks=allow_no_checks)
                msg=(f'Finished: {len(result["merged"])} merged, '
                     f'{len(result["skipped"])} skipped. See events for details.')
                self.operator_event(msg)
                with self.lock:
                    self.snapshot.merge_status='FINISHED'
                    self.snapshot.merge_summary=msg
            except (OSError,ValueError,RuntimeError) as exc:
                self.operator_event('Merge batch failed: '+str(exc)[:260])
                with self.lock:self.snapshot.merge_status='ERROR'
            finally:
                with self.lock:
                    self.action_in_progress=False
                    self.merge_preview=None
                self.force_refresh()
        self.mgmt_thread=threading.Thread(target=job,name='nova-merge-batch',daemon=True)
        self.mgmt_thread.start()

    def perform_action(self, action: str):
        if self.demo:
            with self.lock:
                self.snapshot.last_action = "Demo mode: commands disabled"
            return
        with self.lock:
            if self.action_in_progress:
                self.snapshot.last_action = "Another action is still running"
                return
            self.action_in_progress = True
            self.snapshot.last_action = "Executing " + action + "..."
        def job():
            try:
                if action in {'feed', 'discovery-repair', 'discovery-now',
                              'discovery-toggle', 'export-diagnostics'}:
                    operator_ops.require_owner_access(self.repo)
                if action in {"start", "stop", "restart"}:
                    args = ["systemctl", "--user", action, self.service]
                elif action == "feed":
                    args = ["gh", "workflow", "run", "nova-autofeed.yml", "-R", self.repo]
                elif action == 'discovery-repair':
                    operator_ops.timer_repair(self.root,self.repo,emit=self.operator_event)
                    args = None
                elif action == 'discovery-now':
                    self.operator_event(operator_ops.start_discovery_scan(self.repo))
                    args = None
                elif action == 'discovery-toggle':
                    status=operator_ops.discovery_status(self.root)
                    enabling = status.get('timer',{}).get('ActiveState')!='active'
                    self.operator_event(operator_ops.toggle_discovery_timer(enabling,self.repo))
                    args = None
                elif action == 'export-diagnostics':
                    fp=self.state_dir / ('nova3-operator-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.txt')
                    self.operator_event('Report saved: '+str(operator_ops.export_diagnostics(fp,self.root,self.repo,self.service)))
                    args = None
                else:
                    raise ValueError("Unknown action")
                if args is not None:
                    run_cmd(args, timeout=25)
                result = "OK: " + action + " command submitted"
            except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                result = "FAILED " + action + ": " + str(exc)[:180]
            self.operator_event(result)
            with self.lock:
                self.snapshot.last_action = result
                self.action_in_progress = False
            self.force_refresh()
        self.mgmt_thread = threading.Thread(target=job, name="nova-control", daemon=True)
        self.mgmt_thread.start()


def sample_snapshot() -> Snapshot:
    """Synthetic offline data; always labelled DEMO in UI."""
    now = datetime.now(timezone.utc)
    def stamp(minutes):
        return (now - timedelta(minutes=minutes)).isoformat()
    issues = []
    for n in range(29, 45):
        stage = "nova:queued"
        if n == 29:
            stage = "nova:blocked"
        elif n == 32:
            stage = "nova:claimed"
        elif n == 33:
            stage = "nova:review"
        elif n < 32:
            stage = "nova:integrated"
        issues.append({"number": n, "title": f"Reconstruct libNOVA3_neon.so functions 0x00{n:06x}, 0x00{n+16:06x}, 0x00{n+32:06x}, 0x00{n+48:06x}",
                       "state": "CLOSED" if stage == "nova:integrated" else "OPEN", "labels": [{"name": stage}],
                       "createdAt": stamp(360), "updatedAt": stamp(n * 3),
                       "url": f"https://github.com/{DEFAULT_REPO}/issues/{n}"})
    prs = [{"number": 51, "title": "#30: Reconstruct function cluster", "state": "MERGED", "isDraft": False,
            "createdAt": stamp(190), "mergedAt": stamp(60), "url": f"https://github.com/{DEFAULT_REPO}/pull/51"},
           {"number": 52, "title": "#33: Reconstruct function cluster", "state": "OPEN", "isDraft": True,
            "createdAt": stamp(45), "mergedAt": None, "url": f"https://github.com/{DEFAULT_REPO}/pull/52", "mergeable": "MERGEABLE"}]
    runs = [{"databaseId": 1234, "workflowName": "nova-autofeed.yml", "displayTitle": "Replenish tasks", "status": "completed", "conclusion": "success", "createdAt": stamp(12), "url": "https://github.com/example"},
            {"databaseId": 1235, "workflowName": "CI", "displayTitle": "Build", "status": "in_progress", "conclusion": "", "createdAt": stamp(4), "url": "https://github.com/example"}]
    logs = [f"{stamp(14)} archlinux python[1234]: [10:11:05] #32 AI session 1/3 (codex); provider launched",
            f"{stamp(13)} archlinux python[1234]: [10:12:00] Repo={DEFAULT_REPO} account=crjase provider=codex",
            f"{stamp(4)} archlinux python[1234]: [10:20:00] #33 Codex/AI session finished: exit=0; checking changed files"]
    history = [{"at": stamp(150-i * 3), "queued": 17-i//7, "merged": i//8, "blocked": 1} for i in range(48)]
    snapshot = Snapshot(issues, prs, runs, logs, {"active": "active", "sub": "running", "pid": "1234", "restarts": "0"},
                    now.isoformat(), now.isoformat(), {"available": True, "count": 2400, "integrated": 12, "verified": 3}, [], history, "Demo: commands disabled")
    fake = [{'addr':0x700000+n*32,'lib':'libNOVA3_neon.so', 'verified':n<5,
             'integrated':n<30,'submitted':n<38,'source':n<43}
            for n in range(64)]
    snapshot.coverage = {'known':64,'integrated':30,'submitted':38,'verified':5,'local':43,
            'coverage_scope':'DEMO - synthetic known boundaries, not full game',
            'libraries':[{'name':'libNOVA3_neon.so','identified':64,'integrated':30,
                          'submitted':38,'verified':5,'local':43,'functions':fake}],
            'source':{'files':42,'lines':12600,'test_files':15,'areas':{'reconstruction/engine':20,'reconstruction/tests':15,'reconstruction/recovered':7}},
            'package':{'apks':[{'name':'NOVA3_EXAMPLE_DEMO.apk','entries':600,'kinds':{'NATIVE SO':4,'DEX/JAVA':2,'ASSETS':320,'RESOURCES':273,'MANIFEST':1},'bytes':{}}],
                       'libraries':[{'name':'libNOVA3_neon.so','size':9000000,'edition':'DEMO'}],
                       'component_counts':{'NATIVE SO':4,'DEX/JAVA':2,'ASSETS':320,'RESOURCES':273,'MANIFEST':1},
                       'component_bytes':{}}}
    snapshot.roadmap = progress_map.load_roadmap(Path(__file__).resolve().parent)
    snapshot.worker_presence = [
        {'owner':'crjase','worker':'a'*32,'provider':'codex','state':'WORKING',
         'issue':44,'updated':stamp(2),'comment_id':123},
        {'owner':'example-contributor','worker':'b'*32,'provider':'gemini','state':'IDLE',
         'issue':None,'updated':stamp(3),'comment_id':124},
        {'owner':'test-stale','worker':'c'*32,'provider':'claude','state':'WORKING',
         'issue':32,'updated':stamp(42),'comment_id':125},
    ]
    snapshot.presence_issue = 88
    snapshot.presence_updated = datetime.now(timezone.utc).isoformat()
    return snapshot


# -----------------------------------------------------------------------------
# curses renderer — no pip installs, no raw ANSI terminal-clearing loop.
# -----------------------------------------------------------------------------
class Observatory:
    def __init__(self, collector: Collector, workers: int = 1):
        self.collector = collector
        self.workers = max(1, min(32, workers))
        self.tab = 0
        self.indices = collections.defaultdict(int)
        self.scroll = collections.defaultdict(int)
        self.log_mode = 0
        self.confirm = None
        self.notice = ""
        self.notice_until = 0
        self.theme = 0
        self.map_library = 0
        self.map_mode = 0
        self.roadmap_offset = 0
        self.pr_marked = set()
        self.include_others = False
        self.allow_no_checks = False
        self.merge_phrase = None
        self.merge_input = ''
        self.stdscr = None
        self.snap = Snapshot()
        self.C = {}

    def run(self, screen):
        self.stdscr = screen
        curses.curs_set(0)
        screen.keypad(True)
        screen.timeout(170)
        self.setup_colors()
        while True:
            self.snap = self.collector.view()
            try:
                self.render()
            except curses.error:
                pass
            key = screen.getch()
            if key == -1:
                continue
            if not self.handle(key):
                return

    def setup_colors(self):
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        palette = {
            "normal": curses.COLOR_WHITE, "muted": 8 if curses.COLORS >= 16 else curses.COLOR_WHITE,
            "cyan": curses.COLOR_CYAN, "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
            "red": curses.COLOR_RED, "magenta": curses.COLOR_MAGENTA, "blue": curses.COLOR_BLUE,
        }
        for index, (name, fg) in enumerate(palette.items(), 1):
            try:
                curses.init_pair(index, fg, -1)
                self.C[name] = curses.color_pair(index)
            except curses.error:
                self.C[name] = 0
        self.C["bold"] = curses.A_BOLD

    def put(self, y: int, x: int, s: Any, style: str = "normal", maxlen: int | None = None):
        h, w = self.stdscr.getmaxyx()
        if not 0 <= y < h or x >= w or maxlen == 0:
            return
        s = str(s).replace("\n", " ").replace("\r", " ").replace("\t", "  ")
        if x < 0:
            s = s[-x:]
            x = 0
        limit = min(w - x - 1, maxlen if maxlen is not None else w - x - 1)
        if limit <= 0:
            return
        try:
            self.stdscr.addnstr(y, x, s, limit, self.C.get(style, 0))
        except (curses.error, UnicodeError):
            pass

    def bar(self, y, x, width, value, total, color="cyan"):
        width = max(0, width)
        if width < 3:
            return
        ratio = max(0, min(1, (value / total) if total else 0))
        filled = round((width-2) * ratio)
        self.put(y, x, "[" + "█"*filled + "░"*(width-2-filled) + "]", color, width)

    def frame(self, top, left, height, width, title="", color="muted"):
        h, w = self.stdscr.getmaxyx()
        height = min(height, h-top-1)
        width = min(width, w-left-1)
        if height < 3 or width < 6:
            return
        self.put(top, left, "╭" + "─"*(width-2) + "╮", color, width)
        for y in range(top+1, top+height-1):
            self.put(y, left, "│", color)
            self.put(y, left+width-1, "│", color)
        self.put(top+height-1, left, "╰" + "─"*(width-2) + "╯", color, width)
        if title:
            self.put(top, left+2, " " + title + " ", "cyan", width-4)

    def render(self):
        screen = self.stdscr
        screen.erase()
        h, w = screen.getmaxyx()
        if h < 18 or w < 72:
            self.put(1, 2, "NOVA3 // OBSERVATORY", "cyan")
            self.put(3, 2, "Terminal too small. Resize to >= 72 x 18.", "yellow")
            self.put(5, 2, "q to exit. No project files are changed.", "muted")
            screen.refresh()
            return
        now = datetime.now(timezone.utc)
        stamp = datetime.now().strftime("%a %d %b %Y  %H:%M:%S")
        accent = "magenta" if self.theme else "cyan"
        self.put(0, 2, "◈ NOVA3 // OBSERVATORY", accent)
        self.put(0, max(25, w-len(stamp)-4), stamp, "muted")
        gh_ok = bool(self.snap.github_updated) and not self.snap.github_errors
        self.put(1, 2, f"{self.collector.repo} • {'DEMO' if self.collector.demo else 'LIVE READ-ONLY'} • GitHub role {self.snap.repo_role}", "muted", w-4)
        self.put(2, 0, "─" * (w-1), "muted")
        labels = ['1','2','3','4','5','6','7','8','9','0','W','?']
        lengths = [len(x)+3 for x in TABS]
        if sum(lengths) + 20 < w:
            pos = 2
            for i, title in enumerate(TABS):
                self.put(3, pos, f" {labels[i]}:{title} ", "normal" if i == self.tab else "muted")
                if i == self.tab:
                    self.put(4, pos, "━" * (len(title)+4), accent)
                pos += len(title)+5
        else:
            self.put(3, 2, f"[{self.tab+1}/{len(TABS)}] {TABS[self.tab]}  • TAB switch  •  ? help", accent)
        self.put(5, 0, "─" * (w-1), "muted")
        counts = task_counts(self.snap.issues, self.snap.prs)
        events = parse_log_events(self.snap.logs)
        estimate = build_estimates(self.snap.issues, self.snap.prs, self.snap.inventory, events, self.workers, now)
        health, description = classify_health(self.snap.service,
                                               "; ".join(self.snap.github_errors), counts, events, now)
        bottom = h - 3
        if self.tab == 0:
            self.draw_pulse(6, bottom, w, counts, events, estimate, health, description)
        elif self.tab == 1:
            self.draw_tasks(6, bottom, w, counts)
        elif self.tab == 2:
            self.draw_prs(6, bottom, w)
        elif self.tab == 3:
            self.draw_actions(6, bottom, w)
        elif self.tab == 4:
            self.draw_logs(6, bottom, w)
        elif self.tab == 5:
            self.draw_forecast(6, bottom, w, counts, events, estimate)
        elif self.tab == 6:
            self.draw_source_map(6, bottom, w)
        elif self.tab == 7:
            self.draw_apk_atlas(6, bottom, w)
        elif self.tab == 8:
            self.draw_roadmap(6, bottom, w)
        elif self.tab == 9:
            if self.snap.repo_role in operator_ops.OWNER_ROLES and not self.collector.demo:
                self.draw_operator(6, bottom, w)
            else:
                self.draw_operator_locked(6, bottom, w)
        elif self.tab == 10:
            self.draw_workers(6, bottom, w)
        else:
            self.draw_help(6, bottom, w)
        self.put(h-3, 0, "─"*(w-1), "muted")
        refreshed = fmt_age(dt(self.snap.github_updated), now)
        gh_txt = "LIVE" if gh_ok else ("CACHED" if self.snap.github_updated else "OFFLINE")
        bottom_left = f" GitHub {gh_txt} · {refreshed}  │  systemd {health}  │  workers(plan) {self.workers} "
        self.put(h-2, 1, bottom_left, "yellow" if not gh_ok else "muted", w-3)
        foot = (" TAB / 1-9 pages  W workers  0 owner ops  G sync  ? help  Q quit "
                if self.snap.repo_role in operator_ops.OWNER_ROLES else
                " TAB / 1-9 pages  W workers  0 owner ops LOCKED  G sync  ? help  Q quit ")
        self.put(h-1, 1, foot, "muted", w-3)
        if self.notice and time.monotonic() < self.notice_until:
            self.put(h-4, 2, self.notice, "yellow", w-4)
        if self.merge_phrase is not None:
            self.modal('TYPE '+self.merge_phrase+' TO MERGE: '+self.merge_input+'_   (ESC cancels)', 'red')
        if self.confirm:
            warning = ("This interrupts active Codex; saved work remains. Y continue / N cancel"
                       if self.confirm in ("stop", "restart") else
                       "Confirm " + self.confirm + " command?  Y yes / N cancel")
            self.modal(warning, "red")
        screen.refresh()

    def modal(self, msg: str, color="yellow"):
        h, w = self.stdscr.getmaxyx()
        size = min(w-8, max(len(msg)+6, 35))
        x = max(2, (w-size)//2)
        y = max(5, h//2)
        self.put(y-1, x, "╭"+"─"*(size-2)+"╮", color, size)
        self.put(y, x, "│"+" "*(size-2)+"│", color, size)
        self.put(y, x+2, msg, color, size-4)
        self.put(y+1, x, "╰"+"─"*(size-2)+"╯", color, size)

    def draw_pulse(self, y, end, w, counts, events, est, health, description):
        usable = end - y
        left = max(41, int(w*.58))
        right = w-left-3
        left_x, right_x = 1, left+1
        self.frame(y, left_x, 10, left-1, " WORKER TELEMETRY ")
        self.put(y+2, 4, "SYSTEMD", "muted")
        self.put(y+2, 17, health, "green" if health == "RUNNING" else "yellow" if health in {"QUIET", "ATTENTION", "IDLE"} else "red")
        self.put(y+3, 4, "DETAIL", "muted")
        self.put(y+3, 17, description, "normal", left-22)
        self.put(y+4, 4, "MAIN PID", "muted")
        self.put(y+4, 17, self.snap.service.get("pid", "?"), "normal")
        self.put(y+5, 4, "ACTIVE TASK", "muted")
        in_flight = events.get("unfinished") or []
        task = in_flight[-1] if in_flight else None
        last = events.get("last_task")
        self.put(y+5, 17, f"#{task}" if task else
                 f"None (last #{last})" if last else "None observed", "cyan")
        self.put(y+6, 4, "LAST LOG", "muted")
        self.put(y+6, 17, fmt_age(events.get("last_event")), "normal")
        self.put(y+7, 4, "AI SESSIONS", "muted")
        self.put(y+7, 17, str(len(events.get("sessions", []))) + " observed in retained journal", "normal", left-23)
        self.put(y+8, 4, "RESTARTS", "muted")
        self.put(y+8, 17, self.snap.service.get("restarts", "?"), "normal")
        self.frame(y, right_x, 10, right, " TASK PIPELINE ")
        stages = [("QUEUED", "cyan"), ("CLAIMED", "blue"), ("REVIEW", "yellow"),
                  ("BLOCKED", "red"), ("INTEGRATED", "green"), ("VERIFIED", "green")]
        for i, (s, col) in enumerate(stages):
            self.put(y+2+i, right_x+3, f"{s:<12} {counts.get(s,0):>5}", col)
            if right > 27:
                self.bar(y+2+i, right_x+22, right-26, counts.get(s,0), max(1, counts["TOTAL"]), col)
        if usable < 22:
            self.put(y+11, 4, "Enlarge terminal to see ETA and activity sections.", "muted")
            return
        self.frame(y+11, left_x, 9, left-1, " TIME MACHINE  •  ESTIMATES ")
        self.put(y+13, 4, "TASKS LEFT", "muted")
        self.put(y+13, 22, str(est["remaining_queue"]), "cyan")
        for i, (name, color) in enumerate((('fast','green'),('likely','cyan'),('slow','yellow'))):
            txt = duration(est["queue_seconds"][name])
            self.put(y+14+i, 4, f"{name.upper():<13}", color)
            self.put(y+14+i, 22, txt, "normal")
        self.put(y+18, 4, "For current queue, not the whole game", "muted", left-8)
        self.frame(y+11, right_x, 9, right, " SIGNALS  •  LIVE ")
        self.put(y+13, right_x+3, f"Merged PRs: {est['stats']['merged']}", "green")
        self.put(y+14, right_x+3, f"Open PRs:   {est['stats']['open']}", "normal")
        self.put(y+15, right_x+3, f"CI runs:    {len(self.snap.runs)} shown", "normal")
        self.put(y+16, right_x+3, f"7-day PR merges: {est['stats']['merged_7d']}", "normal")
        self.put(y+17, right_x+3, f"AI median: {duration(est['median_ai_seconds'])}", "normal")
        cov = self.snap.coverage or {}
        if usable >= 24:
            self.put(y+20, 4,
                     f"FUNCTIONS  identified {cov.get('known','?')} / original total UNKNOWN   |  "
                     f"PR merged {cov.get('integrated','?')}   |  independently verified {cov.get('verified','?')}",
                     "cyan", w-9)
        if usable >= 25:
            self.frame(y+21, left_x, min(usable-21, 6), w-3, " RECENT WORKER TRAFFIC ")
            for i, line in enumerate(reversed(self.snap.logs[-max(1, min(usable-24,3)):])):
                color = "red" if "FAILED" in line or "ALERT" in line else "cyan" if "session" in line else "muted"
                self.put(y+23+i, 4, line[-(w-10):], color, w-10)

    def _table(self, y, end, w, title, records, formatter, selector_key):
        self.frame(y, 1, end-y-1, w-3, title)
        height = max(1, end-y-6)
        i = min(self.indices[selector_key], max(0, len(records)-1))
        self.indices[selector_key] = i
        start = max(0, min(i-max(1, height//2), max(0, len(records)-height)))
        self.put(y+2, 4, f"{len(records)} records  ·  ↑/↓ select  ·  O open selected in browser", "muted", w-9)
        for index, record in enumerate(records[start:start+height], start):
            line, style = formatter(record, w)
            if index == i:
                self.put(y+4+index-start, 3, "▶", "cyan")
                self.put(y+4+index-start, 6, line, "cyan", w-12)
            else:
                self.put(y+4+index-start, 6, line, style, w-12)
        if not records:
            self.put(y+5, 5, "No records returned. Check GitHub refresh status.", "yellow")

    def draw_tasks(self, y, end, w, counts):
        issues = [x for x in self.snap.issues if is_task(x)]
        stage_order = {"BLOCKED":0, "CLAIMED":1, "REVIEW":2, "QUEUED":3,"INTEGRATED":4,"VERIFIED":5,"CLOSED*":6,"OTHER":7}
        merged = merged_issue_ids(self.snap.prs)
        issues.sort(key=lambda x: (stage_order.get("INTEGRATED" if x["number"] in merged else issue_stage(x), 8), -(x.get("number") or 0)))
        self.active_records = issues
        def fmt(issue, width):
            stage = "INTEGRATED" if issue["number"] in merged and issue_stage(issue) in ("REVIEW", "CLOSED*", "CLAIMED") else issue_stage(issue)
            label = f"#{issue['number']:<5} [{stage:<10}]  {issue.get('title','')}"
            return label, "red" if stage == "BLOCKED" else "green" if stage == "INTEGRATED" else "yellow" if stage == "REVIEW" else "normal"
        self._table(y, end, w, " GITHUB TASK LEDGER  •  CLOSED ≠ VERIFIED ", issues, fmt, "tasks")

    def draw_prs(self, y, end, w):
        prs = sorted(self.snap.prs, key=lambda x: (x.get("state") != "OPEN", -(x.get("number") or 0)))
        self.active_records = prs
        def fmt(pr, width):
            state = "MERGED" if pr.get("mergedAt") else "DRAFT" if pr.get("isDraft") and pr.get("state") == "OPEN" else pr.get("state", "?")
            return f"{'*' if pr.get('number') in self.pr_marked else ' '}#{pr.get('number',0):<5} [{state:<8}] [+{pr.get('additions',0)}/-{pr.get('deletions',0)}] {pr.get('title','')}", "green" if state=="MERGED" else "yellow" if state=="DRAFT" else "normal"
        self._table(y,end,w,f" PULL REQUEST RADAR  •  {len(self.pr_marked)} MARKED  •  B mark / A all / C scan ",prs,fmt,"prs")

    def draw_actions(self, y, end, w):
        runs = sorted(self.snap.runs, key=lambda x: x.get("createdAt", ""), reverse=True)
        self.active_records = runs
        def fmt(run, width):
            status = (run.get("conclusion") or run.get("status") or "unknown").upper()
            return f"{str(run.get('workflowName') or 'unnamed')[:24]:<24} [{status:<12}]  {run.get('displayTitle','')}", "green" if status=="SUCCESS" else "red" if status=="FAILURE" else "yellow" if status=="IN_PROGRESS" else "normal"
        self._table(y,end,w," GITHUB ACTIONS  •  AUTOMATIC TASK FEEDER & CI ",runs,fmt,"actions")
        if self.snap.last_action:
            self.put(y+3, 4, "Last control: "+self.snap.last_action, "yellow", w-8)

    def filtered_logs(self) -> list[str]:
        if self.log_mode == 1:
            return [x for x in self.snap.logs if any(y in x.upper() for y in ("FAILED", "ERROR", "ALERT", "BLOCKED", "WARNING"))]
        if self.log_mode == 2:
            return [x for x in self.snap.logs if any(y in x for y in ("Trying #", "AI session", "session finished", "No queued", "PR", "reusing", "BLOCKED"))]
        return self.snap.logs

    def draw_logs(self, y, end, w):
        self.frame(y, 1, end-y-1, w-3, " JOURNALCTL  •  LIVE SYSTEMD SERVICE OUTPUT ")
        modes = ["ALL", "WARNINGS", "TASK SIGNALS"]
        self.put(y+2, 4, f"Filter: {modes[self.log_mode]}  [E] change filter   PgUp/PgDn scroll  [End] newest", "muted", w-8)
        logs = self.filtered_logs()
        height = max(1, end-y-6)
        maxoffset = max(0, len(logs)-height)
        offset = max(0, min(self.scroll["logs"], maxoffset))
        self.scroll["logs"] = offset
        stop = len(logs)-offset
        start = max(0, stop-height)
        for i, line in enumerate(logs[start:stop]):
            lower = line.lower()
            col = "red" if "failed" in lower or "alert" in lower or "error" in lower else "yellow" if "blocked" in lower else "green" if "exit=0" in lower else "cyan" if "provider launched" in lower else "normal"
            self.put(y+4+i, 4, line, col, w-9)
        self.put(end-3, 4, f"{len(logs)} lines   ↓ {offset} older lines hidden   source: journalctl --user -u {self.collector.service}", "muted", w-9)

    def draw_forecast(self, y, end, w, counts, events, est):
        left = max(41, int(w*.52))
        right = w-left-3
        self.frame(y, 1, min(16,end-y-1), left-1, " QUEUE CLEARANCE SCENARIOS ")
        self.put(y+2, 4, f"Open work: {est['remaining_queue']} tasks", "cyan")
        self.put(y+3, 4, f"Planning parallelism: {self.workers} worker(s)   +/- adjust", "normal", left-7)
        for idx, (name, col) in enumerate((('fast','green'),('likely','cyan'),('slow','yellow'))):
            secs = est["queue_seconds"][name]
            self.put(y+5+idx*2, 4, f"{name.upper():<8} {duration(secs):<12} @ {est['per_task_minutes'][name]}m/task", col, left-7)
        self.put(y+12, 4, "EMPIRICAL MERGE VELOCITY", "muted")
        self.put(y+13, 4, f"{est['empirical_merges_per_day']:.2f} PRs/day" if est["empirical_merges_per_day"] is not None else "Insufficient merged PR history", "normal")
        self.put(y+14, 4, f"Queue ETA at measured rate: {duration(est['throughput_seconds'])}", "normal", left-8)
        self.frame(y, left+1, min(16,end-y-1), right, " INVENTORY PROJECTION ")
        self.put(y+2, left+4, f"Scope: {est['functions_scope']}", "muted", right-8)
        self.put(y+4, left+4, f"Known addresses: {est['known_functions']}", "normal")
        self.put(y+5, left+4, f"PR-merged addresses: {est['integrated_function_addresses']}", "normal")
        verified = est["verified_ledger_functions"]
        self.put(y+6, left+4, "Ledger-verified: " + (str(verified) if verified is not None else "unknown"), "green")
        self.put(y+7, left+4, f"Batch equivalents left: {est['function_batches'] if est['function_batches'] is not None else '?'}", "cyan")
        for idx, (name, col) in enumerate((('fast','green'),('likely','cyan'),('slow','yellow'))):
            self.put(y+9+idx, left+4, f"{name.upper():<8} {duration(est['function_seconds'].get(name))}", col)
        if end-y > 19:
            panel_h = min(14, end-y-18)
            self.frame(y+17, 1, panel_h, w-3, " HYPOTHETICAL WHOLE-GAME ETA  •  VERY LOW CONFIDENCE ")
            self.put(y+19, 4, "Assume known functions represent 75% / 30% / 10% of all functions (unverified)", "yellow", w-8)
            for j, (mode, col) in enumerate((("fast", "green"), ("likely", "cyan"), ("slow", "yellow"))):
                self.put(y+20+j, 4, f"{mode.upper():<8} {duration(est['whole_game_seconds'].get(mode)):<12} if currently identified functions cover {est['hypothetical_inventory_coverage'][mode]:.0%} of the game", col, w-8)
            if panel_h > 8:
                self.put(y+24, 4, "Excludes porting, graphics upgrades, review delays, missing binaries and major discoveries", "muted", w-8)
                self.put(y+25, 4, "Assumptions: " + est["assumptions"], "muted", w-8)
                if self.snap.history:
                    history = self.snap.history
                    self.put(y+27, 4, "MERGED PR TREND  " + simple_sparkline([x.get("merged", 0) for x in history], min(40,w-30)), "green", w-8)

    def draw_source_map(self, y, end, w):
        cov=self.snap.coverage or {}
        libs=cov.get('libraries') or []
        self.frame(y,1,end-y-1,w-3," RECONSTRUCTION ADDRESS ATLAS  •  LEDGER-BOUNDED ")
        self.put(y+2,4,"KNOWN IDENTIFIED",'muted')
        self.put(y+2,24,str(cov.get('known','?'))+" / original total UNKNOWN",'cyan',w-31)
        self.put(y+3,4,"PR INTEGRATED",'muted')
        self.put(y+3,24,f"{cov.get('integrated','?')}   (source committed, not equivalent)",'green',w-31)
        self.put(y+4,4,"VERIFIED MATCH",'muted')
        self.put(y+4,24,f"{cov.get('verified','?')}   (explicit ledger evidence only)",'green',w-31)
        self.put(y+5,4,"LOCAL CPP FILES",'muted')
        self.put(y+5,24,str(cov.get('local','?'))+' task-specific files found','normal',w-31)
        if cov.get('known'):
            self.bar(y+6,4,min(w-10,68),cov.get('integrated',0),cov['known'],'cyan')
            self.put(y+7,4,f"Merged / identified: {cov.get('integrated',0)/cov['known']:.1%}  — NOT whole-game coverage",'yellow',w-9)
        else:
            self.put(y+7,4,"No machine-readable function boundaries found; use the ledger first.",'yellow',w-9)
        self.put(y+9,4,"GLYPHS: ░ identified   ▦ locally generated   ▒ submitted   ▓ merged   █ verified",'muted',w-9)
        self.put(y+10,4,"[ / ] select library   M toggle stacked map   addresses are per known task, not whole ELF byte ranges",'muted',w-9)
        if self.map_mode == 0:
            if libs:
                index=self.map_library%len(libs)
                lib=libs[index]
                self.put(y+12,4,f"LIBRARY {index+1}/{len(libs)}  {lib['name']}",'cyan',w-9)
                self.put(y+13,4,f"identified={lib['identified']}   integrated={lib['integrated']}   verified={lib['verified']}   local={lib['local']}",'normal',w-9)
                tile_width=min(max(6,w-10),110)
                tiles=progress_map.addr_tiles(lib['functions'],tile_width)
                self.put(y+15,4,tiles,'green',w-9)
                # Motion is a scanning cursor, not another fake verification state.
                cursor=int(time.monotonic()*5)%max(1,len(tiles))
                self.put(y+14,4+cursor,'▾','magenta')
                items=lib['functions']
                if items:
                    self.put(y+16,4,f"0x{items[0]['addr']:08x}".ljust(max(15,tile_width//2)) + f"0x{items[-1]['addr']:08x}", 'muted',w-9)
                self.put(y+18,4,"UNMAPPED REGION: library functions outside the verified ledger remain UNKNOWN",'yellow',w-9)
            else:
                self.put(y+12,4,"No function library discovered in local ledger or GitHub tasks.",'yellow',w-9)
        else:
            lines=max(0,end-y-15)
            for i,lib in enumerate(libs[:lines]):
                yy=y+12+i
                name=lib['name'][:24]
                tiles=progress_map.addr_tiles(lib['functions'],max(4,min(w-43,67)))
                self.put(yy,4,f"{name:<24} {lib['integrated']:>5}/{lib['identified']:<5}",'cyan',36)
                self.put(yy,41,tiles,'green',max(1,w-47))
            self.put(end-3,4,"Scope: "+cov.get('coverage_scope','unknown'), 'yellow',w-9)

    def draw_apk_atlas(self, y, end, w):
        cov=self.snap.coverage or {}
        pkg=cov.get('package') or {}
        src=cov.get('source') or {}
        self.frame(y,1,end-y-1,w-3," SOURCE / APK COMPONENT ATLAS   (METADATA ONLY) ")
        self.put(y+2,4,f"Native source tree: {src.get('files',0)} files  /  {src.get('lines',0):,} lines  /  {src.get('test_files',0)} test files",'cyan',w-9)
        self.put(y+3,4,"Measured source LOC is not a percent of the original game's codebase.",'muted',w-9)
        self.put(y+5,4,"APK COMPONENT".ljust(21)+"ENTRY COUNT".ljust(19)+"ORIGINAL EQUIVALENCE",'yellow',w-9)
        for i,kind in enumerate(progress_map.CATEGORIES):
            if y+7+i>=end-3:break
            count=(pkg.get('component_counts') or {}).get(kind)
            amount=str(count) if count is not None else "UNKNOWN"
            self.put(y+7+i,4,f"{kind:<21}{amount:<19}UNMEASURED",'normal',w-9)
        yy=y+15
        if yy<end-3:
            files=pkg.get('apks') or []
            self.put(yy,4,f"APK archives indexed: {len(files)}    ELF references located: {len(pkg.get('libraries') or [])}",'cyan',w-9)
            for n,entry in enumerate(files[:max(0,min(4,end-yy-5))]):
                self.put(yy+2+n,6,f"{entry['name']}  [{entry['entries']} ZIP entries; contents NOT extracted]",'muted',w-13)
        py=y+23
        if py<end-5:
            self.put(py,4,"RECONSTRUCTED SOURCE AREAS",'cyan')
            for i,(area,count) in enumerate(sorted((src.get('areas') or {}).items(),key=lambda x:-x[1])[:max(0,end-py-5)]):
                self.put(py+1+i,6,f"{area:<40} {count:>5} files",'normal',w-11)
        self.put(end-3,4,"Unknown APK bytes/functions/assets stay UNKNOWN; no copied binaries or inflated percentages.",'yellow',w-9)

    def draw_roadmap(self, y, end, w):
        plan=self.snap.roadmap or {}
        status=progress_map.roadmap_status(self.snap.issues,self.snap.prs,plan)
        base=build_estimates(self.snap.issues,self.snap.prs,self.snap.inventory,
                             parse_log_events(self.snap.logs),self.workers)
        horizon=progress_map.project_forecast(status,base,self.workers)
        self.frame(y,1,end-y-1,w-3," LONG-RUNNING ROADMAP  •  ONE AI ISSUE POOL ")
        self.put(y+2,4,"RECONSTRUCT  >  READABLE CORE  >  NATIVE PORTS  >  RUNTIME  >  EXTENSIONS",'cyan',w-9)
        self.put(y+3,4,"Engineering gate progress != original-game parity or verified gameplay.",'yellow',w-9)
        self.put(y+4,4,"PROJECT ETA*   "+'  |  '.join(
            mode.upper()+': '+duration(horizon['seconds'].get(mode)) for mode in ('fast','likely','slow')),
            'cyan',w-9)
        self.put(y+5,4,"* Very speculative: known inventory = 75%/30%/10% of original + estimated port/QA effort",'muted',w-9)
        self.put(y+6,4,"Targets: Windows/Linux x86_64 · Android ARM64/x86_64 · iOS ARM64 · iOS Simulator",'muted',w-9)
        stages=status['stages']
        start=y+8
        for i,stage in enumerate(stages[self.roadmap_offset:]):
            yy=start+i*3
            if yy+2>=end-4: break
            count=stage['count']; merged=stage['merged']
            text=f"{stage['name']}  {merged}/{count} integrated | queued {stage['queued']}  claimed {stage['claimed']}  review {stage['review']}  blocked {stage['blocked']}"
            self.put(yy,4,text,'green' if count and merged==count else 'cyan',w-9)
            self.bar(yy+1,4,min(50,w-11),merged,max(1,count))
            self.put(yy+2,5,'Gate: '+stage['gate']+'   |   not issued '+str(stage['unissued']),'muted',w-10)
        if end-y>=31:
            yy=end-7
            self.put(yy,4,"FORWARD PLANNING (NOT PROOF OF READINESS)",'yellow',w-9)
            for i,(name,values) in enumerate(list(horizon.get('stage_seconds',{}).items())[:3]):
                self.put(yy+1+i,6,f"{name:<18} likely {duration(values.get('likely'))} labour ETA",'muted',w-11)
        self.put(end-3,4,"No new AI accounts. For actual features: nova3-feature 'Describe feature'",'muted',w-9)

    def draw_operator_locked(self, y, end, w):
        self.frame(y, 1, end-y-1, w-3, ' OWNER OPS  •  LOCKED ')
        self.put(y+2, 4, 'Owner actions require ADMIN or MAINTAIN permission for this repository.', 'yellow', w-10)
        self.put(y+4, 4, 'Your authenticated GitHub role: '+self.snap.repo_role, 'muted', w-10)
        self.put(y+6, 4, 'Use your own GitHub login. Access is checked again before every privileged action.', 'muted', w-10)
        self.put(y+8, 4, 'All telemetry, task, PR, coverage, worker and roadmap pages remain available.', 'cyan', w-10)
        if self.snap.repo_role == 'UNKNOWN':
            self.put(y+10, 4, 'GitHub role could not be verified; owner operations fail closed.', 'red', w-10)

    def draw_operator(self, y, end, w):
        self.frame(y, 1, end-y-1, w-3, ' PRIVATE OWNER CONTROL DECK  •  SAME WORKER / SAME REPOSITORY ')
        d=self.snap.discovery
        timer=d.get('timer',{})
        scan=d.get('service',{})
        git=self.snap.git_alignment
        counts=task_counts(self.snap.issues,self.snap.prs)
        idle=not(counts['QUEUED'] or counts['CLAIMED'] or counts['REVIEW'])
        if idle:
            self.put(y+1,4, 'QUEUE EXHAUSTED: discovery must publish more function metadata', 'red',w-10)
        self.put(y+2,4,'DISCOVERY TIMER', 'cyan')
        timer_on=timer.get('ActiveState')=='active'
        self.put(y+3,4,'Timer: '+('ACTIVE' if timer_on else 'OFFLINE / NOT INSTALLED')+
                  f"  •  file={'yes' if d.get('timer_file') else 'no'}  •  engine={'yes' if d.get('module') else 'no'}",
                  'green' if timer_on else 'red',w-10)
        self.put(y+4,4,f"Scan engine: {scan.get('ActiveState','unknown')}  |  Result: {scan.get('Result','unknown')}",
                  'muted',w-10)
        self.put(y+5,4,'D repair/install timer   N scan now   U enable/disable timer', 'yellow',w-10)
        if self.snap.discovery_logs:
            self.put(y+6,4,'Last scan: '+self.snap.discovery_logs[-1][-max(10,w-22):], 'muted',w-12)
        self.put(y+7,4,'GIT BRANCH ALIGNMENT', 'cyan')
        self.put(y+8,4,f"Local {git.get('branch','?')}  ahead {git.get('ahead','?')}  behind {git.get('behind','?')}  dirty {git.get('dirty','?')}",
                  'muted',w-10)
        self.put(y+9,4,git.get('note','GitHub merges update REMOTE main, not your local checkout.')[:w-12],
                  'yellow' if git.get('behind') not in ('0','?') else 'muted',w-10)
        self.put(y+11,4,'BULK PR MERGE  •  SQUASH, CURRENT USER ONLY BY DEFAULT', 'cyan')
        self.put(y+12,4,f"Marked PRs: {len(self.pr_marked)}  |  Others: {'INCLUDED' if self.include_others else 'EXCLUDED'}"
                  f"  |  No-CI: {'ALLOW' if self.allow_no_checks else 'DENY'}",'muted',w-10)
        self.put(y+13,4,f"Preview: {self.snap.merge_status}  {self.snap.merge_summary}",
                  'green' if self.snap.merge_status=='READY' else 'muted',w-10)
        self.put(y+14,4,'3 PR page: B mark, A all, Z clear. C scan/confirm. I other authors. J no-CI.',
                  'yellow',w-10)
        team=presence_view.summary(self.snap.worker_presence)
        self.put(y+15,4,f"W contributor health: {team['TOTAL']} reporting  |  {team['WORKING']} busy  |  {team['IDLE']} idle  |  {team['ERROR']} errors  |  {team['STALE']} stale",
                  'yellow' if team['ERROR'] or team['STALE'] else 'muted',w-10)
        self.put(y+16,4,'P export all diagnostics privately  |  F trigger GitHub task feeder', 'cyan',w-10)
        visible=max(0,end-(y+19))
        for i,msg in enumerate(self.snap.operator_events[-visible:]):
            self.put(y+18+i,4,msg,'red' if any(x in msg for x in ('failed','SKIPPED','ERROR')) else 'muted',w-10)

    def draw_workers(self, y, end, w):
        self.frame(y, 1, end-y-1, w-3, ' CONTRIBUTOR HEALTH  •  GITHUB SIGNALS / NO CREDENTIAL ACCESS ')
        records=self.snap.worker_presence
        totals=presence_view.summary(records)
        error=self.snap.presence_error
        claim_count=sum(issue_stage(i)=='CLAIMED' for i in self.snap.issues if is_task(i))
        self.put(y+1,4, f"Reporting: {totals['TOTAL']}  |  WORKING: {totals['WORKING']}"
                 f"  IDLE: {totals['IDLE']}  ERROR: {totals['ERROR']}"
                 f"  STALE: {totals['STALE']}  other: {totals['OTHER']}",
                 'yellow' if totals['ERROR'] or totals['STALE'] else 'green',w-10)
        when=fmt_age(dt(self.snap.presence_updated))
        if error:
            self.put(y+2,4,'DATA UNAVAILABLE: '+error,'yellow',w-10)
        else:
            self.put(y+2,4,f"Presence refreshed {when}  |  GitHub claimed issues: {claim_count}",'muted',w-10)
        if not records:
            self.put(y+4,4,'No upgraded worker heartbeats available yet. This does NOT prove workers are offline.',
                     'yellow',w-10)
            self.put(y+6,4,'Publish the shared presence PR, merge it, and have contributors update/restart.',
                     'muted',w-10)
            self.put(y+8,4,'Old clients: only GitHub claims/PR activity are observable.', 'muted',w-10)
        else:
            self.put(y+4,4,f"{'GITHUB ACCOUNT':<22} {'STATE':<11} {'AI':<9} {'ISSUE':<9} {'LAST SIGNAL':<15} DEVICE", 'cyan',w-10)
            priority={'ERROR':0,'STALE':1,'STOPPED':2,'IDLE':3,'WORKING':4}
            items=sorted(records,key=lambda r:(priority.get(presence_view.classify(r)[0],5),r['owner']))
            for n,item in enumerate(items[:max(0,end-y-9)]):
                state,secs=presence_view.classify(item)
                color='red' if state in ('ERROR','STALE','STOPPED') else ('green' if state=='WORKING' else 'muted')
                age=(f'{secs//60}m ago' if secs>=60 else f'{secs}s ago') if secs>=0 else '?'
                line=f"{item['owner'][:22]:<22} {state:<11} {item['provider'][:9]:<9} {('#'+str(item['issue'])) if item.get('issue') else '—':<9} {age:<15} {item['worker'][:8]}"
                self.put(y+5+n,4,line,color,w-10)
        self.put(end-4,4,'Stale = no GitHub status update for >10 min; NOT proof of logout or idling.',
                 'yellow',w-10)
        self.put(end-3,4,'Old clients and machines without telemetry remain UNMONITORED.',
                 'muted',w-10)
        self.put(end-2,4,'W opens this page  |  G refreshes  |  TAB navigates  |  ? help',
                 'muted',w-10)

    def draw_help(self, y, end, w):
        self.frame(y, 1, end-y-1, w-3, " OPERATOR GUIDE  •  NO WORKER MODIFICATIONS ")
        lines = [
            ("WORKERS", "W monitors opt-in contributor heartbeats, idle/error/stale signals."),
            ("SOURCE MAP", "7 shows per-library identified/recovered/merged/verified map; [ ] cycle libraries."),
            ("APK ATLAS", "8 shows discovered original APK zip entries and reference .so filenames."),
            ("ROADMAP", "9 shows staged native ports and future feature extension; ↑↓ scroll."),
            ("MAP MODE", "M switches between selected-library and stacked-address map views."),
            ("NAVIGATION", "1–9,0 jump pages   TAB / Shift-TAB cycle   ↑↓ or J/K select   O opens browser"),
            ("GITHUB", "G immediately refreshes issues, PRs and workflows. F triggers feeder (confirm)."),
            ("WORKER", "S start service (confirm)   X stop service (confirm)   R restart (confirm)"),
            ("LOGS", "E cycles log filters   PgUp/PgDn scroll back   End jumps to live tail"),
            ("ESTIMATES", "+/- change assumed worker parallelism (1–32). Not actual detected workers."),
            ("APPEARANCE", "T switches highlight palette   Q exits TUI (worker stays running)"),
            ("OWNER OPS", "0 opens private controls: timer, one-shot scans, bulk PR merge, diagnostics."),
            ("BULK MERGE", "On PR page B marks a PR, A marks all, Z clears; C checks GitHub then confirms."),
            ("PR SECURITY", "Requires typed MERGE N. Rechecks CI/commit/mergeability before each squash."),
            ("TIMER", "On OWNER OPS press D repair, N scan now, U toggle timer (confirmed)."),
            ("EXPORT", "P saves diagnostic report under private state folder for attachments."),
            ("SAFETY", "Worker service controls require confirmation. Merge never bypasses GitHub rules."),
            ("PRIVACY", "Presence uses private GitHub metadata; never transfers game source or credentials."),
            ("NETWORK", "Uses your existing gh CLI login and reads systemd journal locally."),
            ("FRESHNESS", "GitHub refresh ~70s; journals ~4s; stale data retained on API failures."),
            ("PRESENCE", "Needs upgraded workers; no token, IP, hostname or decompiler data sent."),

            ("RELIABILITY", "Worker exit code 3 stops systemd restarts by design; this UI won't override it."),
            ("CAUTION", "Planning ETAs are speculative; a merged PR is not a verified byte match."),
            ("FUNCTIONS", "Only grounded ledger addresses are mapped. No invented total or byte match."),
            ("LIFECYCLE", "Project roadmap tasks use existing nova:queued AI worker pool."),
            ("EXPANSION", "Gameplay features require a concrete spec; roadmap handles scaffolding."),
        ]
        for i, (left, right) in enumerate(lines[:max(0,end-y-5)]):
            self.put(y+2+i, 4, f"{left:<14}", "cyan")
            self.put(y+2+i, 20, right, "normal", w-26)

    def show_notice(self, msg):
        self.notice = msg
        self.notice_until = time.monotonic() + 8

    def selected_url(self):
        if self.tab not in (1,2,3):
            return None
        records = getattr(self, "active_records", [])
        key = {1:"tasks",2:"prs",3:"actions"}[self.tab]
        idx = self.indices[key]
        if 0 <= idx < len(records):
            return records[idx].get("url")
        return None

    def handle(self, key) -> bool:
        if self.merge_phrase is not None:
            if key in (27,):
                self.merge_phrase = None
                self.merge_input = ''
                self.show_notice('Merge cancelled')
            elif key in (10,13,curses.KEY_ENTER):
                expected=self.merge_phrase
                supplied=self.merge_input
                self.merge_phrase=None
                self.merge_input=''
                if supplied==expected:
                    self.collector.execute_merge(self.include_others,self.allow_no_checks)
                    self.show_notice('Merge batch started; monitor OWNER OPS')
                else:
                    self.show_notice('Confirmation did not match; no merges started')
            elif key in (curses.KEY_BACKSPACE,127,8):
                self.merge_input=self.merge_input[:-1]
            elif 32<=key<127 and len(self.merge_input)<32:
                self.merge_input+=chr(key)
            return True
        if self.confirm:
            if key in (ord('y'), ord('Y')):
                action = self.confirm
                self.confirm = None
                self.collector.perform_action(action)
                self.show_notice("Executing: " + action)
            elif key in (ord('n'), ord('N'), 27):
                self.confirm = None
            return True
        if key in (ord('q'), ord('Q')):
            return False
        if key == ord('?'):
            self.tab = len(TABS)-1
            return True
        if key in (ord('w'),ord('W')):
            self.tab = 10
            return True
        if ord('1') <= key <= ord('9'):
            self.tab = key-ord('1')
            return True
        if key == ord('0'):
            if self.snap.repo_role in operator_ops.OWNER_ROLES and not self.collector.demo:
                self.tab = 9
            else:
                self.show_notice('OWNER OPS LOCKED: ADMIN/MAINTAIN role required')
            return True
        if key in (9, curses.KEY_RIGHT):
            self.tab = (self.tab+1)%len(TABS)
        elif key in (curses.KEY_BTAB, curses.KEY_LEFT):
            self.tab = (self.tab-1)%len(TABS)
        elif key in (ord('g'),ord('G')):
            self.collector.force_refresh()
            self.show_notice("GitHub refresh requested")
        elif key in (ord('b'),ord('B')) and self.tab==2:
            records=sorted(self.snap.prs,key=lambda x:(x.get('state')!='OPEN',-(x.get('number') or 0)))
            idx=self.indices['prs']
            if 0<=idx<len(records) and records[idx].get('state')=='OPEN':
                n=records[idx]['number']
                if n in self.pr_marked:self.pr_marked.remove(n)
                else:self.pr_marked.add(n)
                self.collector.merge_preview=None
                with self.collector.lock:self.collector.snapshot.merge_status='NONE'
                self.show_notice(f'Marked for review: {sorted(self.pr_marked)}')
        elif key in (ord('a'),ord('A')) and self.tab==2:
            self.pr_marked={p['number'] for p in self.snap.prs if p.get('state')=='OPEN'
                            and str(p.get('headRefName','')).startswith('nova/issue-')}
            self.collector.merge_preview=None
            with self.collector.lock:self.collector.snapshot.merge_status='NONE'
            self.show_notice(f'Marked {len(self.pr_marked)} open worker PRs; CI will be checked')
        elif key in (ord('z'),ord('Z')) and self.tab in (2,9):
            self.pr_marked.clear()
            self.collector.merge_preview=None
            with self.collector.lock:self.collector.snapshot.merge_status='NONE'
            self.show_notice('PR selection cleared')
        elif key in (ord('c'),ord('C')) and self.tab in (2,9):
            if self.snap.repo_role not in operator_ops.OWNER_ROLES or self.collector.demo:
                self.show_notice('Bulk merge locked: GitHub ADMIN/MAINTAIN permission required')
                return True
            if self.snap.merge_status=='READY' and self.snap.merge_ready:
                self.merge_phrase=f'MERGE {len(self.snap.merge_ready)}'
                self.merge_input=''
                self.tab=9
                self.show_notice('Typed confirmation required; PRs rechecked before merging')
            else:
                self.collector.prepare_merges(sorted(self.pr_marked) if self.pr_marked else None,
                    self.include_others,self.allow_no_checks)
                self.tab=9
                self.show_notice('Scanning merge eligibility; press C again after READY')
        elif key in (ord('i'),ord('I')) and self.tab==9:
            self.include_others=not self.include_others
            self.collector.merge_preview=None
            with self.collector.lock:self.collector.snapshot.merge_status='NONE'
            self.show_notice('Other contributor PRs '+('INCLUDED: review carefully' if self.include_others else 'excluded'))
        elif key in (ord('j'),ord('J')) and self.tab==9:
            self.allow_no_checks=not self.allow_no_checks
            self.collector.merge_preview=None
            with self.collector.lock:self.collector.snapshot.merge_status='NONE'
            self.show_notice('No-CI merges '+('ALLOWED: tests absent!' if self.allow_no_checks else 'disabled'))
        elif key in (ord('d'),ord('D')) and self.tab==9:
            self.confirm='discovery-repair'
        elif key in (ord('n'),ord('N')) and self.tab==9:
            self.confirm='discovery-now'
        elif key in (ord('u'),ord('U')) and self.tab==9:
            self.confirm='discovery-toggle'
        elif key in (ord('p'),ord('P')) and self.tab==9:
            self.confirm='export-diagnostics'
        elif key in (ord('t'), ord('T')):
            self.theme = 1-self.theme
        elif key in (ord('m'),ord('M')) and self.tab == 6:
            self.map_mode = 1 - self.map_mode
        elif key == ord('[') and self.tab == 6:
            self.map_library = max(0,self.map_library-1)
        elif key == ord(']') and self.tab == 6:
            self.map_library += 1
        elif self.tab == 8 and key in (curses.KEY_UP,ord('k'),ord('K')):
            self.roadmap_offset=max(0,self.roadmap_offset-1)
        elif self.tab == 8 and key in (curses.KEY_DOWN,ord('j'),ord('J')):
            self.roadmap_offset+=1
        elif key in (ord('+'),ord('=')):
            self.workers = min(32, self.workers+1)
        elif key == ord('-'):
            self.workers = max(1, self.workers-1)
        elif key in (ord('s'),ord('S')):
            self.confirm = "start"
        elif key in (ord('x'),ord('X')):
            self.confirm = "stop"
        elif key in (ord('r'),ord('R')):
            self.confirm = "restart"
        elif key in (ord('f'),ord('F')):
            self.confirm = "feed"
        elif key in (ord('o'),ord('O'),10,13):
            url = self.selected_url()
            if url and url.startswith(f"https://github.com/{self.collector.repo}/"):
                # Must be user-driven. Never interpolate URL into a shell command.
                webbrowser.open(url, new=2)
                self.show_notice("Opened in browser: " + url[:70])
            else:
                self.show_notice("Select an issue, PR or action run to open its GitHub page")
        elif key in (ord('e'),ord('E')) and self.tab == 4:
            self.log_mode = (self.log_mode+1)%3
            self.scroll["logs"] = 0
        elif self.tab in (1,2,3) and key in (curses.KEY_UP, ord('k'),ord('K')):
            k = {1:"tasks",2:"prs",3:"actions"}[self.tab]
            self.indices[k] = max(0,self.indices[k]-1)
        elif self.tab in (1,2,3) and key in (curses.KEY_DOWN, ord('j'),ord('J')):
            k = {1:"tasks",2:"prs",3:"actions"}[self.tab]
            self.indices[k] += 1
        elif self.tab == 4:
            if key in (curses.KEY_UP, ord('k'),ord('K')):
                self.scroll["logs"] += 1
            elif key in (curses.KEY_DOWN, ord('j'),ord('J')):
                self.scroll["logs"] = max(0,self.scroll["logs"]-1)
            elif key == curses.KEY_PPAGE:
                self.scroll["logs"] += 12
            elif key == curses.KEY_NPAGE:
                self.scroll["logs"] = max(0,self.scroll["logs"]-12)
            elif key == curses.KEY_END:
                self.scroll["logs"] = 0
        return True


def text_once(snap: Snapshot, workers: int) -> str:
    counts = task_counts(snap.issues, snap.prs)
    est = build_estimates(snap.issues, snap.prs, snap.inventory, parse_log_events(snap.logs), workers)
    def fmt(name, vals):
        return ", ".join(f"{mode}={duration(value)}" for mode, value in vals.items())
    return "\n".join([
        "NOVA3 OBSERVATORY — NONINTERACTIVE REPORT",
        f"Service: {snap.service.get('active','unknown')}, PID {snap.service.get('pid','?')}",
        f"GitHub fetched: {snap.github_updated or 'not yet'}",
        f"GitHub errors: {'; '.join(snap.github_errors) if snap.github_errors else 'none'}",
        f"Contributor presence: {presence_view.summary(snap.worker_presence)}",
        f"Contributor presence source: {snap.presence_error or 'ok'}",
        "Tasks: " + ", ".join(f"{k.lower()}={counts[k]}" for k in ("QUEUED","CLAIMED","REVIEW","BLOCKED","INTEGRATED","VERIFIED")),
        f"Pull requests: merged={est['stats']['merged']} open={est['stats']['open']} recent_7d={est['stats']['merged_7d']}",
        "Queue planning: " + fmt("queue", est["queue_seconds"]),
        f"Inventory known functions: {est['known_functions']} ({est['functions_scope']})",
        f"Function atlas: identified={snap.coverage.get('known','?')} merged={snap.coverage.get('integrated','?')} verified={snap.coverage.get('verified','?')} original_total=UNKNOWN",
        f"Reconstructed C/C++ source: files={snap.coverage.get('source',{}).get('files','?')} lines={snap.coverage.get('source',{}).get('lines','?')}",
        f"Indexed APK packages: {len(snap.coverage.get('package',{}).get('apks', []))} (metadata only)",
        "Inventory projection: " + (fmt("fn",est["function_seconds"]) if est["function_seconds"] else "unknown"),
        "Hypothetical entire-game ETA: " + (fmt("game",est["whole_game_seconds"]) if est["whole_game_seconds"] else "unknown"),
        "IMPORTANT: estimates are planning scenarios; not completion of entire game.",
        "Last journal messages:",
        *["  " + x for x in snap.logs[-10:]],
    ])


def cli(argv=None):
    p=argparse.ArgumentParser(description="Private NOVA3 telemetry TUI (no project changes)")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--service", default=DEFAULT_SERVICE)
    p.add_argument("--state-dir", type=Path, default=Path(os.getenv("XDG_STATE_HOME", str(Path.home()/".local/state"))) / STATE_FOLDER)
    p.add_argument("--workers", type=int, default=1, help="assumed parallel workers for forecasting only")
    p.add_argument("--refresh-seconds", type=int, default=GH_INTERVAL)
    p.add_argument("--demo", action="store_true", help="show clearly synthetic offline data")
    p.add_argument("--once", action="store_true", help="print one snapshot, no terminal UI")
    args=p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        p.error("--repo must be OWNER/REPOSITORY")
    if not re.fullmatch(r"[a-zA-Z0-9@_.-]+\.service", args.service):
        p.error("--service must be a systemd user .service unit")
    c=Collector(args.repo, args.root.expanduser(), args.service, args.state_dir.expanduser(),
                demo=args.demo, gh_interval=max(15,min(args.refresh_seconds,3600)))
    workers=max(1,min(32,args.workers))
    if args.once:
        if not args.demo:
            packet=gh_collect(args.repo)
            for name in ("issues","prs","runs"):
                if not any(x.startswith(name+":") for x in packet["errors"]):
                    setattr(c.snapshot,name,packet[name])
            c.snapshot.github_errors=packet["errors"]
            c.snapshot.github_updated=datetime.now(timezone.utc).isoformat() if not packet["errors"] else c.snapshot.github_updated
            c.snapshot.logs=journal_collect(args.service)
            c.snapshot.service=service_collect(args.service)
            c.snapshot.inventory=load_inventory(args.root.expanduser())
            c.snapshot.coverage=progress_map.source_map(args.root.expanduser(), c.snapshot.issues, c.snapshot.prs)
            c.snapshot.roadmap=progress_map.load_roadmap(args.root.expanduser())
        print(text_once(c.view(), workers))
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error("interactive TUI requires a terminal; use --once for scripts")
    c.begin()
    try:
        curses.wrapper(Observatory(c, workers).run)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
