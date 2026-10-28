from pathlib import Path
import importlib.util
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def mod(file,name):
 spec=importlib.util.spec_from_file_location(name,file)
 m=importlib.util.module_from_spec(spec)
 sys.modules[spec.name]=m
 spec.loader.exec_module(m)
 return m

@unittest.skipUnless(sys.platform == 'linux', 'systemd discovery is Linux-only')
class Tests(unittest.TestCase):
 def test_known_bug_working_directory_quoted(self):
  # This reproduces systemd-analyze's concrete line-8 failure.
  bad='[Unit]\nDescription=Sample\n\n[Service]\nType=oneshot\nWorkingDirectory="/tmp"\nExecStart=/usr/bin/true\n'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'nova3-discovery.service'
   p.write_text(bad)
   result=subprocess.run(['systemd-analyze','verify',str(p)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('path is not absolute',result.stderr)
   p.write_text(bad.replace('WorkingDirectory="/tmp"','WorkingDirectory=/tmp'))
   result=subprocess.run(['systemd-analyze','verify',str(p)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
 def test_owner_repairs_exact_installer_output(self):
  # Verify that private timer repair fixes legacy generator output.
  ops=mod(ROOT/'operator_ops.py','ops_owner_v22')
  with tempfile.TemporaryDirectory() as d:
   project=Path(d)/'proj'
   pkg=project/'nova3_team'
   pkg.mkdir(parents=True)
   (pkg/'__init__.py').write_text('')
   (pkg/'discovery_setup.py').write_text('def unit_text(project,repo,python):\n return ("[Unit]\\nDescription=X\\n[Service]\\nType=oneshot\\nWorkingDirectory=\\\"/tmp\\\"\\nExecStart=/usr/bin/true\\n", "[Timer]\\nOnUnitInactiveSec=30min\\nUnit=nova3-discovery.service\\n")\n')
   home=Path(d)/'home';home.mkdir()
   calls=[]
   def mockrun(args,timeout=25):
    calls.append(args)
    # Use real systemd-analyze verify rather than trusting mocks.
    if args[0]=='systemd-analyze':
     proc=subprocess.run(args,text=True,capture_output=True)
     if proc.returncode:raise ops.CommandFailure(proc.stderr)
    return ''
   with patch.object(ops,'require_owner_access',return_value='ADMIN'),\
        patch.object(ops.Path,'home',return_value=home),patch.object(ops,'run',side_effect=mockrun),\
        patch.object(ops,'discovery_status',return_value={'timer':{'ActiveState':'active'}}):
    ops.timer_repair(project,'Org/repo')
   svc=(home/'.config/systemd/user/nova3-discovery.service').read_text()
   self.assertIn('WorkingDirectory=/tmp',svc)
   self.assertNotIn('WorkingDirectory="',svc)
   for name in ('nova3_team','nova3_team.discovery_setup'):sys.modules.pop(name,None)
