import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import discovery

D='a'*64
E='b'*64


def entry(d=D, a='0x100', size=24, status='pending'):
    return {'reference_sha256':d,'address':a,'size':size,
            'boundary_status':'tool_identified','status':status}


def ref(d=D,name='libNOVA3_neon.so'):
    return {'library':name,'sha256':d,'source':'local ELF'}


class DiscoveryTests(unittest.TestCase):
    def test_elf_detection_skips_non_elf_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'analysis'; d.mkdir()
            (d/'libNOVA3_neon.so').write_bytes(b'\x7fELF' + b'a'*20)
            (d/'libbogus.so').write_bytes(b'NOPE')
            (d/'liblink.so').symlink_to(d/'libNOVA3_neon.so')
            found=discovery.find_elfs(root)
            self.assertEqual([x.name for x in found],['libNOVA3_neon.so'])
            self.assertEqual(discovery.find_elfs(root,root/'nope'),[])

    def test_parse_strips_everything_except_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'ghidra.json'
            f.write_text(json.dumps({'normalization':'image-base-relative','functions':[
                {'address':'0x100','size':12,'full_decompilation':'SECRET','name':'GameloftSecret'},
                {'address':'0x110','size':0},{'address':'0x100','size':12},
                {'address':'0x200','size':128}]}))
            data=discovery.parse_export(f,digest=D,library='libNOVA3_neon.so')
            text=json.dumps(data)
            self.assertNotIn('SECRET',text)
            self.assertNotIn('GameloftSecret',text)
            self.assertEqual(len(data['functions']),2)
            self.assertEqual(data['functions'][0]['address'],'0x100')
            self.assertIn('NOT',data.get('notes','NOT'))

    def test_parse_rejects_untrusted_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'bad.json'
            f.write_text(json.dumps({'functions':[{'address':'0x100','size':10}]}))
            with self.assertRaises(ValueError):
                discovery.parse_export(f,digest=D,library='libNOVA3_neon.so')

    def test_merge_append_only_preserves_existing_metadata(self):
        current={'schema_version':1,'references':[ref()],
                 'functions':[entry(status='matched')]}
        new={'schema_version':1,'references':[ref()],
             'functions':[entry(size=64,status='pending'),entry(a='0x200')]}
        result,added=discovery.merge_ledgers(current,[new,new])
        self.assertEqual(added,1)
        self.assertEqual(len(result['functions']),2)
        self.assertEqual(result['functions'][0]['status'],'matched')
        self.assertEqual(result['functions'][0]['size'],24)
        self.assertEqual(len(current['functions']),1)

    def test_new_library_preserved(self):
        existing={'references':[ref()],'functions':[entry()]}
        update={'references':[ref(E,'libGloftNAHM.so')],
                'functions':[entry(E,'0x300')]}
        result,added=discovery.merge_ledgers(existing,[update])
        self.assertEqual(added,1)
        self.assertEqual(len(result['references']),2)

    def test_reject_hash_aliasing(self):
        old={'references':[ref()],'functions':[]}
        bad={'references':[ref(D,'libOther.so')],'functions':[]}
        with self.assertRaises(ValueError):discovery.merge_ledgers(old,[bad])

    def test_reject_invalid_size_and_too_large(self):
        ledger={'references':[ref()],'functions':[]}
        for size in (0,-1,1048577,True):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    discovery.merge_ledgers(ledger,[{'references':[ref()], 'functions':[entry(size=size)]}])

    def test_write_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'first/second.json'
            discovery.write_atomic(path,b'abc')
            self.assertEqual(path.read_bytes(),b'abc')
            discovery.write_atomic(path,b'xyz')
            self.assertEqual(path.read_bytes(),b'xyz')

    def test_ghidra_fake_executable_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fake=root/'fakeheadless'; elf=root/'libNOVA3_neon.so'; elf.write_bytes(b'\x7fELF')
            script=root/'Nova3FunctionLedger.java';script.write_text('fake script')
            fake.write_text('#!/usr/bin/env python3\n'
                'import json,sys\n'
                'out=sys.argv[sys.argv.index("-postScript")+2]\n'
                'open(out,"w").write(json.dumps({"normalization":"image-base-relative","functions":[{"address":"0x1234","size":20}]}))\n')
            fake.chmod(0o755)
            out=root/'export.json'
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(root/'cache')}):
                discovery.cache_root().mkdir(parents=True)
                discovery.run_ghidra(fake,elf,script=script,digest=D,out=out,timeout=60)
            data=discovery.parse_export(out,digest=D,library=elf.name)
            self.assertEqual(len(data['functions']),1)

    def test_cycle_skips_tools_without_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(Path(tmp)/'cache')}):
                with patch.object(discovery,'ensure_ghidra',side_effect=AssertionError('should not be called')):
                    result=discovery.one_cycle(Path(tmp),'owner/repo',publish=False)
                    self.assertEqual(result['mode'],'no-references')

    def test_cycle_uses_cache_and_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'analysis';source.mkdir()
            elf=source/'libNOVA3_neon.so';elf.write_bytes(b'\x7fELF' + b'1234')
            digest=discovery.sha256_file(elf)
            data={'normalization':'image-base-relative','functions':[{'address':'0x1234','size':20}]}
            with patch.dict(os.environ,{'NOVA3_DISCOVERY_CACHE':str(root/'cache')}):
                out=discovery.cache_root()/'exports'/f'{digest}-{discovery.VERSION}.json'
                out.parent.mkdir(parents=True); out.write_text(json.dumps(data))
                with patch.object(discovery,'ensure_ghidra',return_value=Path('/fake')):
                    with patch.object(discovery,'run_ghidra',side_effect=AssertionError('cache should be reused')):
                        result=discovery.one_cycle(root,'owner/repo',publish=False)
                        self.assertEqual(result['identified'],1)

    def test_official_checksum_missing_fails_closed(self):
        with patch.object(discovery,'run_cmd',return_value=type('P',(),{'stdout':json.dumps({
             'tag_name':'Ghidra_12.2_build','assets':[{'name':'ghidra_12.2_PUBLIC_20261022.zip'}],'body':'no digest'})})()):
            with self.assertRaises(RuntimeError):discovery.latest_official_ghidra()

if __name__=='__main__':unittest.main()
