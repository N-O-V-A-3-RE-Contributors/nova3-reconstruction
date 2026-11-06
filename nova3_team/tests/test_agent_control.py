import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
from nova3_team.agent_control import set_execution, main

class AgentControlTests(unittest.TestCase):
    def test_stop_and_resume_preserve_other_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'checkpoint').write_text('preserve')
            set_execution(root / 'control', False)
            self.assertTrue((root / 'control/STOP').read_text())
            set_execution(root / 'control', True)
            self.assertFalse((root / 'control/STOP').exists())
            self.assertTrue((root / 'control/RESUME').exists())
            self.assertEqual((root / 'checkpoint').read_text(), 'preserve')

    def test_failed_authorization_cannot_resume_paid_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            set_execution(root / 'control', False)
            with mock.patch('nova3_team.worker.state_root', return_value=root), \
                 mock.patch('nova3_team.worker.slot_paths', return_value=SimpleNamespace(state=root)), \
                 mock.patch('nova3_team.agent_control.subprocess.run',
                            return_value=SimpleNamespace(returncode=1)):
                self.assertEqual(main(['on', '--repo', 'owner/project']), 1)
            self.assertTrue((root / 'control/STOP').exists())
            self.assertFalse((root / 'control/RESUME').exists())

    def test_off_stops_execution_even_when_autopilot_command_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch('nova3_team.worker.state_root', return_value=root), \
                 mock.patch('nova3_team.worker.slot_paths', return_value=SimpleNamespace(state=root)), \
                 mock.patch('nova3_team.agent_control.subprocess.run',
                            return_value=SimpleNamespace(returncode=1)):
                self.assertEqual(main(['off', '--repo', 'owner/project']), 1)
            self.assertTrue((root / 'control/STOP').exists())
