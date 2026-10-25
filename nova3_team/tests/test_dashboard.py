import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nova3_team import dashboard


class DashboardTests(unittest.TestCase):
    def test_issue_summary_uses_real_labels(self):
        issues = [
            {'labels': [{'name': 'nova:queued'}]},
            {'labels': [{'name': 'nova:claimed'}]},
            {'labels': [{'name': 'nova:review'}]},
            {'labels': [{'name': 'other'}]},
        ]
        self.assertEqual(dashboard.summarize_issues(issues),
                         {'total': 4, 'queued': 1, 'claimed': 1, 'review': 1, 'other': 1, 'limited': False})

    def test_handoff_events_update_task_and_session(self):
        status = dashboard.Status('team/nova3','codex')
        status.feed('[07:55:21] Trying #4: Build function ledger')
        self.assertEqual(status.task_number, 4)
        self.assertEqual(status.task, 'Build function ledger')
        status.feed('[07:55:39] #4 AI session 1/3 (codex)')
        self.assertEqual(status.phase, 'AI SESSION RUNNING')
        self.assertEqual(status.session, '1 of 3 (codex)')
        self.assertIsNotNone(status.session_since)
        status.feed('PR submitted: https://github.com/team/nova3/pull/12')
        self.assertEqual(status.pr_submitted, 1)
        self.assertIsNone(status.session_since)

    def test_no_unverified_game_completion(self):
        status = dashboard.Status('team/nova3','codex')
        status.counts = {'total': 12, 'queued': 8, 'claimed': 4, 'review': 0, 'open_prs': 0}
        snapshot = dashboard.render(status, now=status.begin+30, width=90)
        self.assertIn('8 queued', snapshot)
        self.assertIn('Source match coverage: UNKNOWN', snapshot)
        self.assertNotIn('game complete', snapshot.lower())
        self.assertEqual(snapshot.count('NOVA3  /  DISTRIBUTED RECONSTRUCTION'), 1)
        self.assertNotIn('\x1b', snapshot)

    def test_control_sequences_sanitized(self):
        self.assertEqual(dashboard.clean('hello\x1b[2J world\x00'), 'hello world')

    def test_worker_without_tty_runs_plainly(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.dict(os.environ, {'NOVA3_PLAIN_OUTPUT': '1'}):
                result = dashboard.run([sys.executable, '-c', 'print("done")'],
                                       cwd=Path(work), repo='abc/repo',provider='codex')
        self.assertEqual(result, 0)

    def test_session_timeout_is_safeguard_not_eta(self):
        s = dashboard.Status('abc/repo', 'codex')
        s.session_since = 100
        self.assertIn('29m 30s', dashboard._session_budget(s, 130))
        self.assertIn('NOT task ETA', dashboard._session_budget(s, 130))

    def test_queue_forecast_requires_real_history(self):
        now = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)
        active = [{'labels': [{'name': 'nova:queued'}]} for _ in range(5)]
        closed = [
            {'labels': [{'name': 'nova:review'}],
             'closedAt': (now-timedelta(hours=4-i)).isoformat()}
            for i in range(4)
        ]
        estimate = dashboard.estimate_backlog(active,closed,now)
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate['backlog'],5)
        self.assertEqual(estimate['samples'],4)
        self.assertAlmostEqual(estimate['seconds'],5*3600)
        self.assertIsNone(dashboard.estimate_backlog(active,closed[:3],now))
        stale = [{**item, 'closedAt': (now-timedelta(days=5,hours=i)).isoformat()}
                 for i,item in enumerate(closed)]
        self.assertIsNone(dashboard.estimate_backlog(active,stale,now))

    def test_non_project_issues_are_not_used_for_estimates(self):
        now = datetime.now(timezone.utc)
        closed = [{'labels':[{'name':'bug'}],
                   'closedAt': (now-timedelta(hours=i)).isoformat()} for i in range(6)]
        self.assertIsNone(dashboard.estimate_backlog([],closed,now))

    def test_compact_mode_is_not_fullscreen(self):
        with patch.dict(os.environ, {'NOVA3_COMPACT':'1'}):
            self.assertFalse(dashboard._supports_curses())

    def test_snapshot_fits_width_and_height_included(self):
        s=dashboard.Status('name/very-very-long-repository','gemini')
        for i in range(90):
            s.feed(f'log line {i} with arbitrary data')
        out=dashboard.render(s, width=60, height=22)
        self.assertTrue(all(len(line) <= 59 for line in out.splitlines()))
        self.assertEqual(out.count('RECENT EVENTS'),1)

    def test_event_drainer_no_duplicated_messages(self):
        import queue
        events=queue.Queue()
        s=dashboard.Status('abc/repo','codex')
        events.put(('worker','task output\n'))
        events.put(('gh', {'total': 1, 'queued': 1,'claimed':0,'review':0}, None, 150.0))
        self.assertEqual(dashboard._drain(events,s), ['task output'])
        self.assertEqual(dashboard._drain(events,s), [])
        self.assertEqual(s.gh_updated,150.0)


if __name__=='__main__':
    unittest.main()
