#!/usr/bin/env python3
"""Source loader integration: rewritten names, real pixels and failure fallback."""
import hashlib,json,pathlib,struct,subprocess,tempfile,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2];cli=ROOT/'build/host/nova3_texture';checks=[]
pvr=struct.pack('<13I',52,4,4,0,0x36,8,4,0,0,0,0,0x21525650,1)+bytes(8)
with tempfile.TemporaryDirectory(prefix='nova3-texture-path-') as td:
 td=pathlib.Path(td)
 for label,members,request,code in [
  ('rewrite',{'test.etc':pvr},'test.tga',0),
  ('fallback',{'test.tga':pvr},'test.tga',0),
  ('prefer-rewrite',{'test.etc':pvr,'test.tga':b'bad'},'test.tga',0),
  ('no-image-fallback',{'test.etc':b'bad','test.tga':pvr},'test.tga',5),
  ('missing',{},'test.tga',4),
  ('case-sensitive',{'test.etc':pvr},'test.Tga',4)]:
  archive=td/(label+'.zip');output=td/label
  with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
   for name,data in members.items():z.writestr(name,data)
  result=subprocess.run([str(cli),str(archive),request,str(output)]);assert result.returncode==code,(label,result.returncode,code)
  if code==0:assert pathlib.Path(str(output)+'.rgb.ppm').read_bytes()==b'P6\n4 4\n255\n'+bytes([2])*48
  checks.append(dict(case=label,exit_code=code))
 # Genuine material filename -> reconstructed ETC rewrite -> ZIP/inflate/CRC ->
 # nestedPVR/ETC1 -> RGB. Compare to explicit actual stored resource path.
 obb=ROOT/'.cache/assets/premium-main.obb'
 for name in ('SF_Intro_Building_02_Wall','TS_ground'):
  outputs=[]
  for suffix in ('.tga','.etc'):
   output=td/(name+suffix);subprocess.run([str(cli),str(obb),name+suffix,str(output)],check=True);outputs.append(pathlib.Path(str(output)+'.rgb.ppm').read_bytes())
  assert outputs[0]==outputs[1];checks.append(dict(case=name,material_request=name+'.tga',stored_resource=name+'.etc',ppm_sha256=hashlib.sha256(outputs[0]).hexdigest()))
(ROOT/'research/elf/texture_path_source_verification.json').write_text(json.dumps(dict(checks=checks,scope='Source name rewrite and original-name archive-open fallback; decoding failure after successful mapped read does not fall back. Two actual first-mesh material image names produce identical RGB bytes through rewritten and explicit data paths. Does not validate GPU/material binding or quality policy.'),indent=2)+'\n');print('PASS:',len(checks),'source texture path/inflate/pixel integration cases')
