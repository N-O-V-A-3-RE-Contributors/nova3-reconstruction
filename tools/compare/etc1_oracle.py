#!/usr/bin/env python3
"""Original full convert ETC1->RGB8 path vs independently built C++ pixels.
No code hooks or helper substitutions. Explicit pitches avoid computePitch calls.
"""
import hashlib,json,pathlib,random,struct,subprocess,tempfile,zipfile
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2];binary=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so'
digest=hashlib.sha256(binary.read_bytes()).hexdigest();assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open('rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;end=(s['p_vaddr']+s['p_memsz']+4095)&~4095;u.mem_map(lo,end-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x400000);u.mem_map(0x3000000,0x20000)
source=0x2000000;dest=0x2100000;stack=0x3008000;stop=0x300fffc
rng=random.Random(0x45544331);cases=[]
# Valid differential colors are generated explicitly: malformed ETC2-style
# differential overflow is rejected by source, outside native-equivalence scope.
for flip in (False,True):
 for mode in ('individual','differential'):
  for _ in range(60):
   w,h=rng.choice((4,8,12,16)),rng.choice((4,8,12));pitch=w//4*8+rng.choice((0,8));data=bytearray((h//4)*pitch)
   for y in range(h//4):
    for x in range(w//4):
     b=bytearray(rng.randbytes(8));b[3]=(b[3]&~2)|(2 if mode=='differential' else 0)
     if mode=='differential':
      for c in range(3):
       first=rng.randrange(32);d=rng.choice([d for d in range(-4,4) if 0<=first+d<32]);b[c]=(first<<3)|(d&7)
     data[y*pitch+x*8:y*pitch+x*8+8]=b
   cases.append((mode,bytes(data),w,h,flip,pitch,rng.choice((0,4,12))))
# Real compressed blocks from local data. No reference executable is production input.
obb=ROOT/'.cache/assets/premium-main.obb'
if obb.exists():
 with zipfile.ZipFile(obb) as archive:
  count=0
  for item in archive.infolist():
   if not item.filename.lower().endswith('.etc'):continue
   data=archive.read(item)
   if data[:4]!=b'4\0\0\0':continue
   h,w=struct.unpack_from('<II',data,4)
   if w<4 or h<4:continue
   pitch=(w//4)*8
   # Top-left block row; full width bounded to64 for cheap instruction runs.
   take=min(w,64);payload=data[52:52+take//4*8]
   cases.append(('asset:'+item.filename,payload,take,4,False,take//4*8,0));count+=1
   if count==32:break
results=[]
with tempfile.TemporaryDirectory(prefix='nova3-etc1-') as td:
 path=pathlib.Path(td);inp=path/'etc1';out=path/'rgb'
 for label,data,w,h,flip,pitch,padding in cases:
  dp=w*3+padding;sentinel=bytes([0x5a])*(dp*h+32);u.mem_write(source,data);u.mem_write(dest,sentinel)
  u.mem_write(stack,struct.pack('<5I',dest,dp,w,h,int(flip)))
  for reg,value in ((UC_ARM_REG_R0,29),(UC_ARM_REG_R1,source),(UC_ARM_REG_R2,pitch),(UC_ARM_REG_R3,10),(UC_ARM_REG_SP,stack),(UC_ARM_REG_LR,stop)):u.reg_write(reg,value)
  u.emu_start(0x7914a4,stop,count=2000000)
  assert u.reg_read(UC_ARM_REG_PC)==stop and u.reg_read(UC_ARM_REG_R0)==1,(label,'native failed')
  actual=bytes(u.mem_read(dest,dp*h+32));expected=b''.join(actual[y*dp:y*dp+w*3] for y in range(h))
  for y in range(h):assert actual[y*dp+w*3:(y+1)*dp]==bytes([0x5a])*padding
  assert actual[dp*h:]==bytes([0x5a])*32
  inp.write_bytes(data);subprocess.run([str(ROOT/'build/host/nova3_etc1'),str(inp),str(out),str(w),str(h),str(int(flip)),str(pitch)],check=True)
  result=out.read_bytes();assert result==expected,(label,w,h,flip,pitch,result[:12].hex(),expected[:12].hex())
  results.append(dict(case=label,width=w,height=h,flip_y=flip,source_pitch=pitch,dest_padding=padding,input_sha256=hashlib.sha256(data).hexdigest(),rgb_sha256=hashlib.sha256(result).hexdigest()))
report=dict(binary_sha256=digest,native_entry='0x7914a4',case_count=len(results),cases=results,scope='Complete original convert ETC1 format29 to RGB8 format10 path, explicit nonzero pitches, valid individual/differential blocks, dimensions divisible by4, both Y directions. No code hooks, helper replacements or binary relocation. New-source tiny-mip clipping and malformed-block rejection are extra checked policies.')
(ROOT/'research/elf/etc1_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(results),'original ARM/source RGB pixel cases, padding/canaries intact')
