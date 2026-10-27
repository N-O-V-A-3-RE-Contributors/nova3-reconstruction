import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import discovery, discovery_resources as res, discovery_setup

class ResourceGuardTests(unittest.TestCase):
    def test_laptop_8gb_high_ram_and_swap_defers(self):
        with patch.object(res,'memory_snapshot',return_value=(8*res.GIB,1100*res.MIB,4*res.GIB,300*res.MIB)):
            good,why=res.headroom()
            self.assertFalse(good)
            self.assertIn('Available memory',why)
            self.assertEqual(res.heap_setting(),'768M')

    def test_swap_93pct_pressure_defers_without_blame(self):
        # NOVA3_SOLO_PSI_TEST_FIX_V1: explicitly simulate active pressure.
        with patch.object(res,'_memory_psi_avg10',return_value=8.0):
            self.assertFalse(res.headroom((8*res.GIB,3*res.GIB,4*res.GIB,260*res.MIB))[0])

    def test_healthy_machine_scans_with_memory_cap(self):
        with patch.object(res,'_memory_psi_avg10',return_value=0.0):  # NOVA3_SOLO_HEALTHY_PSI_FIX_V2
            self.assertTrue(res.headroom((8*res.GIB,5*res.GIB,4*res.GIB,3*res.GIB))[0])
            self.assertEqual(res.heap_mb(5*res.GIB),512)
            self.assertEqual(res.heap_mb(16*res.GIB),1280)
            self.assertEqual(res.heap_mb(32*res.GIB),2048)

    def test_no_memory_information_defers(self):
        with patch.object(res,'memory_snapshot',return_value=None):
            self.assertFalse(res.preflight()[0])

    def test_low_disk_defers(self):
        with patch.object(res,'_memory_psi_avg10',return_value=0.0):  # NOVA3_SOLO_DISK_PSI_FIX_V2
            fake=type('Usage',(),{'free':100*res.MIB})()
            with patch.object(res,'memory_snapshot',return_value=(16*res.GIB,10*res.GIB,0,0)),\
                 patch.object(res.shutil,'disk_usage',return_value=fake):
                self.assertIn('filesystem',res.preflight(Path('/tmp'))[1])

    def test_one_expensive_scan_per_cycle_and_cached_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp); analysis=project/'analysis';analysis.mkdir()
            for name in ('libA.so','libB.so'):
                (analysis/name).write_bytes(b'\x7fELF'+name.encode())
            fake=Path(tmp)/'ghidra';fake.write_text('test');fake.chmod(0o755)
            script=project/'ghidra_scripts'/'Nova3FunctionLedger.java'
            script.parent.mkdir();script.write_text('test')
            calls=[]
            def fake_scan(headless,elf,*,script,digest,out,timeout):
                calls.append(elf.name)
                out.parent.mkdir(parents=True,exist_ok=True)
                out.write_text(json.dumps({'normalization':'image-base-relative',
                                           'functions':[{'address':'0x100','size':8}]}))
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(project/'cache')}),\
                 patch.object(discovery,'ensure_ghidra',return_value=fake),\
                 patch.object(discovery,'__file__',str(project/'discovery.py')),\
                 patch.object(res,'preflight',return_value=(True,'healthy')),\
                 patch.object(discovery,'run_ghidra',side_effect=fake_scan):
                first=discovery.one_cycle(project,'owner/repo',publish=False)
                self.assertEqual(first['identified'],1)
                self.assertEqual(first['deferred'],1)
                second=discovery.one_cycle(project,'owner/repo',publish=False)
                self.assertEqual(second['identified'],2)
                self.assertEqual(second['deferred'],0)
                self.assertEqual(calls,['libA.so','libB.so'])

    def test_pressure_is_not_marked_as_corrupt_lib_or_failed_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);analysis=project/'analysis';analysis.mkdir()
            (analysis/'libA.so').write_bytes(b'\x7fELF123')
            fake=project/'ghidra';fake.write_text('x');fake.chmod(0o755)
            script=project/'ghidra_scripts/Nova3FunctionLedger.java'
            script.parent.mkdir();script.write_text('s')
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(project/'cache')}),\
                 patch.object(discovery,'ensure_ghidra',return_value=fake),\
                 patch.object(discovery,'__file__',str(project/'discovery.py')),\
                 patch.object(res,'preflight',return_value=(False,'Low RAM')),\
                 patch.object(discovery,'run_ghidra',side_effect=AssertionError('must not run')):
                result=discovery.one_cycle(project,'owner/repo',publish=True)
                self.assertEqual(result['mode'],'deferred-resource')
                self.assertFalse((project/'cache/failures').exists())
                status=json.loads((project/'cache/status.json').read_text())
                self.assertEqual(status['kind'],'deferred')

    def test_ghidra_launcher_caps_heap_cpu_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            exe=root/'analyzeHeadless'
            exe.write_text('#!/usr/bin/env python3\n'
                    'import sys,os,pathlib,json\n'
                    'assert sys.argv[sys.argv.index("-max-cpu")+1]=="1"\n'
                    'assert os.environ["GHIDRA_HEADLESS_MAXMEM"]=="768M"\n'
                    'pathlib.Path(sys.argv[sys.argv.index("-postScript")+2]).write_text(json.dumps({"normalization":"image-base-relative","functions":[{"address":"0x10","size":4}]}))\n')
            exe.chmod(0o755)
            script=root/'Nova3FunctionLedger.java';script.write_text('fake')
            elf=root/'libA.so';elf.write_bytes(b'\x7fELF')
            out=root/'out.json'
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(root/'cache')}),\
                 patch.object(res,'memory_snapshot',return_value=(8*res.GIB,5*res.GIB,0,0)):
                discovery.run_ghidra(exe,elf,script=script,digest='a'*64,out=out,timeout=20)
            self.assertTrue(out.is_file())

    @unittest.skipUnless(os.name=='posix','POSIX flock')
    def test_concurrent_manual_scan_is_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            with res.discovery_lock(Path(tmp)):
                with self.assertRaises(res.ResourcePressure):
                    with res.discovery_lock(Path(tmp)):
                        pass

    def test_supervisor_stops_child_when_host_becomes_pressured(self):
        import subprocess
        import time
        start=time.monotonic()
        with patch.object(res,'headroom',return_value=(False,'RAM nearly exhausted')):
            with self.assertRaises(res.ResourcePressure):
                res.supervised_run([sys.executable,'-c','import time;time.sleep(12)'],
                                   output=subprocess.DEVNULL,env=os.environ.copy(),
                                   timeout=20,poll=0.10)
        self.assertLess(time.monotonic()-start,5)

    def test_systemd_has_hard_memory_swap_cpu_guards(self):
        service,timer=discovery_setup.unit_text(Path('/home/test/nova'), 'x/y', Path('/usr/bin/python3'))
        for key in ('MemoryMax=25%', 'MemoryHigh=20%', 'MemorySwapMax=0',
                    'CPUQuota=75%', 'CPUWeight=10', 'IOWeight=10', 'TasksMax=128'):
            self.assertIn(key,service)
        self.assertIn('RandomizedDelaySec=5min',timer)
        self.assertNotIn('OnBootSec=',timer)
        self.assertNotIn('ExecStart=codex',service)

if __name__=='__main__':unittest.main()
