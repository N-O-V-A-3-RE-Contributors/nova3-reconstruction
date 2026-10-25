import hashlib,importlib.util,io,pathlib,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('guard',ROOT/'tools/verify_source_only.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
def test_reject_original_even_disguised(tmp_path):
 blob=b'\x7fELF'+b'original-test-fixture'*100
 path=tmp_path/'texture.dat';path.write_bytes(blob)
 assert not guard.verify(path,{},set([hashlib.sha256(blob).hexdigest()]))['ok']
def test_reject_unapproved_nested_dex(tmp_path):
 path=tmp_path/'game.apk'
 with zipfile.ZipFile(path,'w') as z:z.writestr('assets/disguised',b'dex\n035\0'+b'x'*128)
 assert not guard.verify(path,{},set())['ok']
def test_source_mismatch_and_reference_link(tmp_path):
 blob=b'\x7fELF'+b'fake';path=tmp_path/'test.so';path.write_bytes(blob)
 entry={'sources':[{'path':'CMakeLists.txt','sha256':'wrong'}],'build_command':'compile','link_inputs':['analysis/original.so']}
 result=guard.verify(path,{'executables':{hashlib.sha256(blob).hexdigest():entry}},set())
 assert len(result['errors'])==2

def test_data_allowed(tmp_path):
 path=tmp_path/'data';path.write_bytes(b'BRES'+bytes(64));assert guard.verify(path,{},set())['ok']

def test_prefixed_nested_zip_denied(tmp_path):
 blob=b'\x7fELF'+b'original-code-fixture'*50
 nested=io.BytesIO()
 with zipfile.ZipFile(nested,'w') as z:z.writestr('lib/old.so',blob)
 path=tmp_path/'texture.dat';path.write_bytes(b'prefix'+nested.getvalue())
 assert not guard.verify(path,{},set([hashlib.sha256(blob).hexdigest()]))['ok']

def test_nested_shared_budget(tmp_path,monkeypatch):
 monkeypatch.setattr(guard,'MAX_TOTAL',1000)
 inner=io.BytesIO()
 with zipfile.ZipFile(inner,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('data',bytes(400))
 path=tmp_path/'nested.zip'
 with zipfile.ZipFile(path,'w') as z:
  z.writestr('a.zip',inner.getvalue());z.writestr('b.zip',inner.getvalue())
 assert not guard.verify(path,{},set())['ok']
