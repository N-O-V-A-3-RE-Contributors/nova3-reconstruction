#!/usr/bin/env python3
import importlib.util,io,pathlib,struct,tempfile,unittest,zipfile

def load(name):
    spec=importlib.util.spec_from_file_location(name,pathlib.Path(__file__).with_name(name+".py"));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
archive=load("inventory_archives");table=load("inspect_freemium_table")
class InspectionTests(unittest.TestCase):
    def test_paths(self):
        for name in ("../escape","/absolute","C:/drive","a/../../b","a\\..\\b","bad\0name"):
            self.assertFalse(archive.safe_path(name),name)
        self.assertTrue(archive.safe_path("safe/sub/asset.bdae"))
    def test_zip_valid_and_truncated(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,"w",compression=zipfile.ZIP_DEFLATED) as z:z.writestr("real.dat",b"data"*100)
        data=b.getvalue()
        with tempfile.TemporaryDirectory(dir=archive.ROOT/"research/assets") as temp:
            old=archive.OUT;archive.OUT=pathlib.Path(temp)
            try:
                result=archive.zip_local_index(io.BytesIO(data),"synthetic","test.obb",len(data))
                self.assertEqual(result["total_entries"],1);self.assertTrue(result["central_directory_validated"])
                with self.assertRaises((ValueError,struct.error)):archive.zip_local_index(io.BytesIO(data[:20]),"synthetic","test.obb",20)
            finally:archive.OUT=old
    def test_zip_traversal(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,"w") as z:z.writestr("../escape",b"x")
        with self.assertRaises(ValueError):archive.zip_local_index(io.BytesIO(b.getvalue()),"synthetic","test.obb",len(b.getvalue()))
    def test_zip_encryption_flag(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,"w") as z:z.writestr("x",b"y")
        data=bytearray(b.getvalue());data[6]=1
        with self.assertRaises(ValueError):archive.zip_local_index(io.BytesIO(data),"synthetic","test.obb",len(data))
    def table(self):
        return struct.pack(">4I",6102,52,48,2)+struct.pack(">4I",52,2,0,0)+struct.pack(">4I",54,3,2,0)+b"a\0b\0"
    def test_table_valid(self):self.assertEqual(table.inspect(self.table(),57)["entry_count"],2)
    def test_table_bounds(self):
        for p,size in ((b"",57),(self.table()[:30],57),(self.table(),56),(self.table(),58)):
            with self.assertRaises((ValueError,struct.error)):table.inspect(p,size)
    def test_table_discontiguous(self):
        b=bytearray(self.table());struct.pack_into(">I",b,32,53)
        with self.assertRaises(ValueError):table.inspect(b,57)
if __name__=="__main__":unittest.main(verbosity=2)
