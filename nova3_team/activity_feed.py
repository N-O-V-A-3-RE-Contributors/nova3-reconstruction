"""Bounded observational metadata from private Codex JSONL; never export text.

Only fixed categories, counters and worker-observed times reach the summary.
Unknown schemas and opaque provider output deliberately yield UNKNOWN.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
import time

POLL_SECONDS = 10
QUIET_SECONDS = 60
MAX_READ_BYTES = 256 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_ACTIVE_ITEMS = 1024


def _is_test(command):
    """Recognize only explicit test executables, never search arbitrary text."""
    if not isinstance(command, str) or len(command) > 8192:
        return False
    try:
        words = shlex.split(command)
        # Codex command_execution commonly wraps commands in a login shell.
        if (len(words) == 3 and words[0].split('/')[-1] in ('sh', 'bash', 'zsh')
                and words[1] in ('-c', '-lc')):
            words = shlex.split(words[2])
    except ValueError:
        return False
    if not words or any(x in command for x in ('\n', ';', '|', '&', '`', '$', '<', '>')):
        return False
    executable = words[0].split('/')[-1]
    return (executable in ('ctest', 'pytest', 'pytest-3') or
            (executable in ('python', 'python3') and len(words) >= 3
             and words[1] == '-m' and words[2] in ('unittest', 'pytest')))


def _atomic_write(path, summary):
    """Same-directory replace: readers see one complete 0600 JSON document."""
    descriptor, temporary = tempfile.mkstemp(prefix='.activity-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as sink:
            json.dump(summary, sink, separators=(',', ':'), allow_nan=False)
            sink.write('\n')
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class ActivityFeed:
    """One provider round, incrementally read at most once every ten seconds.

    Counters describe observed tool starts/completions, not successful tests.
    File counts are supplied only by the worker's independent scope check.
    No event strings, item IDs, paths, outputs or arguments are serialized.
    """

    def __init__(self, log, *, structured=False):
        self.log = Path(log)
        self.path = self.log.with_suffix('.activity.json')
        self.structured = structured
        self.offset = 0
        self.identity = None
        self.pending = b''
        self.discard_line = False
        self.active = {}
        self.commands_started = self.commands_completed = 0
        self.tests_started = self.tests_completed = 0
        self.counts_complete = structured
        self.last_activity = None
        self.category = 'unknown'
        self.known = False
        self.last_write = None

    def _uncertain(self):
        self.counts_complete = False
        self.known = False
        self.category = 'unknown'
        self.active.clear()

    def _event(self, event, now):
        if not isinstance(event, dict):
            self._uncertain()
            return
        kind = event.get('type')
        category = None
        if kind in ('thread.started', 'turn.started', 'turn.completed'):
            category = 'lifecycle'
        elif kind in ('turn.failed', 'error'):
            category = 'error'
        elif kind in ('item.started', 'item.completed'):
            item = event.get('item')
            if not isinstance(item, dict):
                self._uncertain()
                return
            item_type = item.get('type')
            if item_type in ('command_execution', 'file_change'):
                item_id = item.get('id')
                if not isinstance(item_id, str) or len(item_id) > 256:
                    self._uncertain()
                    return
                category = 'file_change'
                if item_type == 'command_execution':
                    category = 'test' if _is_test(item.get('command')) else 'command'
                if kind == 'item.started':
                    if item_id in self.active:
                        return
                    if len(self.active) >= MAX_ACTIVE_ITEMS:
                        self._uncertain()
                        return
                    self.active[item_id] = category
                    if item_type == 'command_execution':
                        self.commands_started += 1
                        self.tests_started += int(category == 'test')
                else:
                    prior = self.active.pop(item_id, None)
                    if item_type == 'command_execution':
                        # Retain the observed start category when completion omits it.
                        category = prior or category
                        self.commands_completed += 1
                        self.tests_completed += int(category == 'test')
            elif item_type == 'agent_message':
                category = 'message'
            elif item_type == 'reasoning':
                # Deliberately do not inspect or use reasoning as activity.
                return
        if category is None:
            self._uncertain()
            return
        self.known = True
        self.category = category
        self.last_activity = now

    def _read(self, now):
        if not self.structured:
            return
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(self.log, flags)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    self._uncertain()
                    return
                identity = (info.st_dev, info.st_ino)
                if self.identity not in (None, identity) or info.st_size < self.offset:
                    self.offset = 0
                    self.pending = b''
                    self.discard_line = False
                    self._uncertain()
                self.identity = identity
                if info.st_size - self.offset > MAX_READ_BYTES:
                    # Prefer recent observations; totals are now lower bounds.
                    self.offset = info.st_size - MAX_READ_BYTES
                    self.pending = b''
                    self.discard_line = True
                    self._uncertain()
                source.seek(self.offset)
                chunk = source.read(MAX_READ_BYTES)
                self.offset += len(chunk)
        except OSError:
            self._uncertain()
            return
        lines = (self.pending + chunk).split(b'\n')
        self.pending = lines.pop()
        for line in lines:
            if self.discard_line:
                self.discard_line = False
                continue
            if len(line) > MAX_LINE_BYTES:
                self._uncertain()
                continue
            try:
                self._event(json.loads(line), now)
            except (ValueError, UnicodeError, RecursionError):
                self._uncertain()
        if len(self.pending) > MAX_LINE_BYTES:
            self.pending = b''
            self.discard_line = True
            self._uncertain()

    def update(self, *, force=False, returncode=None, file_changed_count=None):
        """Best effort, no diagnostics containing private paths or event text."""
        now = time.monotonic()
        if not force and self.last_write is not None and now - self.last_write < POLL_SECONDS:
            return False
        self.last_write = now
        self._read(now)
        if returncode is not None and (self.pending or self.discard_line):
            self._uncertain()
        age = None if self.last_activity is None else max(0, int(now - self.last_activity))
        status = 'UNKNOWN'
        if self.known:
            status = 'RUNNING'
            if 'test' in self.active.values():
                status = 'TESTING'
            elif 'file_change' in self.active.values():
                status = 'EDITING'
            if age is not None and age >= QUIET_SECONDS:
                status = 'QUIET'
        if returncode is not None:
            status = 'FINISHED' if returncode == 0 else 'FAILED'
        summary = {
            'schema_version': 1,
            'timestamp_utc': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'status': status,
            'event_category': self.category,
            'last_activity_age_seconds': age,
            'commands_started': self.commands_started,
            'commands_completed': self.commands_completed,
            'tests_started': self.tests_started,
            'tests_completed': self.tests_completed,
            'file_changed_count': (file_changed_count if type(file_changed_count) is int
                                   and file_changed_count >= 0 else None),
            'counts_complete': self.counts_complete,
            'structured_events': self.structured,
        }
        try:
            _atomic_write(self.path, summary)
        except OSError:
            return False
        return True
