"""Idempotently create project labels and scoped initial tasks (no game binaries)."""
from __future__ import annotations
import argparse
from .common import GitHub

TASKS = [
  ('Create a verified ARM ELF function-ledger exporter','nova3_team/indexer.py, nova3_team/tests/test_indexer.py','Study existing tools/ghidra/export_batch.py interfaces. Implement extraction of unique function identifiers, address, size, name, Thumb state, call references where available; stable JSON schema. Test with synthetic fixtures. Never claim complete function inventory without evidence.',94),
  ('Develop function dependency clustering and effort scoring','nova3_team/clustering.py, nova3_team/tests/test_clustering.py','Read existing source indexes as inputs. Cluster dependency-connected native functions; score by instruction count, complexity and shared globals. Make reproducible fixture tests.',94),
  ('Implement reconstruction matching evidence schema','nova3_team/matching.py, nova3_team/tests/test_matching.py','Define a verified vs approximate vs unknown classification. Compare candidate/reference ARM instruction streams when explicitly supplied; do not imply complete ELF matches. Tests with synthetic ARM fixtures.',95),
  ('Build deterministic task-queue generator from function ledger','nova3_team/task_queue.py, nova3_team/tests/test_task_queue.py','Given an authenticated, locally prepared function ledger, generate bounded, path-scoped task specifications with dedup and dependency ordering; never upload function bytes or assets.',93),
  ('Verify reconstructed ZIP header and lookup logic','reconstruction/engine/zip_local.cpp, reconstruction/engine/zip_local.hpp, reconstruction/engine/zip_lookup.cpp, reconstruction/engine/zip_lookup.hpp, reconstruction/tests/zip_local_test.cpp, reconstruction/tests/zip_lookup_test.cpp','Review existing implementation and improve tests for malformed data, boundary conditions, integer overflow and ambiguous offsets. Preserve signatures and functionality. This is not a byte-match claim.',80),
  ('Verify reconstructed ZIP payload and archive logic','reconstruction/engine/zip_archive.cpp, reconstruction/engine/zip_archive.hpp, reconstruction/engine/zip_payload.cpp, reconstruction/engine/zip_payload.hpp, reconstruction/tests/zip_archive_test.cpp, reconstruction/tests/zip_payload_test.cpp','Add tight tests and fix real bugs only. Do not touch other resource modules or original assets.',79),
  ('Verify reconstructed ETC1 and PVR texture parsing','reconstruction/engine/etc1.cpp, reconstruction/engine/etc1.hpp, reconstruction/engine/pvr_texture.cpp, reconstruction/engine/pvr_texture.hpp, reconstruction/tests/etc1_test.cpp, reconstruction/tests/pvr_texture_test.cpp','Improve safe parsing/edge cases and deterministic host tests. Do not claim renderer port completed.',78),
  ('Verify reconstructed BRES resource structures','reconstruction/engine/bres.cpp, reconstruction/engine/bres.hpp, reconstruction/tests/bres_test.cpp','Check pointer safety, overflow, endianness and offset semantics against available noncopyrighted fixture data; strengthen tests. Do not modify source binary.',77),
  ('Verify reconstructed COLLADA mesh processing','reconstruction/engine/collada_mesh.cpp, reconstruction/engine/collada_mesh.hpp, reconstruction/tests/collada_mesh_test.cpp','Add negative-path and boundary tests; document any uncertain interpretation in PR. No redistribution of original meshes.',76),
  ('Verify reconstructed texture bundle and paths','reconstruction/engine/texture_bundle.cpp, reconstruction/engine/texture_bundle.hpp, reconstruction/engine/texture_path.cpp, reconstruction/engine/texture_path.hpp, reconstruction/tests/texture_bundle_test.cpp, reconstruction/tests/texture_path_test.cpp','Improve tests and correctness around texture offsets, names, malformed records. Preserve existing APIs.',75),
  ('Verify reconstructed scramble and scrambled-zip code','reconstruction/engine/scramble.cpp, reconstruction/engine/scramble.hpp, reconstruction/engine/scrambled_zip.cpp, reconstruction/engine/scrambled_zip.hpp, reconstruction/tests/scramble_test.cpp, reconstruction/tests/scrambled_zip_test.cpp','Strengthen deterministic and source-only tests for this module and fix demonstrable flaws.',74),
  ('Add safe symbol registry and type conflict validation','nova3_team/symbols.py, nova3_team/tests/test_symbols.py','Design canonical identifiers keyed by binary SHA256 plus address; detect type layout/signature conflicts; provide reviewable JSON updates. Only synthetic tests.',90),
]


def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--create',action='store_true',help='Actually make GitHub issues; otherwise preview')
    a=p.parse_args()
    if not a.create:
        for title,paths,detail,priority in TASKS:print(f'[{priority}] {title}: {paths}')
        return
    gh=GitHub(a.repo)
    for name,color in [('nova:queued','0e8a16'),('nova:claimed','fbca04'),('nova:review','1d76db')]:
        try:gh.post('labels',{'name':name,'color':color})
        except RuntimeError as e:
            if '422' not in str(e):raise
    existing={i['title'] for i in gh.issues(state='all')}
    for title,paths,detail,priority in TASKS:
        if title in existing:
            print('Exists:',title);continue
        body=f'Priority: {priority}\nAllowed paths: {paths}\n\n{detail}\n\nVerification: build where applicable, add reproducible tests, describe what remains unverified. Do not submit original game content.'
        issue=gh.post('issues',{'title':title,'body':body,'labels':['nova:queued']})
        print('Created issue',issue['number'],title)

if __name__=='__main__':main()
