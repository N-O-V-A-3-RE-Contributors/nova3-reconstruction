#!/usr/bin/env python3
"""Offline original ARM PVR header predicate versus source C++; I/O modeled.
Only seek/read/name/strncmp/logging hooks; validation instructions execute intact.
"""
import hashlib,json,pathlib,random,struct,subprocess,tempfile
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2];binary=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so';digest=hashlib.sha256(binary.read_bytes()).hexdigest();assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open('rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;end=(s['p_vaddr']+s['p_memsz']+4095)&~4095;u.mem_map(lo,end-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x10000);u.mem_map(0x3000000,0x20000)
obj=0x2000000;vt=0x2000100;dest=0x2001000;stop=0x300fffc
put=lambda p,v:u.mem_write(p,struct.pack('<I',v))
put(obj,vt)
for offset,target in ((0xc,0x3010000),(0x18,0x3010100),(0x28,0x3010200)):put(vt+offset,target)
u.mem_write(0x2003000,b'fixture\0');current=b'';position=0
addresses=[0x3010000,0x3010100,0x3010200,0xd2da8,0x69ddac]
def hook(uc,addr,size,data):
 global position
 r0=uc.reg_read(UC_ARM_REG_R0);r1=uc.reg_read(UC_ARM_REG_R1);r2=uc.reg_read(UC_ARM_REG_R2)
 if addr==0x3010100:position=r1;uc.reg_write(UC_ARM_REG_R0,1)
 elif addr==0x3010000:
  data=current[position:position+r2]
  if data:uc.mem_write(r1,data)
  position+=len(data);uc.reg_write(UC_ARM_REG_R0,len(data))
 elif addr==0x3010200:uc.reg_write(UC_ARM_REG_R0,0x2003000)
 elif addr==0xd2da8:
  a,b=bytes(uc.mem_read(r0,r2)),bytes(uc.mem_read(r1,r2));uc.reg_write(UC_ARM_REG_R0,((a>b)-(a<b))&0xffffffff)
 elif addr==0x69ddac:uc.reg_write(UC_ARM_REG_R0,0)
 else:raise AssertionError(addr)
 uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
for address in addresses:u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
base=bytearray(struct.pack('<13I',52,256,256,8,0x136,43704,4,0xffffffff,0xffffffff,0xffffffff,0,0x21525650,1));cases=[bytes(base[:n]) for n in range(53)]
for offset in (0,4,8,12,16,44,48):
 for value in (0,1,6,52,0xffffffff):
  b=bytearray(base);struct.pack_into('<I',b,offset,value);cases.append(bytes(b))
rng=random.Random(0x50565232)
for _ in range(300):
 b=bytearray(base);width=rng.choice((0,1,2,4,128,256,257,1024));height=rng.choice((0,1,2,8,256,512));flags=rng.choice((0x36,0x136,0x1136,0x4136,0x5136));levels=rng.randrange(0,12);surfaces=rng.choice((0,1,6,8))
 for offset,value in ((4,height),(8,width),(12,levels),(16,flags),(48,surfaces)):struct.pack_into('<I',b,offset,value)
 cases.append(bytes(b))
results=[]
with tempfile.TemporaryDirectory(prefix='nova3-pvr-') as td:
 file=pathlib.Path(td)/'header'
 for current in cases:
  u.mem_write(dest,bytes(128));position=0
  for reg,value in ((UC_ARM_REG_R0,obj),(UC_ARM_REG_R1,dest),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)):u.reg_write(reg,value)
  u.emu_start(0x781c78,stop,count=10000);assert u.reg_read(UC_ARM_REG_PC)==stop
  native=u.reg_read(UC_ARM_REG_R0);file.write_bytes(current)
  source=int(subprocess.check_output([str(ROOT/'build/host/nova3_texture'),'--header',str(file)],text=True));assert source==native,(current.hex(),native,source)
  results.append(dict(header_hex=current.hex(),accepted=bool(native)))
report=dict(native_entry='0x781c78',binary_sha256=digest,case_count=len(results),cases=results,hooks='Modeled IReadFile seek/read/name, byte strncmp and log sink; header validation code unchanged. Logging/file ABI side effects excluded.',scope='PVRv2 header predicate, including cubemap/volume/mipmap-count edge cases. Not pixel rendering or full ITexture construction equivalence.')
(ROOT/'research/elf/pvr_header_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(results),'original ARM/source PVR header cases')
