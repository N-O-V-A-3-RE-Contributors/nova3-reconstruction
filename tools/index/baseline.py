#!/usr/bin/env python3
"""Summarize measured inputs/debug/ELF structure without claims of recovery."""
import collections,json,pathlib,sqlite3
from elftools.elf.elffile import ELFFile
ROOT=pathlib.Path(__file__).resolve().parents[2]
j=json.loads((ROOT/'research/elf/inventory.json').read_text());lines=['# Measured baseline — 2026-10-25','','Inputs are read-only reference data. Hashes and exact sections: research/elf/inventory.json. Commands: `.venv/bin/python tools/index/inventory.py`, `.venv/bin/python tools/index/baseline.py`.','','| ELF | Bytes | Defined function symbol names | Symbol tables | DWARF CUs |','| --- | ---: | ---: | --- | ---: |'];detail=[]
for item in j['elf']:
 lines.append(f"| {item['path']} | {item['size']} | {item['defined_function_symbols']} | {item['symbol_tables']} | {item['dwarf']['cu_count']} |")
 with (ROOT/item['path']).open('rb') as f:
  e=ELFFile(f);dyn=e.get_section_by_name('.dynsym');imports=[s.name for s in dyn.iter_symbols() if s['st_shndx']=='SHN_UNDEF'] if dyn else []
  relocations={s.name:s.num_relocations() for s in e.iter_sections() if s['sh_type'] in ('SHT_REL','SHT_RELA')}
  compiler=e.get_section_by_name('.comment');compiler=sorted(set(x.decode(errors='replace') for x in compiler.data().split(b'\0') if x)) if compiler else []
  line_rows=0;source_files=set()
  if e.has_dwarf_info():
   dwarf=e.get_dwarf_info()
   for cu in dwarf.iter_CUs():
    lp=dwarf.line_program_for_CU(cu)
    if lp:
     line_rows+=sum(1 for entry in lp.get_entries() if entry.state is not None)
     for fe in lp['file_entry']:source_files.add(fe.name.decode(errors='replace'))
  detail.append(dict(path=item['path'],undefined_dynamic_symbols=len(imports),import_names=imports,relocations=relocations,compiler_comments=compiler,dwarf_line_rows=line_rows,dwarf_line_filenames=sorted(source_files),arm_exidx_entries=next((s['size']//8 for s in item['sections'] if s['name']=='.ARM.exidx'),0)))
lines+=['','Premium DWARF is19,269 bytes of .debug_info in20 CUs, all GCC/libgcc implementation files. There are276 type-tag records (including typedefs/base/pointer/array qualifiers),29 structure DIEs,2 union DIEs,0 class DIEs and91 subprogram DIEs. These are not recovered game classes. Complete paths/tags are in inventory.json; line/import/relocation/compiler inventory is baseline_details.json. Premium mapping symbols:38,506 ARM,$a;21,148 data,$d; no$t symbols observed. Selected routines are ARM mode; do not assume absence of mapping symbols proves stripped Freemium mode.','','Both supplied APK hashes matched required hashes; each extracted native library hash equals its own APK ZIP member. Exact package/version and startup records research/android/. All inputs remained unchanged in final SHA256 recheck .cache/logs/final-input-sha256.txt.','','Archives: Premium2,306,966,040-byte ZIP mislabeled.rar, Freemium1,352,455,592-byte RAR5. Safe inventories research/assets/. Premium4,281 ZIP entries, Freemium56 structurally inferred table records. Edition compatibility is limited by Premium package/version mismatch; filename label is insufficient.','','Host initial38GiB free disk/7.6GiB RAM/~4GiB available; scoped Ghidra1.4GiB heap, single persistent project access. rev.ng3.8GiB expanded research tool; actual bounded probes failed. No attached Android device. Environment snapshot .cache/logs/environment.json. No original code is a source-build/runtime dependency.']
(ROOT/'research/architecture/BASELINE.md').write_text('\n'.join(lines)+'\n');(ROOT/'research/elf/baseline_details.json').write_text(json.dumps(detail,indent=2)+'\n');print('Baseline and independent ELF/debug metadata saved')
