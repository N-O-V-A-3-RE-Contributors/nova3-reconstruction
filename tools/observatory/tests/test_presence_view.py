import importlib.util
import json
from pathlib import Path
import sys
from datetime import datetime,timedelta,timezone
import unittest

path=Path(__file__).resolve().parents[1]/'presence_view.py'
spec=importlib.util.spec_from_file_location('nova_owner_presence_test',path)
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class Tests(unittest.TestCase):
 def make(self,user='alice',state='IDLE',mins=1):
  updated=(datetime.now(timezone.utc)-timedelta(minutes=mins)).isoformat()
  return {'body':p.MARKER+'\n'+json.dumps({'v':1,'worker':'a'*32,'provider':'codex','state':state,'issue':44}),
          'user':{'login':user},'updated_at':updated,'id':123}
 def test_parse_and_classify(self):
  r=p.parse_comment(self.make())
  self.assertEqual(r['owner'],'alice')
  self.assertEqual(r['issue'],44)
  self.assertEqual(p.classify(r)[0],'IDLE')
 def test_stale_not_offline(self):
  r=p.parse_comment(self.make(state='WORKING',mins=12))
  self.assertEqual(p.classify(r)[0],'STALE')
 def test_untrusted_comment_rejected(self):
  for item in [{'body':'hello'},{'body':p.MARKER+'\nnot json'},
               {'body':p.MARKER+'\n'+json.dumps({'v':1,'worker':'BAD','state':'IDLE'}),'user':{'login':'alice'},'updated_at':'2026-10-27T12:00:00Z'}]:
   self.assertIsNone(p.parse_comment(item))
 def test_no_reporting_issue_does_not_prove_offline(self):
  calls=[]
  result=p.collect('Org/repo',command=lambda a:(calls.append(a) or []))
  self.assertIn('not installed',result['error'])
  self.assertEqual(result['records'],[])
 def test_one_status_comment_reused(self):
  item=self.make()
  def command(args):
   if 'issue' in args:return [{'number':77,'title':'NOVA3 — contributor worker presence'}]
   return [item,item]
  result=p.collect('Org/repo',command=command)
  self.assertEqual(result['issue'],77)
  self.assertEqual(len(result['records']),1)
 def test_maintains_last_known_signal_when_api_fails(self):
  self.assertTrue(p.collect('Not A Repo',command=lambda x:[])['error'])
