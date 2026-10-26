import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from nova3_team import local_index

class TestLocalIndex(unittest.TestCase):
    def test_extract_size_never_guesses(self):
        self.assertEqual(local_index.extract_bounds({'address':'0x10','size':12},'00000010'),(16,12))
        self.assertEqual(local_index.extract_bounds({'start':'0x10','end':'0x30'},'00000010'),(16,32))
        self.assertIsNone(local_index.extract_bounds({'pseudocode':'abc'},'00000010'))
        self.assertIsNone(local_index.extract_bounds({'size':0},'00000010'))

    def test_exact_hash_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            so=root/'analysis/Premium/libNOVA3_neon.so';so.parent.mkdir(parents=True)
            so.write_bytes(b'ELFTESTPRIVATESECRET')
            digest=hashlib.sha256(so.read_bytes()).hexdigest()
            exports=root/'generated/ghidra/exports'/digest;exports.mkdir(parents=True)
            (exports/'00001100.json').write_text(json.dumps({'address':'0x1100','size':28,
                'decompiled_source':'DO_NOT_PUBLISH_PRIVATE_PSEUDOCODE'}))
            (exports/'00001110.json').write_text(json.dumps({'entry':'0x1110','size':0}))
            info=local_index.collect(root)
            self.assertEqual(len(info['functions']),1)
            self.assertEqual(info['functions'][0]['size'],28)
            self.assertNotIn('DO_NOT_PUBLISH_PRIVATE_PSEUDOCODE',json.dumps(info))
            self.assertNotIn('ELFTESTPRIVATESECRET',json.dumps(info))
            self.assertEqual(info['references'][0]['sha256'],digest)

    def test_wrong_hash_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            exports=root/'generated/ghidra/exports'/('a'*64);exports.mkdir(parents=True)
            (exports/'00001100.json').write_text('{"size":20}')
            self.assertEqual(local_index.collect(root)['functions'],[])

if __name__=='__main__':unittest.main()
