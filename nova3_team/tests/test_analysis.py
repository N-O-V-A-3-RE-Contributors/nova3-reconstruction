"""The analysis must report evidence, and say 'unavailable' rather than guess."""
import json
import pathlib
import tempfile
import unittest

from nova3_team import analysis


def _root(folder, *, functions=10, elf=100, exports=3, complete=False,
          progress=None):
    root = pathlib.Path(folder)
    (root / 'coordination').mkdir(parents=True, exist_ok=True)
    (root / 'coordination' / 'function_ledger.json').write_text(json.dumps({
        'functions': [{'address': hex(0x800 + i)} for i in range(functions)],
        'statistics': {'elf_function_count': elf, 'functions_with_ghidra_exports': exports,
                       'inventory_complete': complete},
    }))
    if progress is not None:
        (root / 'coordination' / 'progress_state.json').write_text(json.dumps(progress))
    return root


def _progress(**overrides):
    value = {'schema': 'nova3-progress/1', 'updated_at': '2026-01-11',
             'work_started': '2026-01-01', 'mappings_implemented': 4,
             'mappings_matched_arm': 2, 'mappings_behavioural': 2,
             'utilities_verified': 0, 'source_modules': 2}
    value.update(overrides)
    return value


class InventoryTests(unittest.TestCase):
    def test_inventory_is_read_from_the_ledger_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = analysis.snapshot(_root(folder))
            inv = snap['inventory']
            self.assertEqual(inv['functions_recorded'], 10)
            self.assertEqual(inv['elf_functions'], 100)
            self.assertEqual(inv['unrecorded_functions'], 90)
            self.assertAlmostEqual(inv['inventory_coverage'], 0.1)
            self.assertFalse(inv['inventory_complete'])


class EstimateTests(unittest.TestCase):
    def test_provisional_estimate_uses_the_recorded_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = analysis.snapshot(_root(folder, progress=_progress()))
            est = analysis.estimate(snap)
            self.assertEqual(est['state'], 'provisional')
            self.assertAlmostEqual(est['rate']['mappings_per_day'], 0.4)
            scenario = est['scenarios']['recorded_inventory']
            self.assertEqual(scenario['remaining'], 6)
            low, likely, high = scenario['days']
            self.assertLess(low, likely)
            self.assertLess(likely, high)
            self.assertAlmostEqual(likely, 15.0)
            self.assertIn('full_elf_at_recorded_rate', est['scenarios'])

    def test_complete_inventory_is_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = analysis.snapshot(_root(folder, complete=True, progress=_progress()))
            self.assertEqual(analysis.estimate(snap)['state'], 'measured')

    def test_missing_progress_is_unavailable_with_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            est = analysis.estimate(analysis.snapshot(_root(folder)))
            self.assertEqual(est['state'], 'unavailable')
            self.assertTrue(any('progress_state.json' in r for r in est['reasons']))

    def test_zero_rate_is_unavailable_not_infinite(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = analysis.snapshot(_root(folder, progress=_progress(mappings_implemented=0)))
            est = analysis.estimate(snap)
            self.assertEqual(est['state'], 'unavailable')
            self.assertNotIn('recorded_inventory', est['scenarios'])

    def test_render_names_the_rebaseline(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = analysis.snapshot(_root(folder, progress=_progress()))
            text = analysis.render(snap, analysis.estimate(snap))
            self.assertIn('ETA', text)
            self.assertIn('pre-release source', text)
            self.assertIn('NOT complete', text)


if __name__ == '__main__':
    unittest.main()
