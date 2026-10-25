#!/usr/bin/env python3
"""Offline original ARM typed-accessor oracle, no engine startup or code hooks."""
import hashlib,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
root=Path(__file__).resolve().parents[2]
binary=root/"analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
assert hashlib.sha256(binary.read_bytes()).hexdigest()=="84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
b=bytearray((root/"research/assets/premium_01_room_a.bdae.specimen").read_bytes())
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open("rb") as f:
 for s in ELFFile(f).iter_segments():
  if s["p_type"]=="PT_LOAD":
   lo=s["p_vaddr"]&~4095;hi=(s["p_vaddr"]+s["p_memsz"]+4095)&~4095
   u.mem_map(lo,hi-lo);u.mem_write(s["p_vaddr"],s.data())
base=0x2000000;obj=base+0x10000;db=base+0x11000;u.mem_map(base,0x20000);u.mem_map(0x3000000,0x10000)
u.mem_write(base,bytes(b));u.mem_write(obj,struct.pack("<I",base)+bytes(60))
def invoke(entry,arg0,arg1=0):
 for r,v in [(UC_ARM_REG_R0,arg0),(UC_ARM_REG_R1,arg1),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,0x300fffc)]:u.reg_write(r,v)
 u.emu_start(entry,0x300fffc,count=100000);assert u.reg_read(UC_ARM_REG_PC)==0x300fffc
 return u.reg_read(UC_ARM_REG_R0)
assert invoke(0x69c6f0,obj)==0
# CCDB[0] points CResFile; CResFile+16 points BRES header.
u.mem_write(db,struct.pack("<I",obj+0x100))
u.mem_write(obj+0x110,struct.pack("<I",base))
results=[]
for name,entry,offset,stride in [("geometry",0x710368,0x88,16),("image",0x710318,0x6c,16),("material",0x71034c,0x7c,36)]:
 for index in [0,1,2,-1]:
  actual=invoke(entry,db,index&0xffffffff)
  array=struct.unpack_from("<I",b,0x898+offset)[0]
  expected=(base+array+index*stride)&0xffffffff
  assert actual==expected
  results.append({"name":name,"entry":hex(entry),"index":index,"returned":actual,"expected":expected})
assert invoke(0x71017c,db)==base+0x434
report={"oracle":"Unicorn2.1.4 original ARM, no code hooks or external calls", "scope":"typed accessor pointer arithmetic; out-of-range indices intentionally show original absence of bounds checks", "cases":results,"version_string_offset":0x434}
(root/"research/assets/COLLADA_native_accessors.json").write_text(json.dumps(report,indent=2)+"\n")
print("PASS:12 original ARM geometry/image/material accessor cases;version string pointer")
