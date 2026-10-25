#!/usr/bin/env python3
"""Validate seekable source package index, named reads and BRES resource chain."""
import collections,hashlib,json,pathlib,subprocess,tempfile,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2];results=[];total=0;relocations=0
for p in sorted((ROOT/'research/assets').glob('premium_*.obb.index.json')):
 expected=json.loads(p.read_text());kind='main' if pathlib.PurePosixPath(expected['obb_name']).name.startswith('main.') else 'patch';obb=ROOT/f'.cache/assets/premium-{kind}.obb';assert obb.is_file(),'Run tools/extract/materialize_premium.py first'
 actual=json.loads(subprocess.check_output([str(ROOT/'build/host/nova3_package_probe'),str(obb),'--list'],text=True));assert len(actual)==len(expected['entries'])
 for a,b in zip(actual,expected['entries']):
  want={k:b[k] for k in ('offset','data_offset','compressed_size','size','method','flags')};want['name_hex']=b['name'].encode().hex();want['crc32']=int(b['crc32'],16);assert a==want
 selected=[];groups=collections.defaultdict(list)
 for e in expected['entries']:
  if e['size']<=8<<20 and e['compressed_size']<=16<<20:groups[pathlib.PurePosixPath(e['name']).suffix].append(e)
 for ext,wanted in (('.bdae',32),('.irr',8),('.etc',8),('.txt',3),('.xml',1),('.gla',4)):
  group=groups[ext];take=min(len(group),wanted)
  for i in range(take):selected.append(group[(len(group)-1)*i//max(1,take-1)])
 with tempfile.TemporaryDirectory(prefix='nova3-packages-') as td:
  out=pathlib.Path(td);subprocess.run([str(ROOT/'build/host/nova3_package_probe'),str(obb),str(out),*[x['name'] for x in selected]],check=True)
  records=[]
  with zipfile.ZipFile(obb) as z:
   for i,e in enumerate(selected):
    data=(out/f'{i}.asset').read_bytes();reference=z.read(e['name']);assert data==reference
    records.append(dict(name=e['name'],size=len(data),sha256=hashlib.sha256(data).hexdigest()));total+=1
    if e['name'].endswith('.bdae') and data.startswith(b'BRES'):
     relocated=out/'relocated';subprocess.run([str(ROOT/'build/host/nova3_bres_relocate'),str(out/f'{i}.asset'),'0x02000000',str(relocated)],check=True);relocations+=1
  if kind=='main':
   first=out/'first';subprocess.run([str(ROOT/'build/host/nova3_resource'),'01_room_a.bdae',str(first),'--bres',str(obb)],check=True,stdout=subprocess.DEVNULL)
   native=json.loads((ROOT/'research/assets/BRES_native_verification.json').read_text())['cases'][0]['output_sha256'];assert hashlib.sha256(first.read_bytes()).hexdigest()==native
 results.append(dict(edition=kind,index_entries=len(actual),named_reads=records))
 print('PASS',kind,len(actual),'index entries;',len(selected),'named resource reads',flush=True)
report=dict(index_entries=sum(x['index_entries'] for x in results),named_reads_verified=total,bres_images_relocated=relocations,native_golden_chain=True,results=results,scope='Full source-owned path: file-region ZIP index, native lookup policy, seeking, source-built raw DEFLATE, own CRC and BRES byte relocation. Named outputs match Python ZIP outputs; first BRES relocated output matches original ARM golden. No mesh/level rendering or whole engine class equivalence.')
(ROOT/'research/elf/package_resources_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS',total,'named reads;',relocations,'BRES images;', 'first complete resource chain matches native BRES golden')
