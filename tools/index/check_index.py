#!/usr/bin/env python3
"""Read-only SQLite provenance and export integrity checks."""
import json,pathlib,sqlite3
ROOT=pathlib.Path(__file__).resolve().parents[2]
db=sqlite3.connect(f'file:{ROOT}/analysis_db/index.sqlite?mode=ro',uri=True)
assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert db.execute('SELECT count(*) FROM binaries').fetchone()[0]==4
assert db.execute('SELECT count(*) FROM functions WHERE binary_hash NOT IN (SELECT hash FROM binaries)').fetchone()[0]==0
assert db.execute('SELECT count(*) FROM functions WHERE address != (raw_value & -2) OR thumb != (raw_value & 1)').fetchone()[0]==0
exports=[]
for p in (ROOT/'generated/ghidra/exports').glob('*/*.json'):
 j=json.loads(p.read_text());assert j['byte_mapping_validated'];assert j['address']+j['ghidra_image_base']==j['ghidra_address']
 assert j['instructions'];exports.append(hex(j['address']))
print(json.dumps({'binaries':4,'function_symbols':db.execute('select count(*) from functions').fetchone()[0],'distinct_function_addresses':db.execute('select count(*) from (select distinct binary_hash,address from functions)').fetchone()[0],'types':db.execute('select count(*) from types').fetchone()[0],'states':db.execute('select state,count(*) from functions group by state').fetchall(),'validated_export_addresses':exports},indent=2))
