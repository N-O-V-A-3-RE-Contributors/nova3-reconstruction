#!/usr/bin/env python3
"""Native custom-filesystem texture selection slice; only std::string modeled.
Executes original ordered extension searches and mode4 replacement branch.
"""
import hashlib,json,pathlib,random,struct,subprocess
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R8,UC_ARM_REG_R10,UC_ARM_REG_SP,UC_ARM_REG_PC,UC_ARM_REG_LR
ROOT=pathlib.Path(__file__).resolve().parents[2];binary=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so';digest=hashlib.sha256(binary.read_bytes()).hexdigest();assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open('rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;end=(s['p_vaddr']+s['p_memsz']+4095)&~4095;u.mem_map(lo,end-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x20000);obj=0x2000000;string=0x2000100;data=0x2000200;stack=0x2010000
put=lambda p,v:u.mem_write(p,struct.pack('<I',v))
put(obj+0x4c,4);put(string,data);put(stack+0x18,data);current=b''
def cstring(at):
 result=bytearray()
 for i in range(4096):
  b=u.mem_read(at+i,1)
  if b==b'\0':return bytes(result)
  result.extend(b)
 raise ValueError('unterminated string')
def hook(uc,at,size,user):
 global current
 assert uc.reg_read(UC_ARM_REG_R0)==string
 if at==0x2a2460:uc.reg_write(UC_ARM_REG_R0,current.find(cstring(uc.reg_read(UC_ARM_REG_R1)))&0xffffffff)
 else:
  assert at==0x2bec74;pos=uc.reg_read(UC_ARM_REG_R1);count=uc.reg_read(UC_ARM_REG_R2);replacement=cstring(uc.reg_read(UC_ARM_REG_R3));current=current[:pos]+replacement+current[pos+count:];put(data-12,len(current));uc.mem_write(data,current+b'\0');uc.reg_write(UC_ARM_REG_R0,string)
 uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
for at in (0x2a2460,0x2bec74):u.hook_add(UC_HOOK_CODE,hook,begin=at,end=at)
cases=['','a.tga','a.TGA','a.Tga','a.psd','a.PSD','a.pvx','a.dds','a.etc','a.tga/trailing','.psd','a.tga/b.psd','a.dds/b.pvx','dir\\a.TGA','dir.psd/a.tga']
rng=random.Random(0x455443)
for _ in range(150):cases.append(''.join(rng.choice(('dir/','sub\\','Name','.psd','.tga','.TGA','.pvx','.dds','.etc','tail')) for _ in range(rng.randrange(1,7))))
results=[]
for name in cases:
 current=name.encode();put(data-12,len(current));u.mem_write(data,current+b'\0')
 for reg,value in ((UC_ARM_REG_R8,obj),(UC_ARM_REG_R10,string),(UC_ARM_REG_SP,stack)):u.reg_write(reg,value)
 # Stop either before next nontexture policy or before global quality handling.
 def stop(uc,at,size,user):uc.emu_stop()
 hooks=[u.hook_add(UC_HOOK_CODE,stop,begin=a,end=a) for a in (0x2adbe0,0x2addbc)]
 u.emu_start(0x2adb50,0x2adbe0,count=1000)
 for h in hooks:u.hook_del(h)
 assert u.reg_read(UC_ARM_REG_PC) in (0x2adbe0,0x2addbc)
 source=subprocess.check_output([str(ROOT/'build/host/nova3_texture_path'),name]);assert source==current,(name,source,current)
 results.append(dict(request=name,result=current.decode()))
report=dict(binary_sha256=digest,native_slice='0x2adb50: texture searches -> 0x2adbe0 or nontexture0x2addbc',case_count=len(results),cases=results,scope='Actual ordered extension search/mode4 rewrite control flow. std::string find/replace storage operations modeled; no global quality, normal-map suppression, archive I/O or COW ABI equivalence implied.')
(ROOT/'research/elf/texture_path_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(results),'native ARM/source ETC filename cases')
