#!/usr/bin/env python3
"""Stream actual Premium OBBs into source C++ indexer; compare every entry.
No proprietary OBB extraction, no original executable execution.
"""
import hashlib,json,pathlib,subprocess,tempfile,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
results=[]
with zipfile.ZipFile(ROOT/'Archive/Nova3_premium_only_OBB.rar') as archive:
 for index_path in sorted((ROOT/'research/assets').glob('premium_*.obb.index.json')):
  expected=json.loads(index_path.read_text());obb=expected['obb_name'];size=expected['obb_size']
  with tempfile.TemporaryFile() as output:
   proc=subprocess.Popen([str(ROOT/'build/host/nova3_zip_index'),str(size)],stdin=subprocess.PIPE,stdout=output)
   with archive.open(obb) as stream:
    broken=False
    for data in iter(lambda:stream.read(1<<20),b''):
     if not broken:
      try:proc.stdin.write(data)
      except BrokenPipeError:broken=True
   try:proc.stdin.close()
   except BrokenPipeError:pass
   rc=proc.wait();assert rc==0,(obb,rc)
   output.seek(0);actual=json.load(output)
  assert len(actual)==len(expected['entries'])
  for a,b in zip(actual,expected['entries']):
   e={k:b[k] for k in ('offset','data_offset','compressed_size','size','method','flags')};e['name_hex']=b['name'].encode().hex();e['crc32']=int(b['crc32'],16)
   assert a==e,(a,e)
  print('PASS',obb,len(actual),'all fields match',flush=True)
  results.append(dict(obb=obb,entries_matching=len(actual),fields=['name','offset','data_offset','compressed_size','size','method','flags','crc32'],scope='Source native local-header stream traversal vs independent Python local+central inventory. Outer member CRC checked while draining stream; inner payload decompression and CRC not checked.'))
(ROOT/'research/elf/zip_index_verification.json').write_text(json.dumps(dict(results=results),indent=2)+'\n')
