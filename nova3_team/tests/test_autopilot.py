"""Autopilot must approve only what the owner's grant allows, and nothing else."""
import json
import pathlib
import tempfile
import unittest

from nova3_team import autopilot

NOW = 1793600000
BODY = ('Priority: 3\nAllowed paths: reconstruction/tests/test_x.py\n\n'
        '<!-- NOVA3_DSH_PLANNER_PROPOSAL:abc123 -->')


def issue(number=1, body=BODY, labels=(), state='open'):
    return {'id': 100 + number, 'number': number, 'title': 'Task', 'body': body,
            'state': state, 'created_at': '2026-01-01T00:00:00Z',
            'labels': [{'name': name} for name in labels]}


def grant(**overrides):
    value = {'schema': autopilot.SCHEMA, 'enabled': True, 'expires_at': NOW + 3600,
             'max_tasks': 3, 'tasks_approved': 0, 'actor': 'owner', 'actor_id': 7}
    value.update(overrides)
    return value


class GrantTests(unittest.TestCase):
    def test_live_only_while_enabled_unexpired_and_under_budget(self):
        self.assertTrue(autopilot.grant_live(grant(), now=NOW))
        self.assertFalse(autopilot.grant_live(grant(enabled=False), now=NOW))
        self.assertFalse(autopilot.grant_live(grant(expires_at=NOW - 1), now=NOW))
        self.assertFalse(autopilot.grant_live(grant(tasks_approved=3), now=NOW))

    def test_remaining_counts_down(self):
        self.assertEqual(autopilot.remaining(grant(tasks_approved=1), now=NOW), 2)
        self.assertEqual(autopilot.remaining(grant(enabled=False), now=NOW), 0)


class SelectTests(unittest.TestCase):
    def test_safe_planner_proposal_is_accepted(self):
        accepted, refused = autopilot.select_candidates([issue()], grant(), now=NOW)
        self.assertEqual([i['number'] for i in accepted], [1])
        self.assertEqual(refused, [])

    def test_non_planner_issue_is_ignored(self):
        accepted, _ = autopilot.select_candidates(
            [issue(body='Allowed paths: reconstruction/tests/x.py')], grant(), now=NOW)
        self.assertEqual(accepted, [])

    def test_active_or_already_approved_issue_is_skipped(self):
        for labels in (('nova:blocked',), ('nova:claimed',), ('nova:approved:9',)):
            accepted, _ = autopilot.select_candidates(
                [issue(labels=labels)], grant(), now=NOW)
            self.assertEqual(accepted, [], labels)

    def test_denied_scope_is_refused_not_approved(self):
        body = 'Allowed paths: .github/workflows/x.yml\n\n<!-- NOVA3_DSH_PLANNER_PROPOSAL:a -->'
        accepted, refused = autopilot.select_candidates([issue(body=body)], grant(), now=NOW)
        self.assertEqual(accepted, [])
        self.assertTrue(any('protected' in why or 'denied' in why or 'outside' in why
                            for _, why in refused))

    def test_expired_grant_refuses_everything(self):
        accepted, refused = autopilot.select_candidates(
            [issue()], grant(expires_at=NOW - 1), now=NOW)
        self.assertEqual(accepted, [])
        self.assertEqual(refused, [(1, 'grant_expired')])


class ApproveTests(unittest.TestCase):
    def test_approval_writes_labels_and_audit(self):
        posted, patched = {}, {}
        with tempfile.TemporaryDirectory() as folder:
            record = autopilot.approve(
                repo='O/R', issue=issue(), grant=grant(), folder=pathlib.Path(folder),
                post_comment=lambda n, body: (posted.update(number=n, body=body), {'id': 555})[1],
                patch_labels=lambda n, labels: patched.update(number=n, labels=labels),
                repository={'id': 1, 'owner': {'id': 2}}, now=NOW)
            audit = (pathlib.Path(folder) / autopilot.AUDIT).read_text().splitlines()
        self.assertEqual(record['comment_id'], 555)
        self.assertIn('nova:queued', patched['labels'])
        self.assertIn('nova:approved:555', patched['labels'])
        self.assertEqual(json.loads(audit[0])['issue'], 1)
        self.assertIn('"kind": "nova3.approval"', posted['body'].replace('":', '": '))

    def test_tick_without_a_grant_does_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            report = autopilot.tick('O/R', pathlib.Path(folder), now=NOW)
        self.assertEqual(report['state'], 'disabled')
        self.assertEqual(report['approved'], [])


if __name__ == '__main__':
    unittest.main()
