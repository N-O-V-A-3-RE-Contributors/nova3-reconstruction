#!/usr/bin/env python3
"""Offline ARM lookup-control-flow oracle; COW string library helpers modeled explicitly.
Does not execute in or enter production. Does not test native allocation/refcount semantics.
"""
import hashlib,json,pathlib,struct,random
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2]
binary=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so'
digest=hashlib.sha256(binary.read_bytes()).hexdigest()
assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open('rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;hi=(s['p_vaddr']+s['p_memsz']+4095)&~4095
   u.mem_map(lo,hi-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x100000);u.mem_map(0x3000000,0x10000)
stop=0x300fffc;heap=0x2020000
p32=lambda n:struct.pack('<I',n&0xffffffff)
def rd(p):return struct.unpack('<I',u.mem_read(p,4))[0]
def cstr(p):
 b=bytearray()
 while True:
  x=bytes(u.mem_read(p+len(b),1))
  if x==b'\0':return bytes(b)
  b.extend(x)
def get(p):return cstr(rd(p))
def put(p,b):
 global heap
 q=heap;heap+=(len(b)+32+15)&~15
 u.mem_write(q,struct.pack('<IIi',len(b),len(b),-1)+b+b'\0');u.mem_write(p,p32(q+12))
def hook(uc,a,size,data):
 r0=uc.reg_read(UC_ARM_REG_R0);r1=uc.reg_read(UC_ARM_REG_R1)
 if a==0x164364:put(r0,cstr(r1))
 elif a==0xed2a0:put(r0,get(r1))
 elif a==0x2beec0:
  start=uc.reg_read(UC_ARM_REG_R2);n=uc.reg_read(UC_ARM_REG_R3);put(r0,get(r1)[start:start+n])
 elif a==0x6eb4bc:
  x,y=get(r0),get(r1);uc.reg_write(UC_ARM_REG_R0,((x>y)-(x<y))&0xffffffff)
 elif a==0xed254:uc.reg_write(UC_ARM_REG_R0,r0+12)
 elif a==0xd2778:
  n=uc.reg_read(UC_ARM_REG_R2);x,y=bytes(uc.mem_read(r0,n)),bytes(uc.mem_read(r1,n))
  uc.reg_write(UC_ARM_REG_R0,((x>y)-(x<y))&0xffffffff)
 elif a==0x7fd420:uc.reg_write(UC_ARM_REG_R0,0)
 elif a==0xbd696c:uc.reg_write(UC_ARM_REG_R0,1)
 elif a in (0x8519c8,0xeca5c,0xbd6a90,0x451a14):pass
 else:return
 uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
for a in (0x164364,0xed2a0,0x2beec0,0x6eb4bc,0x8519c8,0xeca5c,0xed254,0xd2778,0x7fd420,0xbd696c,0xbd6a90,0x451a14):u.hook_add(UC_HOOK_CODE,hook,begin=a,end=a)
def call(a,r0,r1):
 for reg,value in ((UC_ARM_REG_R0,r0),(UC_ARM_REG_R1,r1),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)):u.reg_write(reg,value)
 u.emu_start(a,stop,count=100000)
 assert u.reg_read(UC_ARM_REG_PC)==stop,'instruction budget exhausted'
 return u.reg_read(UC_ARM_REG_R0)
def lower(b):return bytes(c+32 if 65<=c<=90 else c for c in b)
def basename(b,seps):
 i=max((i for i,c in enumerate(b) if c in seps),default=-1)
 return b[i+1:] if i>0 else b
results=[]
paths=[b'',b'a',b'A',b'Dir/File.BDAE',b'dir\\File.BDAE',b'/foo',b'//foo',b'/dir/foo',b'dir/',b'/',b'dir\\',bytes([0x80,0xc0,65,90])]
for flags in (0,2,4,6):
 for name in paths:
  heap=0x2020000;obj=0x2000000;entry=0x2000100
  u.mem_write(obj+12,p32(flags));u.mem_write(entry,b'\0'*48)
  for off in (0,4,8):put(entry+off,name if off==0 else b'')
  u.mem_write(entry+42,struct.pack('<h',len(name)));call(0x7fd2d4,obj,entry)
  original=lower(name) if flags&2 else name
  expected=basename(original,b'/') if flags&4 else original
  actual=get(entry+4)
  assert actual==expected,(flags,name,actual,expected)
  results.append(dict(kind='entry',flags=flags,name=name.hex(),key=actual.hex()))
rng=random.Random(3)
for flags in (0,2,4,6):
 keys=sorted(set(paths+[rng.randbytes(4).replace(b'\0',b'!') for _ in range(20)]))
 for key in keys+[b'missing',b'XYZ']:
  heap=0x2020000;obj=0x2000000;base=0x2001000
  u.mem_write(obj+12,p32(flags));u.mem_write(obj+16,p32(base)+p32(base+48*len(keys)))
  for i,k in enumerate(keys):put(base+48*i+4,k)
  q=0x2010000;u.mem_write(q,key+b'\0')
  expectedkey=lower(key) if flags&2 else key
  if flags&4:expectedkey=basename(expectedkey,b'/\\')
  expected=keys.index(expectedkey) if expectedkey in keys else 0xffffffff
  actual=call(0x7fcec0,obj,q)
  assert actual==expected,(flags,key,actual,expected)
  results.append(dict(kind='query',flags=flags,name=key.hex(),keys=[k.hex() for k in keys],index=actual))
sort_cases=[]
for keys in ([b'x',b'x'],[b'x']*3,[b'b',b'a',b'b',b'a']):
 heap=0x2020000;obj=0x2000000;base=0x2001000
 u.mem_write(obj,b'\0'*48);u.mem_write(obj+8,p32(1));u.mem_write(obj+16,p32(base)+p32(base+48*len(keys)))
 for i,k in enumerate(keys):
  e=base+48*i;u.mem_write(e,b'\0'*48)
  for off in (0,4,8):
   put(e+off,k);u.mem_write(rd(e+off)-4,p32(0))
  u.mem_write(e+12,p32(i))
 u.reg_write(UC_ARM_REG_R2,0);u.reg_write(UC_ARM_REG_R3,0)
 call(0x810d84,obj,0)
 order=[rd(base+48*i+12) for i in range(len(keys))]
 expected={(b'x',b'x'):[1,0],(b'x',b'x',b'x'):[1,2,0],(b'b',b'a',b'b',b'a'):[1,3,2,0]}[tuple(keys)]
 assert order==expected,(keys,order,expected)
 sort_cases.append(dict(keys=[k.hex() for k in keys],archive_index_order=order))
report=dict(sort_cases=sort_cases,binary_sha256=digest,entries=['0x7fd2d4','0x7fcec0','0x359ff4'],case_count=len(results),cases=results,scope='Original ARM entry/query normalization, lower_bound and three duplicate heapsort branches; scanLocalHeader mocked to stop, COW ownership/atomic/free helpers mocked for sorting; modeled std::string assignment/comparison/substr/destruction. Original ASCII makeLower instructions execute. No allocator/refcount validation; no source-C++ differential claim.')
(ROOT/'tools/compare/lookup_evidence_results.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS:',len(results),'original ARM normalization and lookup cases; modeled COW library helpers')
