"""Offline routing contracts; no account, original binary or paid request."""
import json
import pathlib
import tempfile
from unittest import mock

import pytest

from nova3_team import dsh_model_router as router, dsh_runtime as runtime


def config():
    return [
        {'id': 'llm-pi-ai', 'config': {'providers': {'openrouter': {
            'baseURL': 'https://openrouter.ai/api/v1', 'apiKeyEnv': 'ROUTER_KEY',
            'apiKey': 'SECRET-NEVER-COPY', 'headers': {'Authorization': 'secret'},
            'models': [{'id': 'deepseek/flash', 'contextWindow': 12345,
                        'maxTokens': 678, 'input': ['text']}],
        }}}},
        {'id': 'agent-default-model', 'config': {'provider': 'openai-codex',
          'model': 'gpt-code', 'reasoningEffort': 'high'}},
        {'id': 'openai-codex-auth', 'name': 'dsh-openai-codex-auth'},
    ]


@pytest.fixture
def home(tmp_path):
    plugin = tmp_path / 'profiles/web/node_modules/dsh-openai-codex-auth/lib/index.js'
    plugin.parent.mkdir(parents=True)
    plugin.write_text('// installed test plugin')
    return tmp_path


def routes(home):
    return router.parse_config(json.dumps(config()), home)


def outcome(status):
    return runtime.SessionOutcome(status, 0 if status == runtime.COMPLETED else 1,
                                  'session-test', 'completed', None, '', 1, 1, 1.0)


def test_discovery_only_uses_explicitly_configured_models(home):
    found = routes(home)
    assert {(r.provider, r.model) for r in found} == {
        ('openrouter', 'deepseek/flash'), ('openai-codex', 'gpt-code')}
    assert found[0].profile['models'][0]['contextWindow'] == 12345
    assert 'SECRET' not in json.dumps(found[0].profile)
    assert 'headers' not in found[0].profile


def test_dsh_js_tags_are_never_executed(home):
    with pytest.raises(router.ConfigError):
        router.parse_config('- id: irrelevant\n  config: !!js process.exit(1)\n', home)
    found = router.parse_config(json.dumps([{'id': 'irrelevant',
                                            'config': 'process.exit(1)'}]), home)
    assert found == []


def test_disabled_adapter_is_not_a_route(home):
    cfg = config()
    cfg[0]['disabled'] = True
    cfg[2]['disabled'] = True
    assert router.parse_config(json.dumps(cfg), home) == []


def test_missing_codex_plugin_is_not_invented(tmp_path):
    found = router.parse_config(json.dumps(config()), tmp_path)
    assert [r.provider for r in found] == ['openrouter']


def test_native_codex_does_not_get_an_openrouter_key_or_base_url(home, tmp_path):
    base = runtime.DshEndpoint('/dsh', home)
    codex = next(r for r in routes(home) if r.provider == 'openai-codex')
    ep = router.write_route(base, codex, tmp_path / 'overlays')
    patches = json.loads(ep.patch.read_text())
    assert 'llm-pi-ai' not in [p.get('id') for p in patches]
    assert 'openrouter' not in ep.patch.read_text()
    assert 'apiKeyEnv' not in ep.patch.read_text()
    assert patches[-1]['config']['reasoningEffort'] == 'high'
    assert patches[-1]['config']['provider'] == 'openai-codex'
    assert ep.patch.stat().st_mode & 0o077 == 0


def test_task_suitability_is_a_preference_not_a_new_model(home):
    rs = routes(home)
    assert router.rank(rs, 'Implement native matrix inverse')[0].provider == 'openai-codex'
    assert router.rank(rs, 'Update documentation freshness test')[0].provider == 'openrouter'
    assert router.rank(rs, 'ARM byte-only metadata')[0].provider == 'openai-codex'


def test_routes_have_separate_immutable_overlays(home, tmp_path):
    base = runtime.DshEndpoint('/dsh', home)
    a, b = routes(home)
    ea = router.write_route(base, a, tmp_path / 'overlays')
    before = ea.patch.read_bytes()
    eb = router.write_route(base, b, tmp_path / 'overlays')
    assert ea.patch != eb.patch
    assert ea.patch.read_bytes() == before
    assert router.write_route(base, a, tmp_path / 'overlays').patch == ea.patch


def test_provider_failure_falls_back_and_accounts_for_attempt(home, tmp_path):
    rs = routes(home)
    r = router.Router(runtime.DshEndpoint('/dsh', home), tmp_path / 'router')
    failures = []
    attempted = []
    def run(ep, *args, **kwargs):
        attempted.append(ep)
        cfg = json.loads(ep.patch.read_text())[-1]['config']
        assert cfg['provider'] == ep.route   # selection actually wired BEFORE launch
        return outcome(runtime.WAITING_CREDITS if len(attempted) == 1 else runtime.COMPLETED)
    with mock.patch.object(router, 'discover', return_value=rs), \
         mock.patch.object(runtime, 'credential_present', return_value=True), \
         mock.patch.object(runtime, 'run_session', side_effect=run):
        result = r.run('task', task='native inverse', cwd=tmp_path,
                       log_path=tmp_path / 'round.log', timeout_s=40,
                       cancel=lambda: False, on_failure=lambda *args: failures.append(args))
    assert result.ok
    assert [ep.route for ep in attempted] == ['openai-codex', 'openrouter']
    assert len(failures) == 1
    assert r.cooldowns[rs[1].identity] > 0
    assert r.state['active']['provider'] == 'openrouter'


def test_code_failure_never_retries_on_another_paid_model(home, tmp_path):
    r = router.Router(runtime.DshEndpoint('/dsh', home), tmp_path / 'router')
    with mock.patch.object(router, 'discover', return_value=routes(home)), \
         mock.patch.object(runtime, 'credential_present', return_value=True), \
         mock.patch.object(runtime, 'run_session', return_value=outcome(runtime.EXECUTION_ERROR)) as run:
        result = r.run('task', task='native', cwd=tmp_path,
                       log_path=tmp_path / 'round.log', timeout_s=40,
                       cancel=lambda: False, on_failure=lambda *a: None)
    assert result.status == runtime.EXECUTION_ERROR
    assert run.call_count == 1


def test_no_credential_never_launches_a_model(home, tmp_path):
    r = router.Router(runtime.DshEndpoint('/dsh', home), tmp_path / 'router')
    with mock.patch.object(router, 'discover', return_value=routes(home)), \
         mock.patch.object(runtime, 'credential_present', return_value=False), \
         mock.patch.object(runtime, 'run_session') as run, pytest.raises(router.ConfigError):
        r.run('task', task='native', cwd=tmp_path,
              log_path=tmp_path / 'round.log', timeout_s=40,
              cancel=lambda: False, on_failure=lambda *a: None)
    run.assert_not_called()


def test_reload_picks_up_new_settings_each_session(home, tmp_path):
    r = router.Router(runtime.DshEndpoint('/dsh', home), tmp_path / 'router')
    first = routes(home)[0]
    next_route = router.Route('openrouter', 'owner-added-model', 'ROUTER_KEY',
                              profile={'apiKeyEnv': 'ROUTER_KEY',
                                       'models': [{'id': 'owner-added-model'}]})
    with mock.patch.object(router, 'discover', side_effect=[[first], [next_route]]), \
         mock.patch.object(runtime, 'credential_present', return_value=True), \
         mock.patch.object(runtime, 'run_session', return_value=outcome(runtime.COMPLETED)) as run:
        for _ in range(2):
            r.run('task', task='native', cwd=tmp_path,
                  log_path=tmp_path / 'round.log', timeout_s=40,
                  cancel=lambda: False, on_failure=lambda *a: None)
    assert [call.args[0].model for call in run.call_args_list] == ['deepseek/flash', 'owner-added-model']
