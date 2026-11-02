"""Execution backends: one interface, two very different guarantees.

A backend's only job is to run ONE bounded model session for a task and report
an objective outcome. It never decides whether the work is correct.

``HeadlessBackend`` (default, current production behaviour)
    Spawns ``dsh --profile headless`` in an explicit working directory. Anything
    can be that directory, so a project can give every task its own checkout and
    run tasks concurrently. The session is invisible in the DSH Agents tab.

``HostBackend`` (native visibility)
    Launches a real DSH Host session through the squad API, so it appears in the
    Agents tab and can be opened in the Web UI. The Host pins the session to the
    squad's registered workspace, so a profile that uses it must declare
    ``execution.isolation: shared-workspace``: tasks then run in one working
    tree, one at a time. That is a real semantic change, not a detail — a
    project whose verification depends on per-task checkouts must stay on
    `headless` until DSH can register a workspace per checkout.

Both backends return the same ``RunResult`` so a supervisor can switch by
configuration.
"""
from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass
from typing import Protocol

from .host import HostClient, HostUnavailable
from .profile import Profile


class BackendError(RuntimeError):
    """The backend could not start a session at all (configuration, not a model)."""


@dataclass(frozen=True)
class RunResult:
    status: str                 # completed | error | timeout | cancelled | unavailable
    backend: str
    session_id: str | None = None
    steps: int = 0
    tool_calls: int = 0
    elapsed_s: float = 0.0
    detail: str = ''
    visible_in_agents_tab: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 'completed'

    def summary(self) -> dict:
        return {'status': self.status, 'backend': self.backend,
                'session_id': self.session_id, 'steps': self.steps,
                'tool_calls': self.tool_calls, 'elapsed_s': round(self.elapsed_s, 1),
                'detail': self.detail[:400],
                'visible_in_agents_tab': self.visible_in_agents_tab}


class Backend(Protocol):
    name: str

    def run(self, *, prompt: str, cwd: pathlib.Path, log_path: pathlib.Path,
            timeout_s: int) -> RunResult: ...


class HostBackend:
    """Run through a native DSH Host session (visible in the Agents tab)."""

    name = 'host'
    visible_in_agents_tab = True

    def __init__(self, profile: Profile, *, client: HostClient | None = None,
                 poll_s: float = 5.0) -> None:
        if profile.execution.isolation != 'shared-workspace':
            raise BackendError(
                'the host backend runs in the squad workspace; the profile must '
                'declare execution.isolation: shared-workspace')
        if not profile.execution.squad:
            raise BackendError('the host backend needs execution.squad')
        self.profile = profile
        self.client = client or HostClient()
        self.poll_s = max(1.0, float(poll_s))

    def run(self, *, prompt: str, cwd: pathlib.Path, log_path: pathlib.Path,
            timeout_s: int) -> RunResult:
        started = time.monotonic()
        try:
            state = self.client.state()
        except HostUnavailable as exc:
            return RunResult('unavailable', self.name, elapsed_s=time.monotonic() - started,
                             detail=str(exc))
        workspace = next((item.get('id') for item in state.get('workspaces') or []
                          if item.get('path') == self.profile.workspace), None)
        if workspace is None:
            return RunResult('unavailable', self.name,
                             detail=f'workspace {self.profile.workspace} is not registered in DSH')
        team = self.client.team(self.profile.execution.squad)
        if not team:
            return RunResult('unavailable', self.name,
                             detail=f'squad {self.profile.execution.squad!r} is not registered')
        member = next((item for item in team.get('members') or []
                       if item.get('name') == self.profile.worker.name), None)
        if member is None:
            return RunResult('unavailable', self.name,
                             detail=f'squad has no member {self.profile.worker.name!r}')
        try:
            session = self.client.run_member(team_id=team['id'],
                                             member_id=member['id'],
                                             first_message=prompt)
        except HostUnavailable as exc:
            return RunResult('unavailable', self.name,
                             elapsed_s=time.monotonic() - started, detail=str(exc))
        deadline = started + timeout_s
        report = None
        while time.monotonic() < deadline:
            report = self.client.report(session)
            if report.status in ('completed', 'error', 'aborted'):
                break
            time.sleep(self.poll_s)
        elapsed = time.monotonic() - started
        if report is None or report.status == 'running':
            return RunResult('timeout', self.name, session_id=session,
                             elapsed_s=elapsed, visible_in_agents_tab=True,
                             detail='host session did not reach a terminal turn')
        return RunResult('completed' if report.ok else 'error', self.name,
                         session_id=session, steps=report.steps,
                         tool_calls=report.tool_calls, elapsed_s=elapsed,
                         detail=report.error_code or '', visible_in_agents_tab=True)


class HeadlessBackend:
    """Run a bounded headless child in an explicit cwd (current behaviour)."""

    name = 'headless'
    visible_in_agents_tab = False

    def __init__(self, profile: Profile, *, runner=None) -> None:
        self.profile = profile
        # Injected for tests; production imports the project's runtime lazily so
        # this package stays usable without nova3_team.
        self._runner = runner

    def _default_runner(self):
        from nova3_team import dsh_runtime  # pragma: no cover - production path
        return dsh_runtime

    def run(self, *, prompt: str, cwd: pathlib.Path, log_path: pathlib.Path,
            timeout_s: int) -> RunResult:
        starter = self._runner
        if starter is None:
            try:
                starter = self._default_runner()
            except Exception as exc:  # noqa: BLE001 - report, never crash
                raise BackendError(f'no headless runtime available: {exc}') from None
        try:
            outcome = starter.run(
                profile=self.profile, prompt=prompt, cwd=cwd,
                log_path=log_path, timeout_s=timeout_s)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendError(str(exc)) from None
        if isinstance(outcome, RunResult):
            return outcome
        status = getattr(outcome, 'status', 'error')
        mapping = {'COMPLETED': 'completed', 'SESSION_TIMEOUT': 'timeout',
                   'CANCELLED': 'cancelled', 'STEP_LIMIT': 'error',
                   'WAITING_CREDITS': 'unavailable', 'AUTH': 'unavailable'}
        return RunResult(mapping.get(str(status), 'error'), self.name,
                         session_id=getattr(outcome, 'session_id', None),
                         steps=int(getattr(outcome, 'steps', 0) or 0),
                         tool_calls=int(getattr(outcome, 'tool_calls', 0) or 0),
                         elapsed_s=float(getattr(outcome, 'elapsed_s', 0.0) or 0.0),
                         detail=str(getattr(outcome, 'error_code', '') or ''))


def backend_for(profile: Profile, *, client: HostClient | None = None,
                runner=None) -> Backend:
    if profile.execution.backend == 'host':
        return HostBackend(profile, client=client)
    return HeadlessBackend(profile, runner=runner)
