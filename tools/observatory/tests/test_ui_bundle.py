"""Isolated installed-file smoke; never writes to the user's installation."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]


class BundleSmoke(unittest.TestCase):
    def test_documented_module_command_starts_control_center(self):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'tools.observatory', '--demo', '--once'],
            cwd=HERE.parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NOVA3 CONTROL CENTER', result.stdout)
        self.assertIn('Whole-game ETA: UNKNOWN', result.stdout)

    def test_installer_manifest_and_isolated_dashboard(self):
        installer = (HERE / 'install.sh').read_text()
        manifests = re.findall(r'^for file in (.+); do$', installer, re.M)
        self.assertEqual(len(manifests), 3)
        self.assertEqual(manifests[0], manifests[1])
        self.assertIn('ui_components.py', manifests[0].split())
        self.assertIn('python3 -B -I "$STAGING/observer.py" --demo --once', installer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in manifests[0].split():
                shutil.copyfile(HERE / name, target / name)
            shutil.copyfile(HERE.parents[1] / 'nova3_team/historical_toolchain.py', target / 'historical_toolchain.py')
            script = r'''
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location('isolated_observer', Path(sys.argv[1]) / 'observer.py')
o = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = o
spec.loader.exec_module(o)
class Screen:
    def __init__(self, w, h): self.w, self.h = w, h
    def erase(self): self.text = []
    def refresh(self): pass
    def getmaxyx(self): return self.h, self.w
    def addnstr(self, y, x, text, n, attr):
        assert 0 <= y < self.h and 0 <= x < self.w
        assert x + o.ui_components.cell_width(text[:n]) < self.w
        self.text.append(text[:n])
ui = o.Observatory(SimpleNamespace(repo='synthetic/fixture', service='fixture.service', demo=True))
ui.snap = o.Snapshot(inventory={'available': True, 'verified': 0})
for w, h in ((40,12), (72,18), (80,24), (120,30), (140,38), (180,48), (220,55)):
    ui.stdscr = Screen(w, h)
    for tab in range(len(o.TABS)):
        ui.tab = tab
        ui.render()
        assert ui.stdscr.text
        if tab in (0, 5):
            assert 'Whole-game ETA: UNKNOWN' in '\n'.join(ui.stdscr.text)
print('isolated synthetic dashboard: 98 renders passed')
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script, tmp],
                                    cwd=tmp, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('98 renders passed', result.stdout)
            result = subprocess.run([sys.executable, '-I', '-B', str(target / 'observer.py'), '--demo', '--once'],
                                    cwd=tmp, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Whole-game ETA: UNKNOWN', result.stdout)

    @unittest.skipIf(sys.platform == 'win32', 'Unix terminal capability smoke')
    def test_real_curses_256_color_monochrome_and_dumb_terminal(self):
        import pty
        import select
        import time

        script = r'''
import curses, importlib.util, sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location('terminal_smoke', sys.argv[1])
o = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = o
spec.loader.exec_module(o)
def main(screen):
    ui = o.Observatory(SimpleNamespace(repo='synthetic/fixture', service='fixture.service', demo=True))
    ui.stdscr = screen
    ui.snap = o.Snapshot(inventory={'available': True, 'verified': 0})
    ui.setup_colors()
    for h, w in ((18,72), (24,80), (38,140), (55,220)):
        curses.resizeterm(h, w)
        for tab in range(14):
            ui.tab = tab
            ui.render()
curses.wrapper(main)
'''
        for terminal in ('xterm-256color', 'vt100', 'dumb'):
            with self.subTest(terminal=terminal):
                master, slave = pty.openpty()
                child = None
                try:
                    child = subprocess.Popen(
                        [sys.executable, '-I', '-B', '-c', script, str(HERE / 'observer.py')],
                        stdin=slave, stdout=slave, stderr=subprocess.PIPE,
                        env=dict(os.environ, TERM=terminal))
                    os.close(slave)
                    slave = None
                    deadline = time.monotonic() + 15
                    # Drain terminal output without displaying or retaining it.
                    while child.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            try:
                                os.read(master, 65536)
                            except OSError:
                                break
                    # A PTY can report EOF just before waitpid observes exit.
                    # communicate gives that normal shutdown a bounded grace.
                    _, errors = child.communicate(timeout=5)
                    self.assertEqual(child.returncode, 0, errors.decode(errors='replace'))
                finally:
                    if child is not None:
                        if child.poll() is None:
                            child.kill()
                        child.communicate()
                    if slave is not None:
                        os.close(slave)
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
