"""Offline contract tests for ``nova3_team.host_runner.run_native``.

These tests use a synthetic ``dsh_runner`` profile and recording fakes for
``profile_for``, ``HostClient`` and ``HostBackend``. No Host is contacted, no
session or subprocess is started, and no real profile/config file is read.
They establish adapter mapping only; they are not game or machine-code
equivalence evidence.
"""
from __future__ import annotations

import dataclasses
import pathlib
import socket
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest

pytest.importorskip(
    'dsh_runner', reason='optional dsh_runner package is not installed'
)
pytest.importorskip(
    'dsh_runner.backends', reason='optional dsh_runner package is not installed'
)

from dsh_runner.backends import RunResult  # noqa: E402
from dsh_runner.profile import (  # noqa: E402
    Execution,
    Member,
    Profile,
    Routing,
    Safety,
)

from nova3_team import dsh_runtime, host_runner  # noqa: E402


def _synthetic_profile() -> Profile:
    """A minimal Host profile whose values are easy to distinguish from overrides."""
    worker = Member(
        name='worker',
        preset='standard',
        permission='workspace-write',
        model='base/worker-model',
        effort='base-effort',
        brief='synthetic base worker brief',
    )
    reviewer = Member(
        name='reviewer',
        preset='review',
        permission='read-only',
        model='base/reviewer-model',
        effort='review-effort',
        brief='synthetic reviewer brief',
    )
    return Profile(
        name='synthetic-project',
        repo='synthetic/repository',
        workspace='/synthetic/host-workspace',
        execution=Execution(
            backend='host',
            isolation='shared-workspace',
            timeout_s=123,
            max_steps=45,
            squad='synthetic-squad',
            worker_member='worker',
        ),
        members={'worker': worker, 'reviewer': reviewer},
        routing=Routing(
            semantic=('base/semantic-route',),
            routine=('base/routine-route',),
        ),
        safety=Safety(
            safe_paths=('reconstruction/tests/',),
            denied_paths=('originals/',),
            verify=('python -m pytest',),
        ),
        path='/synthetic/profile.yaml',
        raw={'synthetic': True, 'members': {'worker': 'base'}},
    )


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    """Fail if the contract test ever tries to escape its recording fakes."""
    def forbidden(*args, **kwargs):
        pytest.fail(
            'host_runner contract tests forbid network and process execution'
        )

    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for method in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
        if hasattr(socket.socket, method):
            monkeypatch.setattr(socket.socket, method, forbidden)
    for method in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, method, forbidden)


@pytest.fixture
def harness(monkeypatch):
    """Patch the three seams and record every construction/run argument."""
    profile = _synthetic_profile()
    state = SimpleNamespace(
        profile=profile,
        profile_requests=[],
        clients=[],
        backends=[],
        result=RunResult(
            status='completed',
            backend='host',
            session_id='synthetic-session',
            steps=0,
            tool_calls=0,
            elapsed_s=0.0,
            detail='',
            visible_in_agents_tab=True,
        ),
    )

    def recording_profile_for(project='nova3'):
        state.profile_requests.append(project)
        return profile

    class RecordingHostClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            state.clients.append(self)

    class RecordingHostBackend:
        def __init__(self, backend_profile, *, client=None, poll_s=None):
            self.profile = backend_profile
            self.client = client
            self.poll_s = poll_s
            self.run_calls = []
            state.backends.append(self)

        def run(self, *, prompt, cwd, log_path, timeout_s):
            self.run_calls.append(
                {
                    'prompt': prompt,
                    'cwd': cwd,
                    'log_path': log_path,
                    'timeout_s': timeout_s,
                }
            )
            return state.result

    monkeypatch.setattr(host_runner, 'profile_for', recording_profile_for)
    monkeypatch.setattr(host_runner, 'HostClient', RecordingHostClient)
    monkeypatch.setattr(host_runner, 'HostBackend', RecordingHostBackend)
    return state


def test_run_native_applies_overrides_only_to_the_per_run_worker(harness):
    base_before = dataclasses.asdict(harness.profile)
    base_worker = harness.profile.members['worker']
    prompt = '  Exact prompt λ\nsecond line\tkeeps trailing space \n'
    cwd = pathlib.Path('/synthetic checkout/naïve task')
    timeout_s = 37

    outcome = host_runner.run_native(
        prompt=prompt,
        cwd=cwd,
        timeout_s=timeout_s,
        model='override/model-345',
        effort='high',
        title='ignored title',
        project='synthetic-project',
    )

    # The real profile loader was never consulted for a file.
    assert harness.profile_requests == ['synthetic-project']

    # Exactly one HostClient and one HostBackend were constructed, through the fakes.
    assert len(harness.clients) == 1
    assert len(harness.backends) == 1
    client = harness.clients[0]
    backend = harness.backends[0]
    assert backend.client is client
    assert client.args == ()
    assert client.kwargs == {}
    assert backend.poll_s == 5.0

    # Overrides land on a replaced worker member in a replaced Profile.
    effective = backend.profile
    assert effective is not harness.profile
    assert effective.worker.name == 'worker'
    assert effective.worker.model == 'override/model-345'
    assert effective.worker.effort == 'high'
    assert effective.execution.worker_member == 'worker'
    assert effective.members['worker'] is not base_worker
    assert effective.members['reviewer'] == harness.profile.members['reviewer']
    assert effective.members is not harness.profile.members

    # The base Profile and its dict are unchanged, including the worker's route.
    assert dataclasses.asdict(harness.profile) == base_before
    assert harness.profile.members['worker'] is base_worker
    assert harness.profile.worker.model == 'base/worker-model'
    assert harness.profile.worker.effort == 'base-effort'

    # The exact prompt, cwd and timeout reach HostBackend.run; no coercion.
    assert backend.run_calls == [
        {
            'prompt': prompt,
            'cwd': cwd,
            'log_path': cwd / 'round-native.log',
            'timeout_s': timeout_s,
        }
    ]
    assert backend.run_calls[0]['timeout_s'] is timeout_s

    assert isinstance(outcome, dsh_runtime.SessionOutcome)
    assert outcome.status == dsh_runtime.COMPLETED
    assert outcome.exit_code == 0
    assert outcome.error_code is None
    assert outcome.error_message == ''


@pytest.mark.parametrize(
    'model,effort,expected_model,expected_effort',
    [
        pytest.param('', '', 'base/worker-model', 'base-effort', id='no-overrides'),
        pytest.param(
            'override/model-345',
            '',
            'override/model-345',
            'base-effort',
            id='model-only',
        ),
        pytest.param(
            '',
            'low',
            'base/worker-model',
            'low',
            id='effort-only',
        ),
    ],
)
def test_run_native_keeps_base_fields_for_absent_overrides(
    harness, model, effort, expected_model, expected_effort
):
    base_before = dataclasses.asdict(harness.profile)

    host_runner.run_native(
        prompt='synthetic prompt',
        cwd=pathlib.Path('/synthetic/cwd'),
        timeout_s=11,
        model=model,
        effort=effort,
    )

    effective = harness.backends[0].profile
    assert effective.worker.model == expected_model
    assert effective.worker.effort == expected_effort
    assert harness.profile.worker.model == 'base/worker-model'
    assert harness.profile.worker.effort == 'base-effort'
    assert dataclasses.asdict(harness.profile) == base_before


@pytest.mark.parametrize(
    'native_status,expected_constant,detail,expected_exit,'
    'expected_error_code,expected_error_message',
    [
        pytest.param(
            'completed',
            'COMPLETED',
            'ignored: completed detail',
            0,
            None,
            '',
            id='completed',
        ),
        pytest.param(
            'timeout',
            'SESSION_TIMEOUT',
            'SESSION_TIMEOUT: host session did not reach a terminal turn',
            1,
            'SESSION_TIMEOUT',
            'SESSION_TIMEOUT: host session did not reach a terminal turn',
            id='timeout',
        ),
        pytest.param(
            'error',
            'EXECUTION_ERROR',
            'EXECUTION_ERROR: model failed: synthetic detail',
            1,
            'EXECUTION_ERROR',
            'EXECUTION_ERROR: model failed: synthetic detail',
            id='error',
        ),
        pytest.param(
            'unavailable',
            'WAITING_CREDITS',
            'bridge plugin is not loaded',
            1,
            'bridge plugin is not loaded',
            'bridge plugin is not loaded',
            id='unavailable',
        ),
        pytest.param(
            'cancelled',
            'CANCELLED',
            'CANCELLED: owner stopped the session',
            1,
            'CANCELLED',
            'CANCELLED: owner stopped the session',
            id='cancelled',
        ),
        pytest.param(
            'unrecognized-status',
            'EXECUTION_ERROR',
            'MYSTERY: unexpected native state',
            1,
            'MYSTERY',
            'MYSTERY: unexpected native state',
            id='unknown-status',
        ),
    ],
)
def test_native_status_and_error_mapping(
    harness,
    native_status,
    expected_constant,
    detail,
    expected_exit,
    expected_error_code,
    expected_error_message,
):
    harness.result = RunResult(
        status=native_status,
        backend='host',
        session_id='session-345',
        steps=7,
        tool_calls=9,
        elapsed_s=1.25,
        detail=detail,
        visible_in_agents_tab=True,
    )

    outcome = host_runner.run_native(
        prompt='synthetic prompt',
        cwd=pathlib.Path('/synthetic/cwd'),
        timeout_s=19,
    )

    assert outcome.status == getattr(dsh_runtime, expected_constant)
    assert outcome.status == {
        'COMPLETED': dsh_runtime.COMPLETED,
        'SESSION_TIMEOUT': dsh_runtime.SESSION_TIMEOUT,
        'EXECUTION_ERROR': dsh_runtime.EXECUTION_ERROR,
        'WAITING_CREDITS': dsh_runtime.WAITING_CREDITS,
        'CANCELLED': dsh_runtime.CANCELLED,
    }[expected_constant]
    assert outcome.exit_code == expected_exit
    assert (outcome.exit_code == 0) is (native_status == 'completed')
    assert outcome.error_code == expected_error_code
    assert outcome.error_message == expected_error_message
    assert outcome.turn_reason == native_status
    assert outcome.session_id == 'session-345'
    assert (outcome.steps, outcome.tool_calls, outcome.elapsed_s) == (7, 9, 1.25)
    assert (outcome.input_tokens, outcome.output_tokens) == (0, 0)
    assert len(harness.backends) == 1
    assert harness.backends[0].run_calls[0]['timeout_s'] == 19
