#!/usr/bin/env python3
"""Offline native table and bounded ARM instruction-slice enum evidence.
Not whole constructors/driver functions; no production binary dependency.
"""
import hashlib,json,pathlib,struct
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_SP,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=ROOT/"analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
digest=hashlib.sha256(p.read_bytes()).hexdigest()
assert digest=="84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with p.open("rb") as f:
 e=ELFFile(f)
 for s in e.iter_segments():
  if s["p_type"]=="PT_LOAD":
   lo=s["p_vaddr"]&~4095;hi=(s["p_vaddr"]+s["p_memsz"]+4095)&~4095
   u.mem_map(lo,hi-lo);u.mem_write(s["p_vaddr"],s.data())
u.mem_map(0x2000000,0x10000)
p32=lambda n:struct.pack("<I",n)
regs=(UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)
def execute(start,end,values):
 for reg,val in values.items():u.reg_write(reg,val)
 u.emu_start(start,end,count=10000)
 assert u.reg_read(UC_ARM_REG_PC)==end
 return [u.reg_read(r) for r in regs]
tables={}
for name,va,size in (("ColladaPrimitiveMap",0xc82050,20),("PrimitiveTypeMap",0xc79dec,36),("VertexAttributeTypeMap",0xc79ef4,28),("IndexTypeMap",0xc79e10,12)):
 data=bytes(u.mem_read(va,size));tables[name]=dict(address=hex(va),bytes=data.hex(),values=list(struct.unpack("<"+"I"*(size//4),data)))
assert tables["ColladaPrimitiveMap"]["values"]==[6,4,3,1,2]
assert tables["VertexAttributeTypeMap"]["values"]==[0x1400,0x1401,0x1402,0x1403,0,0,0x1406]
constructor=[]
for code in range(5):
 obj,mesh,buf,sp=0x2000000,0x2001000,0x2002000,0x200f000
 u.mem_write(mesh+60,p32(buf));u.mem_write(buf,p32(code));u.mem_write(buf+32,p32(0)+p32(11)+p32(18))
 execute(0x844318,0x844378,{UC_ARM_REG_R0:obj,UC_ARM_REG_R1:0,UC_ARM_REG_R2:mesh,UC_ARM_REG_R3:0,UC_ARM_REG_SP:sp})
 mapped=u.reg_read(UC_ARM_REG_R8)
 assert mapped==tables["ColladaPrimitiveMap"]["values"][code]
 constructor.append(dict(serialized=code,runtime=mapped))
copies=[]
for code in range(7):
 source,dest=0x2001000,0x2002000
 u.mem_write(source+4,p32(32));u.mem_write(source+8,p32(code));u.mem_write(source+12,struct.pack("<HH",4,36))
 execute(0x81a44c,0x81a46c,{UC_ARM_REG_R6:source,UC_ARM_REG_R5:dest})
 actual=struct.unpack("<H",bytes(u.mem_read(dest+10,2)))[0]
 assert actual==code
 copies.append(dict(serialized_type=code,runtime_type=actual))
attributes=[]
for code in range(7):
 info,shader,sp=0x2001000,0x2002000,0x200f000
 u.mem_write(info+4,p32(32));u.mem_write(info+10,struct.pack("<HHH",code,4,36));u.mem_write(shader+6,struct.pack("<H",4))
 u.mem_write(sp+20,p32(0));u.mem_write(sp+32,p32(0xc79ef4))
 r=execute(0x772edc,0x772f44,{UC_ARM_REG_R10:info,UC_ARM_REG_R5:shader,UC_ARM_REG_R6:2,UC_ARM_REG_R9:2,UC_ARM_REG_SP:sp})
 assert r[2]==tables["VertexAttributeTypeMap"]["values"][code]
 attributes.append(dict(serialized_type=code,gl_type=r[2],normalized=r[3],components=r[1]))
draws=[]
for primitive in range(7):
 for index_type in range(3):
  stream,sp=0x2001000,0x200f000
  u.mem_write(stream+8,p32(18));u.mem_write(stream+20,struct.pack("<HH",index_type,primitive))
  r=execute(0x77253c,0xd2760,{UC_ARM_REG_R7:stream,UC_ARM_REG_R2:0,UC_ARM_REG_R3:0,UC_ARM_REG_SP:sp})
  assert r[0]==tables["PrimitiveTypeMap"]["values"][primitive]
  assert r[2]==tables["IndexTypeMap"]["values"][index_type]
  draws.append(dict(runtime_primitive=primitive,index_type=index_type,gl_mode=r[0],count=r[1],gl_index_type=r[2]))
report=dict(binary_sha256=digest,tables=tables,constructor_cases=constructor,type_copy_cases=copies,attribute_cases=attributes,draw_cases=draws,scope="Original ARM slices 0x844318..378 (constructor map load),0x772edc..f44(attribute GL call argument setup),0x77253c..glDrawElements PLT(draw GL arguments); synthetic prior state; no complete constructor/driver/frame execution, no GL calls or code hooks")
(ROOT/"tools/compare/gl_enum_evidence_results.json").write_text(json.dumps(report,indent=2)+"\n")
print("PASS:5 serialized primitive mappings,7 type-copy cases,7 vertex types,21 native indexed draw argument cases")
