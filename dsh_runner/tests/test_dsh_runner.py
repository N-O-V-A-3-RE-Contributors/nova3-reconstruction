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


def test_host_backend_requires_shared_workspace_or_squad():
    with pytest.raises(profiles.ProfileError):
        profiles.parse(valid_profile(execution={'backend': 'host',
                                                'isolation': 'per-task-checkout',
                                                'timeout_s': 900, 'max_steps': 120}))
    shared = valid_profile(execution={'backend': 'host', 'isolation': 'shared-workspace',
                                      'squad': 'Demo Squad', 'timeout_s': 900,
                                      'max_steps': 120})
    host_profile = profiles.parse(shared)
    assert host_profile.execution.backend == 'host'
    assert isinstance(backends.backend_for(host_profile, client=object()),
                      backends.HostBackend)
    # A headless profile may not be pushed through the Host backend.
    headless_profile = profiles.parse(valid_profile())
    with pytest.raises(backends.BackendError):
        backends.HostBackend(headless_profile)


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
