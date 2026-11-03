"""Automation must not stop on a local quota while credit remains.

The owner's standing rule: when automation is on, the only automatic pause is a
real provider/credit exhaustion. These tests pin the unlimited defaults and the
explicit opt-in ceiling.
"""
import pathlib
import tempfile
import unittest

from nova3_team import dsh_policy, dsh_runtime


class BudgetDefaults(unittest.TestCase):
    def test_defaults_are_unlimited(self):
        budget = dsh_policy.Budget()
        self.assertEqual(budget.sessions_per_hour, 0)
        self.assertEqual(budget.sessions_per_day, 0)
        self.assertEqual(budget.tokens_per_day, 0)
        self.assertEqual(budget.sessions_per_task, 3)  # anti-loop stays

    def test_zero_is_unlimited_and_positive_is_honoured(self):
        budget = dsh_policy.Budget.from_env({
            'NOVA3_DSH_SESSIONS_PER_HOUR': '0',
            'NOVA3_DSH_SESSIONS_PER_DAY': '12',
            'NOVA3_DSH_TOKENS_PER_DAY': '0',
        })
        self.assertEqual(budget.sessions_per_hour, 0)
        self.assertEqual(budget.sessions_per_day, 12)
        self.assertEqual(budget.tokens_per_day, 0)

    def test_negative_is_treated_as_the_default(self):
        budget = dsh_policy.Budget.from_env({'NOVA3_DSH_SESSIONS_PER_DAY': '-1'})
        self.assertEqual(budget.sessions_per_day, 0)


class NoTimePause(unittest.TestCase):
    def _ledger(self, folder, **record):
        ledger = dsh_policy.SpendLedger(pathlib.Path(folder) / 'spend.jsonl')
        for _ in range(200):
            ledger.append({'task': '1', 'input_tokens': 1000, 'output_tokens': 1000, **record})
        return ledger

    def test_hundreds_of_sessions_today_do_not_stop_a_successful_session(self):
        outcome = dsh_runtime.SessionOutcome(
            status=dsh_runtime.COMPLETED, exit_code=0, session_id='s', turn_reason='completed',
            error_code=None, error_message='', steps=1, tool_calls=0, elapsed_s=1.0)
        with tempfile.TemporaryDirectory() as folder:
            ledger = self._ledger(folder)
            decision = dsh_policy.decide(
                outcome, ledger, dsh_policy.Context(task='2'),
                now=__import__('time').time())
        self.assertEqual(decision.state, dsh_policy.RUNNING, decision.reason)

    def test_an_explicit_ceiling_still_applies(self):
        outcome = dsh_runtime.SessionOutcome(
            status=dsh_runtime.COMPLETED, exit_code=0, session_id='s', turn_reason='completed',
            error_code=None, error_message='', steps=1, tool_calls=0, elapsed_s=1.0)
        with tempfile.TemporaryDirectory() as folder:
            ledger = self._ledger(folder)
            budget = dsh_policy.Budget(sessions_per_day=1)
            decision = dsh_policy.decide(
                outcome, ledger, dsh_policy.Context(task='2', budget=budget),
                now=__import__('time').time())
        self.assertEqual(decision.state, dsh_policy.WAITING_APPROVAL)
        self.assertIn('daily session cap', decision.reason)


if __name__ == '__main__':
    unittest.main()
