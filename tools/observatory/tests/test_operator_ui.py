from pathlib import Path
import importlib.util
import sys
import unittest
from unittest.mock import Mock

P=Path(__file__).resolve().parents[1]/'observer.py'
spec=importlib.util.spec_from_file_location('nova_observer_owner_tests',P)
sys.modules[spec.name]=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sys.modules[spec.name])
o=sys.modules[spec.name]

class Tests(unittest.TestCase):
 def make(self,owner=False):
  c=o.Collector('Org/repo',Path('/tmp/nowhere'), 'nova3-worker.service',Path('/tmp/nowhere'),demo=True)
  if owner:
   c.demo=False
   c.snapshot.repo_role='ADMIN'
  ui=o.Observatory(c)
  ui.snap=c.view()
  return c,ui
 def test_zero_is_locked_for_unprivileged_contributor(self):
  _,ui=self.make()
  ui.handle(ord('0'))
  self.assertEqual(ui.tab,0)
  self.assertIn('LOCKED',ui.notice)
  ui.handle(ord('?'))
  self.assertEqual(ui.tab,11)
 def test_owner_can_open_owner_tab(self):
  _,ui=self.make(owner=True)
  ui.handle(ord('0'))
  self.assertEqual(ui.tab,9)
 def test_workers_page_shortcut(self):
  _,ui=self.make()
  ui.handle(ord('w'))
  self.assertEqual(ui.tab,10)
  ui.handle(ord('0'))
  self.assertEqual(ui.tab,10)
 def test_manual_merge_confirmation_exact_phrase(self):
  c,ui=self.make(owner=True)
  ui.tab=9
  c.snapshot.merge_status='READY'
  c.snapshot.merge_ready=[47,48]
  ui.snap=c.view()
  job=Mock()
  c.execute_merge=job
  ui.handle(ord('c'))
  self.assertEqual(ui.merge_phrase,'MERGE 2')
  for x in 'MERGE 2':ui.handle(ord(x))
  ui.handle(10)
  job.assert_called_once_with(False,False)
  self.assertIsNone(ui.merge_phrase)
 def test_wrong_confirmation_or_escape_has_no_effect(self):
  c,ui=self.make()
  ui.merge_phrase='MERGE 2'
  c.execute_merge=Mock()
  for x in 'MERGE 3':ui.handle(ord(x))
  ui.handle(10)
  c.execute_merge.assert_not_called()
  ui.merge_phrase='MERGE 2'
  ui.handle(27)
  c.execute_merge.assert_not_called()
 def test_mark_and_clear_prs(self):
  c,ui=self.make()
  ui.snap.prs=[{'number':49,'state':'OPEN','headRefName':'nova/issue-49-a'},
               {'number':50,'state':'MERGED','headRefName':'nova/issue-50-b'}]
  ui.tab=2
  ui.handle(ord('a'))
  self.assertEqual(ui.pr_marked,{49})
  ui.handle(ord('z'))
  self.assertEqual(ui.pr_marked,set())
 def test_timer_key_action_requires_yes(self):
  c,ui=self.make(owner=True)
  c.perform_action=Mock()
  ui.tab=9
  ui.handle(ord('d'))
  self.assertEqual(ui.confirm,'discovery-repair')
  ui.handle(ord('n'))
  c.perform_action.assert_not_called()
  ui.handle(ord('d'))
  ui.handle(ord('y'))
  c.perform_action.assert_called_once_with('discovery-repair')
 def test_contributor_cannot_preview_or_merge_prs(self):
  c,ui=self.make()
  c.prepare_merges=Mock()
  c.execute_merge=Mock()
  ui.tab=2
  ui.handle(ord('c'))
  c.prepare_merges.assert_not_called()
  self.assertIn('locked',ui.notice.lower())
  ui.tab=9
  ui.handle(ord('c'))
  c.execute_merge.assert_not_called()

if __name__=='__main__':unittest.main()
