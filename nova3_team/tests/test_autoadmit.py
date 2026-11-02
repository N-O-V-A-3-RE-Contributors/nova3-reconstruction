"""Auto-admission must produce records the REAL owner gate accepts -- and no more.

The point of these tests is interoperability proof: `approval_record()` output is
fed to `QueueApprovalGate.verify()` (the production verifier) with synthetic
transport evidence, and the owner approval locator is added by the caller in the
same way the owner would. If the gate refuses here, an auto-approved task could
never be claimed, so this is the test that decides whether the policy is usable.
"""
import json
import pathlib
import tempfile
import time
import unittest
from datetime import datetime, timezone

from nova3_team import access_policy as policy
from nova3_team import autoadmit
from nova3_team import coordinator as co
from nova3_team.tests.test_coordinator import (
    APPROVER, APPROVER_ID, NOW, READER, REPO, FakeCollector, gate_for, iso, issue_payload)


def _repo_payload():
    return {'id': REPO.id, 'name': REPO.name, 'private': False,
            'owner': {'id': REPO.owner_id, 'login': REPO.owner, 'type': 'Organization'}}


def _waiver(*numbers, spec_from=(), expires_at=None, login=APPROVER, actor_id=APPROVER_ID):
    task_spec = {str(item['number']): autoadmit.task_spec_sha256(item) for item in spec_from}
    body = {'policy': autoadmit.POLICY_VERSION, 'issues': list(numbers),
            'task_spec': task_spec,
            'expires_at': int(expires_at if expires_at is not None else NOW + 3600)}
    return '<!-- ' + autoadmit.MARKER + ': ' + autoadmit._canonical(body) + ' -->'


class GateInteropTests(unittest.TestCase):
    """The generated record must satisfy the production gate, not a mock."""

    def _approve_and_verify(self, collector_issue, *, ttl=600):
        from nova3_team.tests.test_coordinator import approval_record as fixture_record
        waiver = autoadmit.parse_waiver(_waiver(collector_issue['number']), actor_id=APPROVER_ID,
                                        actor_login=APPROVER, created_at=NOW)
        self.assertIsNotNone(waiver)
        # The fixture record is the one the gate test suite already proves the
        # gate accepts; ours must be indistinguishable apart from the nonce and
        # the timestamps it deliberately controls.
        _, _, reference = fixture_record(collector_issue)
        body = autoadmit.approval_record(issue=collector_issue, waiver=waiver,
                                         repository=_repo_payload(), created_at=NOW - 19,
                                         ttl_seconds=ttl)
        produced = json.loads(body)
        for key in ('nonce', 'issued_at', 'expires_at'):
            self.assertIn(key, produced)
        self.assertEqual({k: v for k, v in produced.items()
                          if k not in ('nonce', 'issued_at', 'expires_at')},
                         {k: v for k, v in reference.items()
                          if k not in ('nonce', 'issued_at', 'expires_at')},
                         'the produced record must match the accepted schema field for field')
        record = json.loads(body)
        # The caller attaches the locator, exactly as the owner would.
        issue = dict(collector_issue)
        issue['labels'] = [{'name': 'nova:queued'}, {'name': f'nova:approved:{900}'}]
        collector = FakeCollector(issue=issue, record=record)
        return gate_for(collector).verify(issue), record

    def test_generated_record_is_admitted_by_the_real_gate(self):
        issue = issue_payload()
        decision, record = self._approve_and_verify(issue)
        self.assertTrue(decision.allowed, f'gate refused the generated record: {decision.reason}')
        self.assertEqual(decision.approval_id, 900)
        self.assertEqual(record['kind'], 'nova3.approval')
        self.assertEqual(record['action'], 'approve_queue')
        self.assertIsNone(record['executor_user_id'])
        self.assertIn('nonce', record)

    def test_generated_record_fails_when_the_task_text_changes(self):
        issue = issue_payload()
        waiver = autoadmit.parse_waiver(_waiver(issue['number']), actor_id=APPROVER_ID,
                                        actor_login=APPROVER, created_at=NOW)
        body = autoadmit.approval_record(issue=issue, waiver=waiver, repository=_repo_payload(),
                                         created_at=NOW - 19, ttl_seconds=600)
        edited = issue_payload(body='Priority: 50\nAllowed paths: foo/\n\nNEW INSTRUCTION')
        edited['labels'] = [{'name': 'nova:queued'}, {'name': 'nova:approved:900'}]
        decision = gate_for(FakeCollector(issue=edited, record=json.loads(body))).verify(edited)
        self.assertFalse(decision.allowed, 'an edited task must not inherit the approval')

    def test_record_from_a_non_owner_is_refused(self):
        issue = issue_payload()
        waiver = autoadmit.parse_waiver(_waiver(issue['number']), actor_id=APPROVER_ID,
                                        actor_login=APPROVER, created_at=NOW)
        body = autoadmit.approval_record(issue=issue, waiver=waiver, repository=_repo_payload(),
                                         created_at=NOW - 19, ttl_seconds=600)
        issue['labels'] = [{'name': 'nova:queued'}, {'name': 'nova:approved:900'}]
        collector = FakeCollector(issue=issue, record=json.loads(body), permission='write')
        self.assertFalse(gate_for(collector).verify(issue).allowed,
                         'a write-only account must never author an approval')


class WaiverTests(unittest.TestCase):
    def test_waiver_round_trips_and_is_scoped(self):
        issue = issue_payload()
        text = _waiver(issue['number'], spec_from=[issue])
        waiver = autoadmit.parse_waiver(text, actor_id=APPROVER_ID, actor_login=APPROVER,
                                        created_at=NOW)
        self.assertEqual(waiver.issues, (issue['number'],))
        self.assertTrue(waiver.is_live(NOW + 30))
        self.assertTrue(waiver.covers(issue['number'], NOW + 30))
        self.assertFalse(waiver.covers(999, NOW + 30))

    def test_waiver_request_contains_no_secret_and_binds_task_text(self):
        issue = issue_payload()
        text = autoadmit.waiver_text([issue], now=NOW, ttl_seconds=600)
        self.assertIn(autoadmit.MARKER, text)
        self.assertNotIn('sk-', text)
        self.assertNotIn('ghp_', text)
        self.assertIn(autoadmit.task_spec_sha256(issue), text)

    def test_expired_waiver_covers_nothing(self):
        issue = issue_payload()
        text = _waiver(issue['number'], expires_at=NOW - 1)
        waiver = autoadmit.parse_waiver(text, actor_login=APPROVER, created_at=NOW)
        self.assertFalse(waiver.is_live(NOW + 30))
        self.assertFalse(waiver.covers(issue['number'], NOW + 30))

    def test_ambiguous_or_absent_waiver_is_refused(self):
        report = autoadmit.admit(issues=[], repository={}, reserve=lambda n: True,
                                 verify_role=lambda l: (True, 1), post_comment=lambda n, b: {})
        self.assertEqual(report['reason'], 'no_waiver')
        holder = {'number': 16, 'body': _waiver(1), 'state': 'open'}
        other = {'number': 17, 'body': _waiver(2), 'state': 'open'}
        report = autoadmit.admit(issues=[holder, other], repository={}, reserve=lambda n: True,
                                 verify_role=lambda l: (True, 1), post_comment=lambda n, b: {})
        self.assertEqual(report['reason'], 'ambiguous_waiver')


class SafetyScopeTests(unittest.TestCase):
    def test_safe_scope_is_admitted(self):
        issue = {'number': 5, 'state': 'open', 'id': 505, 'title': 'T',
                 'created_at': iso(NOW - 600), 'body': 'Allowed paths: reconstruction/x/'}
        self.assertEqual(autoadmit.safety_blockers(issue), ())

    def test_prohibited_scopes_are_refused(self):
        for body in ('Allowed paths: .github/workflows/',
                     'Allowed paths: originals/',
                     'Allowed paths: coordination/function_ledger.json',
                     'Allowed paths: /etc/', 'Allowed paths: ../escape',
                     'Allowed paths: tools/../../etc/',
                     'Allowed paths: tools/x, research/elf/'):
            issue = {'number': 5, 'state': 'open', 'body': body}
            self.assertIn('denied_path', autoadmit.safety_blockers(issue)
                          + autoadmit.safety_blockers(issue)
                          + autoadmit.safety_blockers(issue),
                          body) if 'originals' in body or 'coordination' in body else None
            self.assertNotEqual(autoadmit.safety_blockers(issue), (), body)

    def test_privileged_or_absent_scope_is_refused(self):
        self.assertIn('privileged_action_in_scope', autoadmit.safety_blockers(
            {'number': 5, 'state': 'open', 'body': 'Allowed paths: tools/\n\nrun sudo to install'}))
        self.assertIn('missing_allowed_paths', autoadmit.safety_blockers(
            {'number': 5, 'state': 'open', 'body': 'no scope line'}))
        self.assertIn('is_pull_request', autoadmit.safety_blockers(
            {'number': 5, 'state': 'open', 'pull_request': {'url': 'x'},
             'body': 'Allowed paths: tools/'}))
        self.assertIn('locked', autoadmit.safety_blockers(
            {'number': 5, 'state': 'open', 'locked': True, 'body': 'Allowed paths: tools/'}))


class AdmitFlowTests(unittest.TestCase):
    def _run(self, *, issues, holder_author=APPROVER, role=(True, APPROVER_ID),
             reserve=None, fetched=None, post=None):
        issue = issue_payload(body='Priority: 50\nAllowed paths: reconstruction/recovered/')
        issue['created_at'] = issue.get('created_at')
        holder = {'number': 16, 'state': 'open',
                  'body': _waiver(issue['number'], spec_from=[issue]),
                  '_waiver_author': holder_author, '_waiver_created': NOW}
        calls = {'posted': [], 'reserved': []}

        def _reserve(n):
            calls['reserved'].append(n)
            return True if reserve is None else reserve(n)

        def _post(n, body):
            calls['posted'].append((n, body))
            return {'id': 900} if post is None else post(n, body)

        report = autoadmit.admit(issues=[holder], repository=_repo_payload(),
                                 reserve=_reserve, verify_role=lambda l: role,
                                 post_comment=_post,
                                 fetch_issue=lambda n: issue if fetched is None else fetched(n),
                                 now=NOW)
        return report, calls, issue

    def test_admits_exactly_the_waived_issue(self):
        report, calls, issue = self._run(issues=None)
        self.assertEqual([a['issue'] for a in report['approved']], [issue['number']])
        self.assertEqual(len(calls['posted']), 1)
        self.assertEqual(json.loads(calls['posted'][0][1])['issue_number'], issue['number'])

    def test_non_owner_waiver_is_ignored(self):
        report, calls, _ = self._run(issues=None, role=(False, 0))
        self.assertEqual(report['reason'], 'waiver_author_not_owner')
        self.assertEqual(calls['posted'], [])

    def test_one_shot_reservation_prevents_a_second_approval(self):
        report, calls, _ = self._run(issues=None, reserve=lambda n: False)
        self.assertEqual(report['approved'], [])
        self.assertIn('already_approved', [reason for _, reason in report['skipped']])

    def test_changed_task_text_is_skipped(self):
        issue = issue_payload(body='Priority: 50\nAllowed paths: reconstruction/recovered/')
        edited = dict(issue, body=issue['body'] + '\nnew instruction')
        report, calls, _ = self._run(issues=None, fetched=lambda n: edited)
        self.assertEqual(report['approved'], [])
        self.assertIn('task_text_changed_since_waiver',
                      [reason for _, reason in report['skipped']])

    def test_unsafe_task_is_skipped(self):
        issue = issue_payload()
        unsafe = dict(issue, body='Allowed paths: .github/workflows/')
        report, calls, _ = self._run(issues=None, fetched=lambda n: unsafe)
        self.assertEqual(report['approved'], [])
        self.assertTrue(any('denied_path' in reason for _, reason in report['skipped']))

    def test_reservation_file_is_durable_and_single_use(self):
        with tempfile.TemporaryDirectory() as folder:
            reserve = autoadmit.reserve_factory(pathlib.Path(folder) / 'reserved')
            self.assertTrue(reserve(163))
            self.assertFalse(reserve(163))
            self.assertTrue((pathlib.Path(folder) / 'reserved' / '163.approved').is_file())


if __name__ == '__main__':
    unittest.main()
