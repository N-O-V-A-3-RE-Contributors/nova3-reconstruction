"""Installation-token regressions: no token disclosure, network or queue writes."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
import unittest
from unittest import mock
import urllib.error

from nova3_team import access_policy as p, coordinator as co
from nova3_team.tests.test_coordinator import FakeCollector, REPO, NOW, gate_for
from nova3_team.tests.test_access_policy import fixture


class InstallationTransportTests(unittest.TestCase):
    def setUp(self):
        self.data = FakeCollector()
        self.collector = co.GitHubEvidenceCollector('SYNTHETIC-ONLY-TOKEN', REPO)
        self.paths = []

    def api(self, path):
        self.paths.append(path)
        if path == '/user':
            raise co.EvidenceUnavailable('GitHub GET /user: HTTP 403')
        if path.startswith('/installation/repositories?'):
            return {'total_count': 1, 'repositories': [self.data.repository()]}
        if path == REPO.path: return self.data.repository()
        if '/issues/comments/' in path: return self.data.comment(900)
        if '/issues/' in path: return self.data.issue(5)
        if '/collaborators/' in path: return self.data.permission('owner')
        if path == '/users/owner': return self.data.user('owner')
        if '/memberships/' in path: raise co.EvidenceUnavailable('membership unavailable')
        raise AssertionError('Unexpected endpoint: ' + path)

    def test_actions_reader_proves_scope_and_live_owner_approval(self):
        with mock.patch.object(self.collector, '_get', side_effect=self.api), redirect_stderr(io.StringIO()):
            gate = co.QueueApprovalGate(REPO, self.collector, clock=lambda: NOW)
            reader = gate.preflight()
            self.assertIsInstance(reader, p.InstallationReader)
            decision = gate.verify(self.data.issue_data)
            self.assertTrue(decision.allowed, decision.reason)
            lease = dict(approval_binding=decision.binding, approval_id=900,
                         task_text_sha256=decision.task_text_sha256)
            self.assertTrue(gate.revalidate(self.data.issue_data, lease).allowed)
            self.data.permission_level = 'maintain'
            self.assertFalse(gate.verify(self.data.issue_data).allowed)
            self.assertFalse(gate.revalidate(self.data.issue_data, lease).allowed)
        self.assertNotIn('/app', self.paths)
        self.assertIn('/users/owner', self.paths)
        self.assertIn(REPO.path + '/collaborators/owner/permission', self.paths)

    def test_installation_does_not_authorize_edited_or_missing_approval(self):
        with mock.patch.object(self.collector, '_get', side_effect=self.api), redirect_stderr(io.StringIO()):
            gate = co.QueueApprovalGate(REPO, self.collector, clock=lambda: NOW)
            self.data.issue_data['body'] += '\nUnapproved edits'
            self.assertFalse(gate.verify(self.data.issue_data).allowed)
            self.data.issue_data['labels'] = []
            self.assertFalse(gate.verify(self.data.issue_data).allowed)

    def test_pat_user_path_is_preserved(self):
        with mock.patch.object(self.collector, '_get', return_value={
                'id': 101, 'login': 'owner', 'type': 'User'}) as get:
            self.assertEqual(self.collector.identity(), 101)
        get.assert_called_once_with('/user')

    def test_malformed_user_identity_cannot_become_installation(self):
        for payload in ({'id': True}, {'id': 101}, {'id': 101, 'type': 'Bot', 'login': 'bot'}):
            with self.subTest(payload=payload), mock.patch.object(self.collector, '_get', return_value=payload) as get:
                with self.assertRaises(co.EvidenceUnavailable): self.collector.identity()
                get.assert_called_once_with('/user')

    def test_missing_malformed_or_wrong_installation_scope_denies(self):
        repo = self.data.repository()
        wrong_owner = deepcopy(repo); wrong_owner['owner']['id'] += 1
        wrong_name = dict(repo, name='elsewhere')
        for listing in ({}, {'total_count': True, 'repositories': [repo]},
                        {'total_count': 0, 'repositories': []},
                        {'total_count': 1, 'repositories': [wrong_owner]},
                        {'total_count': 1, 'repositories': [wrong_name]},
                        {'total_count': 1, 'repositories': [None]},
                        {'total_count': 1, 'repositories': [dict(repo, id=99)]}):
            with self.subTest(listing=listing), mock.patch.object(self.collector, '_get', side_effect=[
                    co.EvidenceUnavailable('403'), listing]):
                with self.assertRaises(co.EvidenceUnavailable): self.collector.identity()

    def test_pagination_is_bounded_and_can_find_target_on_second_page(self):
        page = {'total_count': 101, 'repositories': [{'id': 99}] * 100}
        last = {'total_count': 101, 'repositories': [self.data.repository()]}
        with mock.patch.object(self.collector, '_get', side_effect=[
                co.EvidenceUnavailable('403'), page, last]) as get, redirect_stderr(io.StringIO()):
            self.assertIsInstance(self.collector.identity(), p.InstallationReader)
            self.assertEqual(get.call_args_list[-1].args[0], '/installation/repositories?per_page=100&page=2')
        page['total_count'] = 100000
        with mock.patch.object(self.collector, '_get', side_effect=[
                co.EvidenceUnavailable('403')] + [page] * 10) as get:
            with self.assertRaises(co.EvidenceUnavailable): self.collector.identity()
            self.assertEqual(get.call_count, 11)

    def test_failed_authenticated_installation_requests_deny(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status), mock.patch.object(self.collector, '_get', side_effect=[
                    co.EvidenceUnavailable('403'), co.EvidenceUnavailable(str(status))]):
                with self.assertRaises(co.EvidenceUnavailable): self.collector.identity()

    def test_http_error_reports_only_endpoint_and_status(self):
        secret = 'PRIVATE-RESPONSE-MUST-NOT-APPEAR'
        self.collector._opener = mock.Mock()
        self.collector._opener.open.side_effect = urllib.error.HTTPError(
            'https://api.github.com/user', 403, secret, {'Secret': secret}, io.BytesIO(secret.encode()))
        with self.assertRaises(co.EvidenceUnavailable) as raised:
            self.collector._get('/user')
        self.assertEqual(str(raised.exception), 'GitHub GET /user: HTTP 403')
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn('SYNTHETIC-ONLY-TOKEN', repr(raised.exception))

    def test_preflight_cli_never_sweeps_or_handles_events(self):
        gh = mock.Mock()
        with mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example-org/reconstruction'}), \
                mock.patch.object(co, 'GitHub', return_value=gh), \
                mock.patch.object(co, 'build_gate') as gate, \
                mock.patch.object(co, 'sweep') as sweep, \
                mock.patch.object(co, 'handle_event') as handle, redirect_stdout(io.StringIO()):
            co.main(['--preflight-only'])
        gate.assert_called_once_with(gh)
        sweep.assert_not_called(); handle.assert_not_called()
        gh.assert_not_called()


class InstallationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.reader = p.InstallationReader(REPO.id, REPO.owner_id, 'a' * 64)

    def test_reader_is_not_a_human_viewer_or_executor(self):
        for action in (*p.STEERING_ACTIONS, p.Action.RUN_ASSIGNED_WORK):
            with self.subTest(action=action):
                request, evidence = fixture(action=action, reader=self.reader)
                self.assertFalse(p.authorize(request, evidence, now=NOW).allowed)
                binding = p.request_binding(request, evidence.issue.payload)
                self.assertFalse(p.verify_approval_record(
                    binding, evidence.approvals[0], request, reader=self.reader,
                    now=NOW, organization=True, replay=evidence.replay).allowed)

    def test_standing_approval_only_with_matching_reader_and_live_author(self):
        request, evidence = fixture(reader=self.reader, executor=None)
        evidence = replace(evidence, viewer=None, consent=None)
        decision = p.verify_owner_approval(request, evidence, now=NOW)
        self.assertTrue(decision.allowed, decision.reason)
        for reader in (101, replace(self.reader, owner_id=52),
                       replace(self.reader, channel='b' * 64),
                       {'repository_id': REPO.id, 'owner_id': REPO.owner_id, 'channel': 'a'*64}):
            with self.subTest(reader=reader):
                changed = replace(evidence, issue=replace(evidence.issue, reader_id=reader))
                self.assertFalse(p.verify_owner_approval(request, changed, now=NOW).allowed)
        for replay in (replace(evidence.replay, revoked_comments=frozenset({900})),
                       replace(evidence.replay, consumed=frozenset({f'github:{REPO.id}:comment:900'}))):
            self.assertFalse(p.verify_owner_approval(request, replace(evidence, replay=replay), now=NOW).allowed)
        for permission in ('maintain', 'write', 'read'):
            request, denied = fixture(reader=self.reader, executor=None)
            author = denied.approvals[0].author
            author = replace(author, permission=replace(author.permission,
                             payload={**author.permission.payload, 'permission': permission}))
            denied = replace(denied, approvals=(replace(denied.approvals[0], author=author),))
            self.assertFalse(p.verify_owner_approval(request, denied, now=NOW).allowed)


if __name__ == '__main__': unittest.main()
