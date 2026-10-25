"""Cross-platform zero-dependency visible status dashboard for the NOVA3 worker.

This does not infer decompilation coverage or call AI progress. Only worker logs,
repository issue labels and pull request state are reported as observed facts.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

ANSI_ESCAPE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
TASK_RE = re.compile(r'Trying #(\d+): (.+)')
SESSION_RE = re.compile(r'#(\d+) AI session (\d+)/(\d+) \(([^)]+)\)')
SPINNER = '|/-\\'


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
    if 'No queued tasks.' in line:
        return 'WAITING FOR TASKS'
    if 'Requested task #' in line:
        return 'RESERVING ISSUE'
    if 'AI session ' in line:
        return 'AI SESSION RUNNING'
    if 'working in ' in line or 'Reusing saved checkout ' in line:
        return 'CHECKOUT READY'
    if 'Session ' in line and 'returned' in line:
        return 'AI SESSION RETRY'
    if 'PR submitted:' in line:
        return 'PULL REQUEST OPENED'
    if 'FAILED #' in line:
        return 'TASK FAILED'
    if 'No claim for #' in line:
        return 'TASK CLAIM BUSY'
    if 'Trying #' in line:
        return 'SELECTING TASK'
    if 'Repo=' in line and 'account=' in line:
        return 'CONNECTED'
    if 'Coordinator/worker transient failure' in line:
        return 'NETWORK/COORDINATOR ERROR'
    return existing


def summarize_issues(issues: list[dict]) -> dict:
    labels = ('nova:queued', 'nova:claimed', 'nova:review')
    result = {'total': len(issues), 'queued': 0, 'claimed': 0, 'review': 0, 'other': 0}
    for issue in issues:
        names = {label['name'] for label in issue.get('labels', []) if isinstance(label, dict) and 'name' in label}
        found = [label for label in labels if label in names]
        if not found:
            result['other'] += 1
        else:
            result[found[0].split(':')[1]] += 1
    result['limited'] = len(issues) >= 1000
    return result


def fetch_stats(repo: str, timeout: float = 15) -> tuple[dict | None, str | None]:
    """Counts GitHub state, not reverse engineering correctness/completion."""
    gh = shutil.which('gh')
    if not gh:
        return None, 'GitHub CLI missing'
    try:
        issues = subprocess.run([gh, 'issue', 'list', '--repo', repo, '--state', 'open',
                                 '--limit', '1000', '--json', 'number,labels'],
                                capture_output=True, text=True, timeout=timeout)
        if issues.returncode:
            return None, ('GitHub unavailable: ' + clean(issues.stderr or issues.stdout).strip())[-140:]
        parsed = json.loads(issues.stdout)
        counts = summarize_issues(parsed)
        open_pr = subprocess.run([gh, 'pr', 'list', '--repo', repo, '--state', 'open',
                                  '--limit', '500', '--json', 'number'],
                                 capture_output=True, text=True, timeout=timeout)
        if open_pr.returncode == 0:
            counts['open_prs'] = len(json.loads(open_pr.stdout))
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
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=11))
    counts: dict | None = None
    error: str | None = None
    gh_updated: float | None = None
    source_files: int | None = None
    test_files: int | None = None
    pr_submitted: int = 0
    returncode: int | None = None

    def feed(self, line: str) -> None:
        line = clean(line).rstrip()
        if not line:
            return
        self.latest_output = time.monotonic()
        self.logs.append(line)
        new_phase = identify_phase(line, self.phase)
        if new_phase != self.phase:
            self.phase = new_phase
            self.phase_since = self.latest_output
        found = TASK_RE.search(line)
        if found:
            self.task_number = int(found.group(1))
            self.task = found.group(2)
        found = SESSION_RE.search(line)
        if found:
            self.task_number = int(found.group(1))
            self.session = f'{found.group(2)} of {found.group(3)} ({found.group(4)})'
        if 'PR submitted:' in line:
            self.pr_submitted += 1


def _color(text: str, shade: str, enable: bool) -> str:
    return f'\033[{shade}m{text}\033[0m' if enable else text


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ''
    return text if len(text) <= width else (text[:max(0, width-1)]+'…')


def _bar(counts: dict | None, width: int, color: bool) -> str:
    width = max(4, width)
    if not counts or not counts.get('total'):
        return _color('░' * width, '90', color)
    total = max(1, counts['total'])
    n_claimed = min(width, round(counts.get('claimed', 0) / total * width))
    n_review = min(width - n_claimed, round(counts.get('review', 0) / total * width))
    n_queued = width - n_claimed - n_review
    return (_color('▓'*n_review, '32', color) +
            _color('▒'*n_claimed, '33', color) +
            _color('░'*n_queued, '90', color))


def render(status: Status, now: float | None = None, width: int = 90, color: bool = True) -> str:
    now = time.monotonic() if now is None else now
    width = max(52, min(120, width))
    div = '─' * (width-2)
    title = _color('NOVA3', '1;36', color) + '  |  DISTRIBUTED RECONSTRUCTION'
    spinner = SPINNER[int(now*3) % len(SPINNER)] if status.returncode is None else ('✓' if status.returncode == 0 else '×')
    lines = [title, _color(div, '90', color),
             f'{_color(spinner, "36", color)}  {status.phase}   •   runtime {elapsed(now-status.begin)}',
             f'Repository: {_fit(status.repo, width-14)}   AI: {status.provider}',
             f'Current task: '+_fit((f'#{status.task_number} ' if status.task_number is not None else '') + status.task, width-17),
             f'AI session: {status.session}   |   stage time {elapsed(now-status.phase_since)}',
             _color(div, '90', color),
             _color('GITHUB TASK QUEUE  ·  LIVE DATA, NOT GAME COMPLETION', '1;37', color)]
    if status.counts is not None:
        c = status.counts
        suffix = '+' if c.get('limited') else ''
        lines.append(f"Open tasks observed: {c['total']}{suffix}    Queued: {c['queued']}    Claimed: {c['claimed']}    Review: {c['review']}")
        lines.append(_bar(c, max(6,width-6), color))
        lines.append(f"Open pull requests: {c.get('open_prs', 'unavailable')}    PRs submitted this run: {status.pr_submitted}")
    else:
        lines += ['Task counts: ' + (status.error or 'Loading from GitHub…'),
                  _bar(None,max(6,width-6),color)]
    age = f'{elapsed(now - status.gh_updated)} ago' if status.gh_updated is not None else 'not yet'
    if status.source_files is not None:
        lines.append(f'Local source inventory: {status.source_files} engine C/C++ files  |  {status.test_files} test source files (not verification)')
    lines += [f'GitHub last checked: {age}  |  Game/source matching %: not established',
              _color(div, '90', color),
              _color('RECENT WORKER ACTIVITY  ·  EXACT WORKER MESSAGES', '1;37', color)]
    logs = list(status.logs)[-min(10,max(4,shutil.get_terminal_size((width, 30)).lines - 19)):]
    for l in logs:
        lines.append('  '+_fit(l, width-4))
    if not logs:
        lines.append('  Waiting for worker output…')
    lines += [_color(div, '90', color),
              _color('Ctrl+C: stop • Relaunch: resume saved work • No original binaries uploaded', '90', color)]
    if status.returncode is not None:
        lines.append(f'Worker exited with code {status.returncode}.')
    return '\n'.join(lines) + '\n'


def _reader(pipe, events: queue.Queue) -> None:
    try:
        for line in pipe:
            events.put(line)
    finally:
        events.put(None)


def _github_poll(repo: str, events: queue.Queue, stop: threading.Event) -> None:
    while not stop.is_set():
        counts, error = fetch_stats(repo)
        events.put(('gh', counts, error, time.monotonic()))
        if stop.wait(55):
            break


def run(argv: list[str], *, cwd: Path | str, repo: str, provider: str) -> int:
    """Run worker with a live status view; degrade to ordinary stdout when noninteractive."""
    if not sys.stdout.isatty() or os.environ.get('NOVA3_PLAIN_OUTPUT') == '1':
        return subprocess.run(argv, cwd=cwd).returncode

    status = Status(repo=repo, provider=provider)
    engine = Path(cwd)/'reconstruction'/'engine'
    tests = Path(cwd)/'reconstruction'/'tests'
    if engine.is_dir():
        status.source_files = sum(1 for p in engine.rglob('*') if p.is_file() and p.suffix.lower() in ('.cpp','.c','.cc','.h','.hpp'))
        status.test_files = sum(1 for p in tests.rglob('*') if p.is_file() and p.suffix.lower() in ('.cpp','.c','.cc','.h','.hpp')) if tests.is_dir() else 0
    event_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    process = None
    color = os.environ.get('NO_COLOR') is None and os.environ.get('TERM') != 'dumb'
    was_interrupted = False
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace', bufsize=1)
        threading.Thread(target=_reader, args=(process.stdout,event_queue), daemon=True).start()
        threading.Thread(target=_github_poll, args=(repo,event_queue,stop), daemon=True).start()
        sys.stdout.write('\033[?25l')
        sys.stdout.flush()
        previous = 0.0
        while process.poll() is None or not event_queue.empty():
            try:
                event = event_queue.get(timeout=0.2)
                if isinstance(event, tuple) and event[0] == 'gh':
                    _, counts, error, stamp = event
                    if counts is not None:
                        status.counts = counts
                    status.error = error
                    status.gh_updated = stamp
                elif isinstance(event, str):
                    status.feed(event)
            except queue.Empty:
                pass
            now = time.monotonic()
            if now - previous >= 0.33:
                sys.stdout.write('\033[H\033[2J' + render(status, now=now, width=shutil.get_terminal_size((88, 30)).columns, color=color))
                sys.stdout.flush()
                previous = now
        status.returncode = process.wait()
    except KeyboardInterrupt:
        was_interrupted = True
        if process is not None and process.poll() is None:
            # On Unix, terminal Ctrl+C will generally signal both processes.
            # Give worker/checkpointing time to stop before escalating.
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
        sys.stdout.write('\033[?25h\033[0m\n')
        if status.returncode is None:
            status.returncode = process.returncode if process is not None else 1
        print(f'NOVA3 worker stopped ({status.returncode}). Saved task checkouts are retained.')
        print('Last worker messages:')
        for line in list(status.logs)[-5:]:
            print('  '+line)
    return 130 if was_interrupted else status.returncode
