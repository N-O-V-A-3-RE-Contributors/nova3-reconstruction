"""The owner/admin review gate must fail closed and never mutate anything."""
import unittest

from nova3_team import review

SCOPE = 'Priority: 2\nAllowed paths: reconstruction/tests/x.py, tools/compare/'


class ScopeTests(unittest.TestCase):
    def test_allowed_paths_parses_csv_and_backticks(self):
        body = 'Allowed paths: `reconstruction/tests/x.py`, tools/compare/'
        self.assertEqual(review.allowed_paths(body),
                         ['reconstruction/tests/x.py', 'tools/compare/'])

    def test_clean_change_passes(self):
        self.assertEqual(
            review.findings_for(files=[{'path': 'tools/compare/y.py'}],
                                diff='+x = 1\n', issue_body=SCOPE), [])

    def test_outside_scope_is_a_blocker(self):
        findings = review.findings_for(files=[{'path': 'nova3_team/worker.py'}],
                                       diff='', issue_body=SCOPE)
        self.assertEqual([f['id'] for f in findings], ['outside_allowed_paths'])

    def test_protected_tree_is_refused_even_when_scoped(self):
        scope = 'Allowed paths: .github/workflows/x.yml'
        findings = review.findings_for(files=[{'path': '.github/workflows/x.yml'}],
                                       diff='', issue_body=scope)
        self.assertIn('protected_path', [f['id'] for f in findings])

    def test_binary_artifact_is_refused(self):
        findings = review.findings_for(files=[{'path': 'tools/compare/y.so'}],
                                       diff='', issue_body=SCOPE)
        self.assertIn('binary_artifact', [f['id'] for f in findings])

    def test_secret_in_added_lines_is_refused(self):
        findings = review.findings_for(
            files=[{'path': 'tools/compare/y.py'}],
            diff='+TOKEN = "ghp_abcdefghijklmnopqrstuvwx"\n', issue_body=SCOPE)
        self.assertEqual(findings[0]['id'], 'possible_secret')

    def test_missing_scope_fails_closed(self):
        findings = review.findings_for(files=[{'path': 'x.py'}], diff='', issue_body='')
        self.assertEqual(findings[0]['id'], 'missing_task_scope')

    def test_oversized_change_is_flagged(self):
        findings = review.findings_for(
            files=[{'path': 'tools/compare/y.py'}],
            diff=''.join('+x\n' for _ in range(review.MAX_ADDED_LINES + 1)), issue_body=SCOPE)
        self.assertIn('oversized_change', [f['id'] for f in findings])

    def test_linked_issue_parsing(self):
        self.assertEqual(review._linked_issue('Closes #171'), 171)
        self.assertEqual(review._linked_issue('Fixes #9 and more'), 9)
        self.assertIsNone(review._linked_issue('no reference'))


if __name__ == '__main__':
    unittest.main()
