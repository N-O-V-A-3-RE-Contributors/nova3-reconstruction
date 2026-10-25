#!/usr/bin/env python3
"""Validate source extracted mesh bytes against native-backed structural fields."""
import hashlib,json,pathlib,struct,subprocess,tempfile,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
selection=json.loads((ROOT/'research/elf/package_resources_verification.json').read_text())['results'][0]['named_reads'];names=[e['name'] for e in selection if e['name'].endswith('.bdae')];results=[];unsupported=[]
with zipfile.ZipFile(ROOT/'.cache/assets/premium-main.obb') as archive,tempfile.TemporaryDirectory(prefix='nova3-mesh-') as td:
 out=pathlib.Path(td)/'mesh.obj'
 for name in names:
  raw=archive.read(name)
  proc=subprocess.run([str(ROOT/'build/host/nova3_mesh'),str(ROOT/'.cache/assets/premium-main.obb'),name,str(out)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if proc.returncode==5:unsupported.append(dict(name=name,error=proc.stderr.strip()));continue
  assert proc.returncode==0,(name,proc.returncode,proc.stderr)
  u=lambda p:struct.unpack_from('<I',raw,p)[0]
  root=u(32);geometry=u(root+0x88);mesh=u(geometry+12);count=u(mesh+4);stride=u(u(mesh+12));offset=u(u(mesh+20));data=u(mesh+0x30)+4
  vertices=[];faces=[]
  for line in out.read_text().splitlines():
   p=line.split()
   if not p:continue
   if p[0]=='v':vertices.append([float(x) for x in p[1:]])
   if p[0]=='f':faces.extend(int(x)-1 for x in p[1:])
  assert len(vertices)==count
  actual=b''.join(struct.pack('<3f',*v) for v in vertices);expected=b''.join(raw[data+i*stride+offset:data+i*stride+offset+12] for i in range(count));assert actual==expected
  reference_indices=[]
  for i in range(u(mesh+0x38)):
   b=u(mesh+0x3c)+i*56;minimum,maximum,n=u(b+0x20),u(b+0x24),u(b+0x28);width=2 if maximum-minimum<=0xfffe else 4
   indices=struct.unpack_from('<'+('H' if width==2 else 'I')*n,raw,u(b+0x2c)+4);reference_indices.extend(i+minimum for i in indices)
  assert faces==reference_indices and len(faces)%3==0 and all(0<=i<count for i in faces)
  bounds=struct.unpack_from('<6f',raw,mesh+0x40)
  assert all(min(v[a] for v in vertices)>=bounds[a]-0.001 and max(v[a] for v in vertices)<=bounds[a+3]+0.001 for a in range(3)),name
  results.append(dict(name=name,vertices=count,triangles=len(faces)//3,position_bytes_sha256=hashlib.sha256(actual).hexdigest(),indices_u32_sha256=hashlib.sha256(struct.pack('<'+'I'*len(faces),*faces)).hexdigest(),bbox=bounds))
assert results and results[0]['vertices']==12 and results[0]['triangles']==6
report=dict(decoded_meshes=len(results),unsupported_assets=unsupported,results=results,scope='First inline geometry from sampled BDAE assets, interleaved float primary stream, triangle lists with per-buffer minimum-vertex offsets. Byte positions/indices compare native-backed field reader; containment bounds validated. Native accessor and GL argument-slice oracles support layouts/type/topology; no original whole-mesh runtime/render comparison.')
(ROOT/'research/elf/mesh_source_verification.json').write_text(json.dumps(report,indent=2)+'\n');print(f'PASS: {len(results)} real mesh decodes byte-match fields and indices; {len(unsupported)} unsupported assets explicitly rejected')
