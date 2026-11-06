"""Status and slash output must not turn mapping rates into game ETAs."""
import unittest
from nova3_team.dsh_status import render


class StatusMetricsTests(unittest.TestCase):
    def test_even_a_measured_scenario_is_not_a_project_completion_date(self):
        text = render({'slot': 'test', 'state_file': 'fixture', 'project': {
            'snapshot': {'inventory': {'functions_recorded': 100, 'elf_functions': 1000},
                         'progress': {'available': True, 'mappings_implemented': 10}},
            'estimate': {'state': 'measured',
                         'rate': {'mappings_per_day': 2.5, 'window': 'fixture'},
                         'scenarios': {'recorded_inventory': {'days': [100, 510, 1000]}}}}})
        self.assertIn('2.50 mappings/day', text)
        self.assertIn('Project completion ETA: unavailable', text)
        self.assertNotIn('510 days', text)
        self.assertNotIn('ETA (provisional)', text)
