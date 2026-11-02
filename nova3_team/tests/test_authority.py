"""Role resolution must fail closed; only verified admins control the system."""
import json
import pathlib
import tempfile
import unittest

from nova3_team import authority


class TeamTests(unittest.TestCase):
    def test_missing_or_bad_file_is_empty(self):
        self.assertEqual(authority.team_allowlist(pathlib.Path('/nonexistent')), set())

    def test_allowlist_reads_logins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'team.json'
            path.write_text(json.dumps({'admins': ['crjase', '  backup-admin  ', 7]}))
            self.assertEqual(authority.team_allowlist(path), {'crjase', 'backup-admin'})


class ResolveTests(unittest.TestCase):
    def test_repo_admin_is_admin(self):
        role = authority.resolve('a', github_permission='ADMIN')
        self.assertEqual(role['role'], authority.ADMIN)

    def test_org_owner_is_admin(self):
        self.assertEqual(authority.resolve('a', github_permission='READ',
                                          org_owner=True)['role'], authority.ADMIN)

    def test_team_member_is_admin(self):
        role = authority.resolve('a', github_permission='READ', team={'a'})
        self.assertEqual(role['source'], 'dsh-team')

    def test_write_is_only_a_contributor(self):
        role = authority.resolve('a', github_permission='WRITE', team={'b'})
        self.assertEqual(role['role'], authority.CONTRIBUTOR)

    def test_unknown_and_anonymous_are_never_admin(self):
        self.assertEqual(authority.resolve('a')['role'], authority.UNKNOWN)
        self.assertEqual(authority.resolve('')['role'], authority.UNKNOWN)

    def test_require_admin_fails_closed(self):
        with self.assertRaises(PermissionError):
            authority.require_admin(login='a', github_permission='WRITE')
        self.assertEqual(authority.require_admin(login='a', github_permission='ADMIN')['role'],
                         authority.ADMIN)


if __name__ == '__main__':
    unittest.main()
