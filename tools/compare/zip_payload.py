#!/usr/bin/env python3
"""Source-built inflater: real Premium assets plus corrupt/truncated regressions."""
import hashlib,json,pathlib,struct,subprocess,tempfile,zlib
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=next((ROOT/'research/assets').glob('premium_main*.obb.index.json'));index=json.loads(p.read_text());prefix=p.with_name(p.name.replace('.index.json','.prefix')).read_bytes();results=[];rejections=0
with tempfile.TemporaryDirectory(prefix='nova3-extract-') as td:
 src=pathlib.Path(td)/'entry.zip';out=pathlib.Path(td)/'asset'
 for entry in index['entries']:
  end=entry['data_offset']+entry['compressed_size']
  if end>len(prefix):continue
  local=prefix[entry['offset']:end];src.write_bytes(local)
  subprocess.run([str(ROOT/'build/host/nova3_zip_extract'),str(src),str(out)],check=True)
  decoded=out.read_bytes();raw=prefix[entry['data_offset']:end]
  expected=zlib.decompress(raw,-15) if entry['method']==8 else raw
  assert decoded==expected and len(decoded)==entry['size'] and zlib.crc32(decoded)==int(entry['crc32'],16)
  digest=hashlib.sha256(decoded).hexdigest()
  if entry['name']=='01_room_a.bdae':assert digest=='7443fa93cddec857399170024f1f7cfb47d2bcd89ab29baed10b7ec4496b0fc7'
  results.append(dict(name=entry['name'],size=len(decoded),sha256=digest))
  # Wrong CRC, truncated bytes, oversized output and wrong uncompressed length.
  cases=[]
  corrupt=bytearray(local);corrupt[14]^=1;cases.append(corrupt)
  cases.append(local[:-1]);corrupt=bytearray(local);struct.pack_into('<I',corrupt,22,0xfffffffe);cases.append(corrupt)
  corrupt=bytearray(local);struct.pack_into('<I',corrupt,22,entry['size']+1);cases.append(corrupt)
  for bad in cases:
   src.write_bytes(bad)
   r=subprocess.run([str(ROOT/'build/host/nova3_zip_extract'),str(src),str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   assert r.returncode in (3,4), ('unexpected failure/crash',r.returncode);rejections+=1
assert results
report=dict(decoded_assets=len(results),corrupt_cases_rejected=rejections,results=results,scope='Observed raw-DEFLATE payload semantics from CZipReader::openFile0x819878 with independently source-built upstream zlib1.3.2, size/CRC validation added; output bytes match separately invoked Python inflater and recorded first-asset hash. No whole-class/engine-equivalence claim.')
(ROOT/'research/elf/zip_payload_verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(f'PASS: {len(results)} real assets decoded; {rejections} corrupt/truncated/size/CRC cases rejected')
