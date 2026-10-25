#!/usr/bin/env python3
"""Execute original ARM addStream field-load/arithmetic slice; no helper hooks."""
import hashlib,json,pathlib,struct
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R10,UC_ARM_REG_R11,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2];p=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so';assert hashlib.sha256(p.read_bytes()).hexdigest()=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with p.open('rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;end=(s['p_vaddr']+s['p_memsz']+4095)&~4095;u.mem_map(lo,end-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x10000);mesh=0x2000000;buffer=0x2000100;arrays=0x2000200;output=0x2000300
put=lambda p,v:u.mem_write(p,struct.pack('<I',v))
for off,at in ((0xc,arrays),(0x14,arrays+4),(0x1c,arrays+8),(0x24,arrays+12)):put(mesh+off,at)
results=[]
for minimum in (0,1,12,914,65535):
 for stride in (12,36,48):
  for offset in (0,12):
   put(buffer+32,minimum)
   for at,value in ((arrays,stride),(arrays+4,offset),(arrays+8,6),(arrays+12,3)):put(at,value)
   for reg,value in ((UC_ARM_REG_R4,mesh),(UC_ARM_REG_R5,output),(UC_ARM_REG_R6,0),(UC_ARM_REG_R7,buffer),(UC_ARM_REG_R11,0)):u.reg_write(reg,value)
   u.emu_start(0x81bd54,0x81bd84,count=100);assert u.reg_read(UC_ARM_REG_PC)==0x81bd84
   got=u.reg_read(UC_ARM_REG_R10);assert got==minimum*stride+offset
   results.append(dict(minimum=minimum,stride=stride,offset=offset,result=got))
(ROOT/'research/elf/mesh_stream_base_verification.json').write_text(json.dumps(dict(cases=results,case_count=len(results),scope='Actual addStream ARM instruction slice confirms vertex byte offset=minimum*stride+attributeOffset. No helper calls, code hooks, render calls or original class ownership assumed.'),indent=2)+'\n');print('PASS:30 original ARM stream-base arithmetic cases')
