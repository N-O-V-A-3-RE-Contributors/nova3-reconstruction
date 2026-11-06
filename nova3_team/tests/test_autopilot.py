"""Autopilot: persistent switch, credit-only pause, reviewed-green merge gate."""
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
    value = {'schema': autopilot.SCHEMA, 'repo': 'O/R', 'enabled': True, 'auto_merge': True,
             'actor': 'owner', 'actor_id': 7}
    value.update(overrides)
    return value


class GrantTests(unittest.TestCase):
    def test_switch_has_no_expiry_and_no_task_budget(self):
        # The whole point: an enabled switch stays live regardless of wall clock
        # or how many tasks were already approved.
        self.assertTrue(autopilot.grant_live(grant(), now=NOW))
        self.assertTrue(autopilot.grant_live(
            grant(expires_at=NOW - 10, max_tasks=0, tasks_approved=999), now=NOW))
        self.assertTrue(autopilot.grant_live(grant(), now=NOW + 10 * 365 * 86400))

    def test_only_an_explicit_off_disables_it(self):
        self.assertFalse(autopilot.grant_live(grant(enabled=False), now=NOW))
        self.assertFalse(autopilot.grant_live(None, now=NOW))

    def test_remaining_is_unlimited_while_on(self):
        self.assertEqual(autopilot.remaining(grant(tasks_approved=1), now=NOW), -1)
        self.assertEqual(autopilot.remaining(grant(enabled=False), now=NOW), 0)

    def test_v1_timed_grant_migrates_to_a_persistent_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            (pathlib.Path(folder) / autopilot.GRANT).write_text(json.dumps({
                'schema': 'nova3-autopilot/1', 'enabled': True,
                'expires_at': NOW - 999, 'max_tasks': 6, 'tasks_approved': 6,
                'actor': 'owner', 'actor_id': 7, 'auto_merge': True}))
            loaded = autopilot.load_grant(pathlib.Path(folder))
            self.assertEqual(loaded['schema'], autopilot.SCHEMA)
            self.assertTrue(autopilot.grant_live(loaded, now=NOW))
            self.assertNotIn('expires_at', loaded)
            self.assertNotIn('max_tasks', loaded)

    def test_disable_keeps_the_record_and_sets_enabled_false(self):
        with tempfile.TemporaryDirectory() as folder:
            autopilot.save_grant(pathlib.Path(folder), grant())
            autopilot.disable(pathlib.Path(folder))
            loaded = autopilot.load_grant(pathlib.Path(folder))
            self.assertFalse(loaded['enabled'])
            self.assertFalse(autopilot.grant_live(loaded))


class SelectTests(unittest.TestCase):
    def test_safe_planner_proposal_is_accepted(self):
        accepted, refused = autopilot.select_candidates([issue()], grant(), now=NOW)
        self.assertEqual([i['number'] for i in accepted], [1])
        self.assertEqual(refused, [])

    def test_safe_proposal_is_still_accepted_long_after_any_old_expiry(self):
        accepted, refused = autopilot.select_candidates(
            [issue()], grant(expires_at=NOW - 1, tasks_approved=99), now=NOW)
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


class ApproveTests(unittest.TestCase):
    def test_approval_writes_labels_and_audit(self):
        posted, patched = {}, {}
        with tempfile.TemporaryDirectory() as folder:
            record = autopilot.approve_proposal(
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

    def test_tick_pauses_but_stays_enabled_when_credit_is_depleted(self):
        original = autopilot.credit_paused
        autopilot.credit_paused = lambda repo, slot='dsh': {'state': 'WAITING_CREDITS'}
        try:
            with tempfile.TemporaryDirectory() as folder:
                autopilot.save_grant(pathlib.Path(folder), grant())
                report = autopilot.tick('O/R', pathlib.Path(folder), now=NOW)
                self.assertTrue(autopilot.grant_live(
                    autopilot.load_grant(pathlib.Path(folder))))
        finally:
            autopilot.credit_paused = original
        self.assertEqual(report['state'], 'paused_credits')


class MergeGateTests(unittest.TestCase):
    def pr(self, **overrides):
        value = {'number': 7, 'draft': False, 'state': 'open', 'mergeable': True}
        value.update(overrides)
        return value

    def checks(self, *runs, combined='success'):
        return {'runs': list(runs), 'combined': combined}

    def run_(self, **overrides):
        return {'status': 'completed', 'conclusion': 'success',
                'name': 'source-and-tests', **overrides}

    def test_reviewed_green_pr_merges(self):
        ok, why = autopilot.merge_decision(
            self.pr(), self.checks(self.run_()), {'verdict': 'pass'})
        self.assertTrue(ok, why)
        self.assertEqual(why, 'reviewed_and_green')

    def test_draft_or_failed_review_or_red_check_never_merges(self):
        review = {'verdict': 'needs_changes'}
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(self.run_()), review)[1], 'review_not_pass')
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(self.run_()), {'verdict': 'pass'})[0], True)
        self.assertEqual(autopilot.merge_decision(
            self.pr(draft=True), self.checks(self.run_()), {'verdict': 'pass'})[1], 'draft')
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(self.run_(conclusion='failure')),
            {'verdict': 'pass'})[1], 'check_failed:source-and-tests')
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(self.run_(status='in_progress')),
            {'verdict': 'pass'})[1], 'checks_pending')
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(), {'verdict': 'pass'})[1], 'no_checks')
        self.assertEqual(autopilot.merge_decision(
            self.pr(), self.checks(self.run_(), combined='failure'),
            {'verdict': 'pass'})[1], 'combined_status:failure')

    def test_auto_merge_off_is_an_explicit_refusal(self):
        ok, why = autopilot.merge_decision(
            self.pr(), self.checks(self.run_()), {'verdict': 'pass'}, auto_merge=False)
        self.assertFalse(ok)
        self.assertEqual(why, 'auto_merge_disabled')


if __name__ == '__main__':
    unittest.main()
