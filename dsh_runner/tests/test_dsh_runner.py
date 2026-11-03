"""Contracts for the project-agnostic runner: profiles, backends, observation.

No model call, no network, no Host: the Host client is exercised against a
temporary DSH_HOME containing a real zstd transcript, and the headless backend
against a fake runtime module.
"""
import json
import pathlib
import subprocess
import types

import pytest

from dsh_runner import backends, host, profile as profiles


def valid_profile(**over):
    data = {
        'schema': profiles.SCHEMA,
        'name': 'demo',
        'repo': 'owner/demo',
        'workspace': '/tmp/demo',
        'execution': {'backend': 'headless', 'isolation': 'per-task-checkout',
                      'timeout_s': 900, 'max_steps': 120},
        'members': {'worker': {'preset': 'standard', 'permission': 'workspace-write',
                               'model': 'openrouter/demo', 'effort': 'high',
                               'brief': 'do the work'}},
        'routing': {'semantic': ['openrouter/demo'], 'routine': ['openrouter/fast']},
        'safety': {'safe_paths': ['src/'], 'denied_paths': ['secrets/'],
                   'verify': ['python -m pytest -q']},
    }
    data.update(over)
    return data


def test_valid_profile_round_trips():
    p = profiles.parse(valid_profile())
    assert p.name == 'demo' and p.worker.model == 'openrouter/demo'
    assert p.purpose('fix the compiler') == 'semantic'
    assert p.purpose('update docs metadata') == 'routine'
    assert p.route_for('update docs') == 'openrouter/fast'
    assert p.route_for('reconstruct a function') == 'openrouter/demo'


def test_schema_is_required_and_versioned():
    with pytest.raises(profiles.ProfileError):
        profiles.parse({**valid_profile(), 'schema': 'anything/1'})


def test_unsafe_values_are_refused():
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(safety={'safe_paths': ['../etc'], 'verify': []}))
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(safety={'safe_paths': ['/abs'], 'verify': []}))
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(routing={'semantic': ['bad value!']}))
    # Several members but no explicit choice of which one a task runs as.
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(members={'alpha': {'preset': 'standard'},
                                              'beta': {'preset': 'minimal'}}))
    # A single member is unambiguous.
    one = profiles.parse(valid_profile(members={'solo': {'preset': 'standard'}}))
    assert one.worker.name == 'solo'
    # An explicit choice wins over the literal "worker" name.
    chosen = profiles.parse(valid_profile(
        execution={'backend': 'headless', 'isolation': 'per-task-checkout',
                   'timeout_s': 900, 'max_steps': 120,
                   'worker_member': 'alpha'},
        members={'worker': {'preset': 'standard'}, 'alpha': {'preset': 'ptc'}}))
    assert chosen.worker.name == 'alpha'


def test_host_backend_selection_and_limits():
    # A shared-workspace host run needs a squad to launch through.
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(execution={'backend': 'host',
                                                'isolation': 'shared-workspace',
                                                'timeout_s': 900, 'max_steps': 120}))
    # Per-task checkouts are legal for the host backend: the dsh-runner bridge
    # registers a workspace for the checkout path instead of the squad workspace.
    per_task = profiles.parse(valid_profile(execution={
        'backend': 'host', 'isolation': 'per-task-checkout',
        'timeout_s': 900, 'max_steps': 120}))
    assert per_task.execution.backend == 'host'
    assert isinstance(backends.backend_for(per_task, client=object()),
                      backends.HostBackend)
    # A shared profile that names its squad parses and constructs; the guard is
    # re-checked at construction for callers that build a Profile themselves.
    shared = profiles.parse(valid_profile(execution={
        'backend': 'host', 'isolation': 'shared-workspace', 'squad': 'Demo',
        'timeout_s': 900, 'max_steps': 120}))
    assert isinstance(backends.HostBackend(shared, client=object()), backends.HostBackend)
    import dataclasses
    broken = dataclasses.replace(shared, execution=dataclasses.replace(
        shared.execution, squad=''))
    with pytest.raises(backends.BackendError):
        backends.HostBackend(broken, client=object())


class _FakeHost:
    def __init__(self, *, fails=None):
        self.calls = []
        self.fails = fails

    def create_session(self, **kwargs):
        self.calls.append(kwargs)
        if self.fails:
            raise self.fails
        return 'session-native'

    def report(self, session_id, **kwargs):
        return host.SessionReport(session_id, 'completed', 'completed', None, 4, 6, 0.2)


def test_host_backend_uses_the_bridge_with_the_checkout_path():
    p = profiles.parse(valid_profile(execution={'backend': 'host',
                                                'isolation': 'per-task-checkout',
                                                'timeout_s': 900, 'max_steps': 120}))
    fake = _FakeHost()
    backend = backends.HostBackend(p, client=fake, poll_s=0.01)
    result = backend.run(prompt='task text', cwd=pathlib.Path('/tmp/checkout-7'),
                         log_path=pathlib.Path('/tmp/checkout-7/round.log'), timeout_s=30)
    assert result.ok and result.visible_in_agents_tab is True
    assert fake.calls[0]['workspace_path'] == '/tmp/checkout-7'
    assert fake.calls[0]['prompt'] == 'task text'
    assert fake.calls[0]['model'] == 'openrouter/demo'
    assert fake.calls[0]['effort'] == 'high'
    assert result.steps == 4 and result.tool_calls == 6


def test_host_backend_reports_a_missing_bridge_clearly():
    p = profiles.parse(valid_profile(execution={'backend': 'host',
                                                'isolation': 'per-task-checkout',
                                                'timeout_s': 900, 'max_steps': 120}))
    fake = _FakeHost(fails=host.HostUnavailable('HTTP 404'))
    backend = backends.HostBackend(p, client=fake, poll_s=0.01)
    result = backend.run(prompt='t', cwd=pathlib.Path('/tmp/c'),
                         log_path=pathlib.Path('/tmp/c/log'), timeout_s=5)
    assert result.status == 'unavailable'
    assert 'bridge plugin is not loaded' in result.detail


def test_headless_adapter_maps_objective_status():
    seen = {}

    def run_stub(**kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(status='COMPLETED', session_id='s-1', steps=3,
                                     tool_calls=4, elapsed_s=1.5, error_code=None)

    p = profiles.parse(valid_profile())
    backend = backends.HeadlessBackend(p, runner=types.SimpleNamespace(run=run_stub))
    result = backend.run(prompt='task', cwd=pathlib.Path('/tmp/x'),
                         log_path=pathlib.Path('/tmp/x/round.log'), timeout_s=60)
    assert result.ok and result.backend == 'headless'
    assert result.visible_in_agents_tab is False
    assert seen['profile'] is p and seen['prompt'] == 'task'
    assert result.steps == 3 and result.tool_calls == 4


def test_headless_adapter_keeps_provider_pause_unavailable():
    def run_stub(**kwargs):
        return types.SimpleNamespace(status='WAITING_CREDITS', session_id=None,
                                     steps=0, tool_calls=0, elapsed_s=0.1,
                                     error_code='QUOTA')

    p = profiles.parse(valid_profile())
    backend = backends.HeadlessBackend(p, runner=types.SimpleNamespace(run=run_stub))
    result = backend.run(prompt='t', cwd=pathlib.Path('/tmp'), log_path=pathlib.Path('/tmp/l'),
                         timeout_s=10)
    assert result.status == 'unavailable' and result.detail == 'QUOTA'


def _transcript(home: pathlib.Path, session_id: str, events) -> None:
    folder = home / 'sessions' / '--tmp-demo--' / session_id
    folder.mkdir(parents=True)
    raw = ''.join(json.dumps(e) + '\n' for e in events).encode()
    path = folder / 'session.v4.jsonl.zstd'
    subprocess.run(['zstd', '-q', '-o', str(path)], input=raw, check=True)


@pytest.mark.skipif(subprocess.run(['sh', '-c', 'command -v zstd'], capture_output=True).returncode,
                    reason='zstd CLI not installed')
def test_host_report_reads_the_terminal_turn(tmp_path):
    _transcript(tmp_path, 'session-ok', [
        {'type': 'tool_call'}, {'type': 'tool_call'},
        {'phase': 'step_end'}, {'phase': 'step_end'},
        {'phase': 'turn_end', 'reason': {'kind': 'completed'}}])
    client = host.HostClient(home=tmp_path)
    report = client.report('session-ok')
    assert report.ok and report.steps == 2 and report.tool_calls == 2
    assert client.report('session-missing').status == 'missing'


@pytest.mark.skipif(subprocess.run(['sh', '-c', 'command -v zstd'], capture_output=True).returncode,
                    reason='zstd CLI not installed')
def test_host_report_distinguishes_error_and_in_progress(tmp_path):
    _transcript(tmp_path, 'session-bad', [
        {'phase': 'turn_end', 'reason': {'kind': 'error',
                                         'error': {'code': 'RATE_LIMIT'}}}])
    _transcript(tmp_path, 'session-open', [{'type': 'tool_call'}])
    client = host.HostClient(home=tmp_path)
    bad = client.report('session-bad')
    assert bad.status == 'error' and bad.error_code == 'RATE_LIMIT'
    assert client.report('session-open').status == 'running'


def test_profiles_are_discovered_from_the_repository(tmp_path):
    folder = tmp_path / 'config' / 'projects'
    folder.mkdir(parents=True)
    (folder / 'demo.yaml').write_text(json.dumps(valid_profile()))
    found = profiles.list_profiles(tmp_path)
    assert [p.name for p in found] == ['demo']
