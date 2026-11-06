"""The Mission tab must show the LIVE headless agent, not just a worker pill.

A supervisor model session is a `dsh --profile headless` child of the systemd
unit, so the DSH Web UI's own agent list never contains it. These tests pin the
projection that makes it visible: durable supervisor state, the router's selected
route, real activity from the private round log, and honest idle reporting.

Nothing here starts a model or touches GitHub; every path is a tmp fixture.
"""
import json
import os
import pathlib
import types

from nova3_team import mission, worker


def _paths(state):
    return lambda root, slot, create=False: types.SimpleNamespace(state=state)


def _fixture(tmp_path, monkeypatch, *, supervisor=None, router=None, events=()):
    state = tmp_path / 'state'
    state.mkdir()
    if supervisor is not None:
        (state / 'supervisor.json').write_text(json.dumps(supervisor))
    if router is not None:
        (state / 'model-router').mkdir()
        (state / 'model-router' / 'status.json').write_text(json.dumps(router))
    if events:
        private = state / '.nova-private' / '195-x'
        private.mkdir(parents=True)
        (private / 'round-1.log').write_text(
            ''.join(json.dumps(event) + '\n' for event in events))
    monkeypatch.setattr(worker, 'state_root', lambda repo: tmp_path)
    monkeypatch.setattr(worker, 'slot_paths', _paths(state))
    return state


def test_reports_selected_route_task_and_activity(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch,
             supervisor={'state': 'RUNNING', 'supervisor_pid': os.getpid(),
                         'task': {'number': 195, 'title': 'Pair operand streams'}},
             router={'active': {'provider': 'openrouter',
                                'model': 'deepseek/deepseek-v4.1-flash-1017'},
                     'purpose': 'semantic', 'status': 'RUNNING',
                     'session_id': 'session-abc',
                     'catalog': [{'provider': 'openai-codex', 'model': 'gpt-6.1-sol'},
                                 {'provider': 'openrouter',
                                  'model': 'deepseek/deepseek-v4.1-flash-1017'}]},
             events=[{'type': 'tool_call'}, {'type': 'tool_call'},
                     {'type': 'status', 'phase': 'step_end'},
                     {'type': 'status', 'phase': 'step_end'}])
    agent = mission.agent_state('owner/repo', now=1_700_000_000)
    assert agent['state'] == 'RUNNING'
    assert agent['task'] == 195
    assert agent['provider'] == 'openrouter'
    assert agent['model'].endswith('flash-1017')
    assert agent['purpose'] == 'semantic'
    assert agent['session_id'] == 'session-abc'
    assert agent['steps'] == 2 and agent['tool_calls'] == 2
    assert agent['last_event_age_s'] is not None
    assert agent['catalog'] == ['openai-codex/gpt-6.1-sol',
                                'openrouter/deepseek/deepseek-v4.1-flash-1017']


def test_running_requires_an_actual_headless_child(tmp_path, monkeypatch):
    # This test process has no `dsh --json` child, so the agent must be idle even
    # though the durable state says RUNNING: the pill never claims a session that
    # does not exist.
    _fixture(tmp_path, monkeypatch,
             supervisor={'state': 'RUNNING', 'supervisor_pid': os.getpid(),
                         'task': {'number': 7, 'title': 'x'}},
             router={'active': {'provider': 'p', 'model': 'm'}})
    agent = mission.agent_state('owner/repo')
    assert agent['running'] is False
    assert agent['pid'] is None
    assert agent['task'] == 7           # durable state is still reported


def test_only_future_cooldowns_are_shown(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch,
             router={'cooldowns': {'openai-codex/gpt-6.1-sol': 1_700_000_500,
                                   'openrouter/m': 1_699_999_000}})
    agent = mission.agent_state('owner/repo', now=1_700_000_000)
    assert agent['cooldowns'] == {'openai-codex/gpt-6.1-sol': 500}


def test_session_identity_comes_from_recorded_events(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, supervisor={'state': 'RUNNING'},
             router={'session_id': 'stale-router-session'},
             events=[{'type': 'session', 'sessionId': 'actual-session'}])
    agent = mission.agent_state('owner/repo')
    assert agent['session_id'] == 'actual-session'
    assert agent['activity_scope'] == 'latest recorded session'
    assert agent['running'] is False


def test_current_task_never_inherits_other_task_activity(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, supervisor={'task': {'number': 7}},
             events=[{'type': 'tool_call'}])  # fixture log belongs to task 195
    agent = mission.agent_state('owner/repo')
    assert agent['tool_calls'] == 0
    assert agent['last_event_age_s'] is None


def test_missing_state_is_idle_not_an_error(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    agent = mission.agent_state('owner/repo')
    assert agent['running'] is False
    assert agent['steps'] == 0
    assert agent['last_event_age_s'] is None
    assert agent['catalog'] == []
