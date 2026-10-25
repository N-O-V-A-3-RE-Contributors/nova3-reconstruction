import io
import json
import os
from pathlib import Path
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

    def test_logs_update_stage_and_task(self):
        status = dashboard.Status('owner/project','codex')
        status.feed('[07:13:00] Trying #3: Repair rendering path')
        self.assertEqual(status.task_number, 3)
        self.assertEqual(status.task,'Repair rendering path')
        status.feed('[07:14:00] #3 AI session 2/3 (codex)')
        self.assertEqual(status.phase, 'AI SESSION RUNNING')
        self.assertEqual(status.session, '2 of 3 (codex)')
        status.feed('PR submitted: https://github.com/owner/project/pull/5')
        self.assertEqual(status.pr_submitted, 1)

    def test_unverified_progress_never_invented(self):
        status = dashboard.Status('owner/project','gemini')
        status.counts = {'total': 12, 'queued': 7, 'claimed': 3,
                         'review': 2, 'open_prs': 2}
        out = dashboard.render(status, now=status.begin+30, color=False)
        self.assertIn('Queued: 7', out)
        self.assertIn('Game/source matching %: not established', out)
        self.assertIn('runtime 30s', out)

    def test_control_sequences_are_removed_from_logs(self):
        self.assertEqual(dashboard.clean('hello\x1b[2J world\x00'), 'hello world')

    def test_non_tty_mode_runs_normal_process(self):
        import sys
        with tempfile.TemporaryDirectory() as work:
            with patch.dict(os.environ, {'NOVA3_PLAIN_OUTPUT': '1'}):
                rc = dashboard.run([sys.executable,'-c','print("ok")'],
                                   cwd=Path(work),repo='abc/repo',provider='codex')
        self.assertEqual(rc,0)

if __name__=='__main__':
    unittest.main()
