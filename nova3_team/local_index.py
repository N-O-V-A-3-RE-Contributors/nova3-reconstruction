"""Build an allow-listed, metadata-only ledger from private, locally held Ghidra exports.

Only exported boundaries and reference file checksums leave this scanner. Never
copy raw code bytes, pseudocode, Java, screenshots, decompiler output, or assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

HEX64=re.compile(r'^[0-9a-f]{64}$')
HEX_ADDR=re.compile(r'^[0-9a-fA-F]{4,16}$')


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            chunk=f.read(1024*1024)
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()


def number(value, *, hex_default=False):
    if type(value) is int: return value
    if not isinstance(value,str): return None
    value=value.strip()
    if value.lower().startswith('0x') and re.fullmatch(r'0[xX][0-9a-fA-F]+',value):
        return int(value,16)
    if hex_default and re.fullmatch(r'[0-9a-fA-F]{1,16}',value):return int(value,16)
    if re.fullmatch(r'[0-9]{1,10}',value):return int(value)
    return None


def extract_bounds(data, filename):
    """Return only explicit numeric boundaries; NEVER guess from code length."""
    obj=data.get('function',data) if isinstance(data,dict) else None
    if not isinstance(obj,dict):return None
    addr=None
    for key in ('address','entry','entry_point','start','function_address','entryAddress'):
        if key in obj:
            addr=number(obj[key],hex_default=True)
            if addr is not None:break
    if addr is None and HEX_ADDR.fullmatch(filename): addr=int(filename,16)
    if addr is None:return None
    size=None
    for key in ('size','length','byte_size','body_size','function_size'):
        if key in obj:
            size=number(obj[key])
            if size is not None:break
    if size is None:
        for key in ('end','end_address','endAddress'):
            if key in obj:
                end=number(obj[key],hex_default=True)
                if end is not None:
                    size=end-addr
                    break
    if size is None or not (0 < size <= 1048576) or not (0<=addr<2**64):return None
    return addr,size


def collect(root, *, max_exports=100000):
    root=Path(root)
    sources=root/'analysis'
    exports=root/'generated'/'ghidra'/'exports'
    digest_to_name={}
    for so in sorted(sources.rglob('*.so')) if sources.exists() else []:
        if not so.is_file() or so.is_symlink():continue
        digest=sha256(so)
        digest_to_name[digest]=so.name
    refs={}
    functions={}
    scanned=0
    rejected=0
    for folder in sorted(exports.glob('*')) if exports.exists() else []:
        if not folder.is_dir() or not HEX64.fullmatch(folder.name.lower()) or folder.is_symlink():continue
        digest=folder.name.lower()
        if digest not in digest_to_name:continue  # Never trust an unverified directory name.
        refs[digest]={'library':digest_to_name[digest], 'sha256':digest,
                      'source':'locally hashed ELF; NOT redistributed'}
        for path in sorted(folder.glob('*.json')):
            scanned+=1
            if scanned>max_exports:raise ValueError('Too many Ghidra exports; refusing unbounded scan')
            if path.is_symlink() or not path.is_file() or path.stat().st_size>4*1024*1024:
                rejected+=1;continue
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                bounds=extract_bounds(data,path.stem)
            except (OSError,UnicodeError,ValueError,TypeError):
                rejected+=1;continue
            if bounds is None:
                rejected+=1;continue
            addr,size=bounds
            # Tool identification is evidence of a candidate function boundary,
            # not equivalence to a reconstructed implementation.
            key=(digest,addr)
            if key not in functions:
                functions[key]={ 'reference_sha256':digest, 'address':f'0x{addr:x}',
                                 'size':size, 'boundary_status':'tool_identified',
                                 'status':'pending', 'source':'private Ghidra export metadata' }
    return {'schema_version':1,'references':sorted(refs.values(),key=lambda x:x['sha256']),
            'functions':sorted(functions.values(),key=lambda x:(x['reference_sha256'],int(x['address'],16))),
            'statistics':{'ghidra_json_examined':scanned,'rejected_or_unbounded':rejected,
                          'boundaries_extracted':len(functions)},
            'notes':'Tool-identified boundaries, NOT matched C++ source, NOT a complete original-function census.'}


def main(argv=None):
    ap=argparse.ArgumentParser(description='Extract only ELF-validated Ghidra function metadata, never original binaries')
    ap.add_argument('--project',default='.')
    ap.add_argument('--out',default='coordination/function_ledger.json')
    ap.add_argument('--write',action='store_true')
    args=ap.parse_args(argv)
    root=Path(args.project).resolve()
    output=(root/args.out).resolve()
    if not output.is_relative_to(root):raise SystemExit('Output must be inside project')
    data=collect(root)
    print(json.dumps(data['statistics'],indent=2))
    if not args.write:
        print('Preview only. Use --write to save sanitised metadata (no raw binary data).')
        return
    if not data['functions']:
        print('No usable function sizes/boundaries found. No ledger file written.')
        return
    if output.exists():
        print(f'Existing ledger left untouched: {output}')
        return
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(f'Created metadata ledger: {output} ({len(data["functions"])} functions)')

if __name__=='__main__':main()
