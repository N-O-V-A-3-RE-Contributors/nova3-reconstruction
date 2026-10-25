import unittest
from nova3_team.coordinator import process_comment, sweep
from nova3_team.common import lease_get, label_names

class FakeGH:
    repo='x/repo'
    def __init__(self):
        self.item={'number':5,'body':'Priority: 50\nAllowed paths: foo/','labels':[{'name':'nova:queued'}]}
        self.messages=[]
    def issue(self,n):return dict(self.item)
    def comment(self,n,msg):self.messages.append(msg)
    def patch(self,path,payload):
        self.item.update(payload)
        self.item['labels']=[{'name':n} for n in payload['labels']] if 'labels' in payload else self.item['labels']
    def issues(self):return [self.item]

def event(actor,body):return {'issue':{'number':5},'comment':{'user':{'login':actor},'body':body}}

class CoordTest(unittest.TestCase):
    def test_only_one_holder_and_release(self):
        gh=FakeGH();tok1='a'*32;tok2='b'*32
        process_comment(gh,event('alice','/nova claim '+tok1))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
        self.assertIn('nova:claimed',label_names(gh.item))
        process_comment(gh,event('bob','/nova claim '+tok2))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
        process_comment(gh,event('alice','/nova release '+tok1))
        self.assertIsNone(lease_get(gh.item['body']))
        self.assertIn('nova:queued',label_names(gh.item))
    def test_spoof_release_denied(self):
        gh=FakeGH();tok='a'*32
        process_comment(gh,event('alice','/nova claim '+tok))
        process_comment(gh,event('bob','/nova release '+tok))
        self.assertEqual(lease_get(gh.item['body'])['owner'],'alice')
    def test_no_pr_submission_outside_repo(self):
        gh=FakeGH();tok='a'*32
        process_comment(gh,event('alice','/nova claim '+tok))
        process_comment(gh,event('alice',f'/nova submitted {tok} https://github.com/elsewhere/repo/pull/4'))
        self.assertIn('nova:claimed',label_names(gh.item))

if __name__=='__main__':unittest.main()
