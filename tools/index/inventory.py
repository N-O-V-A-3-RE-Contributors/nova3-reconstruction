#!/usr/bin/env python3
"""Read-only ELF/DWARF inventory and idempotent SQLite symbol/type index."""
import collections, hashlib, json, pathlib, sqlite3, zipfile
from elftools.elf.elffile import ELFFile
ROOT=pathlib.Path(__file__).resolve().parents[2]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4<<20),b''): h.update(b)
 return h.hexdigest()
def main():
 db=sqlite3.connect(ROOT/'analysis_db/index.sqlite'); db.executescript('''
 PRAGMA user_version=1;
 CREATE TABLE IF NOT EXISTS binaries(hash TEXT PRIMARY KEY,path TEXT,architecture TEXT,size INTEGER,metadata TEXT);
 CREATE TABLE IF NOT EXISTS functions(id TEXT PRIMARY KEY,binary_hash TEXT,address INTEGER,raw_value INTEGER,thumb INTEGER,size INTEGER,name TEXT,state TEXT,provenance TEXT,confidence TEXT,file_offset INTEGER,tool TEXT,signature TEXT,unresolved TEXT);
 CREATE INDEX IF NOT EXISTS function_name ON functions(name);
 CREATE TABLE IF NOT EXISTS types(id TEXT PRIMARY KEY,binary_hash TEXT,die_offset INTEGER,tag TEXT,name TEXT,byte_size INTEGER,provenance TEXT,attributes TEXT);
 CREATE TABLE IF NOT EXISTS edges(source_id TEXT,target_address INTEGER,kind TEXT,evidence TEXT,PRIMARY KEY(source_id,target_address,kind));
 CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY,function_id TEXT,tool TEXT,state TEXT,evidence_path TEXT,error TEXT);
 ''')
 inv=[]
 for p in sorted((ROOT/'analysis').glob('*/lib/*/*.so')):
  digest=sha(p)
  with p.open('rb') as stream:
   e=ELFFile(stream); secs=[dict(name=s.name,type=s['sh_type'],size=s['sh_size'],address=s['sh_addr'],offset=s['sh_offset']) for s in e.iter_sections()]
   dyn=e.get_section_by_name('.dynamic'); needed=[t.needed for t in dyn.iter_tags() if t.entry.d_tag=='DT_NEEDED'] if dyn else []
   counts={}; funcs={}; mapping=collections.Counter()
   for sn in ('.symtab','.dynsym'):
    s=e.get_section_by_name(sn)
    if s is None: continue
    counts[sn]=s.num_symbols()
    for sym in s.iter_symbols():
     name=sym.name; val=int(sym['st_value']); size=int(sym['st_size']); idx=sym['st_shndx']
     if name.startswith(('$a','$t','$d')): mapping[name[:2]]+=1
     if sym['st_info']['type']!='STT_FUNC' or idx=='SHN_UNDEF': continue
     addr=val&~1; ident=f'{digest}:{addr:08x}:{name}'; off=None
     if isinstance(idx,int):
      sec=e.get_section(idx); off=addr-sec['sh_addr']+sec['sh_offset']
     row=(ident,digest,addr,val,val&1,size,name,'INVENTORIED',str(p.relative_to(ROOT))+':'+sn,'OBSERVED',off,'pyelftools 0.33',None,'not analyzed')
     db.execute('INSERT INTO functions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET provenance=excluded.provenance',row)
     funcs[(addr,name)]=size
   dwarf={'cu_count':0,'tags':{},'compilation_units':[]}; tags=collections.Counter()
   if e.has_dwarf_info():
    d=e.get_dwarf_info()
    for cu in d.iter_CUs():
     top=cu.get_top_DIE(); attrs={k:str(v.value.decode(errors='replace') if isinstance(v.value,bytes) else v.value) for k,v in top.attributes.items()}
     dwarf['compilation_units'].append(attrs); dwarf['cu_count']+=1
     for die in cu.iter_DIEs():
      tags[die.tag]+=1
      if die.tag and ('type' in die.tag or die.tag in ('DW_TAG_class_type','DW_TAG_structure_type')):
       a=die.attributes; name=a.get('DW_AT_name'); name=name.value.decode(errors='replace') if name and isinstance(name.value,bytes) else str(name.value) if name else None
       bs=a.get('DW_AT_byte_size'); at={k:str(v.value) for k,v in a.items()}
       db.execute('INSERT OR REPLACE INTO types VALUES(?,?,?,?,?,?,?,?)',(f'{digest}:die:{die.offset:x}',digest,die.offset,die.tag,name,bs.value if bs else None,f'DWARF CU {cu.cu_offset:#x}',json.dumps(at)))
    dwarf['tags']=dict(tags)
   item=dict(path=str(p.relative_to(ROOT)),sha256=digest,size=p.stat().st_size,architecture=e.get_machine_arch(),elfclass=e.elfclass,entry=e['e_entry'],sections=secs,symbol_tables=counts,defined_function_symbols=len(funcs),mapping_symbols=dict(mapping),needed=needed,dwarf=dwarf)
   db.execute('INSERT OR REPLACE INTO binaries VALUES(?,?,?,?,?)',(digest,item['path'],item['architecture'],item['size'],json.dumps(item))); inv.append(item)
 db.commit(); assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; db.close()
 apk=[]
 for p in sorted((ROOT/'originals').glob('*.apk')):
  with zipfile.ZipFile(p) as z:
   unsafe=[i.filename for i in z.infolist() if pathlib.PurePosixPath(i.filename).is_absolute() or '..' in pathlib.PurePosixPath(i.filename).parts]
   native=[]
   for i in z.infolist():
    if i.filename.endswith('.so'):
     h=hashlib.sha256(z.read(i)).hexdigest(); native.append(dict(path=i.filename,size=i.file_size,sha256=h,extract_matches=any(x['sha256']==h for x in inv)))
   apk.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p),members=len(z.infolist()),uncompressed_bytes=sum(i.file_size for i in z.infolist()),unsafe_paths=unsafe,native=native))
 result=dict(elf=inv,apk=apk)
 (ROOT/'research/elf/inventory.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({x['path']:{'functions':x['defined_function_symbols'],'symtabs':x['symbol_tables'],'dwarf':x['dwarf']} for x in inv},indent=2))
if __name__=='__main__': main()
