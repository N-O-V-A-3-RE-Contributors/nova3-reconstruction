#!/usr/bin/env python3
"""Offline research oracle: emulate one original ARM function, never production.
Requires supplied ELF only here. Compare source executable, original ARM and zlib.
"""
import argparse,hashlib,json,pathlib,random,struct,subprocess,tempfile,zlib
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--exe',default='build/host/nova3_crc');args=p.parse_args()
binary=ROOT/'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so'
digest=hashlib.sha256(binary.read_bytes()).hexdigest();assert digest=='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with binary.open('rb') as f:
 e=ELFFile(f)
 for seg in e.iter_segments():
  if seg['p_type']!='PT_LOAD':continue
  start=seg['p_vaddr']&~4095; end=(seg['p_vaddr']+seg['p_memsz']+4095)&~4095
  u.mem_map(start,end-start);u.mem_write(seg['p_vaddr'],seg.data())
 # Read table through symbol evidence, not guessed address.
 syms=e.get_section_by_name('.symtab'); sym=next(s for s in syms.iter_symbols() if s.name=='_ZL11g_nCRCTable') if any(s.name=='_ZL11g_nCRCTable' for s in syms.iter_symbols()) else None
 if sym is None:
  sym=next(s for s in syms.iter_symbols() if 'g_nCRCTable' in s.name)
 table_address=sym['st_value']; table=bytes(u.mem_read(table_address,1024))
values=[]
for i in range(256):
 v=i
 for _ in range(8):v=(v>>1)^(0xedb88320 if v&1 else 0)
 values.append(v)
assert table==struct.pack('<256I',*values)
u.mem_map(0x2000000,0x20000);u.mem_map(0x3000000,0x10000);stop=0x3010000-4
rng=random.Random(0x4e4f5641)
cases=[b'',b'123456789',bytes(range(256))]+[rng.randbytes(n) for n in [1,2,3,4,7,8,15,16,31,32,63,64,255,256,257,1023,4096,65535]]
cases += [rng.randbytes(rng.randrange(0,10000)) for _ in range(100)]
fixture=ROOT/'research/assets/premium_01_room_a.bdae.specimen'
if fixture.exists():
 assert fixture.stat().st_size<=0x20000, 'fixture exceeds mapped oracle buffer'
 cases.append(fixture.read_bytes())
results=[]
with tempfile.TemporaryDirectory(prefix='nova3-crc-') as td:
 path=pathlib.Path(td)/'data'
 for data in cases:
  u.mem_write(0x2000000,data or b'\0');u.reg_write(UC_ARM_REG_R0,0x2000000);u.reg_write(UC_ARM_REG_R1,len(data));u.reg_write(UC_ARM_REG_SP,0x3008000);u.reg_write(UC_ARM_REG_LR,stop)
  u.emu_start(0xde2cc,stop,count=2000000)
  assert u.reg_read(UC_ARM_REG_PC)==stop, 'oracle instruction budget exhausted'
  actual=u.reg_read(UC_ARM_REG_R0);expected=zlib.crc32(data)
  path.write_bytes(data); rebuilt=int(subprocess.check_output([str(ROOT/args.exe),str(path)],text=True).strip(),16)
  assert actual==expected==rebuilt,(len(data),hex(actual),hex(expected),hex(rebuilt))
  results.append(dict(length=len(data),input_sha256=hashlib.sha256(data).hexdigest(),crc32=f'{actual:08x}'))
wrapper_results=[]
with tempfile.TemporaryDirectory(prefix='nova3-crc-wrapper-') as td:
 path=pathlib.Path(td)/'data'
 for n in (0,1,2,3,31,32,64,255,1024):
  data=rng.randbytes(n);path.write_bytes(data)
  for prior in (0,1,0xffffffff,0x12345678):
   for alignment in range(4):
    ptr=0x2000000+alignment;u.mem_write(ptr,data or b'\0')
    for reg,val in ((UC_ARM_REG_R0,ptr),(UC_ARM_REG_R1,n),(UC_ARM_REG_R2,prior),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)):u.reg_write(reg,val)
    u.emu_start(0x8d890c,stop,count=2000000);assert u.reg_read(UC_ARM_REG_PC)==stop
    actual=u.reg_read(UC_ARM_REG_R0);rebuilt=int(subprocess.check_output([str(ROOT/args.exe),str(path),str(prior)],text=True).strip(),16)
    assert actual==rebuilt==zlib.crc32(data,prior),(n,alignment,prior,actual,rebuilt)
    wrapper_results.append(dict(length=n,alignment=alignment,prior=prior,crc32=f'{actual:08x}'))
 for prior in (0,0xffffffff,0x12345678):
  for reg,val in ((UC_ARM_REG_R0,0),(UC_ARM_REG_R1,100),(UC_ARM_REG_R2,prior),(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)):u.reg_write(reg,val)
  u.emu_start(0x8d890c,stop,count=2000000);assert u.reg_read(UC_ARM_REG_PC)==stop
  rebuilt=int(subprocess.check_output([str(ROOT/args.exe),'--null',str(prior)],text=True).strip(),16)
  assert u.reg_read(UC_ARM_REG_R0)==rebuilt==0
  wrapper_results.append(dict(null=True,length=100,prior=prior,crc32='00000000'))
report=dict(binary_sha256=digest,wrapper_entry='0x008d890c',wrapper_case_count=len(wrapper_results),wrapper_cases=wrapper_results,entry='0x000de2cc',mode='ARM',table_address=hex(table_address),table_entries_matching_polynomial=256,oracle='Unicorn 2.1.4 offline reference-only ARM emulation',source_executable=args.exe,case_count=len(results),cases=results,scope='ComputeCRC and glf CRC32 valid nonnegative lengths, seeded and aligned/unaligned buffers, null wrapper; no original code is linked or packaged with source executable')
out=ROOT/'research/elf/crc_verification.json';out.write_text(json.dumps(report,indent=2)+'\n');print(f'PASS: {len(results)} ComputeCRC and {len(wrapper_results)} CRC wrapper original ARM / source C++ / zlib cases; 256 original table entries match')
