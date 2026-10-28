import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import sys

P=Path(__file__).resolve().parents[1]/'progress.py'
spec=importlib.util.spec_from_file_location('nova3_progress_test',P)
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class ProgressTests(unittest.TestCase):
    def test_apk_index_only_does_not_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with zipfile.ZipFile(root/'test.apk','w') as z:
                z.writestr('lib/armeabi-v7a/libNOVA3_neon.so',b'synthetic')
                z.writestr('classes.dex',b'not real dex')
                z.writestr('AndroidManifest.xml',b'example')
                z.writestr('assets/secret.bin',b'fake file')
            mapping=p.collect_packages(root)
            self.assertEqual(mapping['component_counts']['NATIVE SO'],1)
            self.assertEqual(mapping['component_counts']['DEX/JAVA'],1)
            self.assertFalse((root/'assets').exists())

    def test_map_keeps_merged_separate_from_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'coordination').mkdir()
            (root/'coordination/function_ledger.json').write_text(json.dumps({
                'references':[{'sha256':'a'*64,'library':'libNOVA3_neon.so'}],
                'functions':[{'reference_sha256':'a'*64,'address':'0x701160','status':'pending','boundary_status':'verified'},
                             {'reference_sha256':'a'*64,'address':'0x70117c','status':'matched','boundary_status':'verified'}]}))
            issues=[{'number':31,'title':'Reconstruct libNOVA3_neon.so functions 0x00701160','labels':[]}]
            prs=[{'title':'#31: test','mergedAt':'2026-10-27T00:00:00Z'}]
            m=p.source_map(root,issues,prs,scan_files=False)
            self.assertEqual(m['known'],2)
            self.assertEqual(m['integrated'],2)
            self.assertEqual(m['verified'],1)
            self.assertIsNone(m['entire_game_total'])

    def test_identical_addresses_in_different_reference_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'coordination').mkdir()
            (root/'coordination/function_ledger.json').write_text(json.dumps({
                'references':[{'sha256':'a'*64,'library':'libNOVA3_neon.so'},
                              {'sha256':'b'*64,'library':'libNOVA3_neon.so'}],
                'functions':[{'reference_sha256':digest,'address':'0x701160',
                              'status':'pending','boundary_status':'tool_identified'}
                             for digest in ['a'*64,'b'*64]]}))
            issues=[{'number':31,'title':'Reconstruct libNOVA3_neon.so functions 0x00701160',
                     'body':'Reference SHA256: `'+('a'*64)+'`'}]
            prs=[{'title':'#31: reconstructed','mergedAt':'2026-10-27T00:00:00Z'}]
            result=p.source_map(root,issues,prs,scan_files=False)
            self.assertEqual(result['known'],2)
            self.assertEqual(result['integrated'],1)
            issues[0].pop('body')
            result=p.source_map(root,issues,prs,scan_files=False)
            self.assertEqual(result['integrated'],0) # ambiguous reference SHA

    def test_unmentioned_original_functions_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=p.source_map(Path(tmp),[],[],scan_files=False)
            self.assertEqual(m['known'],0)
            self.assertIsNone(m['entire_game_total'])

    def test_tile_states(self):
        items=[{'addr':1,'verified':False,'integrated':False,'submitted':False,'source':False},
               {'addr':2,'verified':False,'integrated':True,'submitted':True,'source':True},
               {'addr':3,'verified':True,'integrated':True,'submitted':True,'source':True}]
        self.assertEqual(p.addr_tiles(items,3),'░▓█')

    def test_local_source_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'reconstruction/tests/a.cpp';path.parent.mkdir(parents=True)
            path.write_text('one\ntwo\n')
            out=p.local_source_stats(root)
            self.assertEqual(out['files'],1)
            self.assertEqual(out['test_files'],1)
            self.assertEqual(out['lines'],2)

    def test_project_forecast_grows_with_slow_assumptions(self):
        plan={'stages':[{'id':'native-platforms','count':4,'merged':1}]}
        obj=p.project_forecast(plan,{'whole_game_seconds':{'fast':3600,'likely':36000,'slow':360000}})
        self.assertLess(obj['seconds']['fast'],obj['seconds']['likely'])
        self.assertLess(obj['seconds']['likely'],obj['seconds']['slow'])

    def test_project_forecast_unknowns_not_concealed(self):
        obj=p.project_forecast({'stages':[]},{'whole_game_seconds':{}})
        self.assertIsNone(obj['seconds']['likely'])

    def test_manifest_lookup(self):
        plan={'stages':[{'id':'native','name':'Native','gate':'always',
                         'tasks':[{'key':'linux','title':'Linux entry'}]}]}
        issues=[{'number':10,'title':'Linux entry','state':'OPEN','labels':[{'name':'nova:queued'}]}]
        status=p.roadmap_status(issues,[],plan)
        self.assertEqual(status['stages'][0]['queued'],1)
        self.assertEqual(status['stages'][0]['merged'],0)

if __name__=='__main__':unittest.main()
