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
import stat
import subprocess
import sys
import threading
import time
import textwrap
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
_forecast_spec = _importlib_util.spec_from_file_location(
    "nova3_private_observer_forecast", Path(__file__).resolve().parent / "forecast_model.py")
if _forecast_spec is None or _forecast_spec.loader is None:
    raise ImportError('NOVA3 Observatory forecast model missing; reinstall Observatory')
forecast_model = _importlib_util.module_from_spec(_forecast_spec)
sys.modules[_forecast_spec.name] = forecast_model
_forecast_spec.loader.exec_module(forecast_model)

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
TABS = ["PULSE", "TASKS", "PULL REQUESTS", "ACTIONS", "SERVICE LOGS", "FORECAST", "SOURCE MAP", "APK ATLAS", "ROADMAP", "OWNER OPS", "WORKERS", "TOOLCHAIN", "ACTIVITY", "HELP"]

# One selected repository, two listing levels, never recursive/home-wide scans.
ACTIVITY_MAX_ENTRIES = 1024
ACTIVITY_MAX_CHECKOUTS = 64
ACTIVITY_MAX_FILES = 128
ACTIVITY_MAX_BYTES = 8192
ACTIVITY_MAX_RECORDS = 16
ACTIVITY_FRESH_SECONDS = 90
ACTIVITY_KEEP_SECONDS = 7 * 86400
ACTIVITY_STATUSES = {'UNKNOWN', 'RUNNING', 'TESTING', 'EDITING', 'QUIET', 'FINISHED', 'FAILED'}
ACTIVITY_CATEGORIES = {'unknown', 'lifecycle', 'error', 'file_change', 'test', 'command', 'message'}


def _directory_fd(path: Path) -> int:
    """Pin every component without following symlinks, including ancestors."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _activity_summary(data: Any, issue: int, round_number: int) -> dict:
    """Strict projection: no untrusted strings or extra JSON fields escape."""
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('unsupported summary')
    if data.get('status') not in ACTIVITY_STATUSES or data.get('event_category') not in ACTIVITY_CATEGORIES:
        raise ValueError('unsupported observation')
    raw_time = data.get('timestamp_utc')
    if not isinstance(raw_time, str) or len(raw_time) > 40:
        raise ValueError('invalid timestamp')
    timestamp = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError('invalid timestamp')
    result = {'issue': issue, 'round': round_number, 'timestamp_utc': timestamp.astimezone(timezone.utc).isoformat(),
              'status': data['status'], 'event_category': data['event_category']}
    for key in ('counts_complete', 'structured_events'):
        if type(data.get(key)) is not bool:
            raise ValueError('invalid flags')
        result[key] = data[key]
    for key in ('last_activity_age_seconds', 'commands_started', 'commands_completed',
                'tests_started', 'tests_completed', 'file_changed_count'):
        value = data.get(key)
        if value is None and key in ('last_activity_age_seconds', 'file_changed_count'):
            result[key] = None
        elif type(value) is int and 0 <= value <= 10**9:
            result[key] = value
        else:
            raise ValueError('invalid count')
    if not result['structured_events']:
        result['event_category'] = 'unknown'
        if result['status'] not in ('FINISHED', 'FAILED'):
            result['status'] = 'UNKNOWN'
    return result


def collect_activity(repo: str, *, home: Path | None = None, now: datetime | None = None) -> dict:
    """Read only worker-produced round-N.activity.json, never raw logs.

    Descriptor-relative opens prevent checkout/file replacement from redirecting
    reads. Unsupported platforms fail closed. Diagnostics contain fixed labels.
    Limits cover *all* directory entries, not just matching files.
    """
    result = {'records': [], 'state': 'MISSING', 'limited': False, 'rejected': 0}
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or any(part in ('.', '..') for part in repo.split('/'))):
        result['state'] = 'UNAVAILABLE'
        return result
    if not all(hasattr(os, flag) for flag in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')):
        result['state'] = 'UNAVAILABLE'
        return result
    now = now or datetime.now(timezone.utc)
    base = (home or Path.home()) / '.cache/nova3-team' / repo.replace('/', '_') / '.nova-private'
    entries = checkouts = files = 0
    try:
        base_fd = _directory_fd(base)
        try:
            with os.scandir(base_fd) as folders:
                for folder in folders:
                    entries += 1
                    if entries > ACTIVITY_MAX_ENTRIES:
                        result['limited'] = True
                        break
                    match = re.fullmatch(r'([1-9][0-9]{0,8})-[A-Za-z0-9-]{1,80}', folder.name)
                    if not match:
                        continue
                    checkouts += 1
                    if checkouts > ACTIVITY_MAX_CHECKOUTS:
                        result['limited'] = True
                        break
                    try:
                        child_fd = os.open(folder.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
                    except OSError:
                        result['rejected'] += 1
                        continue
                    try:
                        with os.scandir(child_fd) as summaries:
                            for entry in summaries:
                                entries += 1
                                if entries > ACTIVITY_MAX_ENTRIES:
                                    result['limited'] = True
                                    break
                                round_match = re.fullmatch(r'round-([1-9][0-9]{0,5})\.activity\.json', entry.name)
                                if not round_match:
                                    continue
                                files += 1
                                if files > ACTIVITY_MAX_FILES:
                                    result['limited'] = True
                                    break
                                try:
                                    descriptor = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=child_fd)
                                    with os.fdopen(descriptor, 'rb') as source:
                                        info = os.fstat(source.fileno())
                                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                                or info.st_size > ACTIVITY_MAX_BYTES):
                                            raise ValueError('unsafe summary')
                                        if now.timestamp() - info.st_mtime > ACTIVITY_KEEP_SECONDS:
                                            continue
                                        payload = source.read(ACTIVITY_MAX_BYTES + 1)
                                    if len(payload) > ACTIVITY_MAX_BYTES:
                                        raise ValueError('large summary')
                                    record = _activity_summary(json.loads(payload), int(match[1]), int(round_match[1]))
                                    age = (now - dt(record['timestamp_utc'])).total_seconds()
                                    if age > ACTIVITY_KEEP_SECONDS:
                                        continue
                                    result['records'].append(record)
                                    result['records'].sort(key=lambda r: r['timestamp_utc'], reverse=True)
                                    del result['records'][ACTIVITY_MAX_RECORDS:]
                                except (OSError, ValueError, TypeError, RecursionError, OverflowError):
                                    result['rejected'] += 1
                    finally:
                        os.close(child_fd)
                    if result['limited']:
                        break
        finally:
            os.close(base_fd)
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        result['state'] = 'UNAVAILABLE'
        return result
    result['state'] = 'OBSERVED' if result['records'] else 'UNKNOWN'
    return result


def activity_records(snap, now: datetime | None = None) -> list[dict]:
    """Re-evaluate freshness on every draw, even if the collector has stalled."""
    now = now or datetime.now(timezone.utc)
    issues = {i.get('number'): i for i in snap.issues}
    newest = {}
    for record in snap.activity.get('records', []):
        key = (record['timestamp_utc'], record['round'])
        newest[record['issue']] = max(newest.get(record['issue'], ('', 0)), key)
    records = []
    for raw in snap.activity.get('records', []):
        item = dict(raw)
        age = (now - dt(item['timestamp_utc'])).total_seconds()
        item['fresh'] = 0 <= age <= ACTIVITY_FRESH_SECONDS
        item['display_status'] = item['status'] if item['fresh'] else 'STALE' if age >= 0 else 'UNKNOWN'
        issue = issues.get(item['issue'])
        stage = issue_stage(issue) if issue else 'UNKNOWN'
        item['task_stage'] = stage
        past = stage in ('INTEGRATED', 'VERIFIED', 'CLOSED*')
        superseded = (item['timestamp_utc'], item['round']) != newest[item['issue']]
        item['task_relation'] = ('SUPERSEDED' if superseded else 'PAST TASK' if past
                                 else 'TASK UNKNOWN' if issue is None else stage)
        item['active'] = (item['fresh'] and item['status'] in ('RUNNING', 'TESTING', 'EDITING', 'QUIET')
                          and stage in ('CLAIMED', 'UNKNOWN') and not past and not superseded)
        records.append(item)
    return sorted(records, key=lambda r: (not r['active'], -dt(r['timestamp_utc']).timestamp()))


def activity_line(snap, now: datetime | None = None) -> str:
    records = activity_records(snap, now)
    if not records:
        return 'AI UNKNOWN: local feed ' + snap.activity.get('state', 'MISSING') + ' (older/missing provider)'
    r = records[0]
    prefix = 'AI observed' if r['active'] else 'AI recent'
    limit = ' [partial scan]' if snap.activity.get('limited') else ''
    return f"{prefix} #{r['issue']} r{r['round']} {r['display_status']} / {r['event_category']} ({r['task_relation']}){limit}"


def toolchain_module():
    """Load the same offline discovery helper in a checkout or standalone install."""
    here = Path(__file__).resolve()
    for path in (here.parent / 'historical_toolchain.py',
                 here.parents[2] / 'nova3_team/historical_toolchain.py'):
        if path.is_file():
            spec = _importlib_util.spec_from_file_location('nova3_observer_toolchain', path)
            if spec is not None and spec.loader is not None:
                module = _importlib_util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
    raise ImportError('Optional historical toolchain helper missing; reinstall Observatory from the checkout.')


def historical_setup_text() -> str:
    """No metadata execution, downloads, collector or GitHub calls."""
    try:
        helper = toolchain_module()
        report = helper.onboarding()
        warnings = '\n'.join(report['warnings'])
        guide = Path(__file__).resolve().parent / 'HISTORICAL_TOOLCHAIN.md'
        guide_notice = ('\nBundled extraction/pilot guide: ' + str(guide) + '\n') if guide.is_file() else ''
        return ('Compiler status: ' + report['status'] + '\n' + warnings + '\n' +
                helper.setup_instructions() + guide_notice)
    except (ImportError, OSError, ValueError) as exc:
        return ('Optional compiler setup unavailable: ' + type(exc).__name__ +
                '\nRead-only observer remains available. Reinstall Observatory from the checkout.\n'
                'Source matching NOT PROVEN; no compiler is required to watch tasks.')


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


def normalized_forecast(snap, now: datetime | None = None):
    """Adapt observed metadata; absent cohorts/retry histories stay unavailable.

    GitHub list limits cannot prove a complete queue. Issue updatedAt is not a
    stage start; session exit=0 is not review readiness. Ledger status labels
    are not reviewed source matching evidence. No such substitutions are made.
    """
    f = forecast_model
    now = now or datetime.now(timezone.utc)
    observed = dt(snap.github_updated)
    observed_at = observed.timestamp() if observed else 0
    tasks, prs, checks, workers = [], [], [], []
    stage_map = {'QUEUED': 'queued', 'CLAIMED': 'active', 'REVIEW': 'review',
                 'BLOCKED': 'blocked', 'INTEGRATED': 'done', 'VERIFIED': 'done'}
    known = set()
    for issue in snap.issues[:MAX_PRS]:  # <=1516 model records, including PR/CI/workers
        number = issue.get('number')
        if not is_task(issue) or type(number) is not int or number <= 0 or str(number) in known:
            continue
        task_id = str(number)
        known.add(task_id)
        stage = issue_stage(issue)
        reasons = []
        if stage not in stage_map:
            reasons.append('unreconciled_task_stage')
        # No current issue contract identifies comparable size/provider cohorts.
        tasks.append(f.Task(task_id, 'unclassified', kind='unknown',
                            stage=stage_map.get(stage, 'blocked'), blockers=tuple(reasons)))
    linked = collections.defaultdict(list)
    for pr in snap.prs[:MAX_PRS]:
        refs = {str(i.get('number')) for i in (pr.get('closingIssuesReferences') or [])[:MAX_ISSUES]
                if isinstance(i, dict)}
        title_ref = re.match(r'^#([0-9]+)\s*:', pr.get('title') or '')
        if not refs and title_ref:
            refs.add(title_ref[1])
        for task_id in refs & known:
            linked[task_id].append(pr)
    for task_id in sorted(known):
        candidates = linked[task_id]
        if not candidates:
            continue
        # Conflicting open PRs cannot be resolved by guessing the newest title.
        open_prs = [pr for pr in candidates if pr.get('state') == 'OPEN']
        if len(open_prs) > 1:
            continue
        pr = open_prs[0] if open_prs else max(candidates, key=lambda pr: pr.get('updatedAt') or '')
        state = 'merged' if pr.get('mergedAt') else 'open' if pr.get('state') == 'OPEN' else 'closed'
        prs.append(f.PullRequest(task_id, observed_at, state, bool(pr.get('isDraft'))))
        # Rollup is current-head data, but cannot prove all required checks are
        # configured/present. Report observed failures/pending, never infer PASS
        # from a historic workflow listing or an empty/partial green rollup.
        rollup = pr.get('statusCheckRollup') or []
        ci_state = 'unknown'
        if isinstance(rollup, list) and rollup:
            states = {str(row.get('conclusion') or row.get('state') or row.get('status') or '').upper()
                      for row in rollup if isinstance(row, dict)}
            if states & {'FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE'}:
                ci_state = 'failed'
            elif states & {'PENDING', 'QUEUED', 'IN_PROGRESS', 'WAITING', 'REQUESTED'}:
                ci_state = 'pending'
        checks.append(f.CI(task_id, observed_at, ci_state))
    for index, record in enumerate(activity_records(snap, now)):
        task_id = str(record['issue'])
        if task_id in known:
            state = 'active' if record['active'] else 'blocked' if record['status'] == 'FAILED' else 'offline'
            workers.append(f.Worker(f'local-{index}', task_id,
                                    dt(record['timestamp_utc']).timestamp(), state))
    matching = f.MatchingEvidence()
    if snap.inventory.get('available') and snap.inventory.get('verified') == 0:
        matching = f.MatchingEvidence(verified_original_matches=0,
                                     verified_count_evidence='local_ledger_reports_zero')
    return f.Snapshot(now=now.timestamp(), observed_at=observed_at,
                      scope='bounded GitHub task sample; completeness unverified',
                      queue_complete=False, tasks=tuple(tasks), prs=tuple(prs), ci=tuple(checks),
                      workers=tuple(workers), matching=matching)


def forecast_lines(report: dict) -> list[str]:
    """Shared PULSE/FORECAST/text labels from the pure model's result only."""
    queue = report['queue']
    values = queue['all_supplied_queue_seconds']
    estimate = ('UNKNOWN' if values is None else
                ' / '.join(duration(values[key]) for key in ('p20', 'median', 'p80')))
    reasons = list(dict.fromkeys(queue['all_supplied_queue_reasons'] + queue['reasons']))
    match = report['source_matching']
    lines = [
        'Whole-game ETA: ' + report['whole_game']['label'],
        'Queue stage work: ' + estimate,
        f"Queue samples: {queue['sample_size']} | confidence: {queue['confidence']}",
        'Setup readiness: ' + report['setup']['state'].upper(),
        'Queue: ' + (', '.join(reasons) if reasons else queue['state']),
        f"Blocked tasks: {len(queue['blocked_tasks'])}",
    ]
    for task_id, blockers in queue['blocked_tasks'].items():
        lines.append('#' + task_id + ': ' + ', '.join(blockers))
    lines.extend([
        'Scope: ' + queue['scope'],
        'Setup: ' + (', '.join(report['setup']['reasons']) or 'evidenced milestones only'),
        'Verified original source matches: ' + (str(match['verified_original_matches'])
                                              if match['verified_original_matches'] is not None else 'UNKNOWN'),
        f"Source matching samples: {match['sample_size']} | confidence: {match['confidence']}",
        'Matching: ' + ', '.join(match['target_reasons']),
        queue['completion_boundary'],
        ('Queue history: no qualifying comparable stage samples.' if queue['sample_size'] == 0 else
         'Queue history: observed comparable stage samples only.'),
        'PR merges, session exits and host tests do not prove original source matches.',
    ])
    return lines


def build_estimates(issues: list[dict], prs: list[dict], inventory: dict, events: dict,
                    workers: int = 1, now: datetime | None = None, *, snapshot=None) -> dict:
    """Metadata compatibility wrapper; all displayed time estimates use #109."""
    now = now or datetime.now(timezone.utc)
    snap = snapshot if snapshot is not None else Snapshot(issues=issues, prs=prs, inventory=inventory)
    counts = task_counts(issues, prs)
    discovered = set().union(*(function_addrs(i.get('title', '')) for i in issues if is_task(i)))
    merged = merged_issue_ids(prs)
    integrated = set().union(*(function_addrs(i.get('title', '')) for i in issues if i.get('number') in merged))
    return {
        'forecast': forecast_model.forecast(normalized_forecast(snap, now)),
        'remaining_queue': sum(counts[k] for k in ('QUEUED', 'CLAIMED', 'REVIEW', 'BLOCKED')),
        'stats': pr_stats(prs, now), 'median_ai_seconds': median_session_seconds(events),
        'known_functions': inventory.get('count', 0) if inventory.get('available') else len(discovered),
        'functions_scope': 'identified boundaries; completeness unverified',
        'integrated_function_addresses': len(integrated),
        'verified_ledger_functions': inventory.get('verified') if inventory.get('available') else None,
        # No speculative legacy caller can accidentally recover a numeric ETA.
        'queue_seconds': {}, 'function_seconds': {}, 'whole_game_seconds': {},
        'throughput_seconds': None,
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
    pr_fields = "number,title,state,isDraft,createdAt,mergedAt,closedAt,updatedAt,headRefName,baseRefName,url,mergeable,reviewDecision,additions,deletions,changedFiles,closingIssuesReferences,statusCheckRollup"
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
    activity: dict = field(default_factory=dict)


class Collector:
    def __init__(self, repo: str, root: Path, service: str, state_dir: Path,
                 *, demo: bool = False, gh_interval: int = GH_INTERVAL, offline: bool = False):
        self.repo, self.root, self.service, self.state_dir = repo, root, service, state_dir
        self.demo, self.gh_interval = demo, gh_interval
        self.offline = offline
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
        if not demo:
            self.snapshot.activity = collect_activity(repo)
        if demo:
            self.snapshot = sample_snapshot()

    def _load_cache(self):
        self.snapshot.inventory = load_inventory(self.root)
        fp = self.state_dir / "snapshot.json"
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            for name in ("issues", "prs", "runs", "github_updated", "github_errors", "history", "worker_presence", "presence_updated", "presence_issue", "presence_error"):
                value = data.get(name)
                if isinstance(value, type(getattr(self.snapshot, name))):
                    setattr(self.snapshot, name, value)
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
        self.log_thread.start()
        if not self.offline:
            self.gh_thread = threading.Thread(target=self._gh_loop, daemon=True, name="nova-gh-poller")
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
            activity = collect_activity(self.repo)
            if self.offline:
                with self.lock:
                    self.snapshot.activity = activity
                if self.stop_event.wait(LOG_INTERVAL):
                    break
                continue
            service = service_collect(self.service)
            logs = journal_collect(self.service)
            try:
                discovery = operator_ops.discovery_status(self.root)
                git_state = operator_ops.git_alignment(self.root)
                discovery_logs = operator_ops.service_error_lines(lines=6)
            except (OSError, ValueError, RuntimeError) as exc:
                discovery, git_state, discovery_logs = {'message': str(exc)}, {'note': str(exc)}, [str(exc)]
            with self.lock:
                self.snapshot.activity = activity
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
            self.put(5, 2, activity_line(self.snap), "cyan", max(0, w-4))
            self.put(7, 2, "Whole-game ETA: UNKNOWN", "yellow")
            self.put(9, 2, "q to exit. No project files are changed.", "muted")
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
        labels = ['1','2','3','4','5','6','7','8','9','0','W','H','A','?']
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
        estimate = build_estimates(self.snap.issues, self.snap.prs, self.snap.inventory, events, self.workers, now, snapshot=self.snap)
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
        elif self.tab == 11:
            self.draw_toolchain(6, bottom, w)
        elif self.tab == 12:
            self.draw_activity(6, bottom, w)
        else:
            self.draw_help(6, bottom, w)
        self.put(h-3, 0, "─"*(w-1), "muted")
        refreshed = fmt_age(dt(self.snap.github_updated), now)
        gh_txt = "LIVE" if gh_ok else ("CACHED" if self.snap.github_updated else "OFFLINE")
        bottom_left = f" GitHub {gh_txt} · {refreshed}  │  systemd {health}  │  slots(config) {self.workers} "
        self.put(h-2, 1, bottom_left, "yellow" if not gh_ok else "muted", w-3)
        foot = (" TAB / 1-9 pages  W workers  H toolchain  A activity  0 owner ops  G sync  ? help  Q quit "
                if self.snap.repo_role in operator_ops.OWNER_ROLES else
                " TAB / 1-9 pages  W workers  H toolchain  A activity  0 owner ops LOCKED  G sync  ? help  Q quit ")
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
        lines = [activity_line(self.snap),
                 f"Systemd: {health} | {description}",
                 ' / '.join(f'{stage} {counts[stage]}' for stage in ('QUEUED', 'CLAIMED', 'REVIEW', 'BLOCKED')),
                 *forecast_lines(est['forecast'])[:6],
                 f"PRs: {est['stats']['merged']} merged / {est['stats']['open']} open (workflow only)",
                 'A: sanitized local activity | TAB: pages']
        self.frame(y, 1, end-y, w-3, ' PULSE  /  OBSERVATIONS ')
        for index, line in enumerate(lines[:max(0, end-y-2)]):
            self.put(y+1+index, 3, line, 'cyan' if index in (0, 3) else 'normal', w-7)

    def draw_activity(self, y, end, w):
        lines = [activity_line(self.snap),
                 'Local JSON observations; no assistant reasoning or internal thoughts.',
                 'Counters are observed starts/completions, never proof tests passed.',
                 f"Feed: {self.snap.activity.get('state', 'MISSING')} | "
                 f"scan limited: {bool(self.snap.activity.get('limited'))} | "
                 f"rejected: {self.snap.activity.get('rejected', 0)}",
                 'Newest observations within scan limits; timestamps are worker-observed UTC.']
        for record in activity_records(self.snap):
            lines.extend([
                f"#{record['issue']} round {record['round']}  {record['display_status']} / {record['event_category']}  {record['task_relation']}",
                f"Observed {record['timestamp_utc']} | {fmt_age(dt(record['timestamp_utc']))}",
                f"Commands {record['commands_started']} started / {record['commands_completed']} completed; "
                f"tests {record['tests_started']} / {record['tests_completed']}",
                f"Changed files: {record['file_changed_count'] if record['file_changed_count'] is not None else 'UNKNOWN'} | "
                f"counts: {'complete' if record['counts_complete'] else 'lower bounds'} | "
                f"event age at observation: {duration(record['last_activity_age_seconds'])}",
            ])
        if not self.snap.activity.get('records'):
            lines.append('Missing/older providers: UNKNOWN. Upgrade/restart worker separately; no raw logs read.')
        wrapped = [part for line in lines for part in textwrap.wrap(line, max(10, w-8))]
        start = min(self.scroll['activity'], max(0, len(wrapped)-1))
        self.scroll['activity'] = start
        self.frame(y, 1, end-y, w-3, ' PRIVATE LOCAL ACTIVITY  /  UP-DOWN SCROLL ')
        for index, line in enumerate(wrapped[start:start+max(0, end-y-2)]):
            self.put(y+1+index, 3, line, 'normal', w-7)

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
        report = est['forecast']
        lines = forecast_lines(report)
        lines.insert(3, f"GitHub observed: {self.snap.github_updated or 'UNKNOWN'} | "
                     f"{fmt_age(dt(self.snap.github_updated))}")
        self.frame(y, 1, end-y, w-3, ' EVIDENCE-GATED FORECAST ')
        # Put setup/matching before potentially long task blocker details.
        concise = [line for line in lines if not line.startswith('#')]
        details = [line for line in lines if line.startswith('#')]
        wrapped = [part for line in concise + details for part in textwrap.wrap(line, max(10, w-8))]
        start = min(self.scroll['forecast'], max(0, len(wrapped)-1))
        self.scroll['forecast'] = start
        for index, line in enumerate(wrapped[start:start+max(0, end-y-2)]):
            self.put(y+1+index, 3, line, 'cyan' if line.startswith('Whole-game') else 'normal', w-7)

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
        self.frame(y,1,end-y-1,w-3," LONG-RUNNING ROADMAP  •  ONE AI ISSUE POOL ")
        self.put(y+2,4,"RECONSTRUCT  >  READABLE CORE  >  NATIVE PORTS  >  RUNTIME  >  EXTENSIONS",'cyan',w-9)
        self.put(y+3,4,"Engineering gate progress != original-game parity or verified gameplay.",'yellow',w-9)
        self.put(y+4,4,"Whole-game ETA: " + forecast_model.forecast(normalized_forecast(self.snap))['whole_game']['label'],
                 'cyan',w-9)
        self.put(y+5,4,"Setup/queue forecasts are separate; no calibrated gameplay/integration history.",'muted',w-9)
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

    def draw_toolchain(self, y, end, w):
        self.put(y, 3, 'OPTIONAL GCC 4.4.3 CANDIDATE / read-only guidance', 'cyan', w-6)
        if not hasattr(self, '_toolchain_text'):
            self._toolchain_text = historical_setup_text()
        lines = []
        for line in self._toolchain_text.splitlines():
            lines.extend(textwrap.wrap(line, max(10, w-8), replace_whitespace=False) or [''])
        count = max(1, end-y-2)
        offset = min(self.scroll['toolchain'], max(0, len(lines)-count))
        self.scroll['toolchain'] = offset
        for i, line in enumerate(lines[offset:offset+count]):
            self.put(y+1+i, 4, line, 'normal', w-8)
        self.put(end, 3, 'Up/Down scroll | V rescan | --toolchain-setup prints copyable commands', 'muted', w-6)

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
            ("FORECAST", "Evidence-gated stage work; up/down scroll. Slots +/- do not establish throughput."),
            ("ACTIVITY", "A opens local sanitized JSON observations. No internal thoughts or raw logs."),
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
            ("CAUTION", "Whole-game ETA UNKNOWN; a merged PR is not a verified source match."),
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
        if key in (ord('a'),ord('A')) and self.tab != 2:
            self.tab = 12
            return True
        if key in (ord('h'),ord('H')):
            self.tab = 11
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
        elif self.tab == 11 and key in (curses.KEY_UP, ord('k'), ord('K')):
            self.scroll['toolchain'] = max(0, self.scroll['toolchain']-1)
        elif self.tab == 11 and key in (curses.KEY_DOWN, ord('j'), ord('J')):
            self.scroll['toolchain'] += 1
        elif self.tab == 11 and key in (ord('v'), ord('V')):
            self._toolchain_text = historical_setup_text()
            self.show_notice('Compiler discovery refreshed; no compiler executed')
        elif self.tab == 5 and key in (curses.KEY_UP, ord('k'), ord('K')):
            self.scroll['forecast'] = max(0, self.scroll['forecast']-1)
        elif self.tab == 5 and key in (curses.KEY_DOWN, ord('j'), ord('J')):
            self.scroll['forecast'] += 1
        elif self.tab == 12 and key in (curses.KEY_UP, ord('k'), ord('K')):
            self.scroll['activity'] = max(0, self.scroll['activity']-1)
        elif self.tab == 12 and key in (curses.KEY_DOWN, ord('j'), ord('J')):
            self.scroll['activity'] += 1
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
    est = build_estimates(snap.issues, snap.prs, snap.inventory, parse_log_events(snap.logs),
                          workers, snapshot=snap)
    report = est['forecast']
    return "\n".join([
        "NOVA3 OBSERVATORY — NONINTERACTIVE REPORT",
        f"Service: {snap.service.get('active','unknown')}, PID {snap.service.get('pid','?')}",
        f"GitHub fetched: {snap.github_updated or 'not yet'}",
        f"GitHub collection errors: {len(snap.github_errors)}",
        f"Contributor presence: {presence_view.summary(snap.worker_presence)}",
        "Tasks: " + ", ".join(f"{k.lower()}={counts[k]}" for k in ("QUEUED","CLAIMED","REVIEW","BLOCKED","INTEGRATED","VERIFIED")),
        f"Pull requests: merged={est['stats']['merged']} open={est['stats']['open']} recent_7d={est['stats']['merged_7d']}",
        f"Inventory known functions: {est['known_functions']} ({est['functions_scope']})",
        f"Function atlas: identified={snap.coverage.get('known','?')} merged={snap.coverage.get('integrated','?')} original_total=UNKNOWN",
        f"Reconstructed C/C++ source: files={snap.coverage.get('source',{}).get('files','?')} lines={snap.coverage.get('source',{}).get('lines','?')}",
        f"Indexed APK packages: {len(snap.coverage.get('package',{}).get('apks', []))} (metadata only)",
        activity_line(snap),
        'Local summary observations only; no assistant reasoning.',
        'Queue planning: evidence-gated; configured worker slots do not establish throughput.',
        *forecast_lines(report),
        'IMPORTANT: merged issues/PRs are not verified source matches.',
    ])


def cli(argv=None):
    p=argparse.ArgumentParser(description="Private NOVA3 telemetry TUI (no project changes)")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--service", default=DEFAULT_SERVICE)
    p.add_argument("--state-dir", type=Path, default=Path(os.getenv("XDG_STATE_HOME", str(Path.home()/".local/state"))) / STATE_FOLDER)
    p.add_argument("--workers", type=int, default=1, help="legacy configured slots; forecasts require observed worker capacity")
    p.add_argument("--refresh-seconds", type=int, default=GH_INTERVAL)
    p.add_argument("--demo", action="store_true", help="show clearly synthetic offline data")
    p.add_argument("--once", action="store_true", help="print one snapshot, no terminal UI")
    p.add_argument("--offline", action="store_true", help="local summaries/cached metadata only; no GitHub login or subprocess collection")
    p.add_argument("--toolchain-setup", action="store_true",
                   help="offline optional compiler status/manual recipe; no login, install or compiler execution")
    args=p.parse_args(argv)
    if args.toolchain_setup:
        print(historical_setup_text())
        return 0
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        p.error("--repo must be OWNER/REPOSITORY")
    if not re.fullmatch(r"[a-zA-Z0-9@_.-]+\.service", args.service):
        p.error("--service must be a systemd user .service unit")
    c=Collector(args.repo, args.root.expanduser(), args.service, args.state_dir.expanduser(),
                demo=args.demo, gh_interval=max(15,min(args.refresh_seconds,3600)), offline=args.offline)
    workers=max(1,min(32,args.workers))
    if args.once:
        if not args.demo and not args.offline:
            packet=gh_collect(args.repo)
            for name in ("issues","prs","runs"):
                if not any(x.startswith(name+":") for x in packet["errors"]):
                    setattr(c.snapshot,name,packet[name])
            c.snapshot.github_errors=packet["errors"]
            c.snapshot.github_updated=datetime.now(timezone.utc).isoformat() if not packet["errors"] else c.snapshot.github_updated
            c.snapshot.activity=collect_activity(args.repo)
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
