import json
from pathlib import Path
import tempfile
import unittest
from nova3_team import autofeed

D='a'*64

class GH:
    def __init__(self,issues=None):
        self.rows={i['number']:i for i in (issues or [])}
        self.calls=[]
    def get(self,path):
        if path.startswith('issues?'):
            return list(self.rows.values())
        if path.startswith('pulls?'):
            return []
        raise AssertionError('unexpected read '+path)
    def post(self,path,obj):
        assert path=='issues'
        n=max(self.rows, default=10)+1
        self.rows[n]={'number':n,'body':obj['body'],'state':'open',
                      'labels':[{'name':x} for x in obj['labels']]}
        self.calls.append(obj)
        return {'number':n}
    def patch(self,path,obj):
        n=int(path.split('/')[1]);self.rows[n].update(obj);return self.rows[n]

class AutoDiscoveryQueueIntegration(unittest.TestCase):
    def test_after_expansion_only_unassigned_addresses_become_issues(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'coordination').mkdir()
            (root/'coordination/discovery_config.json').write_text('{}')
            f=lambda address:{'reference_sha256':D,'address':hex(address),'size':16,
                              'boundary_status':'tool_identified','status':'pending'}
            initial={'references':[{'sha256':D,'library':'libNOVA3_neon.so'}],
                     'functions':[f(v) for v in (0x1000,0x1100,0x1200,0x1300)]}
            old=autofeed.generate_from_ledger(initial)[0]
            old_issue={'number':29,'state':'closed',
                       'labels':[{'name':'nova:integrated'}],
                       'body':autofeed.task_body(old)}
            # Newly discovered function is in the MIDDLE of the original group.
            initial['functions'].append(f(0x1150))
            (root/'coordination/function_ledger.json').write_text(json.dumps(initial))
            gh=GH([old_issue]); response=autofeed.reconcile_and_feed(gh,root)
            self.assertEqual(len(response['created']),1)
            created=gh.calls[0]
            self.assertIn('0x00001150',created['title'])
            self.assertNotIn('0x00001000',created['title'])
            self.assertNotIn('0x00001200',created['title'])
            self.assertEqual(autofeed.reconcile_and_feed(gh,root)['created'],[])
            self.assertEqual(len(gh.calls),1)
    def test_all_addresses_assigned_reports_local_discovery_not_completion(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'coordination').mkdir()
            (root/'coordination/discovery_config.json').write_text('{}')
            data={'references':[{'sha256':D,'library':'libNOVA3_neon.so'}],
                  'functions':[{'reference_sha256':D,'address':'0x1234','size':16,
                                'boundary_status':'tool_identified'}]}
            (root/'coordination/function_ledger.json').write_text(json.dumps(data))
            old=autofeed.generate_from_ledger(data)[0]
            gh=GH([{'number':29,'state':'closed','labels':[{'name':'nova:integrated'}],
                    'body':autofeed.task_body(old)}])
            response=autofeed.reconcile_and_feed(gh,root)
            self.assertEqual(response['created'],[])
            self.assertIn('headless Ghidra discovery',response['note'])
            self.assertEqual(len(gh.rows),1)

if __name__=='__main__': unittest.main()
