#!/usr/bin/env python3
"""Fail-closed executable provenance check, including ZIP/APK nested members.
An approval manifest binds each executable hash to source hashes. This is a
packaging guard, not a proof of compiler integrity or complete gameplay parity.
"""
import argparse,hashlib,io,json,pathlib,sys,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
MAX_MEMBER=128<<20
MAX_TOTAL=512<<20
FORBIDDEN_PARTS={'originals','analysis','generated','research','Archive'}
def sha(data):return hashlib.sha256(data).hexdigest()
def verify(path,manifest,denied):
 errors=[];seen=[];budget=[MAX_TOTAL]
 approved=manifest.get('executables',{})
 def scan(label,data,depth=0):
  digest=sha(data)
  if digest in denied:errors.append(f'{label}: original executable hash');return
  if depth>4:errors.append(f'{label}: nested container depth limit');return
  if zipfile.is_zipfile(io.BytesIO(data)) or data[:4] in (b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08'):
   try:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
     names=set();total=0
     for member in z.infolist():
      name=pathlib.PurePosixPath(member.filename)
      if member.filename in names or name.is_absolute() or '..' in name.parts:errors.append(f'{label}: unsafe/duplicate ZIP member');continue
      names.add(member.filename);total+=member.file_size
      budget[0]-=member.file_size
      if member.flag_bits&1 or member.file_size>MAX_MEMBER or total>512<<20 or budget[0]<0:errors.append(f'{label}/{member.filename}: encrypted/oversize payload');continue
      if not member.is_dir():scan(label+'/'+member.filename,z.read(member),depth+1)
   except (ValueError,OSError,zipfile.BadZipFile,RuntimeError) as ex:errors.append(f'{label}: invalid ZIP: {ex}')
   return
  isexec=data.startswith(b'\x7fELF') or data.startswith(b'dex\n') or data.startswith(b'cdex') or data.startswith(b'MZ')
  # Executable extensions with invalid headers must not bypass checking.
  if pathlib.PurePosixPath(label).suffix.lower() in {'.so','.dex','.exe','.o','.a'}:isexec=True
  if not isexec:return
  seen.append(label)
  entry=approved.get(digest)
  if not entry:errors.append(f'{label}: executable has no source provenance');return
  sources=entry.get('sources',[])
  if not sources:errors.append(f'{label}: empty source provenance')
  for source in sources:
   rel=pathlib.PurePosixPath(source['path'])
   if rel.is_absolute() or '..' in rel.parts or set(rel.parts)&FORBIDDEN_PARTS:errors.append(f'{label}: forbidden source path {rel}');continue
   src=ROOT/rel
   if not src.is_file() or sha(src.read_bytes())!=source['sha256']:errors.append(f'{label}: source hash mismatch {rel}')
  for inp in entry.get('link_inputs',[]):
   if set(pathlib.PurePosixPath(inp).parts)&FORBIDDEN_PARTS:errors.append(f'{label}: forbidden linker input {inp}')
  if not entry.get('build_command'):errors.append(f'{label}: missing build command')
 if path.is_dir():
  for p in sorted(path.rglob('*')):
   if p.is_file():
    if p.stat().st_size>MAX_MEMBER:errors.append(f'{p}: file size limit');continue
    scan(str(p),p.read_bytes())
 else:
  if path.stat().st_size>MAX_MEMBER:errors.append(f'{path}: file size limit')
  else:scan(str(path),path.read_bytes())
 return dict(ok=not errors,executables=seen,errors=errors,scope='Hash/source packaging guard. Dependency/license, linker graph and Android DEX toolchain audits remain separate requirements.')
def main():
 p=argparse.ArgumentParser();p.add_argument('artifact',type=pathlib.Path);p.add_argument('--manifest',type=pathlib.Path,required=True);p.add_argument('--denylist',type=pathlib.Path,default=ROOT/'research/elf/executable_denylist.json');a=p.parse_args()
 try:r=verify(a.artifact,json.loads(a.manifest.read_text()),set(json.loads(a.denylist.read_text())))
 except Exception as e:r=dict(ok=False,errors=[str(e)])
 print(json.dumps(r,indent=2));return 0 if r['ok'] else 1
if __name__=='__main__':sys.exit(main())
