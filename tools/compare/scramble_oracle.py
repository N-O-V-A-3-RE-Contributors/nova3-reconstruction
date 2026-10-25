#!/usr/bin/env python3
"""Offline isolated native unscramble oracle; no engine launch or platform calls."""
import hashlib,json,pathlib,random,subprocess,tempfile
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so';digest=hashlib.sha256(p.read_bytes()).hexdigest();assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with p.open('rb') as f:
 for seg in ELFFile(f).iter_segments():
  if seg['p_type']=='PT_LOAD':
   lo=seg['p_vaddr']&~4095;hi=(seg['p_vaddr']+seg['p_memsz']+4095)&~4095;u.mem_map(lo,hi-lo);u.mem_write(seg['p_vaddr'],seg.data())
u.mem_map(0x2000000,0x20000);u.mem_map(0x3000000,0x10000);stop=0x300fffc
# Only external strlen is hooked, with ABI-correct length return. ARM uidivmod is
# executed from original libgcc already mapped, not replaced with inferred math.
def hook(uc,addr,size,user):
 if addr!=0xd2064:return
 ptr=uc.reg_read(UC_ARM_REG_R0);length=0
 while bytes(uc.mem_read(ptr+length,1))!=b'\x00':length+=1
 uc.reg_write(UC_ARM_REG_R0,length);uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE,hook,begin=0xd2064,end=0xd2064)
rng=random.Random(0x53435241);cases=[]
with tempfile.TemporaryDirectory(prefix='nova3-xor-') as td:
 src=pathlib.Path(td)/'in';dst=pathlib.Path(td)/'out'
 for n in [0,1,2,3,7,16,255,1024]+[rng.randrange(0,2000) for _ in range(40)]:
  data=rng.randbytes(n);key=bytes(rng.randrange(1,128) for _ in range(rng.randrange(1,33)))
  u.mem_write(0x2000000,data or b'\0');u.mem_write(0x2010000,key+b'\0')
  for reg,val in [(UC_ARM_REG_R0,0),(UC_ARM_REG_R1,0x2000000),(UC_ARM_REG_R2,n),(UC_ARM_REG_R3,0x2010000),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)]:u.reg_write(reg,val)
  u.emu_start(0x411610,stop,count=1000000);assert u.reg_read(UC_ARM_REG_PC)==stop
  actual=bytes(u.mem_read(0x2000000,n)) if n else b''
  src.write_bytes(data);subprocess.run([str(ROOT/'build/host/nova3_scramble'),str(src),str(dst),key.decode('ascii')],check=True)
  expected=bytes(v^key[i%len(key)] for i,v in enumerate(data))
  assert actual==dst.read_bytes()==expected
  cases.append(dict(length=n,key_sha256=hashlib.sha256(key).hexdigest(),input_sha256=hashlib.sha256(data).hexdigest(),output_sha256=hashlib.sha256(actual).hexdigest()))
r=dict(binary_sha256=digest,entry='0x00411610',mode='ARM',case_count=len(cases),cases=cases,oracle='Unicorn2.1.4; original uidivmod; external strlen hook',scope='nonempty NUL-terminated keys and valid buffers; added API invalid-input rejection is source policy, not equivalence')
(ROOT/'research/elf/scramble_verification.json').write_text(json.dumps(r,indent=2)+'\n');print(f'PASS: {len(cases)} original ARM / source C++ / independent XOR cases')
