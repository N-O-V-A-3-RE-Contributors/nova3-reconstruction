"""Focused tests for the NOVA3 solo/self-hosted pipeline.

Run with python -m unittest discover -s nova3_team/tests -p '*solo*.py'.
"""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from nova3_team import discovery_resources as res
from nova3_team import autofeed, worker


class SoloAutopilotTests(unittest.TestCase):
    def test_8gb_cold_swap_allows_limited_discovery(self):
        # Cold swap is not equivalent to new swapping or memory stalls.
        with patch.object(res,'_memory_psi_avg10',return_value=0.0):
            good, detail = res.headroom((8*res.GIB,2300*res.MIB,4*res.GIB,245*res.MIB))
        self.assertTrue(good,detail)
        self.assertEqual(res.heap_mb(8*res.GIB),768)

    def test_8gb_actual_pressure_still_blocks(self):
        with patch.object(res,'_memory_psi_avg10',return_value=7.0):
            good, _ = res.headroom((8*res.GIB,2300*res.MIB,4*res.GIB,245*res.MIB))
        self.assertFalse(good)
        with patch.object(res,'_memory_psi_avg10',return_value=0.0):
            good, _ = res.headroom((8*res.GIB,1100*res.MIB,4*res.GIB,245*res.MIB))
        self.assertFalse(good)


    def test_large_elf_oom_rotates_to_other_local_library(self):
        from nova3_team import discovery
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);analysis=root/'analysis';analysis.mkdir()
            # libA is deliberately smaller and is picked first.
            (analysis/'libA.so').write_bytes(b'\x7fELFaaaa')
            (analysis/'libB.so').write_bytes(b'\x7fELFbbbbbbbbbbbbbbbb')
            fake=root/'fake-ghidra';fake.write_text('fake');fake.chmod(0o755)
            script=root/'ghidra_scripts/Nova3FunctionLedger.java'
            script.parent.mkdir();script.write_text('fake script')
            calls=[]
            def scan(_headless,elf,*,script,digest,out,timeout):
                calls.append(elf.name)
                if elf.name=='libA.so':
                    raise res.ResourcePressure('Ghidra killed by protective memory limit')
                out.parent.mkdir(parents=True,exist_ok=True)
                out.write_text(json.dumps({'normalization':'image-base-relative',
                                           'functions':[{'address':'0x100','size':4}]}))
            with patch.dict(__import__('os').environ,{'NOVA3_DISCOVERY_CACHE':str(root/'cache')}),\
                 patch.object(discovery,'ensure_ghidra',return_value=fake),\
                 patch.object(discovery,'__file__',str(root/'discovery.py')),\
                 patch.object(res,'preflight',return_value=(True,'healthy')),\
                 patch.object(discovery,'run_ghidra',side_effect=scan):
                first=discovery.one_cycle(root,'owner/repo',publish=False)
                self.assertEqual(first['mode'],'deferred-resource')
                second=discovery.one_cycle(root,'owner/repo',publish=False)
                self.assertEqual(second['mode'],'preview')
                self.assertEqual(second['identified'],1)
            self.assertEqual(calls,['libA.so','libB.so'])

    def test_unknown_pressure_sensor_stays_conservative(self):
        with patch.object(res,'_memory_psi_avg10',return_value=None):
            good, _ = res.headroom((8*res.GIB,2300*res.MIB,4*res.GIB,245*res.MIB))
        self.assertFalse(good)

    def test_feeder_never_generates_expansion_when_discovery_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'nova3_team').mkdir()
            (root/'nova3_team/discovery.py').write_text('test')
            with patch.object(autofeed,'create_expansion',side_effect=AssertionError('Do not bill Codex')):
                note=autofeed.discovery_or_expansion(None,[],root,'coordination/function_ledger.json')
            self.assertIn('WAITING_FOR_DISCOVERY',note)

    def test_worker_requests_discovery_once_when_idle(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            service=home/'.config/systemd/user/nova3-discovery.service'
            service.parent.mkdir(parents=True)
            service.write_text('[Service]\n')
            calls=[]
            def fake_run(cmd,**kwargs):
                calls.append(cmd)
                if 'is-enabled' in cmd:
                    return subprocess.CompletedProcess(cmd,0,'enabled\n','')
                return subprocess.CompletedProcess(cmd,0,'','')
            with patch.object(worker.pathlib.Path,'home',return_value=home),\
                 patch.object(worker.shutil,'which',return_value='/usr/bin/systemctl'),\
                 patch.object(worker.subprocess,'run',side_effect=fake_run):
                self.assertTrue(worker._kick_local_discovery_if_due())
                self.assertFalse(worker._kick_local_discovery_if_due())
            self.assertEqual(sum('start' in x for x in calls),1)

    def test_manual_disable_discovery_is_respected(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            service=home/'.config/systemd/user/nova3-discovery.service'
            service.parent.mkdir(parents=True)
            service.write_text('[Service]\n')
            calls=[]
            def fake_run(cmd,**kwargs):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd,1,'disabled\n','')
            with patch.object(worker.pathlib.Path,'home',return_value=home),\
                 patch.object(worker.shutil,'which',return_value='/usr/bin/systemctl'),\
                 patch.object(worker.subprocess,'run',side_effect=fake_run):
                self.assertFalse(worker._kick_local_discovery_if_due())
            self.assertFalse(any('start' in x for x in calls))

    def test_legacy_expansion_only_filtered_not_function_tasks(self):
        legacy={'title':'Expand verified ARM ELF function inventory from local references',
                'body':'<!-- NOVA3_AUTOTASK_KEY: expand-evidence-123456 -->',
                'labels':[{'name':'nova:queued'}]}
        function={'title':'Reconstruct functions',
                  'body':'<!-- NOVA3_AUTOTASK_KEY: functions-deadbeef -->',
                  'labels':[{'name':'nova:queued'}]}
        review=dict(legacy,labels=[{'name':'nova:review'}])
        manual=dict(legacy,body='No generated key')
        self.assertEqual(worker._solo_eligible_issues([legacy,function,review,manual]),
                         [function,review,manual])
