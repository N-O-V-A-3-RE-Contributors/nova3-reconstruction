#!/usr/bin/env python3
"""Compare source parser's observed local header fields to archive inventory."""
import json,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
results=[]
for p in sorted((ROOT/'research/assets').glob('premium_*.obb.index.json')):
 index=json.loads(p.read_text());first=index['entries'][0]
 prefix=p.with_name(p.name.replace('.index.json','.prefix'))
 actual=json.loads(subprocess.check_output([str(ROOT/'build/host/nova3_zip_header'),str(prefix),str(index['obb_size'])],text=True))
 expected=dict(name_hex=first['name'].encode().hex(),flags=first['flags'],method=first['method'],crc32=int(first['crc32'],16),compressed_size=first['compressed_size'],size=first['size'],data_offset=first['data_offset'],next_offset=first['data_offset']+first['compressed_size'])
 assert actual==expected,(actual,expected)
 results.append(dict(archive=index['obb_name'],header=actual))
assert len(results)==2
(ROOT/'research/elf/zip_header_verification.json').write_text(json.dumps(dict(case_count=2,results=results,scope='Partial local-header parsing verified against independent observed ZIP inventory; no decompression, whole original scanner, class/vector ownership or resource loading equivalence claimed'),indent=2)+'\n')
print('PASS: source local-header fields match both real Premium OBB first entries')
