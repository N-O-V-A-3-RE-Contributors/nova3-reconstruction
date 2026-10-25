"""NOVA3 terminal dashboard: curses on Unix, quiet event log fallback elsewhere.

Terminal ownership is delegated to curses rather than sending full-screen ANSI
clears several times per second. Estimates are explicitly scoped to current
provider session budgets and observed GitHub issue throughput; never game coverage.
No mandatory third-party Python packages.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

ANSI_ESCAPE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
TASK_RE = re.compile(r'Trying #(\d+): (.+)')
SESSION_RE = re.compile(r'#(\d+) AI session (\d+)/(\d+) \(([^)]+)\)')
# Set by worker.py -> work_one(): run(cmd, timeout=1800) for each model session.
SESSION_TIMEOUT_SECONDS = 1800
SPINNER = ('◐', '◓', '◑', '◒')


def clean(value: str) -> str:
    value = ANSI_ESCAPE.sub('', value)
    return ''.join(ch if ch.isprintable() or ch == '\t' else ' ' for ch in value).strip()


def elapsed(seconds: float) -> str:
    sec = max(0, int(seconds))
    hours, remainder = divmod(sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}h {minutes:02d}m {seconds:02d}s'
    if minutes:
        return f'{minutes}m {seconds:02d}s'
    return f'{seconds}s'


def identify_phase(line: str, existing: str = 'CONNECTING') -> str:
    rules = (
        ('No queued tasks.', 'WAITING FOR TASKS'),
        ('Requested task #', 'RESERVING ISSUE'),
        ('AI session ', 'AI SESSION RUNNING'),
        ('working in ', 'CHECKOUT READY'),
        ('Reusing saved checkout ', 'CHECKOUT READY'),
        ('PR submitted:', 'PULL REQUEST OPENED'),
        ('FAILED #', 'TASK FAILED'),
        ('No claim for #', 'TASK CLAIM BUSY'),
        ('Trying #', 'SELECTING TASK'),
        ('Coordinator/worker transient failure', 'COORDINATOR ERROR'),
    )
    for pattern, phase in rules:
        if pattern in line:
            return phase
    if 'Session ' in line and 'returned' in line:
        return 'AI SESSION RETRY'
    if 'Repo=' in line and 'account=' in line:
        return 'CONNECTED'
    return existing


def summarize_issues(issues: list[dict]) -> dict:
    labels = ('nova:queued', 'nova:claimed', 'nova:review')
    result = {'total': len(issues), 'queued': 0, 'claimed': 0, 'review': 0, 'other': 0}
    for issue in issues:
        names = {label.get('name') for label in issue.get('labels', []) if isinstance(label, dict)}
        found = next((label for label in labels if label in names), None)
        if found:
            result[found.split(':')[1]] += 1
        else:
            result['other'] += 1
    result['limited'] = len(issues) >= 1000
    return result


def estimate_backlog(issues: list[dict], closed: list[dict], now: datetime | None = None) -> dict | None:
    """Conservative forecast for *currently queued GitHub tasks*, never for game source.

    Use >=4 recent closed nova issues, >=30 minutes of observation, and require
    the last completion to be within 72h. Historical completion interval assumes
    contributors continue at the same aggregate rate. Does not infer match rate.
    """
    now = now or datetime.now(timezone.utc)
    events = []
    for issue in closed:
        labels = {lbl.get('name') for lbl in issue.get('labels', []) if isinstance(lbl, dict)}
        if not any(isinstance(l, str) and l.startswith('nova:') for l in labels):
            continue
        raw = issue.get('closedAt')
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if dt.tzinfo and 0 <= (now - dt).total_seconds() <= 7 * 86400:
            events.append(dt)
    events.sort()
    if len(events) < 4 or (now - events[-1]).total_seconds() > 72*3600:
        return None
    interval = (events[-1] - events[0]).total_seconds()
    if interval < 30*60:
        return None
    backlog = sum(1 for i in issues if any(
        label.get('name') in ('nova:queued', 'nova:claimed')
        for label in i.get('labels', []) if isinstance(label, dict)))
    if not backlog:
        return {'seconds': 0, 'samples': len(events), 'backlog': 0}
    rate = (len(events) - 1) / interval
    return {'seconds': backlog / rate, 'samples': len(events), 'backlog': backlog}


def fetch_stats(repo: str, timeout: float = 15) -> tuple[dict | None, str | None]:
    gh = shutil.which('gh')
    if not gh:
        return None, 'GitHub CLI missing'
    try:
        issues = subprocess.run([gh, 'issue', 'list', '--repo', repo, '--state', 'open',
                                 '--limit', '1000', '--json', 'number,labels'],
                                capture_output=True, text=True, timeout=timeout)
        if issues.returncode:
            return None, ('GitHub unavailable: ' + clean(issues.stderr or issues.stdout))[-140:]
        opened = json.loads(issues.stdout)
        counts = summarize_issues(opened)
        prs = subprocess.run([gh, 'pr', 'list', '--repo', repo, '--state', 'open',
                              '--limit', '500', '--json', 'number'],
                             capture_output=True, text=True, timeout=timeout)
        if prs.returncode == 0:
            counts['open_prs'] = len(json.loads(prs.stdout))
        # Failure to obtain history must not hide current live queue data.
        closed = subprocess.run([gh, 'issue', 'list', '--repo', repo, '--state', 'closed',
                                 '--limit', '100', '--json', 'number,closedAt,labels'],
                                capture_output=True, text=True, timeout=timeout)
        if closed.returncode == 0:
            counts['backlog_estimate'] = estimate_backlog(opened, json.loads(closed.stdout))
        return counts, None
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f'GitHub status unavailable: {str(exc)[:110]}'


@dataclass
class Status:
    repo: str
    provider: str
    begin: float = field(default_factory=time.monotonic)
    phase: str = 'STARTING WORKER'
    phase_since: float = field(default_factory=time.monotonic)
    latest_output: float = field(default_factory=time.monotonic)
    task: str = 'Waiting for assignment'
    task_number: int | None = None
    session: str = 'Not started'
    session_since: float | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=30))
    counts: dict | None = None
    error: str | None = None
    gh_updated: float | None = None
    source_files: int | None = None
    test_files: int | None = None
    pr_submitted: int = 0
    returncode: int | None = None

    def feed(self, line: str) -> None:
        line = clean(line)
        if not line:
            return
        now = time.monotonic()
        self.latest_output = now
        self.logs.append(line)
        new_phase = identify_phase(line, self.phase)
        if new_phase != self.phase:
            self.phase = new_phase
            self.phase_since = now
        match = TASK_RE.search(line)
        if match:
            self.task_number = int(match.group(1))
            self.task = match.group(2)
            self.session_since = None
        match = SESSION_RE.search(line)
        if match:
            self.task_number = int(match.group(1))
            self.session = f'{match.group(2)} of {match.group(3)} ({match.group(4)})'
            self.session_since = now
        if 'PR submitted:' in line:
            self.pr_submitted += 1
            self.session_since = None
        if 'Session ' in line and 'returned' in line:
            self.session_since = None


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ''
    return text if len(text) <= width else text[:max(0, width - 1)] + '…'


def _bar(counts: dict | None, width: int) -> str:
    width = max(4, width)
    if not counts or not counts.get('total'):
        return '░' * width
    total = counts['total']
    occupied = round(width * counts.get('claimed', 0) / total)
    reviewed = min(width - occupied, round(width * counts.get('review', 0) / total))
    return '█' * reviewed + '▓' * occupied + '░' * (width - reviewed - occupied)


def _session_budget(status: Status, now: float) -> str:
    if status.session_since is None:
        return 'Session timeout budget: waiting for next session'
    left = max(0, SESSION_TIMEOUT_SECONDS - (now - status.session_since))
    return 'Session timeout ceiling: ' + elapsed(left) + ' remaining (NOT task ETA)'


def _queue_eta(status: Status) -> str:
    estimate = (status.counts or {}).get('backlog_estimate')
    if estimate is None:
        return 'Queue ETA: collecting at least 4 completed tasks'
    if estimate['backlog'] == 0:
        return 'Queue ETA: no queued/claimed tasks observed'
    return ('Queue ETA: approximately ' + elapsed(estimate['seconds']) +
            f" (rough, from {estimate['samples']} task closures)")


def render(status: Status, now: float | None = None, width: int = 90,
           color: bool = False, height: int = 30) -> str:
    """Pure snapshot, does not print or move the cursor. Used by curses/fallback/tests."""
    now = time.monotonic() if now is None else now
    width = max(38, min(140, width))
    rule = '─' * (width - 1)
    spin = SPINNER[int(now * 2) % len(SPINNER)] if status.returncode is None else '■'
    c = status.counts
    lines = [
        'NOVA3  /  DISTRIBUTED RECONSTRUCTION', rule,
        f'{spin} {status.phase}    uptime {elapsed(now-status.begin)}    provider {status.provider}',
        'REPOSITORY   ' + _fit(status.repo, width - 15),
        'CURRENT ISSUE ' + _fit((f'#{status.task_number} ' if status.task_number is not None else '') + status.task, width - 15),
        f'SESSION      {status.session}  |  elapsed in stage {elapsed(now-status.phase_since)}',
        _session_budget(status, now), rule,
        'GITHUB PIPELINE   /   NOT GAME COMPLETION',
    ]
    if c:
        suffix = '+' if c.get('limited') else ''
        lines.extend([
            f"Tasks: {c['total']}{suffix} open    {c['queued']} queued    {c['claimed']} active    {c['review']} review",
            _bar(c, max(4, width-2)),
            f"Open PRs: {c.get('open_prs', '?')}     PRs submitted by this worker: {status.pr_submitted}",
        ])
    else:
        lines.append('GitHub: ' + (status.error or 'Fetching task data…'))
    lines.append(_queue_eta(status))
    if status.source_files is not None:
        lines.append(f'Local inventory: {status.source_files} engine files  |  {status.test_files} test files')
    age = elapsed(now-status.gh_updated) + ' ago' if status.gh_updated is not None else 'not yet'
    lines.extend([f'GitHub last checked: {age}   |   Source match coverage: UNKNOWN', rule,
                  'RECENT EVENTS'])
    available = max(1, height - len(lines) - 3)
    logs = list(status.logs)[-min(12, available):]
    lines.extend('  ' + _fit(log, width - 4) for log in logs)
    if not logs:
        lines.append('  Waiting for first worker event…')
    lines.extend([rule, 'Ctrl+C: stop / Reopen: resume  •  No game binaries uploaded'])
    if status.returncode is not None:
        lines.append('Worker stopped (exit code ' + str(status.returncode) + ')')
    return '\n'.join(_fit(line, width - 1) for line in lines) + '\n'


def _reader(pipe, events: queue.Queue) -> None:
    try:
        for line in pipe:
            events.put(('worker', line))
    finally:
        events.put(('reader-done',))


def _github_poll(repo: str, events: queue.Queue, stop: threading.Event) -> None:
    while not stop.is_set():
        counts, error = fetch_stats(repo)
        events.put(('gh', counts, error, time.monotonic()))
        if stop.wait(55):
            break


def _drain(events: queue.Queue, status: Status) -> list[str]:
    updates = []
    for _ in range(200):
        try:
            event = events.get_nowait()
        except queue.Empty:
            break
        if event[0] == 'worker':
            status.feed(event[1])
            if status.logs:
                updates.append(status.logs[-1])
        elif event[0] == 'gh':
            _, counts, error, stamp = event
            if counts is not None:
                status.counts = counts
            status.error = error
            status.gh_updated = stamp
    return updates


def _supports_curses() -> bool:
    return (os.name != 'nt' and sys.stdin.isatty() and sys.stdout.isatty()
            and os.getenv('TERM', '') not in ('', 'dumb')
            and os.getenv('NOVA3_COMPACT') != '1')


def _curses_loop(stdscr, process, events: queue.Queue, status: Status) -> None:
    import curses
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    stdscr.nodelay(True)
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        for index, fg in ((1, curses.COLOR_CYAN), (2, curses.COLOR_GREEN),
                          (3, curses.COLOR_YELLOW), (4, curses.COLOR_WHITE)):
            curses.init_pair(index, fg, -1)
    last_paint = 0.0
    while process.poll() is None or not events.empty():
        _drain(events, status)
        now = time.monotonic()
        if now - last_paint >= 0.5:
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            if width < 45 or height < 15:
                items = ['NOVA3 | Widen/heighten terminal for full dashboard',
                         'Worker continues running. Ctrl+C stops.']
            else:
                items = render(status, now=now, width=width, height=height).splitlines()
            for idx, line in enumerate(items[:max(0, height - 1)]):
                if not line:
                    continue
                style = 0
                if curses.has_colors():
                    if idx == 0: style = curses.color_pair(1) | curses.A_BOLD
                    elif line.startswith(('◐','◓','◑','◒','■')): style = curses.color_pair(2) | curses.A_BOLD
                    elif line.startswith(('GITHUB PIPELINE', 'RECENT EVENTS', 'Queue ETA:', 'Session timeout ceiling:')):
                        style = curses.color_pair(3)
                    elif line.startswith('─'): style = curses.color_pair(4) | curses.A_DIM
                try:
                    stdscr.addnstr(idx, 0, line, max(0, width - 1), style)
                except (curses.error, ValueError):
                    pass  # Terminal resize may momentarily invalidate the viewport.
            stdscr.noutrefresh()
            curses.doupdate()
            last_paint = now
        time.sleep(0.12)
    _drain(events, status)


def _compact_loop(process, events: queue.Queue, status: Status) -> None:
    """No cursor escapes at all; runs on Windows and unsupported terminal emulators."""
    print('\nNOVA3 | distributed reconstruction — compact status', flush=True)
    print('Provider:', status.provider, '| Repository:', status.repo, flush=True)
    last_pulse = time.monotonic()
    while process.poll() is None or not events.empty():
        for line in _drain(events, status):
            print(line, flush=True)
        now = time.monotonic()
        if now - last_pulse >= 30:
            print(f'[STATUS] {status.phase} | uptime {elapsed(now-status.begin)} | '
                  f'{_session_budget(status,now)} | {_queue_eta(status)}', flush=True)
            last_pulse = now
        time.sleep(0.2)
    for line in _drain(events, status):
        print(line, flush=True)


def run(argv: list[str], *, cwd: Path | str, repo: str, provider: str) -> int:
    if os.environ.get('NOVA3_PLAIN_OUTPUT') == '1' or not sys.stdout.isatty():
        return subprocess.run(argv, cwd=cwd).returncode
    status = Status(repo=repo, provider=provider)
    engine = Path(cwd)/'reconstruction'/'engine'
    tests = Path(cwd)/'reconstruction'/'tests'
    if engine.is_dir():
        suffixes = ('.cpp', '.c', '.cc', '.h', '.hpp')
        status.source_files = sum(p.is_file() and p.suffix.lower() in suffixes for p in engine.rglob('*'))
        status.test_files = (sum(p.is_file() and p.suffix.lower() in suffixes for p in tests.rglob('*'))
                             if tests.is_dir() else 0)
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    process = None
    interrupted = False
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                   errors='replace', bufsize=1)
        threading.Thread(target=_reader, args=(process.stdout, events), daemon=True).start()
        threading.Thread(target=_github_poll, args=(repo, events, stop), daemon=True).start()
        if _supports_curses():
            try:
                import curses
                curses.wrapper(_curses_loop, process, events, status)
            except (curses.error, ImportError, OSError) as exc:
                print('Terminal fullscreen UI unavailable; using compact mode:', exc, flush=True)
                _compact_loop(process, events, status)
        else:
            _compact_loop(process, events, status)
        status.returncode = process.wait()
    except KeyboardInterrupt:
        interrupted = True
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        status.returncode = 130
    finally:
        stop.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if status.returncode is None:
            status.returncode = process.returncode if process is not None else 1
        print(f'\nNOVA3 worker stopped ({status.returncode}). Saved checkouts are retained.')
        for line in list(status.logs)[-5:]:
            print('  ' + line)
    return 130 if interrupted else status.returncode
