import json
from pathlib import Path
import tempfile
import unittest
from nova3_team import recovery

class RecoveryTests(unittest.TestCase):
    def test_atomic_checkpoint_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            work=root/'3-test123'
            (work/'.git').mkdir(parents=True)
            data=recovery.update(root,3,repo='abc/game',user='bob',token='tt',
                                 work=str(work),branch='nova/issue-3-test123',account='bob/game')
            self.assertEqual(data['issue'],3)
            state=recovery.resume_info(root,3,'abc/game','bob')
            self.assertEqual(state['work'],str(work))
            self.assertIsNone(recovery.resume_info(root,3,'abc/game','alice'))
    def test_outside_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            other=Path(tmp).parent
            recovery.update(root,5,repo='abc/game',user='bob',work=str(other),
                            branch='nova/issue-5-abc',account='bob/game')
            self.assertIsNone(recovery.resume_info(root,5,'abc/game','bob'))

if __name__=='__main__': unittest.main()
