import unittest
from nova3_team.reliability import failure_decision, fingerprint, code_for

class ReliabilityTests(unittest.TestCase):
    def test_pr_reference_error_blocks_without_repeat(self):
        d=failure_decision({},"pull request create failed: GraphQL: Head sha can't be blank")
        self.assertTrue(d['blocked'])
        self.assertEqual(d['code'], 'INVALID_PR_REFERENCE')
    def test_transient_error_one_retry_then_block(self):
        a=failure_decision({},'HTTP 502 from GitHub')
        self.assertFalse(a['blocked'])
        self.assertEqual(a['delay'],600)
        b=failure_decision({'failure_key':a['key'],'failure_count':1},'HTTP 502 from GitHub')
        self.assertTrue(b['blocked'])
    def test_different_error_resets_counter(self):
        a=failure_decision({'failure_key':'wrong','failure_count':100},'Some new error')
        self.assertEqual(a['count'],1)
    def test_no_progress_is_not_reported_complete(self):
        self.assertEqual(code_for('No source changes after bounded AI sessions'), 'NO_SOURCE_PROGRESS')
