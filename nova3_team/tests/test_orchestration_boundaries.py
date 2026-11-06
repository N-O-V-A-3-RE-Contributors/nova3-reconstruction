"""Offline budget/approval tests for the real composed worker boundary."""
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from nova3_team import autoadmit, autopilot, coordinator, dsh_runtime as runtime
from nova3_team import dsh_model_router as routing


class OrchestrationBoundaryTests(unittest.TestCase):
    def test_activation_requires_current_process_start_attestation(self):
        from nova3_team import worker, dsh_supervisor
        from types import SimpleNamespace
        capability = dsh_supervisor.runtime_capability()
        if capability is None:
            self.skipTest('Linux procfs activation protocol unavailable')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / 'supervisor.json'
            file.write_text(json.dumps({'supervisor_pid': os.getpid(), 'orchestration': capability}))
            with mock.patch.object(worker, 'state_root', return_value=root), \
                 mock.patch.object(worker, 'slot_paths', return_value=SimpleNamespace(state=root)):
                self.assertTrue(autopilot.worker_orchestration_ready('O/R'))
                capability['process_start_ticks'] = '0'
                file.write_text(json.dumps({'supervisor_pid': os.getpid(), 'orchestration': capability}))
                self.assertFalse(autopilot.worker_orchestration_ready('O/R'))
                file.write_text(json.dumps({'supervisor_pid': os.getpid()}))
                self.assertFalse(autopilot.worker_orchestration_ready('O/R'))

    def test_old_worker_cannot_receive_new_automatic_approvals(self):
        marker = '\n<!-- NOVA3_DSH_PLANNER_PROPOSAL:1234567890abcdef -->'
        issue = {'number': 1, 'state': 'open', 'labels': [], 'body':
                 'Allowed paths: reconstruction/recovered/inverse.cpp' + marker}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(autopilot, 'load_grant', return_value={'repo': 'O/R', 'enabled': True, 'source_first': True}), \
             mock.patch.object(autopilot, 'credit_paused', return_value=None), \
             mock.patch.object(autopilot, 'worker_orchestration_ready', return_value=False), \
             mock.patch.object(autoadmit, '_api', return_value=[issue]), \
             mock.patch.object(autopilot, 'approve_proposal') as approve:
            report = autopilot.tick('O/R', Path(folder), merge=False)
            approve.assert_not_called()
            self.assertIn((1, 'worker_restart_required'), report['refused'])

    def test_delivery_status_exposes_no_raw_model_or_patch_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'delivery').mkdir()
            (root / 'delivery/one.json').write_text(json.dumps({'last': {
                'pr': 1, 'state': 'blocked', 'reason': 'source_unchanged', 'raw': 'do not expose'}}))
            status = autopilot.delivery_summary(root)
            self.assertEqual(status['counts'], {'blocked': 1})
            self.assertNotIn('do not expose', json.dumps(status))

    def test_silent_child_that_never_reads_large_stdin_is_still_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'silent-dsh'
            script.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
            script.chmod(0o700)
            started = time.monotonic()
            result = runtime.run_session(runtime.DshEndpoint(str(script), root),
                'x' * (2 * 1024 * 1024), cwd=root, log_path=root / 'session.log', timeout_s=1)
            self.assertEqual(result.status, runtime.SESSION_TIMEOUT)
            self.assertLess(time.monotonic() - started, 8)

    def test_approval_hash_matches_gate_canonical_task_text(self):
        issue = {'id': 123, 'number': 10, 'title': 'Scoped task',
                 'created_at': '2026-01-01T00:00:00Z', 'body': 'Allowed paths: reconstruction/a.cpp\n\n'}
        canonical = dict(issue, body=coordinator.task_text(issue['body']))
        self.assertEqual(autoadmit.task_spec_sha256(issue), autoadmit.task_spec_sha256(canonical))
        modified = dict(issue, body=issue['body'] + 'New requirement')
        self.assertNotEqual(autoadmit.task_spec_sha256(issue), autoadmit.task_spec_sha256(modified))
        waiver = autoadmit.Waiver(1, 2, 'owner', (10,), 1, 3601, {})
        repo = {'id': 1, 'owner': {'id': 2}}
        record = json.loads(autoadmit.approval_record(issue=issue, waiver=waiver, repository=repo, created_at=10))
        self.assertEqual(record['task_spec_sha256'], autoadmit.task_spec_sha256(canonical))

    def test_source_first_approval_rejects_setup_but_keeps_game_source(self):
        marker = '\n<!-- NOVA3_DSH_PLANNER_PROPOSAL:1234567890abcdef -->'
        setup = {'number': 1, 'state': 'open', 'labels': [], 'body':
                 'Allowed paths: nova3_team/tests/test_poll.py' + marker}
        source = {'number': 2, 'state': 'open', 'labels': [], 'body':
                  'Allowed paths: reconstruction/recovered/inverse.cpp, reconstruction/tests/inverse_test.cpp' + marker}
        accepted, refused = autopilot.select_candidates([setup, source], {'source_first': True})
        self.assertEqual([item['number'] for item in accepted], [2])
        self.assertEqual(refused[0][1], 'source_first_requires_game_implementation')

    def test_discovery_consumes_session_budget_instead_of_resetting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = runtime.DshEndpoint('/dsh', root)
            route = routing.Route('provider', 'model', 'KEY')
            instance = routing.Router(base, root / 'router')
            ticks = iter([0, 20])
            with mock.patch.object(routing, 'discover', return_value=[route]), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(routing.time, 'monotonic', side_effect=lambda: next(ticks)), \
                 mock.patch.object(runtime, 'run_session') as session:
                with self.assertRaises(routing.ConfigError):
                    instance.run('task', task='source', cwd=root, log_path=root / 'log',
                                 timeout_s=10, cancel=lambda: False, on_failure=lambda *a: None)
                session.assert_not_called()
