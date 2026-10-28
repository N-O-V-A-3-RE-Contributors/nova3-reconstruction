"""Offline regressions for bounded, diagnostic-rich AI provider sessions."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import worker
from nova3_team.reliability import code_for, failure_decision


class ProviderExecutionTests(unittest.TestCase):
    def test_timeout_configuration_is_bounded(self):
        with mock.patch.dict(os.environ, {'NOVA3_AI_SESSION_TIMEOUT': '900'}):
            self.assertEqual(worker.ai_session_timeout(), 900)
        for invalid in ('invalid', '60', '3601'):
            with self.subTest(invalid=invalid):
                with mock.patch.dict(os.environ, {'NOVA3_AI_SESSION_TIMEOUT': invalid}):
                    with self.assertRaises(ValueError):
                        worker.ai_session_timeout()

    def test_successful_provider_output_is_incremental_private_log(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'repo' / '82-abcdef'
            work.mkdir(parents=True)
            logfile = worker.private_round_log(work, 1)
            completed = worker.run_provider(
                [sys.executable, '-c', "print('PROVIDER_STARTED', flush=True)"],
                work, logfile, timeout=2)
            self.assertEqual(completed.returncode, 0)
            self.assertIn('PROVIDER_STARTED', completed.stdout)
            self.assertIn('PROVIDER_STARTED', logfile.read_text())
            self.assertNotIn('PROVIDER_STARTED', completed.stderr)
            self.assertNotIn(str(work), logfile.read_text())
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(logfile.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(logfile.parent.stat().st_mode), 0o700)

            # The handoff must not overwrite the complete streaming log.
            recorded = logfile.read_bytes()
            handoff = worker.record_handoff(
                work, 1, ['python', '<prompt>'], completed, [], streamed=True)
            self.assertEqual(logfile.read_bytes(), recorded)
            self.assertIn('Private full output: round-1.log', handoff)

    def test_timeout_retains_partial_output_for_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'repo' / '82-abcdef'
            work.mkdir(parents=True)
            logfile = worker.private_round_log(work, 2)
            completed = worker.run_provider(
                [sys.executable, '-c',
                 "import time; print('BEFORE_TIMEOUT', flush=True); time.sleep(5)"],
                work, logfile, timeout=0.4)
            self.assertEqual(completed.returncode, 124)
            self.assertIn('Provider session timed out', completed.stderr)
            self.assertIn('BEFORE_TIMEOUT', logfile.read_text())
            self.assertIn('BEFORE_TIMEOUT', completed.stdout)
            self.assertEqual(code_for(completed.stderr), 'PROVIDER_TIMEOUT')
            self.assertTrue(failure_decision({}, completed.stderr)['blocked'])


if __name__ == '__main__':
    unittest.main()
