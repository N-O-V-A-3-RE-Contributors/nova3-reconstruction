import json
import unittest
from datetime import datetime, timezone

from nova3_team import access_policy as policy
from nova3_team import coordinator as co
from nova3_team.common import lease_get, label_names
from nova3_team.coordinator import process_comment, sweep

class FakeGH:
    repo='x/repo'
    def __init__(self):
        self.item={'number':5,'body':'Priority: 50\nAllowed paths: foo/','labels':[{'name':'nova:queued'}]}
        self.messages=[]
    def issue(self,n):return dict(self.item)
    def comment(self,n,msg):self.messages.append(msg)
    def patch(self,path,payload):
        self.item.update(payload)
        self.item['labels']=[{'name':n} for n in payload['labels']] if 'labels' in payload else self.item['labels']
    def issues(self):return [self.item]

def event(actor,body):return {'issue':{'number':5},'comment':{'user':{'login':actor},'body':body}}

class CoordTest(unittest.TestCase):
    def test_only_one_holder_and_release(self):
        gh=FakeGH();tok1='a'*32;tok2='b'*32
        process_comment(gh,event('alice','/nova claim '+tok1))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
        self.assertIn('nova:claimed',label_names(gh.item))
        process_comment(gh,event('bob','/nova claim '+tok2))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
        process_comment(gh,event('alice','/nova release '+tok1))
        self.assertIsNone(lease_get(gh.item['body']))
        self.assertIn('nova:queued',label_names(gh.item))
    def test_spoof_release_denied(self):
        gh=FakeGH();tok='a'*32
        process_comment(gh,event('alice','/nova claim '+tok))
        process_comment(gh,event('bob','/nova release '+tok))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
    def test_no_pr_submission_outside_repo(self):
        gh=FakeGH();tok='a'*32
        process_comment(gh,event('alice','/nova claim '+tok))
        process_comment(gh,event('alice',f'/nova submitted {tok} https://github.com/elsewhere/repo/pull/4'))
        self.assertIn('nova:claimed',label_names(gh.item))


# ---------------------------------------------------------------------------
# Owner/admin queue admission (#135): synthetic evidence, no network.
# ---------------------------------------------------------------------------

NOW = 1793275200
REPO = policy.Repository(41, 51, 'example-org', 'reconstruction')
NUMBER = 5
APPROVER_ID = 101
APPROVER = 'owner'
READER = 999


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def issue_payload(number=NUMBER, body='Priority: 50\nAllowed paths: foo/',
                  updated=NOW - 60, labels=('nova:queued', 'nova:approved:900'), repo=REPO):
    return {
        'id': 500 + number, 'number': number, 'title': 'Bound task', 'body': body,
        'updated_at': iso(updated), 'state': 'open',
        'labels': [{'name': name} for name in labels],
        'repository_url': f'{policy.API_ORIGIN}{repo.path}',
        'url': f'{policy.API_ORIGIN}{repo.path}/issues/{number}',
    }


def approval_record(issue, *, arguments=None, nonce='c' * 64, actor_id=APPROVER_ID,
                    actor_login=APPROVER, action=policy.Action.APPROVE_QUEUE,
                    repo=REPO, issued=NOW - 20, expires=NOW + 600):
    arguments = {'transition': 'queue'} if arguments is None else arguments
    request = policy.Request(repo, action, issue['number'], policy.operation_digest(arguments),
                             arguments, nonce, None)
    binding = policy.request_binding(request, issue)
    record = {**binding, 'kind': 'nova3.approval', 'actor_id': actor_id,
              'actor_login': actor_login, 'issued_at': iso(issued), 'expires_at': iso(expires)}
    return request, binding, record


class FakeCollector:
    """Synthetic authenticated transport; every value is caller-controlled."""

    def __init__(self, *, issue=None, permission='admin', membership=None,
                 comment_user=None, record=None, comment_edited=False, fail=False):
        self.issue_data = issue or issue_payload()
        if record is None:
            _, _, record = approval_record(self.issue_data)
        self.record = record
        self.permission_level = permission
        self.membership_data = membership
        self.comment_user = comment_user or {'id': APPROVER_ID, 'login': APPROVER, 'type': 'User'}
        self.comment_edited = comment_edited
        self.fail = fail

    def _maybe_fail(self):
        if self.fail:
            raise co.EvidenceUnavailable('synthetic GitHub outage')

    def identity(self):
        self._maybe_fail()
        return READER

    def repository(self):
        self._maybe_fail()
        return {'id': REPO.id, 'name': REPO.name, 'private': False,
                'owner': {'id': REPO.owner_id, 'login': REPO.owner, 'type': 'Organization'}}

    def issue(self, number):
        self._maybe_fail()
        return json.loads(json.dumps(self.issue_data))

    def comment(self, comment_id):
        self._maybe_fail()
        created = iso(NOW - 19)
        return {'id': comment_id, 'issue_url': f'{policy.API_ORIGIN}{REPO.path}/issues/{NUMBER}',
                'created_at': created, 'updated_at': iso(NOW - 1) if self.comment_edited else created,
                'user': dict(self.comment_user), 'body': json.dumps(self.record)}

    def user(self, login):
        self._maybe_fail()
        return {'id': self.comment_user['id'], 'login': login, 'type': 'User'}

    def permission(self, login):
        self._maybe_fail()
        return {'user': {'id': self.comment_user['id'], 'login': login, 'type': 'User'},
                'permission': self.permission_level, 'role_name': self.permission_level}

    def membership(self, login):
        self._maybe_fail()
        return self.membership_data


def gate_for(collector):
    return co.QueueApprovalGate(REPO, collector, clock=lambda: NOW)


class GateGH(FakeGH):
    def __init__(self, issue=None):
        super().__init__()
        self.repo = 'example-org/reconstruction'
        self.item = issue or issue_payload()
        self.patches = []

    def issue(self, n):
        return json.loads(json.dumps(self.item))

    def patch(self, path, payload):
        self.patches.append(payload)
        if 'body' in payload:
            self.item['body'] = payload['body']
        if 'labels' in payload:
            self.item['labels'] = [{'name': name} for name in payload['labels']]
        if 'state' in payload:
            self.item['state'] = payload['state']


class QueueGateTests(unittest.TestCase):
    def test_approved_owner_task_admits_exact_revision(self):
        collector = FakeCollector()
        decision = gate_for(collector).verify(collector.issue_data)
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.approval_id, 900)
        self.assertIsInstance(decision.binding, dict)
        self.assertEqual(len(decision.task_text_sha256), 64)

    def test_missing_or_ambiguous_approval_locator_denies(self):
        issue = issue_payload(labels=('nova:queued',))
        self.assertFalse(gate_for(FakeCollector(issue=issue)).verify(issue).allowed)
        ambiguous = issue_payload(labels=('nova:queued', 'nova:approved:900', 'nova:approved:901'))
        self.assertEqual(gate_for(FakeCollector(issue=ambiguous)).verify(ambiguous).reason,
                         'owner_approval_locator_required')
        marker = dict(issue, labels=[{'name': 'nova:queued'}],
                      body=issue['body'] + '\n<!-- NOVA3_APPROVAL_COMMENT: 900 -->')
        self.assertTrue(gate_for(FakeCollector(issue=marker)).verify(marker).allowed)

    def test_maintainer_or_spoofed_author_approval_denied(self):
        collector = FakeCollector(permission='maintain')
        self.assertEqual(gate_for(collector).verify(collector.issue_data).reason,
                         policy.Reason.APPROVAL_REQUIRED.value)
        issue = issue_payload()
        spoof = FakeCollector(issue=issue, comment_user={'id': 202, 'login': 'maintainer', 'type': 'User'})
        self.assertFalse(gate_for(spoof).verify(issue).allowed)

    def test_edited_task_body_and_comment_denied(self):
        issue = issue_payload()
        collector = FakeCollector(issue=issue)
        edited = dict(issue, body='Priority: 0\nAllowed paths: everywhere/')
        collector.issue_data = edited
        self.assertFalse(gate_for(collector).verify(edited).allowed)
        collector = FakeCollector(issue=issue, comment_edited=True)
        self.assertFalse(gate_for(collector).verify(issue).allowed)

    def test_changed_binding_arguments_nonce_action_and_repo_denied(self):
        issue = issue_payload()
        for kwargs in ({'actor_id': 202}, {'actor_login': 'crjase'},
                       {'action': policy.Action.MERGE_PR},
                       {'repo': policy.Repository(42, 51, 'example-org', 'reconstruction')},
                       {'issued': NOW - 90}):
            with self.subTest(kwargs=kwargs):
                collector = FakeCollector(issue=issue)
                _, _, record = approval_record(issue, **kwargs)
                collector.record = record
                self.assertFalse(gate_for(collector).verify(issue).allowed)

    def test_github_failure_and_role_revocation_fail_closed(self):
        collector = FakeCollector(fail=True)
        self.assertEqual(gate_for(collector).verify(collector.issue_data).reason,
                         policy.Reason.INVALID_EVIDENCE.value)
        collector = FakeCollector(permission='write')
        self.assertFalse(gate_for(collector).verify(collector.issue_data).allowed)


class CoordinatorAdmissionTests(unittest.TestCase):
    def test_unapproved_claim_mutates_nothing(self):
        gh = GateGH(issue_payload(labels=('nova:queued',)))
        self.assertFalse(co.handle_event(gh, event('alice', '/nova claim ' + 'a' * 32),
                                         gate_for(FakeCollector(issue=gh.item))))
        self.assertEqual(gh.messages, [])
        self.assertNotIn('nova:claimed', label_names(gh.item))
        self.assertEqual(len(gh.patches), 1)
        self.assertIn('nova:attention', label_names(gh.item))
        self.assertNotIn('body', gh.patches[0])

    def test_approved_claim_records_binding_and_duplicate_claim_denied(self):
        gh = GateGH()
        gate = gate_for(FakeCollector(issue=gh.item))
        token = 'a' * 32
        self.assertTrue(co.handle_event(gh, event('alice', f'/nova claim {token}'), gate))
        lease = lease_get(gh.item['body'])
        self.assertEqual(lease['owner'], 'alice')
        self.assertEqual(lease['approval_id'], 900)
        self.assertIsInstance(lease['approval_binding'], dict)
        self.assertIn('nova:claimed', label_names(gh.item))
        # Duplicate claim from another actor never steals the lease.
        saved = gh.item['body']
        co.handle_event(gh, event('bob', '/nova claim ' + 'b' * 32), gate)
        self.assertEqual(gh.item['body'], saved)
        self.assertEqual(lease_get(gh.item['body'])['owner'], 'alice')
        # Replayed claim by the same actor/token is an idempotent no-op.
        patches = len(gh.patches)
        co.handle_event(gh, event('alice', f'/nova claim {token}'), gate)
        self.assertEqual(len(gh.patches), patches)

    def test_release_revalidates_and_requeues_approved_task(self):
        gh = GateGH()
        gate = gate_for(FakeCollector(issue=gh.item))
        token = 'a' * 32
        co.handle_event(gh, event('alice', f'/nova claim {token}'), gate)
        self.assertTrue(co.handle_event(gh, event('alice', f'/nova release {token}'), gate))
        self.assertIn('nova:queued', label_names(gh.item))
        self.assertIsNone(lease_get(gh.item['body']))

    def test_legacy_lease_without_approval_is_quarantined_not_requeued(self):
        gh = GateGH()
        gh.item['labels'] = [{'name': 'nova:claimed'}]
        gh.item['body'] = 'Priority: 50\nAllowed paths: foo/\n\n<!-- NOVA3_LEASE_START\n' + \
                          json.dumps({'owner': 'alice', 'token': 'a' * 32,
                                      'expires_at': iso(NOW + 3600)}) + '\nNOVA3_LEASE_END -->'
        gate = gate_for(FakeCollector(issue=gh.item))
        self.assertFalse(co.handle_event(gh, event('alice', '/nova release ' + 'a' * 32), gate))
        self.assertIn('nova:attention', label_names(gh.item))
        self.assertNotIn('nova:queued', label_names(gh.item))
        self.assertEqual(len(gh.messages), 1)
        self.assertIn('owner_approval_required', gh.messages[0])

    def test_tampered_task_text_before_release_is_quarantined(self):
        gh = GateGH()
        gate = gate_for(FakeCollector(issue=gh.item))
        token = 'a' * 32
        co.handle_event(gh, event('alice', f'/nova claim {token}'), gate)
        gh.item['body'] = gh.item['body'].replace('Allowed paths: foo/', 'Allowed paths: everywhere/')
        self.assertFalse(co.handle_event(gh, event('alice', f'/nova release {token}'), gate))
        self.assertIn('nova:attention', label_names(gh.item))
        self.assertIn('task_text_edited', gh.messages[0])

    def test_expired_legacy_claim_sweep_quarantines(self):
        gh = GateGH()
        gh.item['labels'] = [{'name': 'nova:claimed'}]
        gh.item['body'] = 'Priority: 50\n\n<!-- NOVA3_LEASE_START\n' + \
                          json.dumps({'owner': 'alice', 'token': 'a' * 32,
                                      'expires_at': iso(NOW - 10)}) + '\nNOVA3_LEASE_END -->'
        co.sweep(gh, gate_for(FakeCollector(issue=gh.item)))
        self.assertIn('nova:attention', label_names(gh.item))
        self.assertNotIn('nova:queued', label_names(gh.item))

if __name__=='__main__':unittest.main()
