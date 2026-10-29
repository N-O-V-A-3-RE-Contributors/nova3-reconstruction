"""Offline optional onboarding, including the standalone Observatory bundle."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


o = load(HERE / 'observer.py', 'nova_observer_toolchain_tests')


class ToolchainTests(unittest.TestCase):
    def test_cli_recipe_needs_no_collector_network_or_compiler(self):
        with mock.patch.object(o, 'Collector') as collector, mock.patch.object(o.subprocess, 'run') as run, mock.patch('socket.socket') as socket, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(o.cli(['--toolchain-setup']), 0)
        collector.assert_not_called()
        run.assert_not_called()
        socket.assert_not_called()
        self.assertIn('Compiler status:', output.getvalue())
        self.assertIn('dl.google.com/android/ndk/', output.getvalue())
        self.assertIn('NOT PROVEN', output.getvalue())

    def test_toolchain_key_page_scroll_and_rescan_are_read_only(self):
        collector = o.Collector('X/Y', Path('/tmp'), 'test.service', Path('/tmp'), demo=True)
        ui = o.Observatory(collector)
        with mock.patch.object(o, 'historical_setup_text', return_value='MISSING\nNOT PROVEN\n' * 50) as text, mock.patch.object(collector, 'perform_action') as action:
            ui.handle(ord('h'))
            self.assertEqual(o.TABS[ui.tab], 'TOOLCHAIN')
            ui.put = mock.Mock()
            ui.draw_toolchain(6, 20, 80)
            ui.draw_toolchain(6, 20, 80)
            self.assertEqual(text.call_count, 1)
            ui.handle(o.curses.KEY_DOWN)
            self.assertEqual(ui.scroll['toolchain'], 1)
            ui.handle(o.curses.KEY_UP)
            self.assertEqual(ui.scroll['toolchain'], 0)
            ui.handle(ord('v'))
            self.assertEqual(text.call_count, 2)
            action.assert_not_called()

    def test_absent_helper_does_not_disable_observer(self):
        with mock.patch.object(o, 'toolchain_module', side_effect=ImportError('fixture')):
            output = o.historical_setup_text()
        self.assertIn('Read-only observer remains available', output)
        self.assertIn('NOT PROVEN', output)

    def test_installed_bundle_works_without_repo_or_analysis_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'standalone'
            target.mkdir()
            for name in ('observer.py', 'operator_ops.py', 'presence_view.py', 'progress.py', 'forecast_model.py', 'project_roadmap.json'):
                shutil.copyfile(HERE / name, target / name)
            helper = HERE.parents[1] / 'nova3_team/historical_toolchain.py'
            shutil.copyfile(helper, target / 'historical_toolchain.py')
            shutil.copyfile(HERE.parents[1] / 'docs/progress/HISTORICAL_TOOLCHAIN.md', target / 'HISTORICAL_TOOLCHAIN.md')
            standalone = load(target / 'observer.py', 'nova_observer_standalone_tests')
            with mock.patch.object(standalone.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(standalone.cli(['--toolchain-setup']), 0)
            run.assert_not_called()
            self.assertIn('Official original compressed archive', output.getvalue())
            self.assertIn('Bundled extraction/pilot guide:', output.getvalue())
            self.assertEqual(Path(standalone.toolchain_module().__file__), target / 'historical_toolchain.py')
            installer = (HERE / 'install.sh').read_text()
            self.assertIn('"$HISTORICAL_HELPER" "$TARGET/historical_toolchain.py"', installer)


if __name__ == '__main__':
    unittest.main()
