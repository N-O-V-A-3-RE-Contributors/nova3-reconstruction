import unittest
from nova3_team.recover_saved import safe_extra, allowed_from_body, is_allowed

BODY = '''Priority: 65
Allowed paths: reconstruction/recovered/libNOVA3_neon/block_002ad444.cpp, coordination/evidence/functions-aa.json
Reference library: libNOVA3_neon.so
Function boundaries (tool-identified only): 0x002ad444, 0x002ad998
'''

class SavedRecoveryTests(unittest.TestCase):
    def test_task_specific_recovered_path(self):
        self.assertTrue(safe_extra('reconstruction/recovered/libNOVA3_neon/block_002ad444_helper.hpp',BODY))
        self.assertTrue(safe_extra('reconstruction/tests/recovered/libNOVA3_neon/test_002ad998.cpp',BODY))

    def test_refuse_other_function_file(self):
        self.assertFalse(safe_extra('reconstruction/recovered/libNOVA3_neon/block_00781c78.cpp',BODY))

    def test_refuse_other_library(self):
        self.assertFalse(safe_extra('reconstruction/recovered/libOther/block_002ad444.cpp',BODY))

    def test_refuse_evidence_unexpected(self):
        self.assertFalse(safe_extra('coordination/evidence/random_002ad444.json',BODY))

    def test_scopes_exact(self):
        a=allowed_from_body(BODY)
        self.assertTrue(is_allowed('reconstruction/recovered/libNOVA3_neon/block_002ad444.cpp',a))
        self.assertFalse(is_allowed('reconstruction/recovered/libNOVA3_neon/block_002ad998.cpp',a))

if __name__=='__main__':unittest.main()
