import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('roadmap_queue_test',ROOT/'nova3_team/roadmap_queue.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
PLAN=json.loads((ROOT/'coordination/project_roadmap.json').read_text())


class RoadmapTests(unittest.TestCase):
    def test_valid_manifest(self):
        self.assertTrue(r.validate_plan(PLAN))
        self.assertGreaterEqual(len(PLAN['stages']),4)

    def test_first_batch_bounded(self):
        c=r.plan_candidates(PLAN,[],[])
        self.assertEqual(len(c),3)
        self.assertTrue(all(s=='foundation' for s,t in c))

    def test_gates_open_only_from_merged_prs(self):
        foundation=PLAN['stages'][0]['tasks']
        issues=[{'number':n+1,'body':r.make_body(t,'foundation'),'state':'open',
                 'labels':[{'name':'nova:review'}]} for n,t in enumerate(foundation)]
        candidates=r.plan_candidates(PLAN,issues,[])
        self.assertEqual(candidates,[])
        prs=[{'merged_at':'2026-10-27T00:00:00Z','head':{'ref':f'nova/issue-{n+1}-abcdef'}}
             for n in range(2)]
        candidates=r.plan_candidates(PLAN,issues,prs)
        self.assertTrue(any(s=='native-platforms' for s,t in candidates))
        self.assertFalse(any(s=='integration' for s,t in candidates))

    def test_idempotent_issue_keys(self):
        foundation=PLAN['stages'][0]['tasks']
        issues=[{'number':n+1,'body':r.make_body(t,'foundation'),'state':'open',
                 'labels':[{'name':'nova:queued'}]} for n,t in enumerate(foundation)]
        self.assertEqual(r.plan_candidates(PLAN,issues,[]),[])

    def test_closed_issue_not_automatically_verified(self):
        t=PLAN['stages'][0]['tasks'][0]
        issues=[{'number':1,'body':r.make_body(t,'foundation'),'state':'closed',
                 'labels':[]}]
        self.assertEqual(len(r.plan_candidates(PLAN,issues,[])),2)

    def test_denies_paths_outside_repo(self):
        self.assertFalse(r.valid_path('../test.cpp'))
        self.assertFalse(r.valid_path('analysis/libgame.so'))
        self.assertFalse(r.valid_path('.github/workflows/backdoor.yml'))

    def test_only_owner_can_enqueue_feature_planning(self):
        request={'number':101,'title':'Better enemy AI','body':'Add coordination',
                 'labels':[{'name':'nova:feature-request'}],
                 'user':{'login':'untrusted'}}
        items=r.feature_candidates(PLAN,[request],{'feature-extension-api-v1'},ROOT)
        self.assertEqual(items,[])
        request['user']['login']='crjase'
        items=r.feature_candidates(PLAN,[request],{'feature-extension-api-v1'},ROOT)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0][1]['key'],'feature-plan-issue-101')
        self.assertTrue(all(r.valid_path(x) for x in items[0][1]['paths']))

    def test_github_post_bounded(self):
        plan=json.loads(json.dumps(PLAN))
        with mock.patch.object(r,'command_json',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                r.list_all('org/repo','issues')

if __name__=='__main__':unittest.main()
