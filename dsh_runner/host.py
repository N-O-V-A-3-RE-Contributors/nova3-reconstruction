"""Client for the DSH Host's native agent surface (squads, runs, sessions).

Everything here goes through the Host's own guarded HTTP API on this machine, or
reads the Host's own session files. It never edits the agents ledger, never
writes a session, and never touches a credential.

Verified seams (DSH 0.2.1-rc.5, `@linxin666/dsh-agents`):

* ``GET  /dsh-agents/api/state``  -> teams, runs, presets, workspaces, models,
  efforts. The routes are served to this machine's own browser only, so the
  request must carry an ``Origin`` matching the Host.
* ``POST /dsh-agents/api/team``   -> create a squad definition.
* ``POST /dsh-agents/api/member`` -> add a member (preset, permission, model,
  reasoning effort, brief).
* ``POST /dsh-agents/api/run``    -> launch a REAL Host session for a member and
  record a run. This spends model quota and is the only way to make a run appear
  in the Agents tab.
* ``POST /dsh-agents/api/squad/run`` -> launch the squad Lead.

A Host session's transcript is ``$DSH_HOME/sessions/<cwd-slug>/<session>/session.v4.jsonl.zstd``;
the terminal ``turn_end`` event in it is the objective outcome, exactly as for a
headless child. That is how a launched run is watched without parsing prose.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = 'http://127.0.0.1:3080'
TERMINAL_KINDS = ('completed', 'error', 'aborted')


class HostUnavailable(RuntimeError):
    """The DSH Host API is not reachable or refused the request."""


@dataclass(frozen=True)
class SessionReport:
    """Objective state of one Host session, read from its own transcript."""

    session_id: str
    status: str                # running | completed | error | aborted | missing
    turn_kind: str | None = None
    error_code: str | None = None
    steps: int = 0
    tool_calls: int = 0
    last_event_age_s: float | None = None
    path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 'completed'

    def summary(self) -> dict:
        return {'session_id': self.session_id, 'status': self.status,
                'turn_kind': self.turn_kind, 'error_code': self.error_code,
                'steps': self.steps, 'tool_calls': self.tool_calls,
                'last_event_age_s': self.last_event_age_s}


class HostClient:
    """Minimal, read-mostly client for one DSH Host."""

    def __init__(self, base: str = DEFAULT_BASE, *, home: pathlib.Path | None = None,
                 timeout: float = 30.0) -> None:
        self.base = base.rstrip('/')
        self.home = pathlib.Path(home or os.environ.get('DSH_HOME')
                                 or pathlib.Path.home() / '.local/share/nova3-dsh/data')
        self.timeout = timeout

    # -- HTTP ---------------------------------------------------------------
    def _call(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        headers = {'Origin': self.base}
        if data is not None:
            headers['content-type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data,
                                        headers=headers,
                                        method='POST' if data is not None else 'GET')
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise HostUnavailable(f'{path}: HTTP {exc.code}') from None
        except (urllib.error.URLError, OSError) as exc:
            raise HostUnavailable(f'cannot reach the DSH Host at {self.base}: {exc}') from None
        try:
            value = json.loads(body or b'{}')
        except ValueError:
            raise HostUnavailable(f'{path}: not JSON') from None
        if isinstance(value, dict) and value.get('ok') is False:
            raise HostUnavailable(f'{path}: {value.get("code") or "refused"}')
        return value if isinstance(value, dict) else {}

    def state(self) -> dict:
        return self._call('/dsh-agents/api/state')

    def workspace_id(self, path: str) -> str | None:
        target = str(pathlib.Path(path))
        for item in self.state().get('workspaces') or []:
            if item.get('path') == target:
                return item.get('id')
        return None

    def team(self, name: str) -> dict | None:
        for item in self.state().get('teams') or []:
            if item.get('name') == name:
                return item
        return None

    def ensure_team(self, name: str, *, workspace_id: str, description: str = '',
                    lead_preset: str = 'standard') -> dict:
        existing = self.team(name)
        if existing is not None:
            return existing
        created = self._call('/dsh-agents/api/team', {'team': {
            'name': name, 'description': description, 'workspaceId': workspace_id,
            'leadPreset': lead_preset, 'order': 0}})
        return created.get('team') or {}

    def ensure_member(self, team_id: str, member: dict) -> dict:
        team = next((item for item in self.state().get('teams') or []
                     if item.get('id') == team_id), None) or {}
        for existing in team.get('members') or []:
            if existing.get('name') == member.get('name'):
                return existing
        created = self._call('/dsh-agents/api/member',
                             {'teamId': team_id, 'member': member})
        return created.get('team') or {}

    # -- native session bridge (dsh-runner plugin) ---------------------------
    def bridge_available(self) -> bool:
        """True when the dsh-runner plugin answered (any non-404 response)."""
        try:
            self._call('/dsh-runner/api/session/status?id=probe')
            return True
        except HostUnavailable as exc:
            return 'HTTP 404' not in str(exc)

    def create_session(self, *, workspace_path: str, prompt: str,
                       permission: str | None = None, model: str | None = None,
                       effort: str | None = None, title: str | None = None,
                       agent_preset: str | None = None) -> str:
        """Create a native session in an explicit directory via the dsh-runner plugin.

        This is what keeps a Host run inside a per-task checkout: the plugin
        passes the path as the session's own `cwd` (`session.create` accepts
        either a workspaceId or a cwd), so no Workspace registration is needed.
        """
        payload = {'workspacePath': workspace_path, 'prompt': prompt}
        for key, value in (('permission', permission), ('model', model),
                           ('reasoningEffort', effort), ('title', title),
                           ('agentPreset', agent_preset)):
            if value:
                payload[key] = value
        created = self._call('/dsh-runner/api/session', payload)
        session = created.get('sessionId')
        if not isinstance(session, str) or not session:
            raise HostUnavailable('/dsh-runner/api/session returned no session id')
        return session

    # -- runs ---------------------------------------------------------------
    def run_member(self, *, team_id: str, member_id: str, first_message: str) -> str:
        """Launch a real Host session for one member. Spends quota.

        Returns the Host session id; the caller watches it with `report()`.
        """
        created = self._call('/dsh-agents/api/run', {
            'teamId': team_id, 'memberId': member_id,
            'firstMessage': first_message})
        run = created.get('run') or {}
        session = run.get('sessionId') or created.get('sessionId')
        if not isinstance(session, str) or not session:
            raise HostUnavailable('/dsh-agents/api/run returned no session id')
        return session

    # -- observation ---------------------------------------------------------
    def session_path(self, session_id: str) -> pathlib.Path | None:
        for candidate in self.home.glob(f'sessions/*/{session_id}'):
            if candidate.is_dir():
                return candidate
        return None

    def report(self, session_id: str, *, now: float | None = None) -> SessionReport:
        now = time.time() if now is None else now
        folder = self.session_path(session_id)
        if folder is None:
            return SessionReport(session_id, 'missing')
        transcript = folder / 'session.v4.jsonl.zstd'
        if not transcript.is_file():
            return SessionReport(session_id, 'missing', path=str(folder))
        age = max(0.0, now - transcript.stat().st_mtime)
        text = _decompress(transcript)
        if text is None:
            return SessionReport(session_id, 'running' if age < 120 else 'error',
                                 last_event_age_s=round(age, 1), path=str(folder))
        steps = tools = 0
        kind = error = None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'tool_call':
                tools += 1
            if event.get('phase') == 'step_end':
                steps += 1
            if event.get('phase') == 'turn_end':
                reason = event.get('reason') or {}
                kind = str(reason.get('kind') or '') or None
                err = reason.get('error') or {}
                if err:
                    error = str(err.get('code') or '') or None
        if kind in TERMINAL_KINDS:
            status = 'completed' if kind == 'completed' else (
                'aborted' if kind == 'aborted' else 'error')
        else:
            status = 'running' if age < 180 else 'error'
        return SessionReport(session_id, status, turn_kind=kind, error_code=error,
                             steps=steps, tool_calls=tools,
                             last_event_age_s=round(age, 1), path=str(folder))


def _decompress(path: pathlib.Path) -> str | None:
    """Read a zstd transcript; None when the tool cannot decode it."""
    tool = shutil.which('zstd')
    if tool is None:
        return None
    result = subprocess.run([tool, '-d', '-c', '-q', str(path)],
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        return None
    return result.stdout.decode('utf-8', 'replace')
