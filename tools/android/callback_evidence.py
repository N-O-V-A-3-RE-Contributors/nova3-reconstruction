#!/usr/bin/env python3
"""Resolve one PC-relative JNI method-name literal cross-checked in ARM disassembly."""
import hashlib,json,pathlib,struct
from elftools.elf.elffile import ELFFile
root=pathlib.Path(__file__).resolve().parents[2]
p=root/"analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
assert hashlib.sha256(p.read_bytes()).hexdigest()=="84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
with p.open("rb") as f:
 e=ELFFile(f)
 segments=[(s["p_vaddr"],s.data()) for s in e.iter_segments() if s["p_type"]=="PT_LOAD"]
 def read(addr,n=128):
  for base,data in segments:
   if base<=addr and addr+n<=base+len(data):return data[addr-base:addr-base+n]
  raise ValueError(hex(addr))
 literal=struct.unpack("<I",read(0x8d03c0,4))[0]
 address=0x8cfc98+8+literal
 name=read(address).split(b"\0")[0].decode("ascii")
 assert name=="createView"
 result={"method_name":name,"method_name_address":hex(address),"literal_word_address":"0x8d03c0","add_instruction":"0x8cfc98","cache_store_instruction":"0x8cfcc4","cache_offset":"0xa0","consumer":"glf::AndroidCreateView@0x8cfaf8","consumer_load_instruction":"0x8cfb28","call_static_void_method_instruction":"0x8cfb34","scope":"ARM instruction/data cross-reference; not execution evidence"}
 (root/"research/android/callback_evidence.json").write_text(json.dumps(result,indent=2)+"\n")
 print(json.dumps(result,indent=2))
