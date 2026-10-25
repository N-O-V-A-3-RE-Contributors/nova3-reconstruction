import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nova3_team import recovery, worker
from nova3_team.common import lease_put, iso, now
from datetime import timedelta

class WorkerResumeTests(unittest.TestCase):
    def test_claim_reuses_live_token(self):
        token='a'*36
        lease={'owner':'alice','token':token,'expires_at':iso(now()+timedelta(minutes=30))}
        issue={'number':3,'body':lease_put('task',lease)}
        class GH:
            def __init__(self):self.comments=[]
            def comment(self,n,text):self.comments.append((n,text))
        gh=GH()
        self.assertEqual(worker.claim(gh,issue,'alice',saved={'token':token}),token)
        self.assertEqual(gh.comments,[(3,f'/nova heartbeat {token}')])

    def test_prepare_checkout_preserves_dirty_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            work=root/'3-test123'
            (work/'.git').mkdir(parents=True)
            (work/'existing.cpp').write_text('unfinished work',encoding='utf-8')
            recovery.update(root,3,repo='org/game',user='alice',token='saved',
                            work=str(work),branch='nova/issue-3-123abc',account='alice/game')
            cp=type('Result',(),{'returncode':0,'stdout':'nova/issue-3-123abc\n'})()
            with patch.object(worker,'git',return_value=cp) as fake_git:
                result=worker.prepare_checkout('org/game','alice',root,{'number':3})
            self.assertEqual(result[0],work)
            self.assertEqual((work/'existing.cpp').read_text(),'unfinished work')
            fake_git.assert_called_once_with(work,'branch','--show-current',check=False)

if __name__=='__main__':unittest.main()
