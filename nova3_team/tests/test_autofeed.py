import json
from pathlib import Path
import tempfile
import unittest
from nova3_team import autofeed


class FakeGH:
    repo='ORG/repo'
    def __init__(self,issues=None,prs=None):
        self.rows={i['number']:i for i in (issues or [])}
        self.prs=prs or []
        self.calls=[]
        self.next_number=100
    def get(self,path):
        self.calls.append(('GET',path))
        if path.startswith('issues?'):
            return list(self.rows.values()) if 'page=1' in path else []
        if path.startswith('pulls?'):
            return self.prs if 'page=1' in path else []
        raise AssertionError(path)
    def post(self,path,payload):
        self.calls.append(('POST',path,payload))
        if path!='issues':raise AssertionError(path)
        n=self.next_number;self.next_number+=1
        row={'number':n,'body':payload['body'],'title':payload['title'],'state':'open',
             'labels':[{'name':x} for x in payload.get('labels',[])]}
        self.rows[n]=row
        return row
    def patch(self,path,payload):
        self.calls.append(('PATCH',path,payload))
        n=int(path.split('/')[-1]);row=self.rows[n];row.update(payload)
        if 'labels' in payload:row['labels']=[{'name':x} for x in payload['labels']]
        return row


def label(name):return {'name':name}


def old(n=3,labels=('nova:review',),state='open',body='Closes #3'):
    return {'number':n,'state':state,'body':body,'labels':[label(s) for s in labels]}


def merged_pr(n=3, sha='2026-10-26T02:00:00Z'):
    return {'head':{'ref':f'nova/issue-{n}-098904'},'base':{'ref':'main','repo':{'full_name':'ORG/repo'}},
            'body':f'Closes #{n}', 'merged_at':sha, 'state':'closed'}


class TestAutofeed(unittest.TestCase):
    def test_empty_creates_one_prerequisite_then_no_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            gh=FakeGH()
            self.assertIn('created',autofeed.reconcile_and_feed(gh,d)['note'])
            self.assertIn('existing',autofeed.reconcile_and_feed(gh,d)['note'])
            self.assertEqual(len(gh.rows),1)
            self.assertIn('Allowed paths:',next(iter(gh.rows.values()))['body'])

    def test_closed_prerequisite_becomes_blocked_without_flood(self):
        with tempfile.TemporaryDirectory() as d:
            gh=FakeGH()
            autofeed.reconcile_and_feed(gh,d)
            gh.rows[100]['state']='closed'
            x=autofeed.reconcile_and_feed(gh,d)
            self.assertIn('blocked',x['note'])
            self.assertEqual(gh.rows[100]['state'],'open')
            self.assertIn(label('nova:blocked'),gh.rows[100]['labels'])
            before=len([x for x in gh.calls if x[0]=='PATCH'])
            autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(before,len([x for x in gh.calls if x[0]=='PATCH']))

    def test_reconciles_only_real_merge(self):
        with tempfile.TemporaryDirectory() as d:
            gh=FakeGH(issues=[old()],prs=[merged_pr()])
            x=autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(x['reconciled_merged'],1)
            self.assertEqual(gh.rows[3]['state'],'closed')
            self.assertIn(label('nova:integrated'),gh.rows[3]['labels'])
            self.assertNotIn(label('nova:review'),gh.rows[3]['labels'])

    def test_review_without_merged_pr_doesnt_close(self):
        with tempfile.TemporaryDirectory() as d:
            gh=FakeGH(issues=[old()],prs=[merged_pr(sha=None)])
            autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(gh.rows[3]['state'],'open')
            self.assertIn(label('nova:review'),gh.rows[3]['labels'])

    def test_mismatch_branch_or_body_not_integrated(self):
        with tempfile.TemporaryDirectory() as d:
            pr=merged_pr();pr['body']='Closes #4'
            gh=FakeGH(issues=[old()],prs=[pr])
            self.assertEqual(autofeed.reconcile_and_feed(gh,d)['reconciled_merged'],0)
            self.assertEqual(gh.rows[3]['state'],'open')

    def test_real_ledger_schedules_bounded_groups_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/function_ledger.json';p.parent.mkdir()
            digest='a'*64
            p.write_text(json.dumps({'references':[{'sha256':digest,'library':'libNOVA3_neon.so'}],
                'functions':[{'reference_sha256':digest,'address':f'0x{i*16:x}','size':16,
                              'boundary_status':'tool_identified'} for i in range(16)]}))
            gh=FakeGH()
            first=autofeed.reconcile_and_feed(gh,d,batch=2)
            self.assertEqual(len(first['created']),2)
            second=autofeed.reconcile_and_feed(gh,d,batch=2)
            self.assertEqual(len(second['created']),2)
            last=autofeed.reconcile_and_feed(gh,d,batch=2)
            self.assertEqual(len(last['created']),0)
            self.assertEqual(len(gh.rows),4)
            self.assertIn('NOT MATCHED',next(iter(gh.rows.values()))['body'])

    def test_exhausted_ledger_creates_exactly_one_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/function_ledger.json';p.parent.mkdir()
            digest='a'*64
            p.write_text(json.dumps({'references':[{'sha256':digest,'library':'libNOVA3_neon.so'}],
                'functions':[{'reference_sha256':digest,'address':'0x100','size':16,
                              'boundary_status':'tool_identified'}]}))
            gh=FakeGH()
            first=autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(len(first['created']),1)
            gh.rows[100]['state']='closed';gh.rows[100]['labels']=[label('nova:integrated')]
            next_one=autofeed.reconcile_and_feed(gh,d)
            self.assertIn('Created one evidence expansion',next_one['note'])
            self.assertEqual(len(gh.rows),2)
            final=autofeed.reconcile_and_feed(gh,d)
            self.assertIn('waiting for in-flight',final['note'])
            self.assertEqual(len(gh.rows),2)

    def test_closed_expansion_raises_blocker_instead_of_requeueing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/function_ledger.json';p.parent.mkdir()
            digest='a'*64
            p.write_text(json.dumps({'references':[{'sha256':digest,'library':'libNOVA3_neon.so'}],
                'functions':[{'reference_sha256':digest,'address':'0x100','size':16,
                              'boundary_status':'tool_identified'}]}))
            gh=FakeGH()
            autofeed.reconcile_and_feed(gh,d)
            gh.rows[100]['state']='closed';gh.rows[100]['labels']=[label('nova:integrated')]
            autofeed.reconcile_and_feed(gh,d)
            gh.rows[101]['state']='closed';gh.rows[101]['labels']=[label('nova:integrated')]
            answer=autofeed.reconcile_and_feed(gh,d)
            self.assertIn('blocked',answer['note'])
            self.assertEqual(len(gh.rows),2)
            self.assertIn(label('nova:blocked'),gh.rows[101]['labels'])

    def test_ignores_unverified_function_boundaries(self):
        d='b'*64
        data={'references':[{'sha256':d,'library':'libNOVA3_neon.so'}],
              'functions':[{'reference_sha256':d,'address':'0x1','size':10,'boundary_status':'guess'},
                           {'reference_sha256':d,'address':'0x2','size':0,'boundary_status':'verified'}]}
        self.assertEqual(autofeed.generate_from_ledger(data),[])

    def test_manifest_requires_publish_and_disallows_unsafe_paths(self):
        bad={'tasks':[{'id':'bad-path','publish':True,'title':'Bad','allowed_paths':['analysis/original.so']}]}
        with self.assertRaises(ValueError):autofeed.generate_from_manifest(bad)
        items={'tasks':[{'id':'safe-001','publish':True,'title':'Safe task',
                         'allowed_paths':['nova3_team/new_file.py']},
                        {'id':'not-ready','publish':False,'title':'Hidden','allowed_paths':['nova3_team/nope.py']}]}
        self.assertEqual(len(autofeed.generate_from_manifest(items)),1)

    def test_dependencies_require_integrated_parent_issue(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/tasks.json';p.parent.mkdir()
            tasks={'tasks':[{'id':'task-one','publish':True,'title':'One','allowed_paths':['nova3_team/one.py']},
                            {'id':'task-two','publish':True,'depends_on':['task-one'],'title':'Two','allowed_paths':['nova3_team/two.py']} ]}
            p.write_text(json.dumps(tasks))
            gh=FakeGH()
            x=autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(len(x['created']),1)
            gh.rows[100]['state']='closed';gh.rows[100]['labels']=[label('nova:integrated')]
            x=autofeed.reconcile_and_feed(gh,d)
            self.assertEqual(len(x['created']),1)

    def test_cap_limits_active_and_review_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/tasks.json';p.parent.mkdir()
            p.write_text(json.dumps({'tasks':[{'id':f'task-{i:03}', 'publish':True,
                                'title':'Task '+str(i),'allowed_paths':['nova3_team/newfile.py']}
                                for i in range(35)]}))
            gh=FakeGH()
            x=autofeed.reconcile_and_feed(gh,d,batch=20,cap=12)
            self.assertEqual(len(x['created']),12)
            x=autofeed.reconcile_and_feed(gh,d,batch=20,cap=12)
            self.assertEqual(len(x['created']),0)
            self.assertEqual(len(gh.rows),12)

    def test_review_label_counts_as_capacity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordination/tasks.json';p.parent.mkdir()
            p.write_text(json.dumps({'tasks':[{'id':f'task-{i:03}', 'publish':True,
                                'title':'Task '+str(i),'allowed_paths':['nova3_team/newfile.py']}
                                for i in range(8)]}))
            gh=FakeGH(issues=[old(3),old(4),old(5)])
            x=autofeed.reconcile_and_feed(gh,d,cap=5)
            self.assertEqual(len(x['created']),2)

if __name__ == '__main__':unittest.main()
