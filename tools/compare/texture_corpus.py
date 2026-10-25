#!/usr/bin/env python3
"""Cross-check all source texture planes/mips against actual PVR/SPLIT bytes."""
import collections,hashlib,io,json,pathlib,struct,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
source=json.loads((ROOT/'.cache/logs/stage2-texture-bundles.json').read_text());inventory=json.loads(next((ROOT/'research/assets').glob('premium_main*.obb.index.json')).read_text())['entries'];records=[];splits=0;planes=0;mips=0
with zipfile.ZipFile(ROOT/'.cache/assets/premium-main.obb') as archive:
 for actual in source:
  name=inventory[actual['archive_index']]['name'];assert name.endswith('.etc') and 'error' not in actual
  raw=archive.read(name);expected=[];mode=0
  if raw[:4]==b'PK\x03\x04':
   splits+=1
   with zipfile.ZipFile(io.BytesIO(raw)) as nested:
    names=sorted(nested.namelist(),key=lambda n:n.rsplit('/',1)[-1].lower())
    marker=next(n for n in names if n.rsplit('/',1)[-1].lower()=='split');value=nested.read(marker);assert len(value)==4;mode=struct.unpack('<I',value)[0];assert mode<4
    for prefix in ('rgb','alpha'):expected.append(nested.read(next(n for n in names if n.rsplit('/',1)[-1].lower().startswith(prefix))))
  else:expected=[raw]
  assert len(actual['planes'])==len(expected) and actual['alpha_mode']==mode
  detail=[]
  for plane,data in zip(actual['planes'],expected):
   h=struct.unpack_from('<13I',data);header,height,width,levels,flags,length,bits=h[:7];assert header==52 and data[44:48]==b'PVR!' and flags&255==0x36 and bits==4 and len(data)==52+length
   want=[];offset=52;w=width;y=height
   for _ in range(levels+1):
    size=max(1,(w+3)//4)*max(1,(y+3)//4)*8;want.append([w,y,offset,size]);offset+=size;w=max(1,w//2);y=max(1,y//2)
   assert offset==len(data) and plane==dict(width=width,height=height,bytes=len(data),mips=want)
   planes+=1;mips+=len(want);detail.append(dict(width=width,height=height,mip_count=len(want),bytes=len(data),payload_sha256=hashlib.sha256(data[52:]).hexdigest()))
  records.append(dict(name=name,split=bool(len(expected)==2),alpha_mode=mode,planes=detail))
assert len(records)==1332
report=dict(texture_assets=len(records),split_assets=splits,planes=planes,mip_levels=mips,records=records,scope='Source named ZIP reads, direct/split PVR image selection and compressed mip offsets cover complete supplied Premium .etc corpus. Independently invoked Python ZIP/PVR-byte cross-check. No pixel decoding, alpha-mode shader semantics, material binding or GPU/render equivalence.')
(ROOT/'research/elf/texture_corpus_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(records),'textures;',splits,'split containers;',planes,'planes;',mips,'mip regions')
