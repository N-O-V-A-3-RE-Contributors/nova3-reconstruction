"""Offline acceptance tests for issue #137: NOVA3 Control Center.

No network, no paid model calls, no real systemd/journal mutation. Curses is a
deterministic in-memory surface; GitHub and systemd producers are fake values.
"""
import curses
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from test_ui_refresh import CellScreen, o

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def claimed(age=5, number=134):
    stamp = (NOW - timedelta(seconds=age)).isoformat()
    return {'number': number, 'title': 'synthetic approved task', 'state': 'OPEN',
            'labels': [{'name': 'nova:claimed'}], 'updatedAt': stamp}


def activity(age=120, status='TESTING', commands=8):
    stamp = (NOW - timedelta(seconds=age)).isoformat()
    return {'state': 'OBSERVED', 'limited': False, 'rejected': 0, 'records': [{
        'issue': 134, 'round': 1, 'timestamp_utc': stamp, 'status': status,
        'event_category': 'test', 'counts_complete': True, 'structured_events': True,
        'last_activity_age_seconds': 2, 'commands_started': commands,
        'commands_completed': commands, 'tests_started': 8, 'tests_completed': 8,
        'file_changed_count': 3}]}


def status(service=None, act=None, lease_age=5, log=None, now=NOW):
    service = service or {'active': 'active', 'sub': 'running', 'pid': '4242',
                          'exit_status': '0', 'result': 'success',
                          'active_enter': (now - timedelta(minutes=4)).isoformat()}
    return o.live_status.collect_live_status(
        SimpleNamespace(service=service, issues=[claimed(lease_age)],
                        worker_presence=[], log_health=log if log is not None else {'bytes': 8192,
                        'delta': 64, 'changed_at': (now - timedelta(seconds=4)).isoformat()},
                        activity=act if act is not None else activity()),
        now)


class LiveTruthTests(unittest.TestCase):
    def test_eight_of_eight_commands_and_two_minute_quiet_stays_running(self):
        model = status(act=activity(age=120, commands=8))
        self.assertEqual(model['process']['status'], 'RUNNING')
        self.assertEqual(model['health']['verdict'], 'RUNNING')
        self.assertIn('STRUCTURED-EVENT QUIET', model['health']['badges'])
        self.assertTrue(model['health']['structured_quiet'])
        self.assertIn('process still running', ' '.join(model['health']['notes']))
        self.assertEqual(model['feed']['age_seconds'], 120)
        rendered = '\n'.join(text for text, _ in o.live_status.summary_rows(model))
        self.assertIn('PROCESS RUNNING', rendered)
        self.assertIn('STRUCTURED FEED QUIET', rendered)

    def test_true_failure_keeps_original_exit_reason(self):
        model = status(service={'active': 'failed', 'sub': 'failed', 'pid': '0',
                                'exit_status': '5', 'result': 'exit-code'})
        self.assertEqual(model['health']['verdict'], 'FAILED')
        self.assertIn('exit 5', ' '.join(model['health']['notes']))
        self.assertNotIn('STRUCTURED-EVENT QUIET', model['health']['badges'])

    def test_expired_lease_is_independent_of_process_and_feed(self):
        model = status(lease_age=3600, act=activity(age=5))
        self.assertEqual(model['lease']['state'], 'CLAIM STALE')
        self.assertFalse(model['lease']['fresh'])
        self.assertEqual(model['feed']['state'], 'LIVE')
        self.assertEqual(model['health']['verdict'], 'RUNNING')

    def test_missing_log_and_missing_feed_have_provenance(self):
        model = status(act={'state': 'MISSING', 'records': []}, log={})
        self.assertEqual(model['provider']['state'], 'MISSING')
        self.assertEqual(model['feed']['state'], 'MISSING')
        self.assertIn('LOG MISSING', model['health']['badges'])
        self.assertEqual(model['provider']['source'], 'local systemd journal bytes')
        self.assertIn('round-*.activity.json', model['feed']['source'])

    def test_possible_stall_needs_all_three_quiet_signals(self):
        model = status(lease_age=3600, act=activity(age=1800),
                       log={'bytes': 10, 'delta': 0,
                            'changed_at': (NOW - timedelta(minutes=30)).isoformat()})
        self.assertTrue(model['health']['stalled'])
        self.assertEqual(model['health']['verdict'], 'RUNNING')
        self.assertIn('POSSIBLE STALL', model['health']['badges'])


def snapshot(**changes):
    base = dict(
        repo_role='ADMIN',
        service={'active': 'active', 'sub': 'running', 'pid': '4242',
                 'exit_status': '0', 'result': 'success',
                 'active_enter': (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()},
        issues=[claimed(5)], activity=activity(age=120), log_health={
            'bytes': 900, 'delta': 10, 'changed_at': datetime.now(timezone.utc).isoformat()},
        github_updated=datetime.now(timezone.utc).isoformat())
    base.update(changes)
    return o.Snapshot(**base)


def ui(role='ADMIN', snap=None, width=80, height=24, demo=False):
    snap = snap or snapshot(repo_role=role)
    collector = SimpleNamespace(
        repo='fixture/control', service='fixture.service', demo=demo,
        lock=threading.Lock(), snapshot=snap, merge_preview=None,
        perform_action=mock.Mock(), execute_merge=mock.Mock(),
        prepare_merges=mock.Mock(), force_refresh=mock.Mock())
    view = o.Observatory(collector)
    view.snap, view.stdscr, view.unicode = snap, CellScreen(width, height), True
    return view


class RoleAndLayoutTests(unittest.TestCase):
    def test_contributor_cannot_reach_or_execute_administration(self):
        for role in ('MAINTAIN', 'WRITE', 'TRIAGE', 'READ', 'UNKNOWN', 'REVOKED'):
            view = ui(role)
            self.assertNotIn(9, view.visible_tabs())
            view.handle(ord('0'))
            self.assertEqual(view.tab, 0)
            self.assertIn('ADMINISTRATION LOCKED', view.notice)
            view.tab = 9
            view.render()  # hidden pages redirect instead of exposing controls
            self.assertEqual(view.tab, 0)
            for key in 'dDnNuUpPcCiIjJfF':
                view.handle(ord(key))
            view.collector.prepare_merges.assert_not_called()
            view.collector.perform_action.assert_not_called()
            view.collector.execute_merge.assert_not_called()

    def test_revoked_between_render_and_action_fails_closed(self):
        view = ui('ADMIN', width=120, height=40)
        view.tab = 9
        view.render()
        self.assertIn('ADMINISTRATION', view.stdscr.text())
        view.snap.repo_role = 'READ'  # server revocation after render
        view.handle(ord('c'))
        self.assertIn('locked', view.notice.lower())
        view.collector.prepare_merges.assert_not_called()
        view.handle(ord('f'))
        self.assertIn('locked', view.notice.lower())
        view.collector.perform_action.assert_not_called()
        view.render()
        self.assertFalse(view.is_owner())
        self.assertNotIn('[0:ADMINISTRATION]', view.stdscr.text())

    def test_owner_status_object_wins_over_stale_role_label(self):
        snap = snapshot(repo_role='ADMIN',
                        owner_status={'allowed': False, 'role': 'ADMIN', 'reason': 'revoked'})
        view = ui(snap=snap)
        self.assertFalse(view.is_owner())
        self.assertNotIn(9, view.visible_tabs())

    def test_compact_and_panoramic_keep_activity_and_accounts(self):
        for width, height in ((80, 24), (192, 54)):
            view = ui('ADMIN', width=width, height=height)
            view.tab = 12
            view.render()
            text = view.stdscr.text()
            self.assertIn('LIVE WORKER STATUS', text)
            self.assertIn('RUNNING', text)
            self.assertIn('structured-event quiet', text.lower())
            if width >= 100:
                self.assertIn('INDEPENDENT SIGNALS / PRIVACY', text)
            view.tab = o.ACCOUNTS_TAB
            view.render()
            text = view.stdscr.text()
            self.assertIn('AI PROVIDER ACCOUNTS', text)
            self.assertIn('GITHUB IDENTITY / MEMBERS', text)
            self.assertIn('[U:ACCOUNTS]', text)

    def test_owner_admin_page_hidden_and_no_keyboard_regression(self):
        admin = ui('ADMIN')
        self.assertIn(9, admin.visible_tabs())
        for tab, key in enumerate(o.TAB_KEYS):
            admin.tab = 0
            admin.handle(ord(key))
            self.assertEqual(admin.tab, tab)
        contributor = ui('READ')
        visible = contributor.visible_tabs()
        self.assertEqual(visible, [i for i in range(15) if i != 9])
        for tab in visible:
            contributor.tab = tab
            contributor.render()
            self.assertIn('CONTROL CENTER', contributor.stdscr.text())


class BackendAuthorityTests(unittest.TestCase):
    repo = 'N-O-V-A-3-RE-Contributors/nova3-reconstruction'

    def test_admin_only_and_org_owner_seam(self):
        ops = o.operator_ops
        self.assertEqual(ops.OWNER_ROLES, frozenset({'ADMIN'}))
        for role in ('MAINTAIN', 'WRITE', 'TRIAGE', 'READ', 'UNKNOWN'):
            result = ops.owner_access(self.repo, permission_reader=lambda _: role,
                                      account_reader=lambda: 'contributor',
                                      org_reader=lambda *_: 'MEMBER')
            self.assertFalse(result['allowed'], role)
        admin = ops.owner_access(self.repo, permission_reader=lambda _: 'ADMIN')
        self.assertTrue(admin['allowed'])
        owner = ops.owner_access(self.repo, permission_reader=lambda _: 'WRITE',
                                 account_reader=lambda: 'crjase',
                                 org_reader=lambda *_: 'admin')
        self.assertTrue(owner['allowed'])
        self.assertTrue(owner['organization_owner'])

    def test_api_failure_and_invalid_repo_fail_closed(self):
        ops = o.operator_ops
        def broken(_):
            raise ops.CommandFailure('network down')
        self.assertFalse(ops.owner_access(self.repo, permission_reader=broken)['allowed'])
        self.assertFalse(ops.owner_access('../escape')['allowed'])
        with mock.patch.object(ops, 'repo_permission', side_effect=ops.CommandFailure('offline')):
            with self.assertRaises(ops.CommandFailure):
                ops.require_owner_access(self.repo)

    def test_every_mutating_operation_rechecks_authority(self):
        ops = o.operator_ops
        preview = ops.MergePreview(account='x')
        with mock.patch.object(ops, 'require_owner_access') as check:
            check.side_effect = ops.CommandFailure('revoked')
            for call in (lambda: ops.make_preview(self.repo),
                         lambda: ops.merge_batch(self.repo, preview),
                         lambda: ops.start_discovery_scan(self.repo),
                         lambda: ops.toggle_discovery_timer(True, self.repo),
                         lambda: ops.export_diagnostics(Path('/tmp/nova-control-x'), Path('/tmp'),
                                                        self.repo)):
                with self.assertRaises(ops.CommandFailure):
                    call()
        self.assertGreaterEqual(check.call_count, 5)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        base = self.home / '.cache/nova3-team/fixture_control/.nova-private'
        self.checkout = base / '134-abc123'
        self.checkout.mkdir(parents=True)
        self.log = self.checkout / 'round-1.log'
        self.log.write_text('working\nAuthorization: Bearer supersecretvalue123\n'
                            'token=ghp_abcdefghijklmnop\nordinary line\n')

    def test_bounded_redacted_read(self):
        inspector = o.journal_inspector
        options = inspector.candidates('fixture/control', home=self.home)
        self.assertEqual([item['name'] for item in options],
                         ['134-abc123/round-1.log'])
        report = inspector.read('fixture/control', options[0]['name'], home=self.home)
        self.assertIn('ordinary line', report['text'])
        self.assertNotIn('supersecretvalue123', report['text'])
        self.assertNotIn('ghp_abcdefghijklmnop', report['text'])
        self.assertIn('<redacted', report['text'])

    def test_traversal_absolute_and_symlink_rejected(self):
        inspector = o.journal_inspector
        for name in ('../round-1.log', '/etc/passwd', '134-abc123/../round-1.log',
                     'x/round-1.log', '134-abc123/round-1.log/extra'):
            with self.assertRaises(ValueError):
                inspector.read('fixture/control', name, home=self.home)
        (self.checkout / 'round-2.log').symlink_to('/etc/passwd')
        names = [item['name'] for item in inspector.candidates('fixture/control', home=self.home)]
        self.assertNotIn('134-abc123/round-2.log', names)

    def test_only_owner_can_open_journal_and_confirmation_is_explicit(self):
        view = ui('READ')
        view.collector.home = self.home
        view.tab = 12
        view.handle(ord('J'))
        self.assertIsNone(view.confirm)
        self.assertIn('JOURNAL LOCKED', view.notice)
        admin = ui('ADMIN')
        admin.collector.home = self.home
        admin.tab = 12
        admin.handle(ord('J'))
        self.assertEqual(admin.confirm, 'journal-open')
        self.assertIsNone(admin.journal_view)
        admin.handle(ord('y'))  # explicit consent
        self.assertIsNotNone(admin.journal_view)
        admin.render()
        self.assertIn('LOCAL PRIVATE JOURNAL (REDACTED)', admin.stdscr.text())
        self.assertNotIn('supersecretvalue123', admin.stdscr.text())
        admin.handle(27)
        self.assertIsNone(admin.journal_view)


class OnboardingAndBrandTests(unittest.TestCase):
    def test_contributor_and_admin_have_separate_guidance(self):
        ob = o.onboarding
        contrib = ob.steps_for(ob.CONTRIBUTOR, {})
        admin = ob.steps_for(ob.ADMIN, {})
        self.assertTrue(any('own AI provider' in s['title'] for s in contrib))
        self.assertFalse(any(s['id'] == 'queue' for s in contrib))
        self.assertTrue(any(s['id'] == 'queue' for s in admin))
        self.assertEqual(ob.steps_for(ob.REVOKED, {}), [])
        self.assertFalse(ob.owner_action_allowed(ob.CONTRIBUTOR))
        self.assertFalse(ob.owner_action_allowed(ob.ADMIN, {'allowed': False}))

    def test_installer_refuses_broken_bundle_and_preserves_legacy_state(self):
        import shutil
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / 'install.sh'
            shutil.copyfile(root / 'install.sh', broken)
            result = subprocess.run(['bash', str(broken)], capture_output=True, text=True,
                                    timeout=20, cwd=tmp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing observer.py', result.stderr)
        install = (root / 'install.sh').read_text()
        self.assertIn('OLD_TARGET="$DATA_HOME/nova3-observatory"', install)
        self.assertIn('STAGING="$DATA_HOME/.nova3-control-center.stage.$$"', install)
        self.assertNotIn('rm -rf -- "$OLD_TARGET"', install)
        self.assertLess(install.index('--demo --once'), install.index('install -d -m 700 "$TARGET"'))

    def test_rebrand_strings_and_legacy_aliases(self):
        root = Path(__file__).resolve().parents[1]
        observer = (root / 'observer.py').read_text()
        install = (root / 'install.sh').read_text()
        self.assertIn('NOVA3 // CONTROL CENTER', observer)
        self.assertNotIn('NOVA3 // OBSERVATORY', observer)
        self.assertIn('nova3-control', install)
        self.assertIn('nova3-observer', install)
        self.assertIn('nova3-control-center', install)
        self.assertIn('nova3-observatory', install)  # legacy path preserved
        self.assertIn('structured-event-quiet', install)


if __name__ == '__main__':
    unittest.main()
