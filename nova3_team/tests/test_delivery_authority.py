"""Final delivery rechecks the owner's live authority at write boundaries."""
import pathlib
import tempfile
import unittest
from unittest import mock
from nova3_team import autopilot, autoadmit, delivery, delivery_backend


class DeliveryAuthorityTests(unittest.TestCase):
    def check_rejected(self, change, actor=None, permission='admin', during_permission=None):
        grant = {'repo': 'O/R', 'enabled': True, 'auto_merge': True, 'actor': 'owner', 'actor_id': 5}
        writes = []
        def api(path, *, method='GET', payload=None):
            if method != 'GET':
                writes.append(path)
                return {'merged': True}
            if path == 'user':
                return actor or {'id': 5, 'login': 'owner'}
            if path.endswith('/permission'):
                if during_permission:
                    during_permission(grant)
                return {'permission': permission}
            if path.endswith('pulls?state=open&per_page=100'):
                return [{'number': 1}]
            self.fail('Unexpected API read')
        def attempt(repo, number, folder, **kwargs):
            change(grant)
            with self.assertRaises(RuntimeError):
                kwargs['api']('repos/O/R/pulls/1/merge', method='PUT', payload={'sha': 'head'})
            return {'state': 'retryable', 'reason': 'owner_authority_unavailable'}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(autopilot, 'load_grant', side_effect=lambda f: dict(grant)), \
             mock.patch.object(autoadmit, '_api', side_effect=api), \
             mock.patch.object(delivery, 'process_pr', side_effect=attempt), \
             mock.patch.object(delivery_backend, 'RuntimeBackend') as backend:
            backend.return_value.sessions_started = 0
            report = autopilot.merge_validated('O/R', pathlib.Path(folder))
        self.assertEqual(writes, [])
        self.assertEqual(report['merged'], [])

    def test_malformed_merge_flag_is_not_treated_as_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)
            autopilot.save_grant(path, {'schema': autopilot.SCHEMA, 'repo': 'O/R',
                'enabled': True, 'auto_merge': 'false', 'actor': 'owner', 'actor_id': 5})
            self.assertFalse(autopilot.load_grant(path)['auto_merge'])

    def test_off_during_review_prevents_merge(self):
        self.check_rejected(lambda grant: grant.update(enabled=False))

    def test_merge_off_during_review_prevents_merge(self):
        self.check_rejected(lambda grant: grant.update(auto_merge=False))

    def test_wrong_repository_never_inherits_another_grant(self):
        self.check_rejected(lambda grant: grant.update(repo='Different/Repo'))

    def test_changed_login_or_lost_admin_prevents_merge(self):
        self.check_rejected(lambda g: None, actor={'id': 6, 'login': 'other'})
        self.check_rejected(lambda g: None, permission='write')

    def test_replacing_grant_actor_after_identity_read_prevents_write(self):
        self.check_rejected(lambda grant: None, during_permission=lambda grant:
                            grant.update(actor='new-owner', actor_id=6))

    def test_turning_off_after_identity_read_still_prevents_write(self):
        # Covers the extra post-network grant read, not just the first read.
        grant = {'repo': 'O/R', 'enabled': True, 'auto_merge': True, 'actor': 'owner', 'actor_id': 5}
        writes = []
        def api(path, *, method='GET', payload=None):
            if method != 'GET':
                writes.append(path)
                return {}
            if path.endswith('pulls?state=open&per_page=100'):
                return [{'number': 1}]
            if path == 'user':
                return {'id': 5, 'login': 'owner'}
            if path.endswith('/permission'):
                grant['enabled'] = False
                return {'permission': 'admin'}
            self.fail('Unexpected read')
        def process(repo, number, folder, **kwargs):
            with self.assertRaises(RuntimeError):
                kwargs['api']('repos/O/R/pulls/1/merge', method='PUT', payload={})
            return {'state': 'retryable', 'reason': 'revoked'}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(autopilot, 'load_grant', side_effect=lambda f: dict(grant)), \
             mock.patch.object(autoadmit, '_api', side_effect=api), \
             mock.patch.object(delivery, 'process_pr', side_effect=process), \
             mock.patch.object(delivery_backend, 'RuntimeBackend') as backend:
            backend.return_value.sessions_started = 0
            autopilot.merge_validated('O/R', pathlib.Path(folder))
        self.assertEqual(writes, [])
