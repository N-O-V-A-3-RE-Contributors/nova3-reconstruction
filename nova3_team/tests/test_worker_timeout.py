"""Offline regressions for bounded, diagnostic-rich AI provider sessions."""
from __future__ import annotations

import os
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import worker
from nova3_team.activity_feed import ActivityFeed, POLL_SECONDS
from nova3_team.reliability import code_for, failure_decision


class ProviderExecutionTests(unittest.TestCase):
    def test_json_capability_probe_is_bounded_cached_and_optional(self):
        self.addCleanup(worker._codex_json_supported.cache_clear)
        for result in (worker.subprocess.CompletedProcess([], 0, stdout='  --json\n'),
                       worker.subprocess.CompletedProcess([], 0, stdout='old help'),
                       worker.subprocess.CompletedProcess([], 1, stdout='--json'),
                       OSError('missing'), UnicodeError('invalid help'),
                       worker.subprocess.TimeoutExpired('help', 3)):
            worker._codex_json_supported.cache_clear()
            supported = isinstance(result, worker.subprocess.CompletedProcess) and result.returncode == 0 and '--json' in result.stdout
            with mock.patch.object(worker.subprocess, 'run') as probe:
                if isinstance(result, Exception):
                    probe.side_effect = result
                else:
                    probe.return_value = result
                for _ in range(2):
                    cmd = worker.command_for('codex', 'private prompt')
                    self.assertEqual('--json' in cmd[:-1], supported)
                    self.assertEqual(cmd[-1], 'private prompt')
                    self.assertEqual(cmd[:5], ['codex', 'exec', '--ephemeral', '--sandbox', 'workspace-write'])
                probe.assert_called_once_with(['codex', 'exec', '--help'], capture_output=True,
                                              text=True, timeout=3, check=False)

    def test_other_provider_commands_do_not_probe_or_change_format(self):
        with mock.patch.object(worker, '_codex_json_supported') as probe:
            for provider in ('claude', 'gemini'):
                cmd = worker.command_for(provider, 'prompt')
                self.assertNotIn('--json', cmd)
                self.assertIn('text', cmd)
            probe.assert_not_called()

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
            summary = json.loads(logfile.with_suffix('.activity.json').read_text())
            self.assertEqual(summary['status'], 'FINISHED')
            self.assertFalse(summary['structured_events'])
            self.assertNotIn('PROVIDER_STARTED', json.dumps(summary))
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
            summary = json.loads(logfile.with_suffix('.activity.json').read_text())
            self.assertEqual(summary['status'], 'FAILED')
            self.assertNotIn('BEFORE_TIMEOUT', json.dumps(summary))

    def test_structured_stream_and_independently_observed_file_count(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'repo' / '106-checkout'
            work.mkdir(parents=True)
            logfile = worker.private_round_log(work, 1)
            activity = ActivityFeed(logfile, structured=True)
            events = [
                {'type': 'item.started', 'item': {'id': 'a', 'type': 'command_execution',
                                                'command': 'python -m unittest'}},
                {'type': 'item.completed', 'item': {'id': 'a', 'type': 'command_execution',
                                                  'command': 'python -m unittest', 'exit_code': 0}},
            ]
            # A tiny local emitter, never a child AI session.
            script = 'import sys; sys.stdout.write(' + repr(''.join(json.dumps(e) + '\n' for e in events)) + ')'
            result = worker.run_provider([sys.executable, '-c', script], work, logfile,
                                         timeout=2, activity=activity)
            self.assertEqual(result.returncode, 0)
            value = json.loads(activity.path.read_text())
            self.assertEqual(value['tests_completed'], 1)
            self.assertIsNone(value['file_changed_count'])
            worker._publish_activity(activity, force=True, returncode=0, file_changed_count=3)
            self.assertEqual(json.loads(activity.path.read_text())['file_changed_count'], 3)

    def test_ten_second_activity_poll_preserves_sixty_second_heartbeat_and_deadline(self):
        for deadline, finish_at in ((125, None), (200, 125)):
            with self.subTest(deadline=deadline), tempfile.TemporaryDirectory() as temp:
                clock = [0.0]
                waits = []
                process = mock.Mock(returncode=0)

                def wait(timeout):
                    waits.append(timeout)
                    clock[0] += timeout
                    if finish_at is not None and clock[0] >= finish_at:
                        return 0
                    raise worker.subprocess.TimeoutExpired('synthetic', timeout)

                process.wait.side_effect = wait
                activity = mock.Mock()
                with mock.patch.object(worker.time, 'monotonic', side_effect=lambda: clock[0]), \
                        mock.patch.object(worker.subprocess, 'Popen', return_value=process), \
                        mock.patch.object(worker, '_stop_provider_group') as stop, \
                        mock.patch.object(worker, 'note') as note:
                    result = worker.run_provider(['synthetic'], temp, Path(temp) / 'round.log',
                                                 deadline, activity=activity)
                self.assertTrue(all(0 < timeout <= POLL_SECONDS for timeout in waits))
                self.assertEqual(note.call_count, 2)
                self.assertIn('elapsed=60s', note.call_args_list[0].args[0])
                self.assertIn('elapsed=120s', note.call_args_list[1].args[0])
                self.assertGreaterEqual(activity.update.call_count, 13)
                self.assertEqual(result.returncode, 124 if finish_at is None else 0)
                if finish_at is None:
                    self.assertEqual(clock[0], deadline)
                    stop.assert_called_once_with(process)
                else:
                    stop.assert_not_called()

    def test_summary_failure_cannot_fail_provider_or_replace_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            activity = mock.Mock()
            activity.update.side_effect = RuntimeError('telemetry failure')
            result = worker.run_provider([sys.executable, '-c', 'raise SystemExit(7)'], temp,
                                         Path(temp) / 'round.log', 2, activity=activity)
            self.assertEqual(result.returncode, 7)


if __name__ == '__main__':
    unittest.main()
