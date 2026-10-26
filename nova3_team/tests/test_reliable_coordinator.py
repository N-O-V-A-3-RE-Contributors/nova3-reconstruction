import unittest
from datetime import timedelta
from nova3_team.coordinator import process_comment, sweep, process_merged_pr
from nova3_team.common import lease_get, lease_put, label_names, iso, now

class GH:
    repo='owner/repo'
    def __init__(self):
        self.item={'number':4,'title':'Some task','body':'Allowed paths: tools/',
                   'labels':[{'name':'nova:queued'}], 'state':'open'}
        self.messages=[]
    def issue(self,n): return dict(self.item)
    def issues(self): return [self.item]
    def patch(self,p,d):
        self.item.update(d)
        if 'labels' in d:self.item['labels']=[{'name':x} for x in d['labels']]
    def comment(self,n,msg):self.messages.append(msg)

def cmd(who,value):return {'issue':{'number':4},'comment':{'user':{'login':who},'body':value}}

class CoordinatorTests(unittest.TestCase):
    def test_claim_heartbeat_and_release_are_quiet(self):
        gh=GH();t='a'*32
        process_comment(gh,cmd('user','/nova claim '+t))
        self.assertIn('nova:claimed',label_names(gh.item))
        process_comment(gh,cmd('user','/nova heartbeat '+t))
        process_comment(gh,cmd('user','/nova release '+t))
        self.assertFalse(gh.messages)
        self.assertIn('nova:queued',label_names(gh.item))
    def test_block_once_and_no_further_claim(self):
        gh=GH();t='b'*32
        process_comment(gh,cmd('user','/nova claim '+t))
        process_comment(gh,cmd('user','/nova block '+t+' INVALID_PR_REFERENCE'))
        process_comment(gh,cmd('user','/nova claim '+t))
        self.assertIn('nova:blocked',label_names(gh.item))
        self.assertEqual(len(gh.messages),1)
    def test_submitted_moves_to_review_quietly(self):
        gh=GH();t='c'*32
        process_comment(gh,cmd('user','/nova claim '+t))
        process_comment(gh,cmd('user','/nova submitted '+t+' https://github.com/owner/repo/pull/10'))
        self.assertIn('nova:review',label_names(gh.item))
        self.assertFalse(gh.messages)
    def test_merged_pr_closes_matching_issue(self):
        gh=GH();gh.item['labels']=[{'name':'nova:review'}]
        process_merged_pr(gh, {'pull_request':{'merged':True,'base':{'ref':'main','repo':{'full_name':'owner/repo'}},
                             'body':'Closes #4','head':{'ref':'nova/issue-4-a1b2c3'}}})
        self.assertEqual(gh.item['state'],'closed')
        self.assertIn('nova:integrated',label_names(gh.item))
    def test_stalled_claim_warns_once(self):
        gh=GH()
        gh.item['labels']=[{'name':'nova:claimed'}]
        gh.item['body']=lease_put(gh.item['body'],{
            'owner':'user','token':'a'*32,
            'expires_at':iso(now()+timedelta(minutes=30)),
            'stage':'AI_RUNNING',
            'last_heartbeat_at':iso(now()-timedelta(minutes=60)),
        })
        sweep(gh)
        self.assertIn('nova:attention',label_names(gh.item))
        self.assertEqual(len(gh.messages),1)
        sweep(gh)
        self.assertEqual(len(gh.messages),1)

    def test_no_merge_no_completion(self):
        gh=GH()
        process_merged_pr(gh, {'pull_request':{'merged':False,'base':{'ref':'main'},'body':'Closes #4','head':{'ref':'nova/issue-4-a1b2c3'}}})
        self.assertEqual(gh.item['state'],'open')
