#!/usr/bin/env python3
"""Materialize checked asset-data OBBs for seekable source-reader tests.
Only observed inventory members are accepted; output names are fixed local cache
paths, never archive-supplied paths. Original APKs/ELFs are not read or executed.
"""
import hashlib,json,os,pathlib,tempfile,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
cache=ROOT/'.cache/assets';cache.mkdir(parents=True,exist_ok=True)
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4<<20),b''):h.update(b)
 return h.hexdigest()
with zipfile.ZipFile(ROOT/'Archive/Nova3_premium_only_OBB.rar') as archive:
 for p in sorted((ROOT/'research/assets').glob('premium_*.obb.index.json')):
  index=json.loads(p.read_text());member=index['obb_name'];info=archive.getinfo(member)
  assert info.file_size==index['obb_size'] and 0<info.file_size<3*(1<<30)
  kind='main' if pathlib.PurePosixPath(member).name.startswith('main.') else 'patch'
  out=cache/f'premium-{kind}.obb';record=out.with_suffix('.json')
  if out.exists() and record.exists():
   prior=json.loads(record.read_text())
   if prior['member']==member and out.stat().st_size==info.file_size and digest(out)==prior['sha256']:
    print('VERIFIED CACHE',out,flush=True);continue
  h=hashlib.sha256();count=0
  with tempfile.NamedTemporaryFile(dir=cache,prefix=f'{kind}-',suffix='.part',delete=False) as f:
   temp=pathlib.Path(f.name)
   try:
    with archive.open(info) as stream:
     for b in iter(lambda:stream.read(4<<20),b''):
      count+=len(b)
      if count>info.file_size:raise ValueError('member size limit')
      h.update(b);f.write(b)
    assert count==info.file_size
   except BaseException:
    temp.unlink(missing_ok=True);raise
  os.replace(temp,out)
  record.write_text(json.dumps(dict(member=member,size=count,sha256=h.hexdigest(),outer_member_crc32=f'{info.CRC:08x}',scope='asset data only; complete outer member ZIP CRC verified on read'),indent=2)+'\n')
  print('MATERIALIZED',out,count,h.hexdigest(),flush=True)
