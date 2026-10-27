"""Regression tests for the ledger-scoped feeder expansion gate."""
import json
from pathlib import Path
import tempfile
import unittest
from nova3_team import autofeed as af


class FakeGitHub:
    repo='ORG/repo'
    def __init__(self, issues=()):
        self.rows={i['number']:i for i in issues}
        self.prs=[]
        self.created=[]
        self.next_number=100
    def get(self, path):
        if path.startswith('issues?'):
            return list(self.rows.values()) if 'page=1' in path else []
        if path.startswith('pulls?'):
            return self.prs if 'page=1' in path else []
        raise AssertionError(path)
    def post(self,path,payload):
        if path!='issues':raise AssertionError(path)
        row={'number':self.next_number,'title':payload['title'],'body':payload['body'],
             'state':'open','labels':[{'name':x} for x in payload['labels']]}
        self.rows[self.next_number]=row
        self.next_number+=1
        self.created.append(row)
        return row
    def patch(self,path,payload):
        if not path.startswith('issues/'):
            raise AssertionError(path)
        number=int(path.split('/')[-1])
        self.rows[number].update(payload)
        return self.rows[number]


def task_issue(number,key,label='nova:integrated',state='closed'):
    return {'number':number,'body':af.issue_marker(key),'state':state,
            'labels':[{'name':label}]}


def make_ledger(path):
    p=Path(path)/'coordination'/'function_ledger.json'
    p.parent.mkdir(parents=True)
    digest='a'*64
    p.write_text(json.dumps({'references':[{'sha256':digest,'library':'libNOVA3_neon.so'}],
        'functions':[{'reference_sha256':digest,'address':'0x100','size':16,
                      'boundary_status':'tool_identified','status':'pending'}]}))
    return p


class LedgerExhaustionRegression(unittest.TestCase):
    def test_unrelated_review_does_not_stall_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            make_ledger(d)
            task=af.load_tasks(d)[0][0]
            gh=FakeGitHub([task_issue(29,task['key']),
                task_issue(4,'historic-infra-task','nova:review','open')])
            status=af.reconcile_and_feed(gh,d)
            self.assertIn('Created one evidence expansion',status['note'])
            self.assertEqual(len(gh.created),1)
            status=af.reconcile_and_feed(gh,d)
            self.assertEqual(len(gh.created),1)
            self.assertIn('Existing evidence expansion',status['note'])

    def test_real_pending_function_work_still_waits(self):
        with tempfile.TemporaryDirectory() as d:
            make_ledger(d)
            task=af.load_tasks(d)[0][0]
            gh=FakeGitHub([task_issue(29,task['key'],'nova:review','open')])
            status=af.reconcile_and_feed(gh,d)
            self.assertIn('Waiting for pending function tasks',status['note'])
            self.assertEqual(gh.created,[])

    def test_new_ledger_tasks_are_published_before_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            make_ledger(d)
            gh=FakeGitHub([task_issue(4,'historic-infra-task','nova:review','open')])
            status=af.reconcile_and_feed(gh,d)
            self.assertEqual(len(status['created']),1)
            self.assertEqual(len(gh.created),1)
            self.assertIn('Reconstruct',gh.created[0]['title'])

    def test_expansion_closed_without_new_evidence_blocks_once(self):
        with tempfile.TemporaryDirectory() as d:
            make_ledger(d)
            task=af.load_tasks(d)[0][0]
            gh=FakeGitHub([task_issue(29,task['key'])])
            af.reconcile_and_feed(gh,d)
            expanded=gh.created[0]
            expanded['state']='closed';expanded['labels']=[{'name':'nova:integrated'}]
            status=af.reconcile_and_feed(gh,d)
            self.assertIn('blocked',status['note'])
            self.assertEqual(len(gh.created),1)
            self.assertEqual(gh.rows[expanded['number']]['state'],'open')
            self.assertIn('nova:blocked',[x['name'] if isinstance(x,dict) else x for x in gh.rows[expanded['number']]['labels']])

if __name__=='__main__':unittest.main()
