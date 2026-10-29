"""Offline tests for the guided Accounts screen model (issue #125).

Every provider/gh boundary is injected or mocked.  No test starts a CLI,
submits a prompt, reads a credential or touches the network.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('nova_provider_accounts', HERE.parent / 'provider_accounts.py')
pa = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pa
spec.loader.exec_module(pa)
runtime = pa.provider_runtime


def status(provider_id, state='available', reason='fixture'):
    return runtime.ProviderStatus(provider_id, runtime.RuntimeState(state), '/fake/' + provider_id,
                                 '1.2.3', reason)


def auth(state='authenticated', reason='fixture auth'):
    return runtime.AuthEvidence(runtime.AuthState(state), reason)


class RegistryTests(unittest.TestCase):
    def make(self, **kwargs):
        kwargs.setdefault('probe', mock.Mock(return_value=status('codex')))
        kwargs.setdefault('auth', mock.Mock(return_value=auth()))
        return pa.AccountsController('owner/repo', **kwargs)

    def test_rows_are_projected_from_the_verified_registry(self):
        c = self.make()
        self.assertEqual(c.ids, runtime.provider_ids())
        self.assertEqual(tuple(row.provider_id for row in c.rows()), c.ids)
        for row in c.rows():
            spec = runtime.PROVIDERS[row.provider_id]
            self.assertEqual(row.display_name, spec.display_name)
            self.assertEqual(row.install_url, spec.install_url)
            self.assertEqual(row.docs_url, spec.docs_url)
            self.assertEqual(row.guidance, spec.login_guidance)
        self.assertEqual(c.rows()[0].label, 'UNKNOWN')

    def test_missing_provider_is_not_installed_and_does_not_probe_auth(self):
        probe = mock.Mock(return_value=status('codex', 'missing', 'Optional CLI absent.'))
        auth_mock = mock.Mock()
        c = pa.AccountsController('owner/repo', probe=probe, auth=auth_mock)
        c.refresh_providers()
        row = c.rows()[0]
        self.assertEqual(row.install_state, pa.InstallState.NOT_INSTALLED)
        self.assertEqual(row.account_state, pa.AccountState.UNAVAILABLE)
        self.assertEqual(row.label, 'NOT INSTALLED')
        auth_mock.assert_not_called()

    def test_installed_but_logged_out_is_honest(self):
        c = pa.AccountsController('owner/repo',
                                  probe=mock.Mock(return_value=status('codex')),
                                  auth=mock.Mock(return_value=auth('unauthenticated', 'No login.')))
        c.refresh_providers()
        row = c.rows()[0]
        self.assertEqual(row.install_state, pa.InstallState.INSTALLED)
        self.assertEqual(row.account_state, pa.AccountState.UNAUTHENTICATED)
        self.assertEqual(row.label, 'LOGGED OUT')
        self.assertFalse(row.ready)

    def test_unknown_auth_stays_unknown_and_never_implies_login(self):
        c = pa.AccountsController('owner/repo',
                                  probe=mock.Mock(return_value=status('codex')),
                                  auth=mock.Mock(return_value=auth('unknown', 'No status command.')))
        c.refresh_providers()
        row = c.rows()[0]
        self.assertEqual(row.label, 'LOGIN UNKNOWN')
        self.assertFalse(row.ready)

    def test_probe_failure_remains_unknown(self):
        c = pa.AccountsController('owner/repo',
                                  probe=mock.Mock(side_effect=RuntimeError('boom')),
                                  auth=mock.Mock(side_effect=AssertionError('must not run')))
        c.refresh_providers()
        row = c.rows()[0]
        self.assertEqual(row.install_state, pa.InstallState.UNKNOWN)
        self.assertEqual(row.account_state, pa.AccountState.UNKNOWN)
        self.assertNotIn('boom', row.detail)

    def test_unknown_provider_id_is_rejected_without_echo_or_command(self):
        probe = mock.Mock()
        c = pa.AccountsController('owner/repo', probe=probe, auth=mock.Mock())
        report = c.test_connection('codex; rm -rf / #$(id)')
        self.assertFalse(report.ok)
        self.assertEqual(report.summary, 'UNKNOWN PROVIDER')
        self.assertNotIn('rm -rf', report.detail)
        self.assertNotIn('rm -rf', report.summary)
        probe.assert_not_called()

    def test_test_connection_is_bounded_and_reports_redacted_detail(self):
        secret = 'ghp_' + 'A' * 36
        probe = mock.Mock(return_value=status('codex', reason='ok ' + secret + ' token=abc123'))
        auth_mock = mock.Mock(return_value=auth('authenticated', 'Bearer ' + 'x' * 20))
        c = pa.AccountsController('owner/repo', probe=probe, auth=auth_mock)
        report = c.test_connection('codex')
        self.assertTrue(report.ok)
        self.assertIn('READY' if False else 'LOGGED IN', report.summary)
        self.assertNotIn(secret, report.detail)
        self.assertNotIn('abc123', report.detail)
        probe.assert_called_once_with('codex')
        auth_mock.assert_called_once_with('codex')

    def test_redaction_masks_common_fake_secrets(self):
        for secret in ('ghp_' + 'B' * 36, 'github_pat_' + 'C' * 30, 'sk-' + 'd' * 20,
                       'Bearer abcdef', 'password=hunter2', 'api_key: zzz999'):
            text = pa.redact('provider said ' + secret)
            self.assertNotIn(secret.split('=')[-1].split(':')[-1].strip(), text)
            self.assertNotIn(secret, text)

    def test_enable_disable_persists_private_preference_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'accounts.json'
            c = pa.AccountsController('owner/repo', path, probe=mock.Mock(return_value=status('codex')),
                                      auth=mock.Mock(return_value=auth()))
            self.assertTrue(c.toggle_enabled('codex'))
            self.assertFalse(c.rows()[0].enabled)
            self.assertFalse(c.rows()[0].ready)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertFalse(c.toggle_enabled('not-a-provider'))
            self.assertFalse(c.set_enabled('codex', 'yes'))
            again = pa.AccountsController('owner/repo', path)
            self.assertFalse(again.rows()[0].enabled)

    def test_initial_rows_do_not_probe_anything(self):
        probe = mock.Mock(side_effect=AssertionError('render must stay passive'))
        c = pa.AccountsController('owner/repo', probe=probe, auth=mock.Mock())
        for row in c.rows():
            self.assertEqual(row.install_state, pa.InstallState.UNKNOWN)
            self.assertEqual(row.account_state, pa.AccountState.UNKNOWN)
        probe.assert_not_called()


class IdentityTests(unittest.TestCase):
    def test_logged_in_identity_reports_account_and_scopes_only(self):
        c = pa.AccountsController('owner/repo', identity_probe=lambda: pa.GitHubIdentity(
            pa.AccountState.AUTHENTICATED, 'fixture-user', ('repo', 'read:org'),
            'gh auth status succeeded'))
        ident = c.refresh_identity()
        self.assertEqual(ident.label, 'LOGGED IN')
        self.assertEqual(ident.account, 'fixture-user')
        self.assertEqual(ident.scope_label, 'repo, read:org')
        self.assertEqual(ident.role, 'UNKNOWN')  # role needs a separate server check

    def test_logged_out_identity_is_not_reported_ready(self):
        c = pa.AccountsController('owner/repo', identity_probe=lambda: pa.GitHubIdentity(
            pa.AccountState.UNAUTHENTICATED, detail='not logged in'))
        ident = c.refresh_identity()
        self.assertEqual(ident.label, 'LOGGED OUT')
        self.assertEqual(ident.account, '')

    def test_unknown_identity_probe_fails_closed(self):
        c = pa.AccountsController('owner/repo', identity_probe=mock.Mock(side_effect=OSError('offline')))
        ident = c.refresh_identity()
        self.assertEqual(ident.state, pa.AccountState.UNKNOWN)
        self.assertNotIn('offline', ident.detail)

    def test_gh_auth_status_parser_reports_scopes_and_redacts_token(self):
        output = ("github.com\n  ✓ Logged in to github.com account fixture-user (keyring)\n"
                  "  - Active account: true\n  - Token scopes: 'repo', 'read:org', 'workflow'")
        c = pa.AccountsController('owner/repo')
        with mock.patch.object(pa.provider_runtime.shutil, 'which', return_value='/usr/bin/gh'), \
                mock.patch.object(pa, '_bounded_gh', return_value=(0, output + '\n token=ghp_' + 'E' * 36)):
            ident = c._probe_github_identity()
        self.assertEqual(ident.state, pa.AccountState.AUTHENTICATED)
        self.assertEqual(ident.account, 'fixture-user')
        self.assertEqual(ident.scopes, ('repo', 'read:org', 'workflow'))
        self.assertNotIn('ghp_', ident.detail)

    def test_gh_not_logged_in_maps_to_logged_out(self):
        c = pa.AccountsController('owner/repo')
        with mock.patch.object(pa.provider_runtime.shutil, 'which', return_value='/usr/bin/gh'), \
                mock.patch.object(pa, '_bounded_gh', return_value=(1, 'You are not logged into any GitHub hosts.')):
            ident = c._probe_github_identity()
        self.assertEqual(ident.state, pa.AccountState.UNAUTHENTICATED)

    def test_members_are_read_only_and_use_official_invitation_url(self):
        rows = [{'login': 'alice', 'role_name': 'admin', 'permissions': {'admin': True}},
                {'login': 'bob', 'role_name': 'write', 'permissions': {'push': True}},
                {'login': 'bad login; rm -rf /', 'role_name': 'read'}]
        c = pa.AccountsController('owner/repo')
        self.assertEqual(c.invitation_url,
                         'https://github.com/owner/repo/settings/access')
        with mock.patch.object(pa.provider_runtime.shutil, 'which', return_value='/usr/bin/gh'), \
                mock.patch.object(pa, '_bounded_gh', return_value=(0, json.dumps(rows))) as run:
            view = c.refresh_members()
        self.assertEqual(view.state, 'READ ONLY')
        self.assertEqual([m.login for m in view.members], ['alice', 'bob'])
        self.assertEqual(view.members[0].role, 'ADMIN')
        args = run.call_args.args[0]
        self.assertTrue(any('collaborators?per_page=50' in arg for arg in args))
        self.assertNotIn('--method', args)
        self.assertNotIn('POST', args)

    def test_members_unavailable_does_not_fabricate_rows(self):
        c = pa.AccountsController('owner/repo')
        with mock.patch.object(pa.provider_runtime.shutil, 'which', return_value=None):
            view = c.refresh_members()
        self.assertEqual(view.state, 'UNAVAILABLE')
        self.assertEqual(view.members, ())


class OwnerGateTests(unittest.TestCase):
    def controller(self, role):
        return pa.AccountsController('owner/repo', role_resolver=lambda: role)

    def test_contributor_is_denied_owner_actions(self):
        for role in ('READ', 'WRITE', 'TRIAGE', '', 'UNKNOWN', 'admin'):
            c = self.controller(role)
            allowed, resolved = c.authorize_owner()
            self.assertFalse(allowed, role)
            self.assertIsNone(c.owner_member_url())
            self.assertEqual(c.identity.role, 'UNKNOWN' if role not in pa.ROLE_VALUES else role)

    def test_owner_roles_allow_only_the_official_url(self):
        for role in ('ADMIN', 'MAINTAIN'):
            c = self.controller(role)
            self.assertTrue(c.authorize_owner()[0])
            self.assertEqual(c.owner_member_url(),
                             'https://github.com/owner/repo/settings/access')

    def test_role_failure_fails_closed(self):
        c = pa.AccountsController('owner/repo', role_resolver=mock.Mock(side_effect=RuntimeError('offline')))
        allowed, role = c.authorize_owner()
        self.assertFalse(allowed)
        self.assertEqual(role, 'UNKNOWN')

    def test_role_is_rederived_each_time_and_can_be_revoked(self):
        roles = iter(('ADMIN', 'READ'))
        c = pa.AccountsController('owner/repo', role_resolver=lambda: next(roles))
        self.assertTrue(c.authorize_owner()[0])
        self.assertIsNone(c.owner_member_url())
        self.assertEqual(c.identity.role, 'READ')


class LoginTests(unittest.TestCase):
    def test_login_guidance_uses_registry_and_official_url(self):
        c = pa.AccountsController('owner/repo')
        guidance = c.login_guidance('codex')
        self.assertIn(runtime.PROVIDERS['codex'].login_guidance, guidance)
        self.assertEqual(c.vendor_login_url('codex'), runtime.PROVIDERS['codex'].docs_url)

    def test_unknown_login_provider_returns_no_url_or_text(self):
        c = pa.AccountsController('owner/repo')
        self.assertIsNone(c.vendor_login_url('codex; rm -rf /'))
        self.assertIn('Unknown provider', c.login_guidance('..'))

    def test_unsafe_urls_are_never_opened(self):
        opener = mock.Mock(return_value=True)
        c = pa.AccountsController('owner/repo', opener=opener)
        for url in ('http://github.com/a/b', 'file:///etc/passwd', 'javascript:alert(1)',
                    'https://evil.example/login', '', None):
            self.assertFalse(c.open_url(url))
        opener.assert_not_called()
        self.assertTrue(c.open_url('https://github.com/a/b/settings/access'))
        opener.assert_called_once()


if __name__ == '__main__':
    unittest.main()
