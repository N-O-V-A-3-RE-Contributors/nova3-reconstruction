#!/usr/bin/env python3
"""Check completion/counts of source ETC1 corpus decoding; not a full pixel oracle.
Produce results with build/release/nova3_texture OBB --pixels > RESULTS first.
"""
import argparse,hashlib,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[2];p=argparse.ArgumentParser();p.add_argument('--results',type=pathlib.Path,default=ROOT/'.cache/logs/etc1-corpus-pixels.json');args=p.parse_args()
raw=args.results.read_bytes();records=json.loads(raw);metadata=json.loads((ROOT/'.cache/logs/stage2-texture-bundles.json').read_text());assert len(records)==len(metadata)==1332
mips=planes=pixels=0
for got,want in zip(records,metadata):
 assert 'error' not in got and got['archive_index']==want['archive_index'] and got['alpha_mode']==want['alpha_mode'] and len(got['planes'])==len(want['planes'])
 for image,layout in zip(got['planes'],want['planes']):
  crcs=image['rgb_crc32'];assert len(crcs)==len(layout['mips']) and all(isinstance(v,int) and 0<=v<=0xffffffff for v in crcs)
  assert {k:v for k,v in image.items() if k!='rgb_crc32'}==layout
  planes+=1;mips+=len(crcs);pixels+=sum(w*h for w,h,_,_ in layout['mips'])
assert (planes,mips)==(1691,17505)
report=dict(texture_assets=len(records),planes=planes,mip_levels=mips,decoded_rgb_pixels=pixels,decoded_rgb_bytes=pixels*3,result_sha256=hashlib.sha256(raw).hexdigest(),records=records,scope='Complete supplied .etc corpus decodes without invalid-block/bounds/limit errors. Per-mip source RGB CRCs recorded for regression. Layout matches independent corpus reader; original ARM pixel equivalence established separately on272 cases, not every corpus pixel. Tiny mip cropping is extra source policy.')
(ROOT/'research/elf/etc1_corpus_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(records),'textures;',planes,'planes;',mips,'mips;',pixels,'decoded RGB pixels')
