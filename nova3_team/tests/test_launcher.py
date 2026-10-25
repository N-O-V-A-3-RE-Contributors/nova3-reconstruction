import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import nova3_start


class LauncherTests(unittest.TestCase):
    def test_provider_override(self):
        self.assertEqual(nova3_start.choose_provider('gemini'), 'gemini')

    def test_remembered_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'config.json'
            nova3_start.save_provider('claude', cfg)
            self.assertEqual(json.loads(cfg.read_text())['provider'], 'claude')
            self.assertEqual(nova3_start.choose_provider(None, config=cfg), 'claude')

    def test_invalid_saved_provider_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'config.json'
            cfg.write_text('{"provider":"unknown"}', encoding='utf-8')
            with mock.patch('builtins.input', return_value='3'):
                self.assertEqual(nova3_start.choose_provider(None, config=cfg), 'gemini')

    def test_python_requirement_instructions(self):
        self.assertIn('git', nova3_start.requirements_hint().lower())


if __name__ == '__main__':
    unittest.main()
