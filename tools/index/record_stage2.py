#!/usr/bin/env python3
"""Record scoped source package/BRES evidence without promoting whole routines."""
import hashlib,json,pathlib,sqlite3
ROOT=pathlib.Path(__file__).resolve().parents[2];db=sqlite3.connect(ROOT/'analysis_db/index.sqlite');binary='84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
r=json.loads((ROOT/'research/elf/package_resources_verification.json').read_text());assert r['index_entries']==4281 and r['named_reads_verified']==60 and r['native_golden_chain']
b=json.loads((ROOT/'research/elf/bres_source_verification.json').read_text());assert b['real_base_cases']==4 and b['header_cases']==7
l=json.loads((ROOT/'research/elf/lookup_source_verification.json').read_text());assert l['normalization_lookup_cases']==184 and l['duplicate_sort_cases']==3
units=[('zip_lookup.cpp',0x7fcec0,'logical ASCII query normalization and lower_bound; COW helpers modeled','lookup_source_verification.json'),('zip_lookup.cpp',0x7fd2d4,'logical entry normalization, not original string ownership/filename length hazards','lookup_source_verification.json'),('zip_lookup.cpp',0x810d84,'heap duplicate ordering, not whole init','lookup_source_verification.json'),('zip_archive.cpp',0x810d84,'classic ZIP index and native lookup; extra checked central validation','package_resources_verification.json'),('zip_archive.cpp',0x819ed8,'named resource reads backed by owned file regions, not original IReadFile ABI','package_resources_verification.json'),('zip_archive.cpp',0x83f454,'forward ZIP mount precedence and decode-failure fallback; excludes PAK/physical paths','package_resources_verification.json'),('bres.cpp',0x69c6f0,'contiguous-buffer byte relocation only; checked safety rejects control aliases and short data','bres_source_verification.json')]
m=json.loads((ROOT/'research/elf/mesh_source_verification.json').read_text());assert m['decoded_meshes']==31
for address in (0x710368,0x6d7f60,0x81bd20,0x844318):
 units.append(('collada_mesh.cpp',address,'inline geometry/stream/triangle field subset only; primary float streams and bounded tail buffers','mesh_source_verification.json'))
t=json.loads((ROOT/'research/elf/texture_corpus_verification.json').read_text());assert (t['texture_assets'],t['split_assets'],t['planes'],t['mip_levels'])==(1332,359,1691,17505)
for file,address,scope in [('pvr_texture.cpp',0x781c78,'PVRv2 header predicate only, no original I/O/logging ownership'),('pvr_texture.cpp',0x781e08,'checked ETC1 mip layout subset, no GPU image allocation'),('texture_bundle.cpp',0x7b13e0,'SPLIT marker and sorted rgb/alpha data selection only, no shaders/cache/GPU ownership')]:
 units.append((file,address,scope,'texture_corpus_verification.json'))
e=json.loads((ROOT/'research/elf/etc1_verification.json').read_text());assert e['case_count']==272
units.append(('etc1.cpp',0x7914a4,'ETC1 format29 to RGB8 format10 branch, valid blocks, multiples-of-four dimensions and Y flip; source adds tiny-mip cropping and invalid-block rejection','etc1_verification.json'))
t=json.loads((ROOT/'research/elf/texture_path_verification.json').read_text());assert t['case_count']==165
units.append(('texture_path.cpp',0x2ad998,'ETC mode4 ordered texture extension rewrite only, excludes global quality/normal suppression and other resource aliases; string helpers modeled','texture_path_verification.json'))
s=json.loads((ROOT/'research/elf/scrambled_zip_source_verification.json').read_text());assert s['archive_count']==30 and s['entry_count']==4885
units.append(('scrambled_zip.cpp',0x411fc0,'SB local-header XOR/body parsing subset, no original vector/COW ownership or SDIR/descriptor handling; all4885 payload fields independently validated','scrambled_zip_source_verification.json'))
for file,address,scope,evidence in units:
 source='reconstruction/engine/'+file;path='research/elf/'+evidence;sha=hashlib.sha256((ROOT/source).read_bytes()).hexdigest();scope='PARTIAL: '+scope
 db.execute('INSERT OR REPLACE INTO source_units VALUES(?,?,?,?,?,?)',(source,address,scope,'VERIFIED',sha,path))
 for ident, in db.execute('SELECT id FROM functions WHERE binary_hash=? AND address=?',(binary,address)).fetchall():db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',(ident+':stage2:'+file,ident,'C++17 / ARM goldens or data+native-policy evidence','VERIFIED',path,scope))
db.commit();print(f'Recorded{len(units)} stage2 scoped source mappings; whole-function completion totals unchanged')
