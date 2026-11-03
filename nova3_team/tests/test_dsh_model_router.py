"""Offline routing contracts; no account, original binary or paid request.

This suite is unittest-style on purpose: CI runs `unittest discover` over
nova3_team/tests before pytest is installed, so a pytest-only module fails
collection there even though it passes locally. Every test in this directory
must import under the standard library alone.
"""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

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


def outcome(status):
    return runtime.SessionOutcome(status, 0 if status == runtime.COMPLETED else 1,
                                  'session-test', 'completed', None, '', 1, 1, 1.0)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.home = pathlib.Path(self._temp.name)
        plugin = self.home / 'profiles/web/node_modules/dsh-openai-codex-auth/lib/index.js'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('// installed test plugin')

    def routes(self, home=None):
        return router.parse_config(json.dumps(config()), home or self.home)

    def test_discovery_only_uses_explicitly_configured_models(self):
        found = self.routes()
        self.assertEqual({(r.provider, r.model) for r in found},
                         {('openrouter', 'deepseek/flash'), ('openai-codex', 'gpt-code')})
        self.assertEqual(found[0].profile['models'][0]['contextWindow'], 12345)
        self.assertNotIn('SECRET', json.dumps(found[0].profile))
        self.assertNotIn('headers', found[0].profile)

    def test_dsh_js_tags_are_never_executed(self):
        with self.assertRaises(router.ConfigError):
            router.parse_config('- id: irrelevant\n  config: !!js process.exit(1)\n',
                                self.home)
        found = router.parse_config(json.dumps([{'id': 'irrelevant',
                                                'config': 'process.exit(1)'}]), self.home)
        self.assertEqual(found, [])

    def test_disabled_adapter_is_not_a_route(self):
        cfg = config()
        cfg[0]['disabled'] = True
        cfg[2]['disabled'] = True
        self.assertEqual(router.parse_config(json.dumps(cfg), self.home), [])

    def test_missing_codex_plugin_is_not_invented(self):
        with tempfile.TemporaryDirectory() as empty:
            found = router.parse_config(json.dumps(config()), pathlib.Path(empty))
        self.assertEqual([r.provider for r in found], ['openrouter'])

    def test_native_codex_does_not_get_an_openrouter_key_or_base_url(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            base = runtime.DshEndpoint('/dsh', self.home)
            codex = next(r for r in self.routes() if r.provider == 'openai-codex')
            ep = router.write_route(base, codex, out / 'overlays')
            patches = json.loads(ep.patch.read_text())
            self.assertNotIn('llm-pi-ai', [p.get('id') for p in patches])
            self.assertNotIn('openrouter', ep.patch.read_text())
            self.assertNotIn('apiKeyEnv', ep.patch.read_text())
            self.assertEqual(patches[-1]['config']['reasoningEffort'], 'high')
            self.assertEqual(patches[-1]['config']['provider'], 'openai-codex')
            self.assertEqual(ep.patch.stat().st_mode & 0o077, 0)

    def test_task_suitability_is_a_preference_not_a_new_model(self):
        rs = self.routes()
        self.assertEqual(router.rank(rs, 'Implement native matrix inverse')[0].provider,
                         'openai-codex')
        self.assertEqual(router.rank(rs, 'Update documentation freshness test')[0].provider,
                         'openrouter')
        self.assertEqual(router.rank(rs, 'ARM byte-only metadata')[0].provider,
                         'openai-codex')

    def test_routes_have_separate_immutable_overlays(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            base = runtime.DshEndpoint('/dsh', self.home)
            a, b = self.routes()
            ea = router.write_route(base, a, out / 'overlays')
            before = ea.patch.read_bytes()
            eb = router.write_route(base, b, out / 'overlays')
            self.assertNotEqual(ea.patch, eb.patch)
            self.assertEqual(ea.patch.read_bytes(), before)
            self.assertEqual(router.write_route(base, a, out / 'overlays').patch, ea.patch)

    def test_provider_failure_falls_back_and_accounts_for_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            rs = self.routes()
            r = router.Router(runtime.DshEndpoint('/dsh', self.home), out / 'router')
            failures = []
            attempted = []

            def run(ep, *args, **kwargs):
                attempted.append(ep)
                cfg = json.loads(ep.patch.read_text())[-1]['config']
                # Selection is wired BEFORE launch: the overlay names the route.
                self.assertEqual(cfg['provider'], ep.route)
                return outcome(runtime.WAITING_CREDITS if len(attempted) == 1
                               else runtime.COMPLETED)

            with mock.patch.object(router, 'discover', return_value=rs), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(runtime, 'run_session', side_effect=run):
                result = r.run('task', task='native inverse', cwd=out,
                               log_path=out / 'round.log', timeout_s=40,
                               cancel=lambda: False,
                               on_failure=lambda *args: failures.append(args))
            self.assertTrue(result.ok)
            self.assertEqual([ep.route for ep in attempted],
                             ['openai-codex', 'openrouter'])
            self.assertEqual(len(failures), 1)
            self.assertGreater(r.cooldowns[rs[1].identity], 0)
            self.assertEqual(r.state['active']['provider'], 'openrouter')

    def test_code_failure_never_retries_on_another_paid_model(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            r = router.Router(runtime.DshEndpoint('/dsh', self.home), out / 'router')
            with mock.patch.object(router, 'discover', return_value=self.routes()), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(runtime, 'run_session',
                                   return_value=outcome(runtime.EXECUTION_ERROR)) as run:
                result = r.run('task', task='native', cwd=out,
                               log_path=out / 'round.log', timeout_s=40,
                               cancel=lambda: False, on_failure=lambda *a: None)
            self.assertEqual(result.status, runtime.EXECUTION_ERROR)
            self.assertEqual(run.call_count, 1)

    def test_no_credential_never_launches_a_model(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            r = router.Router(runtime.DshEndpoint('/dsh', self.home), out / 'router')
            with mock.patch.object(router, 'discover', return_value=self.routes()), \
                 mock.patch.object(runtime, 'credential_present', return_value=False), \
                 mock.patch.object(runtime, 'run_session') as run:
                with self.assertRaises(router.ConfigError):
                    r.run('task', task='native', cwd=out,
                          log_path=out / 'round.log', timeout_s=40,
                          cancel=lambda: False, on_failure=lambda *a: None)
            run.assert_not_called()

    def test_reload_picks_up_new_settings_each_session(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            r = router.Router(runtime.DshEndpoint('/dsh', self.home), out / 'router')
            first = self.routes()[0]
            next_route = router.Route('openrouter', 'owner-added-model', 'ROUTER_KEY',
                                      profile={'apiKeyEnv': 'ROUTER_KEY',
                                               'models': [{'id': 'owner-added-model'}]})
            with mock.patch.object(router, 'discover',
                                   side_effect=[[first], [next_route]]), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(runtime, 'run_session',
                                   return_value=outcome(runtime.COMPLETED)) as run:
                for _ in range(2):
                    r.run('task', task='native', cwd=out,
                          log_path=out / 'round.log', timeout_s=40,
                          cancel=lambda: False, on_failure=lambda *a: None)
            self.assertEqual([call.args[0].model for call in run.call_args_list],
                             ['deepseek/flash', 'owner-added-model'])

    def test_reasoning_route_beats_flash_for_semantic_work(self):
        remembered = [{'provider': 'openai-codex', 'model': 'gpt-code', 'effort': 'high'}]
        merged = router.parse_config(json.dumps(config()), self.home,
                                     known_models=remembered)
        self.assertEqual([r.identity for r in router.rank(merged, 'native decompilation')],
                         ['openai-codex/gpt-code', 'openrouter/deepseek/flash'])
        self.assertEqual(router.rank(merged, 'docs freshness test')[0].identity,
                         'openrouter/deepseek/flash')

    def test_remembered_route_is_reoffered_only_while_plugin_is_installed(self):
        remembered = [{'provider': 'openai-codex', 'model': 'gpt-code', 'effort': 'high'}]
        self.assertTrue(any(r.provider == 'openai-codex' for r in
                            router.parse_config(json.dumps(config()), self.home,
                                                known_models=remembered)))
        (self.home / 'profiles/web/node_modules/dsh-openai-codex-auth/lib/index.js').unlink()
        self.assertEqual([r.provider for r in router.parse_config(
            json.dumps(config()), self.home, known_models=remembered)], ['openrouter'])

    def test_unknown_codex_model_is_never_invented(self):
        found = router.parse_config(
            json.dumps(config()), self.home,
            known_models=[{'provider': 'openai-codex', 'model': 'not a model'}])
        # Only the configured Web default, never a guess.
        self.assertEqual([r.identity for r in found if r.provider == 'openai-codex'],
                         ['openai-codex/gpt-code'])

    def test_router_persists_known_routes_for_later_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp)
            r = router.Router(runtime.DshEndpoint('/dsh', self.home), out / 'router')
            remembered = [{'provider': 'openai-codex', 'model': 'gpt-code',
                           'effort': 'high'}]
            r.state['known'] = remembered
            seen = {}

            def fake_discover(endpoint, profile='web', known_models=()):
                seen['known'] = list(known_models)
                return router.parse_config(json.dumps(config()), self.home,
                                           known_models=known_models)

            with mock.patch.object(router, 'discover', side_effect=fake_discover), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(runtime, 'run_session',
                                   return_value=outcome(runtime.COMPLETED)):
                r.run('task', task='native', cwd=out, log_path=out / 'log',
                      timeout_s=30, cancel=lambda: False, on_failure=lambda *a: None)
            self.assertEqual(seen['known'], remembered)
            saved = json.loads((out / 'router/status.json').read_text())
            self.assertEqual({entry['provider'] for entry in saved['known']},
                             {'openrouter', 'openai-codex'})


if __name__ == '__main__':
    unittest.main()
