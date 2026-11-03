"""Standing waivers and approval renewal.

Why this exists: a one-run waiver is clock-bound to MAX_APPROVAL_AGE (one hour),
so a durable worker went idle an hour after the owner approved the queue and
every subsequent cycle denied admission with `invalid_or_unavailable_evidence`.
The standing form keeps the SAME per-record window (the gate still refuses any
approval older than an hour) but lets the automated approver renew records for
tasks the owner named, as long as the owner's persistent autopilot switch is on.
Turning the switch off revokes every standing waiver; editing a task text still
requires a new waiver because the waiver pins the task-spec digest.

These tests fix the failure mode that made sessions stop after the first task:
an expired-but-still-owner-approved queue must become admissible again, and a
live approval must never be double-issued.
"""
import json
import pathlib
import tempfile
import time
import unittest

from nova3_team import autoadmit

NOW = 1_800_000_000
REPO = {'id': 1433565792, 'name': 'nova3-reconstruction',
        'owner': {'id': 347984779, 'login': 'N-O-V-A-3-RE-Contributors',
                  'type': 'Organization'}}


def issue(number=167, body=None):
    return {
        'id': 6155762981 + number,
        'number': number,
        'title': f'task {number}',
        'body': body if body is not None else (
            'Priority: 1\nAllowed paths: tools/compare/x.py\n\n## Objective\nDo a thing.\n'),
        'created_at': '2027-01-15T00:00:00Z',
        'state': 'open',
        'labels': [],
    }


def standing_waiver(number=167, *, created_at=NOW, login='crjase'):
    text = autoadmit.waiver_text([issue(number)], standing=True)
    return text, autoadmit.parse_waiver(text, comment_id=42, actor_id=25498208,
                                        actor_login=login, created_at=created_at)


class StandingWaiverTests(unittest.TestCase):
    def test_standing_waiver_text_has_no_expiry_and_round_trips(self):
        text, waiver = standing_waiver()
        self.assertIsNotNone(waiver)
        self.assertTrue(waiver.standing)
        self.assertEqual(waiver.expires_at, 0)
        self.assertNotIn('"expires_at"', text)
        self.assertIn('"standing":true', text.replace(' ', ''))
        self.assertIn(str(167), text)

    def test_standing_waiver_liveness_follows_the_autopilot_switch(self):
        _, waiver = standing_waiver()
        self.assertFalse(waiver.is_live(NOW, standing_enabled=False))
        self.assertTrue(waiver.is_live(NOW, standing_enabled=True))
        # The owner switch cannot resurrect a future-dated (unusable) waiver.
        _, future = standing_waiver(created_at=NOW + 100)
        self.assertFalse(future.is_live(NOW, standing_enabled=True))

    def test_one_run_waiver_is_unchanged(self):
        text = autoadmit.waiver_text([issue(170)], now=NOW)
        waiver = autoadmit.parse_waiver(text, comment_id=7, actor_id=1,
                                        actor_login='crjase', created_at=NOW)
        self.assertFalse(waiver.standing)
        self.assertTrue(waiver.is_live(NOW))
        self.assertFalse(waiver.is_live(NOW + autoadmit.policy.MAX_APPROVAL_AGE + 1))

    def test_standing_enabled_for_is_fail_closed(self):
        # No autopilot grant anywhere: standing waivers are dead.
        self.assertFalse(autoadmit.standing_enabled_for('nobody/nova3-reconstruction'))


class RenewalTests(unittest.TestCase):
    def _admit(self, *, waiver_source, enabled=True, previous=None, tmp=None,
               repo_issue=None, now=NOW):
        posted = []
        reserved = []

        def reserve(number):
            reserved.append(number)
            return True

        def post_comment(number, body):
            posted.append({'number': number, 'body': body})
            return {'id': 900 + len(posted)}

        report = autoadmit.admit(
            issues=[], repository=REPO, reserve=reserve,
            verify_role=lambda login: (True, 25498208),
            post_comment=post_comment,
            fetch_issue=lambda number: repo_issue or issue(number),
            now=now, ttl_seconds=600, waiver_source=waiver_source,
            standing_enabled=enabled, previous=previous)
        return report, posted, reserved

    def _source(self, number=167, created_at=NOW):
        text, _ = standing_waiver(number, created_at=created_at)
        return {'text': text, 'comment_id': 42, 'actor_login': 'crjase',
                'created_at': created_at}

    def test_standing_waiver_is_dead_without_the_autopilot_switch(self):
        report, posted, reserved = self._admit(waiver_source=self._source(),
                                               enabled=False)
        self.assertEqual(report['reason'], 'standing_waiver_needs_autopilot')
        self.assertEqual(posted, [])
        self.assertEqual(reserved, [])

    def test_standing_waiver_approves_once_when_the_switch_is_on(self):
        report, posted, reserved = self._admit(waiver_source=self._source())
        self.assertEqual(len(posted), 1)
        self.assertFalse(report['approved'][0]['renewal'])
        record = json.loads(posted[0]['body'])
        self.assertEqual(record['issue_number'], 167)

    def test_a_live_approval_is_never_reissued(self):
        live = {'at': NOW - 60, 'expires_at': NOW + 540}
        report, posted, _ = self._admit(waiver_source=self._source(),
                                        previous=lambda number: live)
        self.assertEqual(posted, [])
        self.assertIn((167, 'already_approved'), report['skipped'])

    def test_an_expired_approval_is_renewed(self):
        expired = {'at': NOW - 4000, 'expires_at': NOW - 3400}
        report, posted, _ = self._admit(waiver_source=self._source(),
                                        previous=lambda number: expired)
        self.assertEqual(len(posted), 1)
        self.assertTrue(report['approved'][0]['renewal'])
        record = json.loads(posted[0]['body'])
        self.assertEqual(record['issued_at'], '2027-01-15T08:00:00Z')
        self.assertEqual(record['expires_at'], '2027-01-15T08:10:00Z')

    def test_a_legacy_reservation_without_expiry_stays_single_use(self):
        legacy = {'at': NOW - 4000}
        report, posted, _ = self._admit(waiver_source=self._source(),
                                        previous=lambda number: legacy)
        self.assertEqual(posted, [])
        self.assertIn((167, 'already_approved'), report['skipped'])

    def test_changed_task_text_still_requires_a_new_waiver(self):
        changed = issue(167, body='Priority: 1\nAllowed paths: tools/compare/x.py\n'
                                  '## Objective\nSomething else entirely.\n')
        report, posted, _ = self._admit(waiver_source=self._source(),
                                        repo_issue=changed)
        self.assertEqual(posted, [])
        self.assertIn((167, 'task_text_changed_since_waiver'), report['skipped'])


class AutopilotAuditCoverageTests(unittest.TestCase):
    """Future work the AUTOPILOT approved stays renewable under a standing waiver."""

    def _run(self, *, waiver, issues, audit, repo_issue=None, now=NOW):
        posted = []
        report = autoadmit.admit(
            issues=[], repository=REPO,
            reserve=lambda number: True,
            verify_role=lambda login: (True, 25498208),
            post_comment=lambda number, body: (posted.append((number, body)),
                                               {'id': 1000 + len(posted)})[1],
            fetch_issue=lambda number: (repo_issue or {}).get(number) or issue(number),
            now=now, ttl_seconds=600, waiver_source=waiver,
            standing_enabled=True, previous=lambda number: None,
            audit_specs=audit)
        return report, posted

    def test_standing_waiver_can_opt_into_audit_renewal(self):
        text = autoadmit.waiver_text([issue(167)], standing=True,
                                     renew_autopilot=True)
        waiver = autoadmit.parse_waiver(text, comment_id=1, actor_id=2,
                                        actor_login='crjase', created_at=NOW)
        self.assertTrue(waiver.renew_autopilot)
        source = {'text': text, 'comment_id': 1, 'actor_login': 'crjase',
                  'created_at': NOW}
        spec = autoadmit.task_spec_sha256(issue(200))
        report, posted = self._run(waiver=source, issues=[167],
                                   audit={200: spec})
        approved = {entry['issue'] for entry in report['approved']}
        self.assertEqual(approved, {167, 200})
        self.assertEqual(report['audit_coverage'], [200])

    def test_audit_coverage_is_refused_when_the_waiver_does_not_opt_in(self):
        text = autoadmit.waiver_text([issue(167)], standing=True)
        source = {'text': text, 'comment_id': 1, 'actor_login': 'crjase',
                  'created_at': NOW}
        spec = autoadmit.task_spec_sha256(issue(200))
        report, posted = self._run(waiver=source, issues=[167], audit={200: spec})
        self.assertEqual([entry['issue'] for entry in report['approved']], [167])
        self.assertNotIn('audit_coverage', report)

    def test_audited_task_text_change_is_refused(self):
        text = autoadmit.waiver_text([issue(167)], standing=True,
                                     renew_autopilot=True)
        source = {'text': text, 'comment_id': 1, 'actor_login': 'crjase',
                  'created_at': NOW}
        spec = autoadmit.task_spec_sha256(issue(200))
        changed = issue(200, body='Priority: 1\nAllowed paths: tools/compare/x.py\n'
                                  '## Objective\nEdited after approval.\n')
        report, _ = self._run(waiver=source, issues=[167], audit={200: spec},
                              repo_issue={200: changed})
        self.assertIn((200, 'task_text_changed_since_autopilot_approval'),
                      report['skipped'])
        self.assertNotIn(200, [entry['issue'] for entry in report['approved']])


class ReserveFactoryTests(unittest.TestCase):
    def test_expired_reservations_renew_but_live_ones_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            reserve = autoadmit.reserve_factory(directory, ttl_seconds=60)
            self.assertTrue(reserve(5))
            self.assertFalse(reserve(5), 'a live reservation must not be reissued')
            state = json.loads((directory / '5.approved').read_text())
            self.assertGreater(int(state['expires_at']), int(state['at']) - 1)
            # Simulate the record ageing past its window, once per hour.
            aged = dict(state, expires_at=int(time.time()) - 1)
            (directory / '5.approved').write_text(json.dumps(aged))
            self.assertTrue(reserve(5), 'an expired reservation must be renewable')

    def test_legacy_reservation_without_expiry_is_permanent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            (directory / '9.approved').write_text(json.dumps({'at': 1}))
            reserve = autoadmit.reserve_factory(directory, ttl_seconds=60)
            self.assertFalse(reserve(9))


class LabelLocatorTests(unittest.TestCase):
    """A renewal must REPLACE the old approval locator, never accumulate it.

    Two locators make `coordinator.approval_comment_id` return None, which is the
    `owner_approval_locator_required` denial that followed the first live
    renewal. The tests run against the module's own API functions, stubbed.
    """

    def _with_stubs(self, issue_payload, fn):
        calls = []
        original_api, original_issue = autoadmit._api, autoadmit._issue_payload

        def fake_api(path, *, method='GET', payload=None):
            calls.append((path, method, payload))
            return {}

        autoadmit._api = fake_api
        autoadmit._issue_payload = lambda repo, number: issue_payload
        try:
            fn()
        finally:
            autoadmit._api, autoadmit._issue_payload = original_api, original_issue
        return calls

    def test_renewal_replaces_the_superseded_locator(self):
        calls = self._with_stubs(
            {'labels': [{'name': 'nova:queued'}, {'name': 'nova:approved:111'}]},
            lambda: autoadmit._queue_labels('o/r', 167, 222))
        patches = [call for call in calls if call[1] == 'PATCH']
        self.assertEqual(len(patches), 1)
        self.assertEqual(set(patches[0][2]['labels']),
                         {'nova:queued', 'nova:approved:222'})

    def test_correct_labels_are_not_rewritten(self):
        calls = self._with_stubs(
            {'labels': [{'name': 'nova:queued'}, {'name': 'nova:approved:222'}]},
            lambda: autoadmit._queue_labels('o/r', 167, 222))
        self.assertEqual([call for call in calls if call[1] == 'PATCH'], [])


if __name__ == '__main__':
    unittest.main()
