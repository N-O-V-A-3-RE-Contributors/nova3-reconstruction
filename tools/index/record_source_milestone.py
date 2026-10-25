#!/usr/bin/env python3
"""Index evidence-backed source mappings without inflating partial coverage."""
import hashlib,json,pathlib,sqlite3
ROOT=pathlib.Path(__file__).resolve().parents[2]
db=sqlite3.connect(ROOT/'analysis_db/index.sqlite');digest='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
db.execute('CREATE TABLE IF NOT EXISTS source_units(source_path TEXT,mapped_address INTEGER,scope TEXT,state TEXT,source_hash TEXT,evidence_path TEXT,PRIMARY KEY(source_path,mapped_address))')
units=[('reconstruction/engine/crc32.cpp',0xde2cc,'ComputeCRC valid nonnegative lengths','research/elf/crc_verification.json','VERIFIED'),('reconstruction/engine/crc32.cpp',0x8d890c,'glf CRC32 wrapper and underlying checksum with seeds/alignment/null','research/elf/crc_verification.json','VERIFIED'),('reconstruction/engine/scramble.cpp',0x411610,'unscramble valid nonempty NUL-terminated key domain','research/elf/scramble_verification.json','VERIFIED'),('reconstruction/engine/zip_local.cpp',0x7fd420,'PARTIAL: flags0/UTF8 local-header parsing and stream traversal, not original vector/class/descriptor behavior','research/elf/zip_index_verification.json','VERIFIED')]
units.append(('reconstruction/engine/zip_payload.cpp',0x819878,'PARTIAL: stored/raw-DEFLATE with bounded size/CRC, not whole openFile/class ownership','research/elf/zip_payload_verification.json','VERIFIED'))
for src,addr,scope,evidence,state in units:
 ev=json.loads((ROOT/evidence).read_text())
 if addr==0x8d890c:assert ev['wrapper_case_count']==147
 elif addr==0xde2cc:assert ev['case_count']==122
 elif addr==0x411610:assert ev['case_count']==48
 elif addr==0x819878:assert ev['decoded_assets']==10 and ev['corrupt_cases_rejected']==40
 else:assert sum(x['entries_matching'] for x in ev['results'])==4281
 sha=hashlib.sha256((ROOT/src).read_bytes()).hexdigest()
 db.execute('INSERT OR REPLACE INTO source_units VALUES(?,?,?,?,?,?)',(src,addr,scope,state,sha,evidence))
 if 'PARTIAL' not in scope:
  db.execute('UPDATE functions SET state=? WHERE binary_hash=? AND address=?',(state,digest,addr))
 for ident, in db.execute('SELECT id FROM functions WHERE binary_hash=? AND address=?',(digest,addr)).fetchall():
  db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',(ident+':source-'+('partial' if 'PARTIAL' in scope else 'full'),ident,'source C++17 / ARM oracle or independent container invariants',state,evidence,scope))
db.commit();print('Source mappings: 3 full functions VERIFIED in documented domains; 2 partial ZIP routines verified on observed headers/traversal')
