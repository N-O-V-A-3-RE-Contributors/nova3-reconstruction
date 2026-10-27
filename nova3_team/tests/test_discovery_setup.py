import unittest
from pathlib import Path
from nova3_team import discovery_setup

class BackgroundTests(unittest.TestCase):
    def test_systemd_unit_has_discovery_runner_and_no_codex(self):
        service,timer=discovery_setup.unit_text(Path('/home/example/Projects/nova3_re'), 'owner/repo',Path('/usr/bin/python3'))
        self.assertIn('nova3_team.discovery run',service)
        self.assertNotIn('codex',service)
        self.assertIn('OnCalendar=*-*-* *:00,30:00',timer)
        self.assertIn('Unit=nova3-discovery.service',timer)
    def test_paths_with_spaces_quoted(self):
        service,_=discovery_setup.unit_text(Path('/home/me/NOVA 3'), 'owner/repo',Path('/usr/bin/python3'))
        self.assertIn('WorkingDirectory=/home/me/NOVA 3',service)
        self.assertIn('--project "/home/me/NOVA 3"',service)
    def test_reject_systemd_percent_specifier(self):
        with self.assertRaises(ValueError):discovery_setup.systemd_escape('/x%H')

if __name__=='__main__':unittest.main()
