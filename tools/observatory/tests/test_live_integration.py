"""Synthetic local metadata only: no providers, GitHub, or proprietary fixtures."""
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nova_observer_live_tests', HERE / 'observer.py')
o = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = o
spec.loader.exec_module(o)
NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
REPO = 'example/project'


def issue(number=111, stage='claimed', state='OPEN'):
    return {'number': number, 'title': 'synthetic task', 'state': state,
            'labels': [{'name': 'nova:' + stage}]}


def summary(**changes):
    data = dict(schema_version=1, timestamp_utc=NOW.isoformat(), status='TESTING',
                event_category='test', last_activity_age_seconds=2,
                commands_started=4, commands_completed=3, tests_started=2,
                tests_completed=1, file_changed_count=None, counts_complete=True,
                structured_events=True)
    data.update(changes)
    return data


def packet(data=None, issue_number=111, round_number=1):
    return {'state': 'OBSERVED', 'records': [o._activity_summary(data or summary(), issue_number, round_number)]}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.base = self.home / '.cache/nova3-team/example_project/.nova-private'
        self.folder = self.base / '111-abc123'
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'round-1.activity.json'

    def write(self, data=None):
        self.path.write_text(json.dumps(data or summary()))
        os.utime(self.path, (NOW.timestamp(), NOW.timestamp()))

    def collect(self):
        return o.collect_activity(REPO, home=self.home, now=NOW)

    def test_fresh_and_only_whitelisted_fields_leave_consumer(self):
        secret = 'secret-token /private/home command arguments reasoning'
        self.write(summary(command=secret, text=secret, reasoning=secret, private_path=secret))
        # A raw log beside the summary must never be opened.
        (self.folder / 'round-1.log').write_text(secret)
        opened = []
        original = o.os.open
        def record(path, *args, **kwargs):
            opened.append(str(path))
            return original(path, *args, **kwargs)
        with patch.object(o.os, 'open', side_effect=record):
            result = self.collect()
        self.assertEqual(result['state'], 'OBSERVED')
        self.assertEqual(len(result['records']), 1)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(str(self.home), json.dumps(result))
        self.assertFalse(any(name.endswith('.log') for name in opened))
        snap = o.Snapshot(activity=result, issues=[issue()])
        self.assertIn('#111 r1 TESTING / test', o.activity_line(snap, NOW))
        self.assertTrue(o.activity_records(snap, NOW)[0]['active'])

    def test_missing_or_older_provider_is_unknown_and_no_raw_fallback(self):
        (self.folder / 'round-1.log').write_text('not for display')
        self.assertEqual(self.collect()['records'], [])
        self.write({'schema_version': 0})
        result = self.collect()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['rejected'], 1)
        self.assertIn('AI UNKNOWN', o.activity_line(o.Snapshot(activity=result), NOW))
        self.assertEqual(o.collect_activity(REPO, home=self.home / 'absent', now=NOW)['state'], 'MISSING')

    def test_malformed_json_enums_counts_and_depth_fail_safely(self):
        bad = ['{', '[' * 2000 + '0' + ']' * 2000, 'null',
               json.dumps(summary(status='secret')),
               json.dumps(summary(event_category=['secret'])),
               json.dumps(summary(commands_started=True)),
               json.dumps(summary(tests_completed=-1)),
               json.dumps(summary(timestamp_utc='invalid')),
               json.dumps(summary(timestamp_utc='2026-10-29T10:00:00')),
               json.dumps(summary(timestamp_utc='2026-10-29T10:00:00+11:00')),
               json.dumps(summary(counts_complete='yes')),
               json.dumps(summary(schema_version=True))]
        for value in bad:
            with self.subTest(value=value[:80]):
                self.path.write_text(value)
                result = self.collect()
                self.assertEqual(result['records'], [])
                self.assertEqual(result['rejected'], 1)

    def test_opaque_provider_stays_unknown(self):
        self.write(summary(structured_events=False))
        result = self.collect()['records'][0]
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['event_category'], 'unknown')

    def test_symlink_file_directory_and_ancestor_rejected(self):
        outside = self.home / 'outside.json'
        outside.write_text(json.dumps(summary()))
        self.path.symlink_to(outside)
        self.assertEqual(self.collect()['records'], [])
        self.path.unlink()
        self.folder.rmdir()
        self.folder.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(self.collect()['records'], [])
        self.folder.unlink()
        self.base.rmdir()
        self.base.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(self.collect()['state'], 'UNAVAILABLE')
        self.assertEqual(o.collect_activity('../escape', home=self.home, now=NOW)['state'], 'UNAVAILABLE')

    def test_fifo_and_hardlink_rejected_without_reading(self):
        os.mkfifo(self.path)
        result = self.collect()
        self.assertEqual(result['records'], [])
        self.assertEqual(result['rejected'], 1)
        self.path.unlink()
        self.write()
        os.link(self.path, self.home / 'hardlink')
        self.assertEqual(self.collect()['records'], [])

    def test_oversized_and_expired_files_not_parsed(self):
        self.path.write_bytes(b'x' * (o.ACTIVITY_MAX_BYTES + 1))
        with patch.object(o.json, 'loads', side_effect=AssertionError('must not parse')):
            self.assertEqual(self.collect()['records'], [])
        self.write()
        old = NOW.timestamp() - o.ACTIVITY_KEEP_SECONDS - 1
        os.utime(self.path, (old, old))
        with patch.object(o.json, 'loads', side_effect=AssertionError('expired')):
            self.assertEqual(self.collect()['records'], [])
        self.write(summary(timestamp_utc=(NOW-timedelta(days=8)).isoformat()))
        self.assertEqual(self.collect()['records'], [])

    def test_entry_checkout_file_and_retained_record_limits(self):
        self.write()
        for n in range(2, 7):
            (self.folder / f'round-{n}.activity.json').write_text(json.dumps(summary()))
        with patch.object(o, 'ACTIVITY_MAX_FILES', 2):
            result = self.collect()
            self.assertTrue(result['limited'])
            self.assertLessEqual(len(result['records']), 2)
        with patch.object(o, 'ACTIVITY_MAX_ENTRIES', 2):
            result = self.collect()
            self.assertTrue(result['limited'])
            self.assertLessEqual(len(result['records']), 1)
        with patch.object(o, 'ACTIVITY_MAX_RECORDS', 2):
            self.assertEqual(len(self.collect()['records']), 2)
        for n in range(112, 116):
            (self.base / f'{n}-abc').mkdir()
        with patch.object(o, 'ACTIVITY_MAX_CHECKOUTS', 1):
            self.assertTrue(self.collect()['limited'])

    def test_no_other_repo_or_nested_directory_scan(self):
        self.write()
        other = self.base.parent.parent / 'unselected' / '.nova-private' / '112-abc'
        other.mkdir(parents=True)
        (other / 'round-1.activity.json').write_text(json.dumps(summary()))
        nested = self.folder / 'nested'
        nested.mkdir()
        (nested / 'round-2.activity.json').write_text(json.dumps(summary()))
        self.assertEqual(len(self.collect()['records']), 1)

    def test_stale_future_past_task_and_superseded_are_not_active(self):
        for data in (summary(timestamp_utc=(NOW-timedelta(seconds=91)).isoformat()),
                     summary(timestamp_utc=(NOW+timedelta(seconds=1)).isoformat())):
            record = o.activity_records(o.Snapshot(activity=packet(data), issues=[issue()]), NOW)[0]
            self.assertFalse(record['active'])
            self.assertIn(record['display_status'], ('STALE', 'UNKNOWN'))
        for stage in ('integrated', 'verified'):
            record = o.activity_records(o.Snapshot(activity=packet(), issues=[issue(stage=stage)]), NOW)[0]
            self.assertFalse(record['active'])
            self.assertEqual(record['task_relation'], 'PAST TASK')
        activity = packet(summary(timestamp_utc=(NOW-timedelta(seconds=1)).isoformat()))
        activity['records'] += packet(summary(status='FINISHED'), round_number=2)['records']
        records = o.activity_records(o.Snapshot(activity=activity, issues=[issue()]), NOW)
        self.assertFalse(any(r['active'] for r in records))
        self.assertIn('SUPERSEDED', [r['task_relation'] for r in records])
        # Provider writes have second resolution: higher rounds win ties.
        activity = packet()
        activity['records'] += packet(summary(status='FINISHED'), round_number=2)['records']
        records = o.activity_records(o.Snapshot(activity=activity, issues=[issue()]), NOW)
        self.assertFalse(any(r['active'] for r in records))

    def test_newest_active_precedes_more_recent_completed_task(self):
        activity = packet(summary(timestamp_utc=(NOW-timedelta(seconds=1)).isoformat()))
        activity['records'] += packet(summary(status='FINISHED'), issue_number=110)['records']
        snap = o.Snapshot(activity=activity, issues=[issue(), issue(110, 'integrated')])
        self.assertIn('AI observed #111', o.activity_line(snap, NOW))
        self.assertIn('AI recent', o.activity_line(snap, NOW+timedelta(seconds=92)))


class ForecastIntegrationTests(unittest.TestCase):
    def snap(self, **kwargs):
        return o.Snapshot(github_updated=NOW.isoformat(), **kwargs)

    def test_no_matches_no_history_and_incomplete_queue_are_unknown(self):
        snap = self.snap(issues=[issue()], inventory={'available': True, 'count': 2400, 'integrated': 25, 'verified': 0})
        result = o.forecast_model.forecast(o.normalized_forecast(snap, NOW))
        self.assertEqual(result['whole_game']['label'], 'UNKNOWN')
        self.assertIn('zero_verified_original_matches', result['whole_game']['reasons'])
        self.assertEqual(result['queue']['sample_size'], 0)
        self.assertEqual(result['queue']['confidence'], 'none')
        self.assertIn('incomplete_queue_snapshot', result['queue']['reasons'])
        self.assertIsNone(result['queue']['all_supplied_queue_seconds'])
        self.assertEqual(result['setup']['state'], 'unknown')
        self.assertEqual(result['source_matching']['sample_size'], 0)

    def test_integrated_issues_do_not_become_verified_matches(self):
        for verified in (0, 25):
            snap = self.snap(issues=[issue(stage='integrated')], inventory={'available': True, 'verified': verified})
            s = o.normalized_forecast(snap, NOW)
            self.assertEqual(s.tasks[0].stage, 'done')
            self.assertEqual(s.history, ())
            self.assertEqual(s.milestones, ())
            self.assertIsNone(s.matching.inventory_functions)
            self.assertIsNone(o.forecast_model.forecast(s)['whole_game']['eta_seconds'])
            if verified:
                self.assertIsNone(s.matching.verified_original_matches)

    def test_observed_stage_pr_and_ci_negative_signals(self):
        for rows, expected in (([{'status': 'IN_PROGRESS'}], 'pending'),
                               ([{'conclusion': 'FAILURE'}], 'failed'),
                               ([{'conclusion': 'SUCCESS'}], 'unknown'), ([], 'unknown')):
            snap = self.snap(issues=[issue(stage='review')], prs=[{
                'state': 'OPEN', 'isDraft': True, 'closingIssuesReferences': [{'number': 111}],
                'statusCheckRollup': rows}])
            s = o.normalized_forecast(snap, NOW)
            self.assertEqual(s.tasks[0].stage, 'review')
            self.assertEqual(s.prs[0].state, 'open')
            self.assertTrue(s.prs[0].draft)
            self.assertEqual(s.ci[0].state, expected)
            out = o.forecast_model.forecast(s)['queue']
            self.assertIn('draft_pr', out['blocked_tasks']['111'])
            self.assertIn('ci_' + expected, out['blocked_tasks']['111'])

    def test_blockers_closed_unknown_stage_stale_snapshot(self):
        snap = self.snap(issues=[issue(stage='blocked'), issue(112, state='CLOSED')])
        s = o.normalized_forecast(snap, NOW+timedelta(seconds=901))
        out = o.forecast_model.forecast(s)['queue']
        self.assertIn('task_blocked', out['blocked_tasks']['111'])
        self.assertIn('unreconciled_task_stage', out['blocked_tasks']['112'])
        self.assertIn('stale_or_future_snapshot', out['reasons'])

    def test_worker_signals_do_not_invent_start_times_or_history(self):
        snap = self.snap(issues=[issue()], activity=packet(), history=[{'merged': 100}])
        s = o.normalized_forecast(snap, NOW)
        self.assertEqual(s.workers[0].task_id, '111')
        self.assertEqual(s.workers[0].state, 'active')
        self.assertIsNone(s.tasks[0].stage_started_at)
        self.assertEqual(s.history, ())
        self.assertEqual(s.sessions, ())
        self.assertEqual(o.normalized_forecast(snap, NOW+timedelta(seconds=91)).workers[0].state, 'offline')

    def test_empty_queue_is_unknown_not_zero(self):
        out = o.forecast_model.forecast(o.normalized_forecast(self.snap(), NOW))
        self.assertIsNone(out['queue']['all_supplied_queue_seconds'])
        self.assertEqual(out['queue']['state'], 'unknown')

    def test_model_record_limit_and_ambiguous_prs_fail_closed(self):
        snap = self.snap(issues=[issue(n) for n in range(1, 1001)], prs=[{
            'state': 'OPEN', 'closingIssuesReferences': [{'number': n} for n in range(1, 1001)]}])
        s = o.normalized_forecast(snap, NOW)
        self.assertEqual(len(s.tasks), 500)
        o.forecast_model.forecast(s)  # no record-cap crash
        snap.prs *= 2
        self.assertEqual(o.normalized_forecast(snap, NOW).prs, ())

    def test_unknown_kind_blocks_even_if_a_caller_supplies_history(self):
        f = o.forecast_model
        s = f.Snapshot(now=NOW.timestamp(), observed_at=NOW.timestamp(), scope='synthetic',
                       queue_complete=True, tasks=(f.Task('111', 'unknown', kind='unknown'),))
        out = f.forecast(s)['queue']
        self.assertIsNone(out['all_supplied_queue_seconds'])
        self.assertIn('task_kind_and_comparable_cohort_unavailable', out['blocked_tasks']['111'])

    def test_observed_historical_samples_and_confidence_are_rendered(self):
        # Independently completed synthetic stages, never inferred from PR totals.
        f = o.forecast_model
        for n, confidence in ((4, 'none'), (5, 'low'), (10, 'medium')):
            s = f.Snapshot(now=NOW.timestamp(), observed_at=NOW.timestamp(), scope='synthetic', queue_complete=True,
                           tasks=(f.Task('111', 'small', stage='active', stage_started_at=NOW.timestamp()-10),),
                           workers=(f.Worker('local', '111', NOW.timestamp()),),
                           history=tuple(f.Attempt(str(i), 'old'+str(i), 'small', 'source', 'execution',
                                                   NOW.timestamp()-1000-i*1000, NOW.timestamp()-800-i*1000)
                                         for i in range(n)))
            report = f.forecast(s)
            text = '\n'.join(o.forecast_lines(report))
            self.assertIn(f'Queue samples: {n} | confidence: {confidence}', text)
            self.assertIn('Whole-game ETA: UNKNOWN', text)
            self.assertEqual(report['queue']['all_supplied_queue_seconds'] is None, n < 5)


class OfflineAndRenderTests(unittest.TestCase):
    def test_local_offline_report_needs_no_subprocess_or_login(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(o.subprocess, 'run', side_effect=AssertionError('offline')), \
                patch.object(o, 'collect_activity', return_value=packet()), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(o.cli(['--offline', '--once', '--root', tmp, '--state-dir', tmp]), 0)
        self.assertIn('Whole-game ETA: UNKNOWN', out.getvalue())
        self.assertIn('#111', out.getvalue())

    def test_polling_uses_existing_thread_and_does_not_persist_private_activity(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(o, 'collect_activity', return_value=packet()):
            c = o.Collector(REPO, Path(tmp), 'test.service', Path(tmp), offline=True)
            with patch.object(o.threading, 'Thread') as thread:
                c.begin()
            self.assertEqual(thread.call_count, 1)
            self.assertIsNone(c.gh_thread)
            c.close()
            # Run exactly one local iteration without background activity.
            c.stop_event.clear()
            with patch.object(c.stop_event, 'wait', return_value=True), \
                    patch.object(o.subprocess, 'run', side_effect=AssertionError('offline')):
                c._log_loop()
            self.assertEqual(c.view().activity['records'][0]['issue'], 111)
            self.assertFalse((Path(tmp) / 'snapshot.json').exists())

    def test_all_pages_and_narrow_status_navigation(self):
        from test_render import FakeScreen
        c = o.Collector(REPO, Path('/tmp'), 'test.service', Path('/tmp'), demo=True)
        ui = o.Observatory(c)
        ui.snap = c.view()
        ui.snap.activity = packet(summary(timestamp_utc=datetime.now(timezone.utc).isoformat()))
        ui.snap.issues.append(issue())
        for width, height in ((40, 12), (72, 18), (80, 24), (120, 35), (400, 45)):
            ui.stdscr = FakeScreen(width, height)
            for tab in range(len(o.TABS)):
                ui.tab = tab
                ui.render()
                text = '\n'.join(ui.stdscr.lines.values())
                self.assertNotIn('HYPOTHETICAL WHOLE-GAME', text)
                self.assertNotIn('75%/30%/10%', text)
                if tab == 0 or width < 72:
                    self.assertIn('#111', text)
                    self.assertIn('UNKNOWN', text)
        ui.tab = 0
        ui.handle(ord('a'))
        self.assertEqual(o.TABS[ui.tab], 'ACTIVITY')
        ui.handle(o.curses.KEY_DOWN)
        self.assertEqual(ui.scroll['activity'], 1)
        ui.handle(ord('h'))
        self.assertEqual(o.TABS[ui.tab], 'TOOLCHAIN')
        ui.handle(ord('w'))
        self.assertEqual(o.TABS[ui.tab], 'WORKERS')
        ui.handle(ord('?'))
        self.assertEqual(ui.tab, len(o.TABS)-1)
        ui.tab = 2
        ui.snap.prs = []
        ui.handle(ord('a'))
        self.assertEqual(ui.tab, 2)  # existing mark-all-PRs binding

    def test_bundled_standalone_import_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            names = ('observer.py', 'operator_ops.py', 'presence_view.py', 'progress.py',
                     'forecast_model.py', 'project_roadmap.json', 'ui_components.py')
            for name in names:
                shutil.copyfile(HERE / name, target / name)
            # -I ignores PYTHONPATH and cwd, so this exercises sibling module loading.
            result = subprocess.run([sys.executable, '-I', str(target / 'observer.py'), '--offline', '--once',
                                     '--root', str(target / 'empty'), '--state-dir', str(target / 'state')],
                                    cwd=target, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Whole-game ETA: UNKNOWN', result.stdout)
            installer = (HERE / 'install.sh').read_text()
            self.assertEqual(installer.count('progress.py forecast_model.py project_roadmap.json ui_components.py'), 2)
            self.assertIn('"$LIVE_GUIDE" "$TARGET/OBSERVATORY_LIVE_INTEGRATION.md"', installer)
            self.assertIn('"$FORECAST_GUIDE" "$TARGET/FORECAST_POLICY.md"', installer)


if __name__ == '__main__':
    unittest.main()
