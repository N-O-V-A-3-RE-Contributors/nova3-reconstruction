"""Verification retries exactly once, and a real failure still fails.

Reason: this host drops its link for a few seconds at a time. A transient
outage can fail a probe-style test with no defect in the change, and one false
FAIL turns a good checkpoint into a draft PR that never merges.
"""
import unittest

from nova3_team import worker


class VerificationRetryPolicyTests(unittest.TestCase):
    def _sequence(self, codes):
        """An executor that returns the given exit codes in order."""
        remaining = list(codes)
        calls = []

        def execute():
            code = remaining.pop(0)
            calls.append(code)
            label = ' (retry)' if len(calls) > 1 else ''
            return code, f'TASK VERIFY{label}: sample\nEXIT: {code}'

        return execute, calls

    def test_one_transient_failure_is_retried_and_passes(self):
        execute, calls = self._sequence([1, 0])
        passed, text = worker.run_verification_command(execute)
        self.assertTrue(passed)
        self.assertEqual(len(calls), 2)
        self.assertIn('(retry)', text)
        self.assertIn('transient-link guard', text)

    def test_a_real_failure_still_fails_after_the_retry(self):
        execute, calls = self._sequence([1, 1])
        passed, text = worker.run_verification_command(execute)
        self.assertFalse(passed)
        self.assertEqual(len(calls), 2)
        self.assertIn('(retry)', text)

    def test_a_passing_command_runs_once(self):
        execute, calls = self._sequence([0])
        passed, text = worker.run_verification_command(execute)
        self.assertTrue(passed)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('(retry)', text)

    def test_an_unrunnable_command_is_not_retried(self):
        execute, calls = self._sequence([None])
        passed, text = worker.run_verification_command(execute)
        self.assertFalse(passed)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
