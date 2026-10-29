"""Offline tests for bounded multi-provider worker slots (issue #126).

No provider is launched, no network call is made and no paid model is called.
Linux, Windows and macOS lock/path behaviour is exercised through injected
lock APIs, so this file runs unchanged on every host.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tempfile
import unittest
from unittest import mock

from nova3_team import coordinator as co
from nova3_team import presence, worker
from nova3_team.common import iso, lease_put
from nova3_team.tests.test_worker import FakeGH


def candidate(provider, installed=True, authenticated=True):
    return worker.ProviderCandidate(provider, installed, authenticated)


class EventLog:
    def __init__(self):
        self.events = []

    def add(self, name):
        self.events.append(name)


class FakeGate:
    """Records ordering; optional preflight failure; decision per issue."""

    def __init__(self, allowed=True, *, fail_preflight=False, log=None):
        self.allowed = allowed
        self.fail_preflight = fail_preflight
        self.log = log or EventLog()
        self.verifications = []

    def preflight(self):
        self.log.add('gate.preflight')
        if self.fail_preflight:
            raise RuntimeError('pinned deployment identity missing')
        return 1

    def verify(self, issue):
        self.log.add(f"gate.verify:{issue['number']}")
        self.verifications.append(issue['number'])
        reason = 'allowed' if self.allowed else 'owner_approval_required'
        return co.QueueDecision(self.allowed, reason, 900 if self.allowed else None,
                                {'nonce': 'n', 'operation_sha256': '0'*64,
                                 'operation_arguments': {}} if self.allowed else None,
                                'a'*64 if self.allowed else None)


class FakeMsvcrt:
    """Minimal msvcrt.locking emulation keyed by file identity."""

    LK_NBLCK = 1
    LK_UNLCK = 0

    def __init__(self):
        self.held = set()
        self.calls = []

    def locking(self, descriptor, mode, nbytes):
        self.calls.append((mode, nbytes))
        key = os.fstat(descriptor).st_ino
        if mode == self.LK_NBLCK:
            if key in self.held:
                raise OSError(errno.EACCES, 'locked')
            self.held.add(key)
        else:
            self.held.discard(key)


class FakeFcntl:
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self):
        self.held = set()

    def flock(self, descriptor, operation):
        key = os.fstat(descriptor).st_ino
        if operation & self.LOCK_UN:
            self.held.discard(key)
            return
        if key in self.held:
            raise OSError(errno.EWOULDBLOCK, 'busy')
        self.held.add(key)


class IdentityAndPathTests(unittest.TestCase):
    def test_machine_uuid_is_stable_and_atomically_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = worker.machine_uuid(root)
            second = worker.machine_uuid(root)
            self.assertEqual(first, second)
            self.assertRegex(first, r'[0-9a-f]{32}')
            payload = json.loads((root/'identity'/'machine.json').read_text())
            self.assertEqual(payload['machine_id'], first)
            leftovers = [p for p in (root/'identity').iterdir() if p.suffix == '.tmp']
            self.assertEqual(leftovers, [])

    def test_identity_is_slot_scoped_and_refuses_silent_rebinding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one = worker.worker_identity(root, 'alice', 'slot-1', 'codex')
            two = worker.worker_identity(root, 'alice', 'slot-2', 'claude')
            self.assertEqual(one.machine_id, two.machine_id)
            self.assertNotEqual(one.worker_id, two.worker_id)
            self.assertEqual(one.worker_id, f'alice:{one.machine_id}:slot-1:codex')
            self.assertNotIn('token', json.dumps(one.sanitized()).casefold())
            with self.assertRaises(worker.IdentityError):
                worker.worker_identity(root, 'bob', 'slot-1', 'codex')
            with self.assertRaises(worker.IdentityError):
                worker.worker_identity(root, 'alice', 'slot-1', 'claude')

    def test_slot_paths_are_distinct_on_posix_and_windows(self):
        for root in (PurePosixPath('/home/alice/.cache/nova3-team/repo'),
                     PureWindowsPath(r'C:\Users\Alice\AppData\Local\nova3-team\repo')):
            with self.subTest(root=str(root)):
                one = worker.slot_paths(root, 'slot-1')
                two = worker.slot_paths(root, 'slot-2')
                for name in ('state', 'worktrees', 'cache', 'logs', 'identity', 'presence'):
                    self.assertNotEqual(str(getattr(one, name)), str(getattr(two, name)))
                    self.assertIn('slot-2', str(getattr(two, name)))
                self.assertTrue(worker.assert_slot_isolation(root, ['slot-1', 'slot-2']))
                self.assertNotEqual(str(one.cache), str(root/'.cache'))

    def test_invalid_slot_ids_fail_closed(self):
        for bad in ('', '../escape', 'Slot 1', 'slot/2', 'a'*25):
            with self.subTest(bad=bad), self.assertRaises(worker.SlotError):
                worker.validate_slot_id(bad)

    def test_legacy_recovery_migration_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root/'.nova-state'
            legacy.mkdir()
            (legacy/'7.json').write_text(json.dumps({'issue': 7, 'token': 'saved',
                                                     'phase': 'checkpoint'}))
            (root/'worker-failure.json').write_text(json.dumps({'exit_code': 3}))
            paths = worker.slot_paths(root, worker.DEFAULT_SLOT, create=True)
            report = worker.migrate_legacy_state(root, paths)
            self.assertEqual(sorted(report['migrated']), ['7', 'worker-failure'])
            self.assertTrue((legacy/'7.json').exists())
            copied = worker.recovery.load(paths.state, 7)
            self.assertEqual(copied['token'], 'saved')
            # Re-running migration never overwrites newer slot state.
            worker.recovery.update(paths.state, 7, token='newer')
            worker.migrate_legacy_state(root, paths)
            self.assertEqual(worker.recovery.load(paths.state, 7)['token'], 'newer')
            # Non-default slots do not inherit a second copy of legacy state.
            other = worker.slot_paths(root, 'slot-2', create=True)
            self.assertEqual(worker.migrate_legacy_state(root, other)['migrated'], [])
            self.assertEqual(worker.recovery.load(other.state, 7), {})


class LockTests(unittest.TestCase):
    def test_posix_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'slot.lock'
            first = worker.ProcessFileLock(path, backend='posix')
            second = worker.ProcessFileLock(path, backend='posix')
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_windows_lock_uses_msvcrt_and_blocks_second_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'slot.lock'
            api = FakeMsvcrt()
            first = worker.ProcessFileLock(path, backend='nt', api=api)
            second = worker.ProcessFileLock(path, backend='nt', api=api)
            self.assertTrue(first.acquire())
            self.assertEqual(os.fstat(first._descriptor).st_size, 1)
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()
            self.assertIn(FakeMsvcrt.LK_NBLCK, [mode for mode, _ in api.calls])

    def test_macos_uses_the_posix_flock_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'slot.lock'
            api = FakeFcntl()
            self.assertEqual(worker.lock_backend('darwin'), 'posix')
            first = worker.ProcessFileLock(path, platform_name='darwin', api=api)
            second = worker.ProcessFileLock(path, platform_name='darwin', api=api)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stale_lock_file_from_a_crash_is_reusable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'slot.lock'
            path.write_bytes(b'crash-left-behind')
            lock = worker.ProcessFileLock(path, backend='posix')
            self.assertTrue(lock.acquire())
            lock.release()
            self.assertFalse(worker.slot_is_active(Path(temp), 'slot-1'))

    def test_slot_and_work_id_collisions_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = worker.acquire_slot_lock(root, 'slot-1')
            with self.assertRaises(worker.LockHeld):
                worker.acquire_slot_lock(root, 'slot-1')
            self.assertTrue(worker.slot_is_active(root, 'slot-1'))
            worker.acquire_slot_lock(root, 'slot-2').release()
            first = worker.CheckoutGuard(root, 'slot-1')
            second = worker.CheckoutGuard(root, 'slot-2')
            first.claim('issue-7')
            with self.assertRaises(worker.WorkIdConflict):
                second.claim('issue-7')
            second.claim('issue-8')  # a different work id is fine
            self.assertNotEqual(first.checkout_path('issue-7'), second.checkout_path('issue-7'))
            first.release()
            second.release()
            lock.release()


class SelectionAndBudgetTests(unittest.TestCase):
    def test_default_is_one_process_and_more_needs_consent(self):
        candidates = [candidate('codex'), candidate('claude')]
        plan = worker.select_slot_providers(candidates)
        self.assertEqual(plan.total, 1)
        self.assertEqual([(s.slot, s.provider) for s in plan.slots], [('slot-1', 'codex')])
        with self.assertRaises(worker.SlotLimitError):
            worker.select_slot_providers(candidates, total=2)
        plan = worker.select_slot_providers(candidates, total=2, consent=True)
        self.assertEqual([s.provider for s in plan.slots], ['codex', 'claude'])
        self.assertEqual(worker.default_concurrency(), 1)

    def test_only_installed_and_authenticated_providers_are_selected(self):
        candidates = [candidate('codex', installed=True, authenticated=False),
                      candidate('claude', installed=False),
                      candidate('gemini')]
        with self.assertRaises(worker.SlotLimitError):
            worker.select_slot_providers(candidates, total=2, consent=True)
        plan = worker.select_slot_providers(candidates)
        self.assertEqual(plan.slots[0].provider, 'gemini')
        self.assertTrue(any('authentication unconfirmed' in note for note in plan.notes))
        self.assertTrue(any('not installed' in note for note in plan.notes))

    def test_duplicate_provider_needs_explicit_consent(self):
        candidates = [candidate('codex'), candidate('codex')]
        with self.assertRaises(worker.SlotLimitError):
            worker.select_slot_providers(candidates, total=2, consent=True)
        plan = worker.select_slot_providers(candidates, total=2, consent=True,
                                            allow_duplicate_provider=True)
        self.assertEqual([s.provider for s in plan.slots], ['codex', 'codex'])

    def test_hard_ceiling_and_resource_cap(self):
        with self.assertRaises(worker.SlotLimitError):
            worker.resource_budget(worker.MAX_SLOTS + 1)
        with self.assertRaises(worker.SlotLimitError):
            worker.select_slot_providers([candidate('codex')], total=worker.MAX_SLOTS + 1,
                                         consent=True)
        budget = worker.resource_budget(4, cores=4, memory_mb=3000)
        self.assertEqual(budget.cap(), 1)  # ~2 GiB per session, N5100 class
        generous = worker.resource_budget(2, cores=4, memory_mb=8192)
        self.assertEqual(generous.cap(), 2)

    def test_preview_is_shell_free_and_rejects_secrets(self):
        plan = worker.select_slot_providers([candidate('codex')])
        preview = worker.preview_launch(plan, python='/usr/bin/python3', repo='org/repo')[0]
        self.assertFalse(preview['shell'])
        self.assertEqual(preview['argv'][:4], ['/usr/bin/python3', '-m', 'nova3_team.worker', 'run'])
        self.assertIn('--slot', preview['argv'])
        with self.assertRaises(worker.SlotError):
            worker.assert_argv_safe(['codex', 'exec', 'ghp_' + 'A'*36])
        with self.assertRaises(worker.SlotError):
            worker.assert_argv_safe(['codex', '--password=hunter2'])
        with self.assertRaises(worker.SlotError):
            worker.assert_argv_safe(['codex', 'a\x00b'])

    def test_provider_candidates_use_bounded_probe_and_auth_only(self):
        class Status:
            available = True
            reason = 'help advertises flags'
        class Auth:
            state = worker.provider_runtime.AuthState.AUTHENTICATED
            reason = 'ok'
        with mock.patch.object(worker.provider_runtime, 'probe_provider', return_value=Status()) as probe, \
                mock.patch.object(worker.provider_runtime, 'check_auth', return_value=Auth()) as auth:
            rows = worker.provider_candidates(['codex'])
        probe.assert_called_once_with('codex')
        auth.assert_called_once_with('codex')
        self.assertEqual(rows, [worker.ProviderCandidate('codex', True, True,
                                                         'help advertises flags')])


class LaunchAuthorizationTests(unittest.TestCase):
    def plan(self, total=1, consent=False):
        candidates = [candidate('codex'), candidate('claude')]
        return worker.select_slot_providers(candidates, total=total, consent=consent)

    def test_approval_gate_is_consulted_before_any_slot_starts(self):
        log = EventLog()
        gate = FakeGate(log=log)
        issues = [{'number': 7}]
        calls = []

        def fake_popen(argv, **kwargs):
            log.add('popen:'+argv[argv.index('--slot')+1])
            calls.append((argv, kwargs))
            return object()

        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(total=2, consent=True)
            result = worker.launch_slots(plan, gate=gate, issues=issues,
                                         repository_root=Path(temp), account='alice',
                                         consent=True, popen=fake_popen,
                                         budget=worker.resource_budget(2, cores=4, memory_mb=8192))
        self.assertTrue(result.started)
        self.assertEqual(len(calls), 2)
        self.assertEqual(log.events, ['gate.preflight', 'gate.verify:7',
                                      'popen:slot-1', 'popen:slot-2'])
        for argv, kwargs in calls:
            self.assertIs(kwargs['shell'], False)
            self.assertEqual(kwargs['start_new_session'], os.name == 'posix')

    def test_launch_is_refused_without_gate_or_approved_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.plan()
            no_gate = worker.launch_slots(plan, gate=None, issues=[{'number': 7}],
                                          repository_root=root, popen=mock.Mock())
            self.assertFalse(no_gate.started)
            self.assertEqual(no_gate.reason, 'approval_gate_unavailable')
            denied = worker.launch_slots(plan, gate=FakeGate(allowed=False),
                                         issues=[{'number': 7}], repository_root=root,
                                         popen=mock.Mock())
            self.assertFalse(denied.started)
            self.assertEqual(denied.reason, 'no_approved_tasks')
            failed = worker.launch_slots(plan, gate=FakeGate(fail_preflight=True),
                                         issues=[{'number': 7}], repository_root=root,
                                         popen=mock.Mock())
            self.assertFalse(failed.started)
            self.assertEqual(failed.reason, 'approval_gate_unavailable')

    def test_dry_run_never_consults_gate_or_launches(self):
        plan = self.plan()
        gate = FakeGate()
        popen = mock.Mock()
        with tempfile.TemporaryDirectory() as temp:
            result = worker.launch_slots(plan, gate=gate, issues=[{'number': 7}],
                                         repository_root=Path(temp), dry_run=True,
                                         popen=popen)
        self.assertFalse(result.started)
        self.assertEqual(result.reason, 'dry_run')
        popen.assert_not_called()
        self.assertEqual(gate.verifications, [])

    def test_two_slots_cannot_claim_the_same_issue_locally(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = worker.CheckoutGuard(root, 'slot-1')
            second = worker.CheckoutGuard(root, 'slot-2')
            first.claim('7')
            with self.assertRaises(worker.WorkIdConflict):
                second.claim('7')
            # And a plan whose slot is already running is refused before spawn.
            held = worker.acquire_slot_lock(root, 'slot-1')
            popen = mock.Mock()
            try:
                result = worker.launch_slots(self.plan(), gate=FakeGate(),
                                             issues=[{'number': 7}],
                                             repository_root=root, popen=popen)
            finally:
                held.release()
            self.assertFalse(result.started)
            self.assertIn('local_guard', result.reason)
            popen.assert_not_called()

    def test_stale_slot_lock_file_does_not_block_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = worker.slot_lock_path(root, 'slot-1')
            lock_path.parent.mkdir(parents=True)
            lock_path.write_bytes(b'stale')
            result = worker.launch_slots(self.plan(), gate=FakeGate(),
                                         issues=[{'number': 7}], repository_root=root,
                                         popen=lambda argv, **kw: object())
            self.assertTrue(result.started)


class SchedulerTests(unittest.TestCase):
    def build(self, total=2, clock=None):
        scheduler = worker.SlotScheduler(total_processes=total,
                                         clock=clock or (lambda: 1000.0))
        scheduler.add('slot-1', 'codex', timeout_s=600)
        scheduler.add('slot-2', 'claude', timeout_s=900)
        return scheduler

    def test_cap_is_enforced_and_default_is_one(self):
        capped = worker.SlotScheduler(total_processes=2)
        capped.add('slot-1', 'codex')
        capped.add('slot-2', 'claude')
        with self.assertRaises(worker.SlotLimitError):
            capped.add('slot-3', 'gemini')
        scheduler = worker.SlotScheduler()
        scheduler.add('slot-1', 'codex')
        with self.assertRaises(worker.SlotLimitError):
            scheduler.add('slot-2', 'claude')
        with self.assertRaises(worker.SlotLimitError):
            worker.SlotScheduler(total_processes=worker.MAX_SLOTS + 1)

    def test_fair_round_robin_and_isolated_stop(self):
        scheduler = self.build()
        first = scheduler.next_ready()
        self.assertEqual(first.slot, 'slot-1')
        scheduler.start(first.slot, process='proc-a', now=1000)
        scheduler.start('slot-2', process='proc-b', now=1001)
        self.assertIsNone(scheduler.next_ready(now=1002))
        scheduler.mark_finished('slot-1', state='idle')
        self.assertEqual(scheduler.next_ready(now=1002).slot, 'slot-1')
        scheduler.pause('slot-2')
        self.assertEqual(scheduler.get('slot-2').state, 'paused')
        scheduler.resume('slot-2')
        self.assertEqual(scheduler.get('slot-2').state, 'idle')
        terminated = []
        scheduler.stop('slot-2', terminate=terminated.append)
        self.assertEqual(terminated, ['proc-b'])
        self.assertEqual(scheduler.get('slot-2').state, 'stopped')
        self.assertEqual(scheduler.get('slot-1').state, 'idle')

    def test_failures_cooldown_then_block_without_a_retry_storm(self):
        scheduler = self.build()
        scheduler.start('slot-1', now=1000)
        first = scheduler.record_failure('slot-1', 'provider crashed unexpectedly', now=1001)
        self.assertEqual(first['state'], 'cooldown')
        self.assertEqual(scheduler.next_ready(now=1002).slot, 'slot-2')  # other slot unaffected
        scheduler.get('slot-1').state = 'idle'
        scheduler.get('slot-1').cooldown_until = 0
        scheduler.start('slot-1', now=1003)
        second = scheduler.record_failure('slot-1', 'provider crashed unexpectedly', now=1004)
        self.assertEqual(second['state'], 'blocked')
        self.assertNotEqual(scheduler.next_ready(now=1005).slot, 'slot-1')
        self.assertEqual(scheduler.get('slot-2').state, 'idle')

    def test_rate_limit_uses_bounded_exponential_backoff(self):
        self.assertEqual([worker.rate_limit_backoff(n) for n in (1, 2, 3, 4)],
                         [300, 600, 1200, 2400])
        self.assertEqual(worker.rate_limit_backoff(50), 3600)
        scheduler = self.build()
        scheduler.start('slot-1', now=1000)
        decision = scheduler.record_rate_limit('slot-1', now=1001)
        self.assertEqual(decision, {'code': 'RATE_LIMITED', 'state': 'cooldown',
                                    'retry_after': 300})
        self.assertTrue(worker.is_rate_limit('HTTP 429 Too Many Requests'))
        self.assertTrue(worker.is_rate_limit('quota exceeded'))
        self.assertFalse(worker.is_rate_limit('merge conflict'))

    def test_crash_recovery_drops_stale_running_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scheduler = self.build()
            scheduler.start('slot-1', process='gone', now=1000)
            scheduler.record_rate_limit('slot-2', retry_after=600, now=1000)
            scheduler.save(root)
            restored = worker.SlotScheduler.load(root, ['slot-1', 'slot-2'],
                                                 total_processes=2, clock=lambda: 1100.0)
            self.assertEqual(restored.get('slot-1').state, 'idle')
            self.assertEqual(restored.get('slot-1').process, None)
            self.assertEqual(restored.get('slot-2').state, 'cooldown')
            self.assertGreater(restored.get('slot-2').cooldown_until, 1100)
            self.assertEqual(restored.next_ready(now=1100).slot, 'slot-1')
            self.assertEqual(restored.get('slot-2').state, 'cooldown')
            self.assertIsNotNone(restored.next_ready(now=2000))

    def test_telemetry_has_no_prompt_or_provider_output(self):
        scheduler = self.build()
        scheduler.start('slot-1', now=1000)
        scheduler.record_failure('slot-1', 'secret prompt text ghp_' + 'A'*36, now=1001)
        payload = json.dumps(scheduler.telemetry(now=1002))
        self.assertNotIn('secret prompt text', payload)
        self.assertNotIn('ghp_', payload)
        self.assertEqual(set(scheduler.telemetry()['slots'][0]),
                         {'slot', 'provider', 'state', 'failures', 'failure_code',
                          'cooldown_s', 'timeout_s'})


class PresenceAndLoggingTests(unittest.TestCase):
    def test_presence_is_per_slot_and_body_is_sanitized(self):
        calls = []

        def fake_command(args, payload=None, timeout=18):
            calls.append((list(args), payload))
            if 'list' in args:
                return [{'number': 5, 'title': 'NOVA3 — contributor worker presence'}]
            if 'comments?per_page=100' in ' '.join(args):
                return []
            if 'POST' in args:
                return {'id': 11}
            return {}

        with tempfile.TemporaryDirectory() as temp:
            one = presence.Reporter('org/repo', 'alice', 'codex', state_dir=Path(temp),
                                    command=fake_command, slot_id='slot-1',
                                    machine_id='a'*32)
            two = presence.Reporter('org/repo', 'alice', 'claude', state_dir=Path(temp),
                                    command=fake_command, slot_id='slot-2',
                                    machine_id='a'*32)
            self.assertNotEqual(one.path, two.path)
            self.assertIn('slot-1', one.path.name)
            one.publish()
        bodies = [payload['body'] for args, payload in calls
                  if payload and 'body' in payload and 'POST' in args]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        self.assertIn('"slot":"slot-1"', body)
        self.assertIn('"v":2', body)
        self.assertNotIn('token', body.casefold())
        self.assertEqual(presence.aggregate([one, two]), [
            {'worker': one.worker, 'slot': 'slot-1', 'provider': 'codex',
             'state': one.state, 'issue': None, 'machine': 'a'*32},
            {'worker': two.worker, 'slot': 'slot-2', 'provider': 'claude',
             'state': two.state, 'issue': None, 'machine': 'a'*32}])

    def test_failure_log_redacts_secrets(self):
        secret = 'ghp_' + 'A'*36
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(worker, 'note'):
            payload = worker.record_worker_failure(Path(temp), 3,
                                                   f'unauthorized token {secret} present')
        self.assertNotIn(secret, payload['cause'])
        self.assertNotIn(secret, json.dumps(payload))

    def test_slot_state_file_is_owner_only_on_posix(self):
        if os.name != 'posix':
            self.skipTest('POSIX permission semantics only')
        with tempfile.TemporaryDirectory() as temp:
            worker.worker_identity(Path(temp), 'alice', 'slot-1', 'codex')
            mode = stat.S_IMODE((Path(temp)/'.nova-slots/slot-1/identity/worker.json').stat().st_mode)
            self.assertEqual(mode, 0o600)


class LeaseIntegrationTests(unittest.TestCase):
    def test_same_issue_double_claim_still_uses_the_github_lease(self):
        """The local guard is additional; the coordinator lease remains primary."""
        from datetime import timedelta
        from nova3_team.common import now
        token_a = 'a'*36
        lease = {'owner': 'alice', 'token': token_a,
                 'expires_at': iso(now() + timedelta(minutes=30))}
        issue = {'number': 7, 'body': lease_put('task', lease)}
        gh = FakeGH(issue)
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(worker, 'note'):
                token = worker.claim(gh, issue, 'bob', maxwait=0)
        self.assertIsNone(token)
        self.assertEqual(len(gh.comments), 1)
        self.assertTrue(gh.comments[0].startswith('/nova claim '))

    def test_stale_lease_allows_a_fresh_claim_request(self):
        expired = {'owner': 'alice', 'token': 'a'*36,
                   'expires_at': '2000-01-01T00:00:00Z'}
        issue = {'number': 8, 'body': lease_put('task', expired)}
        gh = FakeGH(issue)
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(worker, 'note'):
            token = worker.claim(gh, issue, 'alice', maxwait=0, saved={'token': 'a'*36})
        self.assertIsNone(token)
        self.assertEqual(len(gh.comments), 1)
        self.assertNotIn('heartbeat', gh.comments[0])


class LauncherSelectionTests(unittest.TestCase):
    def test_guided_selection_preview_and_consent(self):
        import nova3_easy
        lines = []
        candidates = [candidate('codex'), candidate('claude'),
                      candidate('gemini', installed=False)]
        plan, previews = nova3_easy.select_worker_slots(
            'org/repo', total=2, consent=True, candidates=candidates,
            python='/usr/bin/python3', out=lines.append)
        self.assertEqual([s.provider for s in plan.slots], ['codex', 'claude'])
        self.assertEqual(len(previews), 2)
        self.assertTrue(any('not installed' in line for line in lines))
        nova3_easy.print_slot_preview(previews, out=lines.append)
        self.assertTrue(any('Planned AI slots' in line for line in lines))
        self.assertTrue(any('shell=False' in line for line in lines))
        with self.assertRaises(RuntimeError):
            nova3_easy.select_worker_slots('org/repo', total=2, consent=False,
                                           candidates=candidates, out=lines.append)

    def test_unknown_provider_is_rejected_before_any_probe(self):
        import nova3_easy
        with self.assertRaises(RuntimeError):
            nova3_easy.slot_provider_list('codex,not-a-provider')
        self.assertEqual(nova3_easy.slot_provider_list('codex,claude'), ['codex', 'claude'])
        self.assertIsNone(nova3_easy.slot_provider_list(''))


if __name__ == '__main__':
    unittest.main()
