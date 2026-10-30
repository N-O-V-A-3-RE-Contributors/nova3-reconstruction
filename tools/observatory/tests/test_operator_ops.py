import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1]/'operator_ops.py'
spec=importlib.util.spec_from_file_location('nova_owner_ops_tests',MODULE)
ops=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=ops
spec.loader.exec_module(ops)
REAL_REQUIRE = ops.require_owner_access
SHA_A='a'*40
SHA_B='b'*40

def pr(n=47,sha=SHA_A,author='crjase',mergeable='MERGEABLE',merge_state='CLEAN'):
 return dict(number=n,title=f'#{n}: recover',author={'login':author},headRefName=f'nova/issue-{n}-abc',
   headRefOid=sha,baseRefName='main',mergeable=mergeable,mergeStateStatus=merge_state,
   isDraft=False,url=f'https://github.com/Org/repo/pull/{n}',reviewDecision=None,state='OPEN')

class OperatorTests(unittest.TestCase):
 def setUp(self):
  # Existing synthetic merge/timer fixtures model an authorized maintainer.
  authorization=patch.object(ops,'require_owner_access',return_value='ADMIN')
  authorization.start()
  self.addCleanup(authorization.stop)
 def test_permission_policy_deny_by_default(self):
  self.assertEqual(ops.OWNER_ROLES, frozenset({'ADMIN'}))
  self.assertNotIn('MAINTAIN', ops.OWNER_ROLES)
  with patch.object(ops,'gh_json',return_value={'viewerPermission':'READ'}):
   self.assertEqual(ops.repo_permission('Org/repo'),'READ')
  with patch.object(ops,'gh_json',return_value={'viewerPermission':'INVALID'}):
   with self.assertRaises(ops.CommandFailure):ops.repo_permission('Org/repo')
  with patch.object(ops,'gh_json',return_value={'viewerPermission':'WRITE'}),\
       patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
   with self.assertRaises(ops.CommandFailure):ops.require_owner_access('Org/repo')

 def _auth_responses(self, role, *, membership=None, identity=None, fail=False):
  identity=identity or {'login':'crjase','id':101,'type':'User'}
  def fake(*args, **kwargs):
   if fail: raise ops.CommandFailure('GitHub unavailable')
   if args[:2]==('repo','view'): return {'viewerPermission':role}
   if args[:2]==('api','user'): return identity
   if len(args)>1 and str(args[1]).startswith('orgs/'):
    if membership is None: raise ops.CommandFailure('membership unavailable')
    return membership
   raise ops.CommandFailure('unexpected GitHub call')
  return fake

 def test_maintain_write_triage_read_never_confer_owner_access(self):
  for role in ('MAINTAIN','WRITE','TRIAGE','READ'):
   with self.subTest(role=role),\
        patch.object(ops,'gh_json',side_effect=self._auth_responses(role)),\
        patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
    with self.assertRaises(ops.CommandFailure):ops.require_owner_access('Org/repo')

 def test_verified_repository_admin_and_org_owner_allowed(self):
  with patch.object(ops,'gh_json',side_effect=self._auth_responses('ADMIN')),\
       patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
   self.assertEqual(ops.require_owner_access('Org/repo'),'ADMIN')
  owner={'role':'admin','state':'active','organization':'Org'}
  with patch.object(ops,'gh_json',side_effect=self._auth_responses('READ',membership=owner)),\
       patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
   self.assertEqual(ops.require_owner_access('Org/repo'),ops.ORG_OWNER)
  for membership in ({'role':'member','state':'active','organization':'Org'},
                     {'role':'admin','state':'pending','organization':'Org'},
                     {'role':'admin','state':'active','organization':'OtherOrg'},
                     None):
   with self.subTest(membership=membership),\
        patch.object(ops,'gh_json',side_effect=self._auth_responses('MAINTAIN',membership=membership)),\
        patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
    with self.assertRaises(ops.CommandFailure):ops.require_owner_access('Org/repo')

 def test_github_failure_and_missing_identity_fail_closed(self):
  with patch.object(ops,'gh_json',side_effect=self._auth_responses('ADMIN',fail=True)),\
       patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
   with self.assertRaises(ops.CommandFailure):ops.require_owner_access('Org/repo')
  # Invalid numeric identity can never be promoted by a login string.
  with patch.object(ops,'gh_json',side_effect=self._auth_responses('READ',identity={'login':'crjase','id':None})),\
       patch.object(ops,'require_owner_access',side_effect=REAL_REQUIRE):
   with self.assertRaises(ops.CommandFailure):ops.require_owner_access('Org/repo')

 def test_role_revoked_after_preview_blocks_merge_at_execution(self):
  preview=ops.MergePreview('crjase',{47:pr()})
  calls=[]
  def recheck(repo):
   calls.append(repo)
   if len(calls)==1: return 'ADMIN'
   raise ops.CommandFailure('revoked before execution')
  with patch.object(ops,'require_owner_access',side_effect=recheck),\
       patch.object(ops,'load_pr',return_value=pr()),\
       patch.object(ops,'check_state',return_value=('PASS','ok')),\
       patch.object(ops,'run') as run:
   result=ops.merge_batch('Org/repo',preview,wait=0)
  self.assertEqual(result['merged'],[])
  self.assertIn('revoked before execution',result['skipped'][47])
  run.assert_not_called()
  self.assertEqual(len(calls),2)

 def test_owner_privilege_requires_fresh_gh_role(self):
  with self.assertRaises(ops.CommandFailure):ops.repo_permission('../bad')

 def test_permission_denial_blocks_merge_before_gh_mutation(self):
  with patch.object(ops,'require_owner_access',side_effect=ops.CommandFailure('denied')),\
       patch.object(ops,'run') as run:
   with self.assertRaises(ops.CommandFailure):ops.make_preview('Org/repo',[47])
   with self.assertRaises(ops.CommandFailure):ops.merge_batch('Org/repo',ops.MergePreview('user',{47:pr()}))
   run.assert_not_called()
 def test_own_authored_pass_ci_eligible(self):
  self.assertEqual(ops.issues_with_pr(pr(), 'crjase', checks=('PASS','1 passed')),[])
 def test_ci_no_checks_and_others_rejected(self):
  self.assertTrue(ops.issues_with_pr(pr(author='someone-else'),'crjase',checks=('PASS','1 passed')))
  self.assertTrue(ops.issues_with_pr(pr(),'crjase',checks=('NONE','no checks')))
  self.assertEqual(ops.issues_with_pr(pr(),'crjase',allow_no_checks=True,checks=('NONE','no checks')),[])
 def test_conflicts_and_failed_ci_refused(self):
  self.assertTrue(ops.issues_with_pr(pr(mergeable='CONFLICTING'),'crjase',checks=('PASS','pass')))
  self.assertTrue(ops.issues_with_pr(pr(),'crjase',checks=('FAIL','bad')))
 def test_pr_preview_excludes_failed_ci(self):
  with patch.object(ops,'viewer',return_value='crjase'),\
       patch.object(ops,'gh_json',return_value=[pr(47),pr(48)]),\
       patch.object(ops,'check_state',side_effect=[('PASS','ok'),('FAIL','fail')]):
   result=ops.make_preview('Org/repo')
   self.assertEqual(result.eligible,[47])
   self.assertIn(48,result.rejected)
 def test_unknown_then_ready_after_previous_merge(self):
  preview=ops.MergePreview('crjase',{47:pr(47),48:pr(48)})
  calls={47:0,48:0}
  def load(repo,n):
   calls[n]+=1
   return pr(n,mergeable='UNKNOWN',merge_state='UNKNOWN') if n==48 and calls[n]<=2 else pr(n)
  def json_mock(*args,**kw):
   return {'state':'MERGED','mergedAt':'2026-10-27T00:00:00Z'}
  clock=[0]
  def now():return clock[0]
  def sleep(sec):clock[0]+=sec
  with patch.object(ops,'load_pr',side_effect=load),\
       patch.object(ops,'check_state',return_value=('PASS','ok')),\
       patch.object(ops,'run',return_value='ok') as run,\
       patch.object(ops,'gh_json',side_effect=json_mock):
   result=ops.merge_batch('Org/repo',preview,clock=now,sleep=sleep)
   self.assertEqual(result['merged'],[47,48])
   self.assertEqual(run.call_count,2)
 def test_commit_change_does_not_merge(self):
  preview=ops.MergePreview('crjase',{47:pr()})
  with patch.object(ops,'load_pr',return_value=pr(sha=SHA_B)),\
       patch.object(ops,'run') as run:
   result=ops.merge_batch('Org/repo',preview)
   self.assertEqual(result['merged'],[])
   self.assertIn('head commit changed',result['skipped'][47])
   run.assert_not_called()
 def test_timeout_unknown_does_not_merge(self):
  preview=ops.MergePreview('crjase',{47:pr()})
  tick=[0]
  with patch.object(ops,'load_pr',return_value=pr(mergeable='UNKNOWN')),\
       patch.object(ops,'run') as run:
   result=ops.merge_batch('Org/repo',preview,wait=3,clock=lambda:tick[0],sleep=lambda x:tick.__setitem__(0,tick[0]+x))
   self.assertFalse(result['merged'])
   run.assert_not_called()
 def test_git_status_read_only(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertIn('not a Git working tree',ops.git_alignment(Path(d))['note'])
 def test_timer_repair_no_worker_or_sudo(self):
  if sys.platform != 'linux':self.skipTest('systemd is Linux-only')
  # Test with synthetic unit generator, never call a real systemctl.
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'repo'; (root/'nova3_team').mkdir(parents=True)
   (root/'nova3_team/__init__.py').write_text('')
   (root/'nova3_team/discovery_setup.py').write_text('def unit_text(root,repo,python):\n return ("[Service]\\nWorkingDirectory=/tmp/fixture\\nExecStart=/usr/bin/python3 -V\\n","[Timer]\\nOnBootSec=75s\\n")\n')
   home=Path(d)/'home';home.mkdir()
   actions=[]
   def runner(args,timeout=25):actions.append(args);return ''
   with patch.object(ops.Path,'home',return_value=home),patch.object(ops,'run',side_effect=runner),\
        patch.object(ops,'discovery_status',return_value={'timer':{'ActiveState':'active'}}):
    # Fresh package importing is controlled with a local test fixture.
    try:
     result=ops.timer_repair(root,'Org/repo')
    finally:
     for name in ['nova3_team.discovery_setup','nova3_team']:
      sys.modules.pop(name,None)
   self.assertEqual(result['timer']['ActiveState'],'active')
   self.assertTrue(any('start' in a and '--no-block' in a for a in actions))
   self.assertFalse(any('nova3-worker' in ' '.join(a) for a in actions))
   self.assertTrue((home/'.config/systemd/user/nova3-discovery.timer').is_file())
 def test_operator_ui_confirmation_never_auto_merges(self):
  # UI handling tested in observer suite; core preview never invokes `gh pr merge`.
  with patch.object(ops,'viewer',return_value='crjase'),\
       patch.object(ops,'gh_json',return_value=[pr(47)]),\
       patch.object(ops,'check_state',return_value=('PASS','ok')),\
       patch.object(ops,'run') as run:
   self.assertEqual(ops.make_preview('Org/repo').eligible,[47])
   run.assert_not_called()

if __name__=='__main__':unittest.main()
