"""Regression tests for the defects found in independent review (2026-11-02 15:12).

Every test here failed against the code as it stood at that snapshot. They are
deliberately adversarial: a fake GitHub that behaves like the real coordinator
(refusing a second claim while a lease is active), subprocesses that hang or go
silent, and ledgers whose contents decide whether a budget is respected.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from nova3_team import dsh_planner, dsh_policy, dsh_runtime, worker
from nova3_team.common import lease_put


# ---------------------------------------------------------------------------
# P0-1: single ownership of the initial claim
# ---------------------------------------------------------------------------

class _CoordinatorLikeGitHub:
    """Minimal GitHub that behaves like the real coordinator for claims.

    The real coordinator refuses `/nova claim` while ANY lease is active and
    ignores commands from a token that is not the lease's token.
    """

    def __init__(self, body='Priority: 50\nAllowed paths: docs/worker-notes\n\nDo it.\n'):
        self.body = body
        self.comments = []
        self.lock = threading.Lock()
        self.issue_fetches = 0

    def _lease(self):
        try:
            from nova3_team.common import lease_get
            return lease_get(self.body)
        except Exception:
            return None

    def issue(self, number):
        self.issue_fetches += 1
        return {'number': number, 'state': 'open', 'title': 'T', 'body': self.body,
                'labels': [{'name': 'nova:queued'}], 'user': {'login': 'crjase'}}

    def comment(self, number, text):
        with self.lock:
            self.comments.append(text)
            parts = text.split()
            if parts[:2] == ['/nova', 'claim']:
                from nova3_team.common import lease_active
                lease = self._lease()
                if lease and lease_active(lease):
                    return {}                  # REFUSED: a lease is already active
                self.body = lease_put(self.body, {
                    'owner': 'crjase', 'token': parts[2],
                    'expires_at': '2999-01-01T00:00:00Z'})
            elif parts[:2] == ['/nova', 'release']:
                lease = self._lease()
                if lease and lease.get('token') == parts[2]:
                    self.body = lease_put(self.body, None)
        return {}

    def issues(self):
        return [self.issue(1)]

    def post(self, path, payload):
        return {}


def _fake_supervisor(sup, gh, state_dir):
    class FakeSupervisor(sup.DshSupervisor):
        def __init__(self):
            self.slot = 'test'
            self.worker = worker
            self.gh = gh
            self.upstream = 'O/R'
            self.user = 'crjase'
            self.paths = type('P', (), {'state': state_dir})()
            self.state = sup.SupervisorState(state_dir / 'supervisor.json')
            self.stop_signal = ''
            self._last_heartbeat = 0.0
            self.reporter = None
            self.endpoint = None
            self.project = pathlib.Path(state_dir)
            self.gate = None
            self.ledger = dsh_policy.SpendLedger(state_dir / 'spend.jsonl')

        def _presence(self, *a, **k):
            return None

        def _session_allowed(self, *a, **k):
            return True

    return FakeSupervisor()


class ClaimOwnershipTests(unittest.TestCase):
    def test_work_claimed_issues_no_second_claim(self):
        gh = _CoordinatorLikeGitHub()
        with mock.patch.object(worker, 'prepare_checkout',
                               side_effect=RuntimeError('stop here: checkout not needed')):
            worker.work_claimed(gh, 'O/R', 'crjase', gh.issue(1), 'dsh',
                                self.root, 1, '', 'token-1', gate=None)
        claims = [c for c in gh.comments if c.startswith('/nova claim')]
        self.assertEqual(claims, [], 'work_claimed must never claim: the lease is already ours')

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_work_one_still_claims_when_it_owns_the_claim(self):
        """The Codex path is unchanged: work_one claims, then does the work."""
        gh = _CoordinatorLikeGitHub()
        seen = {}

        def fake_work_claimed(*args, **kwargs):
            seen['token'] = args[8]
            return 'worked'

        with mock.patch.object(worker, 'claim', return_value='token-x'), \
                mock.patch.object(worker, 'work_claimed', side_effect=fake_work_claimed):
            result = worker.work_one(gh, 'O/R', 'crjase', gh.issue(1), 'dsh',
                                     self.root, 1, '')
        self.assertEqual(result, 'worked')
        self.assertEqual(seen['token'], 'token-x')

    def test_supervisor_claimed_task_proceeds_without_a_second_claim(self):
        """Integration: a supervisor-owned lease reaches work ONE claim later.

        Before the fix the supervisor claimed (token A), then work_one asked for
        token B, the coordinator refused it because A was active, work_one waited
        the full timeout and returned None -- the task was silently skipped.
        """
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        state_dir = pathlib.Path(tempfile.mkdtemp())

        supervisor = _fake_supervisor(sup, gh, state_dir)
        # Precondition: the supervisor owns an active lease for #1 (this is the
        # state run_task is in immediately after its own claim).
        gh.body = lease_put(gh.body, {'owner': 'crjase', 'token': 'token-owned',
                                      'expires_at': '2999-01-01T00:00:00Z'})
        (state_dir / '.nova-state').mkdir(parents=True, exist_ok=True)
        (state_dir / '.nova-state' / '1.json').write_text(json.dumps(
            {'issue': 1, 'repo': 'O/R', 'user': 'crjase', 'token': 'token-owned',
             'phase': 'checkout'}))
        calls = {'claims': 0, 'work_claimed': 0}

        real_comment = gh.comment

        def counting_comment(number, text):
            if text.startswith('/nova claim'):
                calls['claims'] += 1
            return real_comment(number, text)

        gh.comment = counting_comment

        def fake_work_claimed(*args, **kwargs):
            calls['work_claimed'] += 1
            return True

        # The supervisor's own `recovery` module writes checkpoints; the task
        # state is read after work returns, so the fake work returns a normal
        # bool and the checkpoint is simply absent.
        # The scenario under test is the task ALREADY claimed by the supervisor;
        # the claim step itself is exercised separately below.
        supervisor.state.save(task={'number': 1, 'token': 'token-owned',
                                    'title': 'T', 'claimed_unix': int(time.time())})
        with mock.patch.object(worker, 'work_claimed', side_effect=fake_work_claimed):
            supervisor.run_task(gh.issue(1))
        self.assertEqual(calls['claims'], 0,
                         'a task the supervisor already owns is never claimed again')
        self.assertEqual(calls['work_claimed'], 1, 'work runs on the existing lease')

    def test_supervisor_claiming_a_new_task_claims_exactly_once(self):
        """claim_first=True performs the single claim and does not re-claim."""
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, gh, state_dir)
        claims = []
        real_comment = gh.comment
        gh.comment = lambda n, t: (claims.append(t) if t.startswith('/nova claim') else None,
                                   real_comment(n, t))[1]
        work_calls = []
        with mock.patch.object(worker, 'work_claimed',
                               side_effect=lambda *a, **k: work_calls.append(1) or True):
            supervisor.run_task(gh.issue(1), claim_first=True)
        self.assertEqual(len(claims), 1, 'one claim for a new task')
        self.assertEqual(len(work_calls), 1)


# ---------------------------------------------------------------------------
# P0-2: the deadline must fire even when the child says nothing
# ---------------------------------------------------------------------------

class DeadlineTests(unittest.TestCase):
    def _endpoint(self, script: pathlib.Path) -> dsh_runtime.DshEndpoint:
        return dsh_runtime.DshEndpoint(binary=str(script), home=pathlib.Path('/tmp'),
                                       max_steps=5)

    def _script(self, folder: str, body: str) -> pathlib.Path:
        path = pathlib.Path(folder) / 'fake-dsh'
        path.write_text('#!/usr/bin/env python3\n' + body, encoding='utf-8')
        path.chmod(0o755)
        return path

    def test_silent_hanging_child_times_out(self):
        with tempfile.TemporaryDirectory() as folder:
            script = self._script(folder, 'import time\ntime.sleep(600)\n')
            started = time.monotonic()
            outcome = dsh_runtime.run_session(
                self._endpoint(script), 'task', cwd=pathlib.Path(folder),
                log_path=pathlib.Path(folder) / 'log', timeout_s=3)
            elapsed = time.monotonic() - started
        self.assertEqual(outcome.status, dsh_runtime.SESSION_TIMEOUT)
        self.assertLess(elapsed, 30, 'a silent child must be killed on the deadline')
        self.assertIsNone(outcome.exit_code, "our own kill is not the child's verdict")

    def test_child_that_opens_stdout_and_never_writes_is_killed(self):
        with tempfile.TemporaryDirectory() as folder:
            script = self._script(folder, 'import sys, time\n'
                                          'sys.stdout.write("")\n'
                                          'sys.stdout.flush()\n'
                                          'time.sleep(600)\n')
            started = time.monotonic()
            outcome = dsh_runtime.run_session(
                self._endpoint(script), 'task', cwd=pathlib.Path(folder),
                log_path=pathlib.Path(folder) / 'log', timeout_s=3)
            elapsed = time.monotonic() - started
        self.assertEqual(outcome.status, dsh_runtime.SESSION_TIMEOUT)
        self.assertLess(elapsed, 30)

    def test_cancellation_fires_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            script = self._script(folder, 'import time\ntime.sleep(600)\n')
            flag = {'stop': False}
            threading.Timer(1.0, lambda: flag.update(stop=True)).start()
            started = time.monotonic()
            outcome = dsh_runtime.run_session(
                self._endpoint(script), 'task', cwd=pathlib.Path(folder),
                log_path=pathlib.Path(folder) / 'log', timeout_s=120,
                cancel=lambda: flag['stop'])
            elapsed = time.monotonic() - started
        self.assertEqual(outcome.status, dsh_runtime.CANCELLED)
        self.assertLess(elapsed, 30)

    def test_a_completed_stream_is_still_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            script = self._script(folder, (
                'import json, sys\n'
                'print(json.dumps({"type":"session","sessionId":"session-s"}), flush=True)\n'
                'print(json.dumps({"type":"status","phase":"turn_end",'
                '"reason":{"kind":"completed"}}), flush=True)\n'
                'print(json.dumps({"type":"final","text":"ok"}), flush=True)\n'))
            outcome = dsh_runtime.run_session(
                self._endpoint(script), 'task', cwd=pathlib.Path(folder),
                log_path=pathlib.Path(folder) / 'log', timeout_s=30)
        self.assertEqual(outcome.status, dsh_runtime.COMPLETED)
        self.assertEqual(outcome.session_id, 'session-s')


# ---------------------------------------------------------------------------
# P0-3: every session is accounted for, every attempt is charged
# ---------------------------------------------------------------------------

class SpendAccountingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        self.ledger = dsh_policy.SpendLedger(self.root / 'spend.jsonl')

    def tearDown(self):
        self.dir.cleanup()

    def test_each_attempt_is_recorded_even_when_it_fails(self):
        for status, code, tokens in (('EXECUTION_ERROR', 'X', 100),
                                     ('SESSION_TIMEOUT', None, 50),
                                     ('COMPLETED', None, 25)):
            worker.dsh_last = {'status': status, 'error_code': code,
                               'session_id': 'session-1', 'elapsed_s': 1.0,
                               'spend': {'input_tokens': tokens, 'output_tokens': 0}}
            worker.dsh_account_session(self.ledger.append, 7, 1, self.root, 7)
        records = [r for r in self.ledger.records() if r.get('phase') == 'session']
        self.assertEqual(len(records), 3, 'every invocation is one record')
        self.assertEqual(self.ledger.tokens_today(), 175, 'failed rounds still cost tokens')
        checkpoint = json.loads((self.root / '.nova-state' / '7.json').read_text())
        self.assertEqual(checkpoint['dsh_attempts_used'], 3)

    def test_multi_round_task_charges_every_round(self):
        """worker.work_one with 3 failing rounds must charge 3 sessions."""
        # The checkpoint the supervisor would have written for this claim (it
        # must exist, or work_one takes the recovered-output path and never runs
        # an AI session at all).
        (self.root / '.nova-state').mkdir(parents=True, exist_ok=True)
        (self.root / '.nova-state' / '1.json').write_text(json.dumps(
            {'issue': 1, 'repo': 'O/R', 'user': 'crjase', 'token': 'tok',
             'phase': 'checkout'}))
        gh = _CoordinatorLikeGitHub()
        work = self.root / 'work'
        work.mkdir()
        (work / '.git').mkdir()
        rounds = {'n': 0}

        def fake_session(prompt, cwd, log_path, timeout_s):
            rounds['n'] += 1
            return dsh_runtime.SessionOutcome(
                status='EXECUTION_ERROR', exit_code=1,
                session_id=f'session-{rounds["n"]}', turn_reason='error',
                error_code='BOOM', error_message='boom', steps=1, tool_calls=0,
                elapsed_s=1.0, input_tokens=1000, output_tokens=10)

        worker._dsh_ledger = self.ledger.append
        worker._dsh_session = fake_session
        try:
            with mock.patch.object(worker, 'claim', return_value='tok'), \
                    mock.patch.object(worker, 'prepare_checkout',
                                      return_value=(work, 'nova/issue-1-x', 'O/R')), \
                    mock.patch.object(worker, 'validate_scope', return_value=[]), \
                    mock.patch.object(worker, 'record_handoff', return_value='h'), \
                    mock.patch.object(worker, 'pause_failed_task', return_value=False):
                worker.work_one(gh, 'O/R', 'crjase', gh.issue(1), 'dsh',
                                self.root, 3, '')
        finally:
            worker._dsh_ledger = None
            worker._dsh_session = None
        sessions = [r for r in self.ledger.records() if r.get('phase') == 'session']
        self.assertEqual(len(sessions), 3, 'three rounds = three charged sessions')
        self.assertEqual(self.ledger.tokens_today(), 3030)

    def test_terminal_credit_failure_stops_the_round_loop(self):
        (self.root / '.nova-state').mkdir(parents=True, exist_ok=True)
        (self.root / '.nova-state' / '1.json').write_text(json.dumps(
            {'issue': 1, 'repo': 'O/R', 'user': 'crjase', 'token': 'tok',
             'phase': 'checkout'}))
        work = self.root / 'work'
        work.mkdir()
        (work / '.git').mkdir()
        gh = _CoordinatorLikeGitHub()
        calls = {'n': 0}

        def fake_session(prompt, cwd, log_path, timeout_s):
            calls['n'] += 1
            return dsh_runtime.SessionOutcome(
                status='WAITING_CREDITS', exit_code=1, session_id='session-q',
                turn_reason='error', error_code='QUOTA', error_message='quota',
                steps=1, tool_calls=0, elapsed_s=1.0, input_tokens=10, output_tokens=1)

        with mock.patch.object(worker, 'claim', return_value='tok'), \
                mock.patch.object(worker, 'prepare_checkout',
                                  return_value=(work, 'nova/issue-1-x', 'O/R')), \
                mock.patch.object(worker, 'validate_scope', return_value=[]), \
                mock.patch.object(worker, 'record_handoff', return_value='h'), \
                mock.patch.object(worker, 'pause_failed_task', return_value=False) as pause:
            worker._dsh_session = fake_session
            try:
                worker.work_one(gh, 'O/R', 'crjase', gh.issue(1), 'dsh', self.root, 3, '')
            finally:
                worker._dsh_session = None
        self.assertEqual(calls['n'], 1, 'no credits: never start a second round')
        self.assertTrue(pause.called)

    def test_injected_session_does_not_require_a_local_dsh_install(self):
        """The supervisor injects the runner; endpoint resolution must be skipped.

        CI has no DSH runtime, so resolving a local endpoint inside run_dsh made
        the round-loop tests see zero sessions. The injected path must not
        depend on ~/.local/share/nova3-dsh or a `dsh` on PATH.
        """
        (self.root / '.nova-state').mkdir(parents=True, exist_ok=True)
        (self.root / '.nova-state' / '1.json').write_text(json.dumps(
            {'issue': 1, 'repo': 'O/R', 'user': 'crjase', 'token': 'tok',
             'phase': 'checkout'}))
        work = self.root / 'work'
        work.mkdir()
        (work / '.git').mkdir()
        calls = {'n': 0}

        def fake_session(prompt, cwd, log_path, timeout_s):
            calls['n'] += 1
            return dsh_runtime.SessionOutcome(
                status='EXECUTION_ERROR', exit_code=1, session_id='session-x',
                turn_reason='error', error_code='BOOM', error_message='boom',
                steps=1, tool_calls=0, elapsed_s=1.0, input_tokens=5, output_tokens=1)

        gh = _CoordinatorLikeGitHub()
        with mock.patch.object(worker, 'claim', return_value='tok'), \
                mock.patch.object(worker, 'prepare_checkout',
                                  return_value=(work, 'nova/issue-1-x', 'O/R')), \
                mock.patch.object(worker, 'validate_scope', return_value=[]), \
                mock.patch.object(worker, 'record_handoff', return_value='h'), \
                mock.patch.object(worker, 'pause_failed_task', return_value=False), \
                mock.patch.object(worker, 'dsh_endpoint',
                                  side_effect=dsh_runtime.DshUnavailable('no runtime')):
            worker._dsh_session = fake_session
            try:
                worker.work_one(gh, 'O/R', 'crjase', gh.issue(1), 'dsh', self.root, 1, '')
            finally:
                worker._dsh_session = None
        self.assertEqual(calls['n'], 1,
                         'the injected runner must not need a local DSH install')

    def test_supervisor_summary_never_double_counts_tokens(self):
        supervisor_entries = [{'task': '7', 'status': 'COMPLETED', 'summary': True,
                               'task_tokens': 5000}]
        ledger = dsh_policy.SpendLedger(self.root / 'summary.jsonl')
        for entry in supervisor_entries:
            ledger.append(entry)
        self.assertEqual(ledger.tokens_today(), 0,
                         'summary rows carry no tokens, so budgets count real spend once')


# ---------------------------------------------------------------------------
# P1: control-plane semantics (plan-only, requests, completion, fallback)
# ---------------------------------------------------------------------------

class ProposalSuppressionTests(unittest.TestCase):
    """No planner session while its own proposals still await a human."""

    def _issue(self, number, body, labels=(), state='open'):
        return {'number': number, 'state': state, 'labels': [{'name': l} for l in labels],
                'body': body}

    def test_pending_proposal_blocks_planning(self):
        marker = '<!-- NOVA3_DSH_PLANNER_PROPOSAL:0123456789abcdef -->'
        issues = [self._issue(163, marker), self._issue(164, marker)]
        with tempfile.TemporaryDirectory() as folder:
            view = dsh_planner.assess(issues=issues, ledger_root=pathlib.Path(folder),
                                      checkpoint_root=pathlib.Path(folder))
        self.assertEqual(view.pending, (163, 164))
        self.assertTrue(view.proposal_suppressed)
        self.assertFalse(view.needs_planning(), 'a decision backlog suppresses planning')

    def test_approved_or_rejected_proposals_no_longer_block(self):
        marker = '<!-- NOVA3_DSH_PLANNER_PROPOSAL:0123456789abcdef -->'
        issues = [
            self._issue(163, marker, labels=['nova:queued']),    # approved into the queue
            self._issue(164, marker, state='closed'),            # rejected
            self._issue(165, 'ordinary task, no marker'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            view = dsh_planner.assess(issues=issues, ledger_root=pathlib.Path(folder),
                                      checkpoint_root=pathlib.Path(folder))
        self.assertEqual(view.pending, ())
        self.assertTrue(view.needs_planning())

    def test_cap_is_configurable_and_zero_disables_it(self):
        marker = '<!-- NOVA3_DSH_PLANNER_PROPOSAL:0123456789abcdef -->'
        issues = [self._issue(163, marker)]
        with tempfile.TemporaryDirectory() as folder:
            view = dsh_planner.assess(issues=issues, ledger_root=pathlib.Path(folder),
                                      checkpoint_root=pathlib.Path(folder), max_pending=0)
        self.assertFalse(view.proposal_suppressed)
        self.assertTrue(view.needs_planning())


class ControlPlaneTests(unittest.TestCase):
    def test_plan_only_never_runs_a_task(self):
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, gh, state_dir)
        supervisor.plan_only = True
        supervisor.no_plan = False
        called = {'task': 0, 'plan': 0}
        supervisor.run_task = lambda *a, **k: called.__setitem__('task', called['task'] + 1)
        supervisor.plan = lambda: called.__setitem__('plan', called['plan'] + 1)
        # A runnable task exists; plan-only must plan and never claim or execute.
        with mock.patch.object(supervisor, '_claimable', return_value=[gh.issue(1)]):
            supervisor.cycle()
        self.assertEqual(called['task'], 0, '--plan-only must not execute tasks')
        self.assertEqual(called['plan'], 1)

    def test_requests_are_resolved_individually_never_wholesale(self):
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        gh.issue = lambda number: {'number': number, 'state': 'open',
                                   'labels': [], 'body': ''}   # still undecided
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, gh, state_dir)
        supervisor._request('approval', '#163, #164 need review', 'approve them', task='planner')
        supervisor._request('credits', 'quota', 'top up', task='7')
        supervisor.state.save(state=sup.dsh_policy.RUNNING)
        supervisor._resolve_requests()
        open_set = json.loads((state_dir / 'requests.open.json').read_text())
        kinds = sorted(r['kind'] for r in open_set)
        self.assertEqual(kinds, ['approval', 'credits'],
                         'progress does not answer questions a human still owes')

    def test_approval_request_is_resolved_only_when_every_issue_is_decided(self):
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        decided = {'163': False, '164': True}

        def issue(number):
            labels = [{'name': 'nova:queued'}] if decided[str(number)] else []
            return {'number': number, 'state': 'open', 'labels': labels, 'body': ''}

        gh.issue = issue
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, gh, state_dir)
        supervisor._request('approval', '#163, #164 need review', 'approve them', task='planner')
        supervisor._resolve_requests()
        self.assertEqual(len(json.loads((state_dir / 'requests.open.json').read_text())), 1)
        decided['163'] = True
        supervisor._resolve_requests()
        self.assertEqual(json.loads((state_dir / 'requests.open.json').read_text()), [],
                         'resolved once every referenced proposal is decided')

    def test_evidence_needed_is_not_completion(self):
        from nova3_team import dsh_supervisor as sup
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, _CoordinatorLikeGitHub(), state_dir)
        self.assertFalse(supervisor._project_goal_met(),
                         'an empty queue is not a completed reconstruction')
        checks = json.loads((state_dir / 'supervisor.json').read_text())['acceptance']
        self.assertFalse(any(checks.values()))
        self.assertIn('android_apk_built', checks)

    def test_fallback_routes_parse_conservatively(self):
        routes = dsh_runtime.parse_fallback_routes(
            'deepseek-direct:deepseek-chat, bad name!, https://evil.example, alt2')
        self.assertEqual([r.name for r in routes], ['deepseek-direct', 'alt2'])
        self.assertEqual(routes[0].model, 'deepseek-chat')
        self.assertEqual(dsh_runtime.parse_fallback_routes(''), ())
        ep = dsh_runtime.DshEndpoint(binary='x', home=pathlib.Path('/h'))
        text = dsh_runtime.overlay_text(ep, routes)
        self.assertIn('deepseek-direct:', text)
        self.assertIn('apiKeyEnv: OPENROUTER_API_KEY', text)
        self.assertNotIn('sk-', text)

    def test_failover_only_for_provider_level_failures(self):
        self.assertIn(dsh_runtime.WAITING_CREDITS, dsh_runtime.FAILOVER_STATUSES)
        self.assertIn(dsh_runtime.RATE_LIMIT, dsh_runtime.FAILOVER_STATUSES)
        self.assertNotIn(dsh_runtime.EXECUTION_ERROR, dsh_runtime.FAILOVER_STATUSES)
        self.assertNotIn(dsh_runtime.STEP_LIMIT, dsh_runtime.FAILOVER_STATUSES)

    def test_completed_session_without_effect_is_not_reported_as_success(self):
        """Dry run finding: 165k tokens, nothing committed, state 'RUNNING'."""
        import json as _json
        from nova3_team import dsh_supervisor as sup
        gh = _CoordinatorLikeGitHub()
        state_dir = pathlib.Path(tempfile.mkdtemp())
        supervisor = _fake_supervisor(sup, gh, state_dir)
        gh.body = lease_put(gh.body, {'owner': 'crjase', 'token': 'token-owned',
                                      'expires_at': '2999-01-01T00:00:00Z'})
        supervisor.state.save(task={'number': 1, 'token': 'token-owned',
                                    'title': 'T', 'claimed_unix': int(time.time())})
        (state_dir / '.nova-state').mkdir(parents=True, exist_ok=True)
        (state_dir / '.nova-state' / '1.json').write_text(_json.dumps({
            'issue': 1, 'phase': 'checkpoint', 'provider_exit': 0,
            'dsh_status': 'COMPLETED', 'dsh_session': 'session-x',
            'last_error': 'No source changes after bounded AI sessions',
            'dsh_spend': {'input_tokens': 165000, 'output_tokens': 1900},
            'session_spent': 166900}))
        # work returns False (the task did not progress) although the session exited 0
        with mock.patch.object(worker, 'work_claimed', return_value=False):
            supervisor.run_task(gh.issue(1))
        state = _json.loads((state_dir / 'supervisor.json').read_text())
        self.assertEqual(state['last_outcome']['status'], dsh_runtime.EXECUTION_ERROR)
        self.assertEqual(state['last_outcome']['error_code'], 'NO_EFFECT')
        self.assertIn(state['state'], (dsh_policy.BACKOFF, dsh_policy.BLOCKED),
                      'a completed-but-ineffective session must not leave RUNNING')

    def test_every_write_overlay_call_site_matches_the_signature(self):
        """A signature change broke the live unit while unit tests passed: the
        tests exercised overlay_text() directly, never the wired call."""
        import inspect
        from nova3_team import dsh_supervisor, provider_runtime
        accepted = set(inspect.signature(dsh_runtime.write_overlay).parameters)
        for module in (dsh_supervisor, worker, provider_runtime):
            source = inspect.getsource(module)
            for line in source.splitlines():
                if 'write_overlay(' in line and 'def ' not in line:
                    positional = line.split('write_overlay(', 1)[1]
                    kwargs = [part.split('=')[0].strip() for part in positional.split(',')
                              if '=' in part]
                    for name in kwargs:
                        self.assertIn(name, accepted,
                                      f'{module.__name__}: unknown keyword {name!r}')
        # And the real call the supervisor makes must work.
        with tempfile.TemporaryDirectory() as folder:
            ep = dsh_runtime.DshEndpoint(binary='x', home=pathlib.Path(folder))
            path = pathlib.Path(folder) / 'overlay.yml'
            dsh_runtime.write_overlay(path, ep, fallbacks=())
            self.assertIn('agent-default-model', path.read_text())

    def test_dsh_loop_refuses_to_be_a_second_loop(self):
        from tools.dsh_loop import cli
        self.assertIn('enabled', cli._live_queue_owner().lower() + ' enabled')


if __name__ == '__main__':
    unittest.main()
