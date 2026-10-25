#!/usr/bin/env python3
"""Compare independently compiled relocation bytes to original ARM goldens."""
import hashlib,json,pathlib,struct,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
fixture=ROOT/'research/assets/premium_01_room_a.bdae.specimen';raw=fixture.read_bytes()
golden=json.loads((ROOT/'research/assets/BRES_native_verification.json').read_text());checks=[]
with tempfile.TemporaryDirectory(prefix='nova3-bres-source-') as td:
 src=pathlib.Path(td)/'input';out=pathlib.Path(td)/'output';again=pathlib.Path(td)/'again'
 def run(data,base,expected,label):
  src.write_bytes(data);subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(src),str(base),str(out)],check=True)
  result=out.read_bytes();assert hashlib.sha256(result).hexdigest()==expected,label
  subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(out),str(base),str(again)],check=True);assert result==again.read_bytes()
  checks.append(dict(label=label,base=base,sha256=expected))
 for case in golden['cases']:run(raw,case['base'],case['output_sha256'],'specimen')
 for case in golden['header_cases']:
  variant=bytearray(raw);struct.pack_into('<H' if case['offset'] in (4,6) else '<I',variant,case['offset'],case['value'])
  run(variant,0x02000000,case['output_sha256'],f'header{case["offset"]}:{case["value"]}')
 synthetic=bytearray(128);synthetic[:4]=b'BRES'
 for offset,value in ((16,3),(24,60),(60,24),(64,28),(68,28)):struct.pack_into('<I',synthetic,offset,value)
 src.write_bytes(synthetic);subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(src),'0x02000000',str(out)],check=True)
 assert struct.unpack_from('<I',out.read_bytes(),28)[0]==golden['alias_case']['slot28_after']
 for n in range(60):
  src.write_bytes(raw[:n]);r=subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(src),'0',str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);assert r.returncode==3
 bad=bytearray(raw);bad[0]=0;src.write_bytes(bad);r=subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(src),'0',str(out)],stderr=subprocess.DEVNULL);assert r.returncode==3
report=dict(native_entry='0x69c6f0',source='reconstruction/engine/bres.cpp',checks=checks,real_base_cases=4,header_cases=7,duplicate_zero_case=True,idempotence_cases=len(checks),truncated_inputs_rejected=60,bad_magic_rejected=True,scope='Contiguous BRES byte-relocation branch only, native object metadata/shared-string/split-buffer paths excluded. Source adds bounds checks and atomic output; table/target arithmetic follows sequential unsigned ARM32 operations.')
(ROOT/'research/elf/bres_source_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS: 4 real bases,7 header variants,idempotence and duplicate/zero targets match original ARM;60 truncated inputs rejected')
