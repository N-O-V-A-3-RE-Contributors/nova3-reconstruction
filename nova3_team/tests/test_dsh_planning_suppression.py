"""Offline actual supervisor planning-path regressions; no services or models."""
import copy
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from nova3_team import dsh_planner as planner, dsh_policy as policy
from nova3_team import dsh_runtime as runtime, dsh_supervisor as sup
from nova3_team.common import lease_put


class PlanningSuppressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.s = sup.DshSupervisor(project=self.root, once=True)
        self.s.paths = SimpleNamespace(state=self.root / 'private', logs=self.root / 'logs')
        self.s.state = sup.SupervisorState(self.s.paths.state / 'supervisor.json')
        self.s.ledger = policy.SpendLedger(self.s.paths.state / 'spend.jsonl')
        self.issues = [dict(number=23, title='Recover matrix source', body='Source objective',
                            labels=[{'name': 'nova:review'}], state='open')]
        self.s.gh = mock.Mock()
        self.s.gh.issues.side_effect = lambda: copy.deepcopy(self.issues)
        self.s._session_allowed = mock.Mock(return_value=True)
        self.s._request = mock.Mock()
        self.s._run_with_failover = mock.Mock(return_value=self.outcome())
        self.head = mock.patch.object(planner.subprocess, 'run',
                                      return_value=SimpleNamespace(stdout='head-a'))
        self.git = self.head.start()
        self.addCleanup(self.head.stop)

    def outcome(self, text='[]', status=runtime.COMPLETED):
        return runtime.SessionOutcome(status=status, exit_code=0, session_id='offline',
            turn_reason=None, error_code=None, error_message='', steps=1, tool_calls=0,
            elapsed_s=0, events=({'type': 'final', 'text': text},))

    def test_empty_plan_durable_skip_ignores_volatile_inputs(self):
        self.s.plan()
        self.assertEqual(self.s.state.load()['planner']['outcome'], 'no_proposals')
        # A fresh state object simulates a supervisor restart.
        self.s.state = sup.SupervisorState(self.s.state.path)
        self.s.state.save(updated_unix=123, cycle_errors=8)
        self.s.ledger.append({'task': 'planner', 'status': 'COMPLETED', 'input_tokens': 99})
        self.issues[0].update(updated_at='tomorrow', comments=100)
        self.issues[0]['body'] = lease_put('Source objective',
            {'owner': 'worker', 'token': 'first', 'expires_at': 'tomorrow'})
        # Ownership is meaningful and permits one pass; renewal does not.
        self.s.plan()
        self.issues[0]['body'] = lease_put('Source objective',
            {'owner': 'worker', 'token': 'second', 'expires_at': 'later'})
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 2)
        self.assertNotEqual(self.s.state.load()['state'], policy.COMPLETE)

    def test_meaningful_inputs_each_allow_replanning(self):
        self.s.plan()
        changes = [lambda: setattr(self.git.return_value, 'stdout', 'head-b'),
                   lambda: self.issues[0].update(labels=[{'name': 'nova:blocked'}]),
                   lambda: self.issues[0].update(body='Materially new source evidence'),
                   lambda: self.issues[0].update(head={'sha': 'new-pr-head'}),
                   lambda: self.write('docs/progress/NEXT.md', 'Recover C++ matrix'),
                   lambda: self.write('.local/dsh-loop/prompt-overrides.md', 'Source first')]
        for index, change in enumerate(changes, 2):
            change()
            self.s.plan()
            self.assertEqual(self.s._run_with_failover.call_count, index)
            self.s.plan()
            self.assertEqual(self.s._run_with_failover.call_count, index)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_provider_failure_cooldown_then_retry_not_empty_success(self):
        self.s._run_with_failover.return_value = self.outcome(status=runtime.PROVIDER_OUTAGE)
        with mock.patch.object(sup.time, 'time', return_value=10000):
            self.s.plan()
            self.s.plan()
        first = self.s.state.load()['planner']
        self.assertEqual(first['outcome'], 'failure')
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        with mock.patch.object(sup.time, 'time', return_value=first['retry_after'] + 1):
            self.s.plan()
        second = self.s.state.load()['planner']
        self.assertEqual(second['failures'], 2)
        self.assertGreater(second['retry_after'] - first['retry_after'], 300)
        self.assertNotEqual(self.s.state.load()['state'], policy.COMPLETE)

    def test_invalid_response_retries_but_rejected_proposal_suppresses(self):
        self.s._run_with_failover.return_value = self.outcome('not JSON')
        self.s.plan()
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        self.assertEqual(self.s.state.load()['planner']['outcome'], 'failure')
        self.s.state.save(planner={})
        self.s._run_with_failover.return_value = self.outcome('[{"title":"bad"}]')
        self.s.plan()
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 2)
        self.assertEqual(self.s.state.load()['planner']['outcome'], 'no_proposals')

    def test_pending_proposal_skips_even_direct_plan(self):
        self.issues.append(dict(number=24, body=planner.PROPOSAL_MARKER + '0123456789abcdef -->'))
        self.s.plan()
        self.s._run_with_failover.assert_not_called()
        self.assertEqual(self.s.state.load()['state'], policy.WAITING_APPROVAL)

    def test_cycle_executes_queue_before_planning_even_after_empty(self):
        self.s.plan()
        self.issues.append(dict(number=25, title='repair source', labels=[{'name': 'nova:queued'}]))
        self.s._claimable = mock.Mock(return_value=[self.issues[-1]])
        self.s.run_task = mock.Mock()
        self.s.cycle()
        self.s.run_task.assert_called_once_with(self.issues[-1], claim_first=True)
        self.assertEqual(self.s._run_with_failover.call_count, 1)

    def test_empty_queue_never_complete_even_if_goal_check_would_pass(self):
        self.issues.clear()
        self.s._project_goal_met = mock.Mock(return_value=True)
        self.s.plan()
        self.s.plan()
        self.s._project_goal_met.assert_not_called()
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        self.assertNotEqual(self.s.state.load()['state'], policy.COMPLETE)

    def test_checkpoint_semantics_not_timestamps_permit_replanning(self):
        self.write('private/.nova-state/23.json', json.dumps({'phase': 'review', 'updated_at': 1}))
        self.s.plan()
        self.write('private/.nova-state/23.json', json.dumps({'phase': 'review', 'updated_at': 2}))
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        self.write('private/.nova-state/23.json', json.dumps({'phase': 'repair', 'updated_at': 3}))
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 2)

    def test_deduplicated_plan_suppresses_and_filing_failure_is_not_rejection(self):
        proposal = dict(title='Recover matrix source', objective='Implement matrix source multiplication',
                        acceptance=['Compile matrix source correctly'],
                        allowed_paths=['reconstruction/matrix.cpp'], verify=['true'])
        self.s._run_with_failover.return_value = self.outcome(json.dumps([proposal]))
        with mock.patch.object(planner, 'deduplicate', return_value=[]):
            self.s.plan()
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        self.s.state.save(planner={})
        self.issues.clear()
        self.s.gh.post.side_effect = OSError('offline publication')
        self.s.plan()
        self.assertEqual(self.s.state.load()['planner']['outcome'], 'failure')
        self.assertFalse(self.s.state.load().get('rejected_proposals'))

    def test_session_exception_is_backed_off(self):
        self.s._run_with_failover.side_effect = OSError('offline provider')
        self.s.plan()
        self.s.plan()
        self.assertEqual(self.s._run_with_failover.call_count, 1)
        self.assertEqual(self.s.state.load()['planner']['outcome'], 'failure')

    def test_prompt_is_explicit_source_first_with_live_priorities(self):
        self.write('docs/progress/NEXT.md', 'Recover actual C++ source')
        self.write('.local/dsh-loop/prompt-overrides.md', 'Continue source reconstruction')
        self.s.plan()
        prompt = self.s._run_with_failover.call_args.args[0]
        for phrase in ('SOURCE FIRST', 'Standalone generic infrastructure',
                       'concrete necessary blocker', 'Recover actual C++ source',
                       'Continue source reconstruction'):
            self.assertIn(phrase, prompt)


if __name__ == '__main__':
    unittest.main()
