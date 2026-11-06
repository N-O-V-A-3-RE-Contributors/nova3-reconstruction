"""Owner-added routes, cheap operation selection and account-level fallback.

No model calls, network, Web restarts, credentials or paid availability probes.
"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from nova3_team import dsh_model_router as router, dsh_runtime as runtime


class RoutingPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        plugin = self.home / 'profiles/web/node_modules/dsh-openai-codex-auth/lib/index.js'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('// installed fixture')
        self.base = runtime.DshEndpoint('/dsh', self.home)
        self.config = json.dumps([
            {'id': 'openai-codex-auth', 'name': 'dsh-openai-codex-auth'},
            {'id': 'agent-default-model', 'config': {'provider': 'openai-codex',
                'model': 'gpt-6.1-sol', 'reasoningEffort': 'high'}},
            {'id': 'llm-pi-ai', 'config': {'providers': {'openrouter': {
                'apiKeyEnv': 'ROUTER_KEY', 'models': [
                    {'id': 'deepseek/deepseek-v4.1-flash-1017'}]}}}},
        ])

    def discover(self, catalog, remembered=()):
        result = SimpleNamespace(returncode=0, stdout=self.config)
        with mock.patch.object(router.subprocess, 'run', return_value=result), \
             mock.patch.object(router, 'host_models', return_value=catalog):
            return router.discover(self.base, known_models=remembered)

    def test_all_added_models_are_eligible_not_just_web_default(self):
        catalog = [{'provider': 'openai-codex', 'model': name, 'efforts': ['low', 'high']}
                   for name in ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra')]
        routes = self.discover(catalog)
        self.assertEqual({r.model for r in routes}, {i['model'] for i in catalog})
        self.assertTrue(all(r.effort == 'high' for r in routes))

    def test_online_catalog_does_not_resurrect_removed_models(self):
        remembered = [{'provider': 'openai-codex', 'model': 'removed'}]
        routes = self.discover([{'provider': 'openai-codex', 'model': 'gpt-6.1-sol'}], remembered)
        self.assertEqual([r.model for r in routes], ['gpt-6.1-sol'])
        self.assertEqual(self.discover([], remembered), [])

    def test_offline_host_preserves_configured_fallback(self):
        self.assertTrue(self.discover(None))

    def test_cheap_testing_does_not_become_semantic_due_to_subsystem_name(self):
        for operation in ('[planner] Offline test for native ARM compiler metadata',
                          'Test matrix inverse fixtures', 'Docs about native ABI'):
            with self.subTest(operation=operation):
                self.assertEqual(router.purpose(operation), 'routine')
        for operation in ('planner', 'security review', 'Implement matrix inverse'):
            self.assertEqual(router.purpose(operation), 'semantic')

    def test_primary_models_match_operation(self):
        routes = [router.Route('openai-codex', 'gpt-6-luna', 'OAUTH', effort='high'),
                  router.Route('openrouter', 'deepseek/deepseek-v4.1-flash-1017', 'KEY'),
                  router.Route('openai-codex', 'gpt-6.1-sol', 'OAUTH', effort='high')]
        self.assertEqual(router.rank(routes, 'Test ARM fixtures')[0].provider, 'openrouter')
        self.assertEqual(router.rank(routes, 'planner')[0].model, 'gpt-6.1-sol')

    def test_quota_skips_sibling_models_in_depleted_account(self):
        routes = [router.Route('openai-codex', 'gpt-6.1-sol', 'OAUTH', effort='high'),
                  router.Route('openai-codex', 'gpt-6-luna', 'OAUTH', effort='high'),
                  router.Route('openrouter', 'deepseek/deepseek-v4.1-flash-1017', 'KEY')]
        seen = []
        def run(endpoint, *args, **kwargs):
            seen.append(endpoint.model)
            status = runtime.WAITING_CREDITS if endpoint.route == 'openai-codex' else runtime.COMPLETED
            return runtime.SessionOutcome(status, 0, 's', 'completed', None, '', 1, 1, 1.0)
        instance = router.Router(self.base, self.home / 'router')
        with mock.patch.object(router, 'discover', return_value=routes), \
             mock.patch.object(runtime, 'credential_present', return_value=True), \
             mock.patch.object(runtime, 'run_session', side_effect=run):
            result = instance.run('task', task='planner', cwd=self.home,
                log_path=self.home / 'round.log', timeout_s=30, cancel=lambda: False,
                on_failure=lambda *args: None)
        self.assertTrue(result.ok)
        self.assertEqual(seen, ['gpt-6.1-sol', 'deepseek/deepseek-v4.1-flash-1017'])
        self.assertIn('openai-codex/gpt-6-luna', instance.cooldowns)
