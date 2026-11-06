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
        self.assertEqual([f['id'] for f in findings],
                         ['outside_allowed_paths', 'protected_path'])

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

    def test_authority_files_require_manual_review_even_with_explicit_scope(self):
        required = {'nova3_team/' + name for name in (
            'autopilot.py', 'autoadmit.py', 'access_policy.py', 'coordinator.py', 'common.py',
            'worker.py', 'delivery.py', 'delivery_backend.py', 'model_agents.py', 'task_agents.py',
            'agent_guard.mjs', 'dsh_runtime.py', 'dsh_model_router.py', 'dsh_supervisor.py',
            'dsh_planner.py', 'scope_guard.py', 'reliability.py', 'review.py')}
        self.assertTrue(required <= review.PROTECTED_EXACT)
        for path in required:
            with self.subTest(path=path):
                findings = review.findings_for(files=[{'path': path}], diff='+safe = True',
                                               issue_body='Allowed paths: ' + path)
                self.assertEqual([f['id'] for f in findings], ['protected_path'])
                self.assertIn('manual_required', findings[0]['fix'])
        self.assertIn('nova3_team/review.py', review.PROTECTED_EXACT)

    def test_nested_instruction_and_control_paths_are_protected(self):
        for path in ('AGENTS.md', 'CLAUDE.md', 'reconstruction/AGENTS.md',
                     'nova3_team/nested/CLAUDE.md', '.agents/policy.json',
                     'reconstruction/.agents/role.md', '.dsh/config.yaml',
                     'reconstruction/.dsh/control.json', '.env',
                     'reconstruction/.env.local'):
            with self.subTest(path=path):
                findings = review.findings_for(files=[{'path': path}], diff='',
                                               issue_body='Allowed paths: ' + path)
                self.assertIn('protected_path', [f['id'] for f in findings])

    def test_unrelated_helpers_tests_and_similar_basenames_remain_allowed(self):
        for path in ('nova3_team/dsh_status.py', 'nova3_team/object_matching.py',
                     'nova3_team/tests/test_delivery.py', 'nova3_team/delivery_helpers.py',
                     'reconstruction/agents.md.cpp'):
            with self.subTest(path=path):
                self.assertEqual(review.findings_for(files=[{'path': path}], diff='+value = 1',
                                                    issue_body='Allowed paths: ' + path), [])

    def test_assembly_files_require_provenance_review_but_removal_is_allowed(self):
        for path in ('reconstruction/recovered/recovered.s', 'reconstruction/start.S',
                     'reconstruction/global.asm'):
            with self.subTest(path=path):
                findings = review.findings_for(files=[{'path': path, 'status': 'added'}],
                    diff='+nop', issue_body='Allowed paths: ' + path)
                self.assertEqual([f['id'] for f in findings], ['assembly_payload'])
                self.assertEqual(review.findings_for(files=[{'path': path, 'status': 'removed'}],
                    diff='-nop', issue_body='Allowed paths: ' + path), [])

    def test_production_inline_global_asm_and_payload_includes_are_refused(self):
        for added in ('asm("nop");', '__asm__("nop");', '__asm volatile ("nop");',
                      'asm goto ("branch");', 'asm\nvolatile\n("nop");',
                      'asm/**/("nop");', '__asm__ /* macro expansion */ (PAYLOAD);',
                      '".incbin \\\"original.bin\\\""', '".include \\\"global.s\\\""'):
            with self.subTest(added=added):
                patch = '\n'.join('+' + line for line in added.splitlines())
                findings = review.findings_for(files=[{'path': 'reconstruction/engine/file.cpp',
                    'patch': patch}], diff='', issue_body='Allowed paths: reconstruction/engine/file.cpp')
                self.assertEqual([f['id'] for f in findings], ['assembly_payload'])
        findings = review.findings_for(files=[{'path': 'reconstruction/include/file.hpp'}],
            diff='+asm("payload");', issue_body='Allowed paths: reconstruction/include/file.hpp')
        self.assertEqual([f['id'] for f in findings], ['assembly_payload'])

    def test_synthetic_asm_strings_in_tests_and_tools_do_not_taint_source(self):
        files = [{'path': 'reconstruction/engine/genuine.cpp', 'patch': '+return a + b;'},
                 {'path': 'reconstruction/tests/asm_test.cpp', 'patch': '+asm("fixture");'},
                 {'path': 'tools/compare/fixture.py', 'patch': '+text = ".incbin fixture"'}]
        scope = 'Allowed paths: ' + ', '.join(f['path'] for f in files)
        diff = '\n'.join(f"diff --git a/{f['path']} b/{f['path']}\n{f['patch']}" for f in files)
        self.assertEqual(review.findings_for(files=files, diff=diff, issue_body=scope), [])
        # Same guarantee for the CLI path, where files contain no REST patch.
        self.assertEqual(review.findings_for(files=[{'path': f['path']} for f in files],
                                            diff=diff, issue_body=scope), [])
        self.assertEqual(review.findings_for(files=[{'path': 'reconstruction/engine/genuine.cpp',
            'patch': '-asm("old payload");\n+return a + b;'}], diff='',
            issue_body='Allowed paths: reconstruction/engine/genuine.cpp'), [])

    def test_linked_issue_parsing(self):
        self.assertEqual(review._linked_issue('Closes #171'), 171)
        self.assertEqual(review._linked_issue('Fixes #9 and more'), 9)
        self.assertEqual(review._linked_issue('Related to #171'), 171)
        self.assertIsNone(review._linked_issue('no reference'))


if __name__ == '__main__':
    unittest.main()
