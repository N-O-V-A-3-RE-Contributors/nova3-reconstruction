"""Execute NOVA3 task sessions as native DSH Host sessions.

The supervisor keeps its own queue, approval gate, scope guard, verification and
publication. Only the model session changes: instead of a headless child it asks
the `dsh-runner` Host plugin for a session inside the task's checkout, so the run
is visible in the DSH Agents tab while the checkout stays per task.

Routing is passed in per session (the supervisor's router still decides), so
native and headless paths share one model policy. A profile that says
`backend: headless` never reaches this module.
"""
from __future__ import annotations

import dataclasses
import pathlib

from dsh_runner import HostClient, HostUnavailable, profile as profiles
from dsh_runner.backends import HostBackend

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / 'config' / 'projects'


def profile_for(project: str = 'nova3'):
    return profiles.load_profile(PROFILE_DIR / f'{project}.yaml')


def run_native(*, prompt: str, cwd: pathlib.Path, timeout_s: int,
               model: str = '', effort: str = '', title: str = '',
               project: str = 'nova3'):
    """One native Host session in `cwd`. Returns a dsh_runtime.SessionOutcome."""
    from . import dsh_runtime
    base = profile_for(project)
    worker = dataclasses.replace(
        base.worker,
        model=model or base.worker.model,
        effort=effort or base.worker.effort)
    members = dict(base.members)
    members[worker.name] = worker
    execution = dataclasses.replace(base.execution, worker_member=worker.name)
    profile = dataclasses.replace(base, members=members, execution=execution)
    backend = HostBackend(profile, client=HostClient(), poll_s=5.0)
    result = backend.run(prompt=prompt, cwd=pathlib.Path(cwd),
                         log_path=pathlib.Path(cwd) / 'round-native.log',
                         timeout_s=int(timeout_s))
    status = {'completed': dsh_runtime.COMPLETED,
              'timeout': dsh_runtime.SESSION_TIMEOUT,
              'error': dsh_runtime.EXECUTION_ERROR,
              'unavailable': dsh_runtime.WAITING_CREDITS,
              'cancelled': dsh_runtime.CANCELLED}.get(result.status,
                                                      dsh_runtime.EXECUTION_ERROR)
    return dsh_runtime.SessionOutcome(
        status=status, exit_code=0 if result.ok else 1,
        session_id=result.session_id or '', turn_reason=result.status,
        error_code=None if result.ok else (result.detail or result.status).split(':')[0][:60],
        error_message='' if result.ok else result.detail,
        steps=result.steps, tool_calls=result.tool_calls,
        elapsed_s=result.elapsed_s, input_tokens=0, output_tokens=0)
