"""Routing must follow task strengths and host limits, not a fixed model."""
import unittest

from nova3_team import fleet


class HostTests(unittest.TestCase):
    def test_small_host_runs_one_job_and_no_heavy_analysis(self):
        host = fleet.host_profile(os_name='linux', cpu=2, ram_gb=4)
        self.assertFalse(host['heavy_ok'])
        self.assertEqual(host['max_parallel'], 1)

    def test_large_host_scales_parallelism_slowly(self):
        host = fleet.host_profile(os_name='linux', cpu=16, ram_gb=32)
        self.assertTrue(host['heavy_ok'])
        self.assertEqual(host['max_parallel'], 4)


class RecommendTests(unittest.TestCase):
    def setUp(self):
        self.big = fleet.host_profile(os_name='linux', cpu=8, ram_gb=16)
        self.small = fleet.host_profile(os_name='linux', cpu=2, ram_gb=4)
        self.models = ['deepseek', 'codex', 'claude', 'gemini']

    def test_recon_prefers_a_code_model(self):
        result = fleet.recommend('recon', host=self.big, models=self.models)
        self.assertEqual(result['model'], 'deepseek')
        self.assertEqual(result['max_parallel'], 1)

    def test_docs_prefer_a_long_form_model(self):
        self.assertEqual(
            fleet.recommend('docs', host=self.big, models=self.models)['model'],
            'gemini')

    def test_load_shifts_the_choice_away(self):
        plain = fleet.recommend('recon', host=self.big, models=self.models)
        loaded = fleet.recommend('recon', host=self.big, models=self.models,
                                 load={plain['model']: 3})
        self.assertNotEqual(loaded['model'], plain['model'])

    def test_small_host_warns_instead_of_pretending(self):
        result = fleet.recommend('analysis', host=self.small, models=self.models)
        self.assertFalse(result['heavy_ok'])
        self.assertIn('8 GiB', result['warning'])

    def test_unknown_model_is_eligible_but_loses(self):
        result = fleet.recommend('test', host=self.big,
                                 models=['mystery-model', 'deepseek'])
        self.assertEqual(result['model'], 'deepseek')

    def test_no_models_is_an_error(self):
        with self.assertRaises(ValueError):
            fleet.recommend('recon', host=self.big, models=[])

    def test_unknown_task_is_an_error(self):
        with self.assertRaises(ValueError):
            fleet.recommend('gardening', host=self.big, models=self.models)


if __name__ == '__main__':
    unittest.main()
