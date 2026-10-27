"""Tests for Ghidra diagnostics, retry avoidance, and bounded private logs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import discovery, discovery_setup


class DiscoveryHealthTests(unittest.TestCase):
    def test_ghidra_error_before_script_directories_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            elf = root/'libNOVA3_neon.so'
            elf.write_bytes(b'\x7fELF'+b'xxxx')
            script = root/'Nova3FunctionLedger.java'
            script.write_text('public class Nova3FunctionLedger {}')
            fake = root/'analyzeHeadless'
            fake.write_text('#!/usr/bin/env python3\n'
                            'import sys,pathlib\n'
                            'a=sys.argv\n'
                            'pathlib.Path(a[a.index("-log")+1]).write_text("ERROR Invalid script: Nova3FunctionLedger.java\\n")\n'
                            'pathlib.Path(a[a.index("-scriptlog")+1]).write_text("Script not found\\n")\n'
                            'print("ERROR Invalid script: Nova3FunctionLedger.java")\n'
                            'print("HEADLESS Script Paths:")\n'
                            'print("/opt/ghidra/Ghidra/Features/Base/ghidra_scripts")\n'
                            'sys.exit(1)\n')
            fake.chmod(0o755)
            with patch.dict(os.environ, {'NOVA3_DISCOVERY_CACHE':str(root/'cache')}):
                with self.assertRaisesRegex(RuntimeError, 'Invalid script') as exc:
                    discovery.run_ghidra(fake, elf, script=script, digest='a'*64,
                                         out=root/'export.json', timeout=30)
                self.assertIn('private logs:',str(exc.exception))
                saved=root/'cache/logs'/('a'*64)
                self.assertTrue((saved/'headless.log').exists())
                self.assertTrue((saved/'application.log').exists())
                self.assertTrue((saved/'script.log').exists())
                self.assertIn('Invalid script',(saved/'headless.log').read_text())
                if os.name=='posix':
                    self.assertEqual((saved/'headless.log').stat().st_mode & 0o777, 0o600)
                self.assertFalse((root/'export.json').exists())

    def test_private_excerpt_preserves_head_and_tail(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'gigantic.log'
            p.write_text('ERROR root at beginning\n'+'x'*40000+'\nFATAL last line')
            result=discovery._private_excerpt(p,limit=1024)
            self.assertIn('ERROR root at beginning',result)
            self.assertIn('FATAL last line',result)
            self.assertIn('middle omitted',result)

    def test_failed_scans_are_rate_limited_and_force_can_retry(self):
        with patch.object(discovery.resource_guard,'preflight',return_value=(True,'fixture healthy')):  # NOVA3_SOLO_RETRY_PREFLIGHT_FIX_V2
            with tempfile.TemporaryDirectory() as td:
                root=Path(td); d=root/'analysis';d.mkdir()
                elf=d/'libNOVA3_neon.so';elf.write_bytes(b'\x7fELF'+b'\x00'*4)
                script=root/'Nova3FunctionLedger.java';script.write_text('script')
                fake=root/'analyzeHeadless';fake.write_text('fake executable')
                fake.chmod(0o755)
                env={'NOVA3_DISCOVERY_CACHE':str(root/'cache')}
                # NOVA3_RESOURCE_GUARD_HEALTH_TEST_FIX_V1: this test exercises Ghidra failure cooldown, not RAM pressure.
                # Mock a healthy host; memory deferral has separate coverage.
                with patch.dict(os.environ,env),\
                     patch('nova3_team.discovery_resources.preflight',
                           return_value=(True, 'healthy test fixture')):
                    with patch.object(discovery,'ensure_ghidra',return_value=fake),\
                         patch.object(discovery,'run_ghidra',side_effect=RuntimeError('Ghidra script missing')) as scan,\
                         patch.object(discovery,'__file__',str(root/'discovery.py')):
                        # Use the real bundled path with a dummy file for caching key.
                        (root/'ghidra_scripts').mkdir()
                        (root/'ghidra_scripts/Nova3FunctionLedger.java').write_text('script')
                        self.assertEqual(discovery.one_cycle(root,'owner/repo',publish=False)['mode'],'analysis-failed')
                        self.assertEqual(scan.call_count,1)
                        self.assertEqual(discovery.one_cycle(root,'owner/repo',publish=False)['mode'],'analysis-failed')
                        self.assertEqual(scan.call_count,1)
                        with patch.dict(os.environ,{'NOVA3_DISCOVERY_FORCE':'1'}):
                            self.assertEqual(discovery.one_cycle(root,'owner/repo',publish=False)['mode'],'analysis-failed')
                        self.assertEqual(scan.call_count,2)

    def test_timer_has_predictable_half_hour_calendar(self):
        service,timer=discovery_setup.unit_text(Path('/home/user/nova3'), 'owner/repo', Path('/usr/bin/python3'))
        self.assertIn('WorkingDirectory=/home/user/nova3',service)
        self.assertNotIn('WorkingDirectory="',service)
        self.assertIn('OnCalendar=*-*-* *:00,30:00',timer)
        self.assertNotIn('OnUnitInactiveSec=',timer)
        self.assertIn('Persistent=true',timer)

if __name__=='__main__':unittest.main()
