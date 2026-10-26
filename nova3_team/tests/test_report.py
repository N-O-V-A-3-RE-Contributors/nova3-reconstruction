import unittest
from nova3_team import report

class FakeGH:
    repo='owner/repo'
    def __init__(self):
        self.issues_data=[
          {'number':3,'title':'A','state':'open','labels':[{'name':'nova:review'}],'comments':550},
          {'number':4,'title':'B','state':'open','labels':[{'name':'nova:queued'}],'comments':141},
          {'number':99,'title':'Status','state':'open','labels':[{'name':'nova:dashboard'}],'comments':0},
        ]
        self.prs=[{'body':'Refs #3','head':{'ref':'nova/issue-3-123abc'},'merged_at':None,
                   'state':'open','html_url':'https://github.com/owner/repo/pull/5'}]
        self.updated=[]
    def issues(self,state='open'):
        return self.issues_data
    def get(self,p):
        if 'pulls?' in p:return self.prs
        raise RuntimeError('Unexpected GET')
    def post(self,p,body):
        self.updated.append((p,body))
        if p=='labels':raise RuntimeError('GitHub POST labels: HTTP 422 already exists')
        return {'html_url':'url'}
    def patch(self,p,body):
        self.updated.append((p,body))

class ReportTests(unittest.TestCase):
    def test_only_actual_nova_tasks_counted(self):
        v=report.collect(FakeGH())
        self.assertEqual(len(v['tasks']),2)
        self.assertEqual(v['counts']['review'],1)
        self.assertEqual(v['counts']['integrated'],0)
        self.assertEqual(v['open_prs'],1)
        self.assertTrue(any('550' in s for s in v['alerts']))
        self.assertIn('UNKNOWN',report.format_markdown(v))
    def test_published_status_replaces_body_without_comments(self):
        gh=FakeGH()
        r=report.publish(gh,'new report')
        self.assertEqual(r,'https://github.com/owner/repo/issues/99')
        self.assertTrue(any(p=='issues/99' for p,_ in gh.updated))
    def test_merged_branch_counts_integrated_even_if_label_lags(self):
        gh=FakeGH();gh.prs[0]['merged_at']='2026-10-26T00:00:00Z'
        s=report.collect(gh)
        self.assertEqual(s['counts']['integrated'],1)
