#!/usr/bin/env python3
"""Record current host target hashes after a successful source build.
Trust boundary: this manifest is a local build record; review sources/build graph.
"""
import hashlib,json,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
sources=[{'path':str(p.relative_to(ROOT)),'sha256':digest(p)} for p in sorted((ROOT/'reconstruction').rglob('*')) if p.suffix in ('.cpp','.hpp','.c','.h') or p.name=='CMakeLists.txt']+[{'path':'CMakeLists.txt','sha256':digest(ROOT/'CMakeLists.txt')}]
artifacts=['nova3_crc','nova3_scramble','crc32_test','scramble_test','libnova3_core.a','nova3_zip_header','zip_local_test','nova3_zip_index','nova3_zip_extract','zip_payload_test','nova3_bres_relocate','bres_test','zip_lookup_test','zip_archive_test','nova3_resource','nova3_zip_lookup','nova3_package_probe','nova3_mesh','collada_mesh_test','nova3_texture','pvr_texture_test','texture_bundle_test','nova3_etc1','etc1_test','nova3_texture_path','texture_path_test','nova3_scrambled_zip','scrambled_zip_test']
manifest={'executables':{digest(ROOT/'build/host'/a):dict(sources=sources,build_command='cmake --build build/host',link_inputs=[str(p.relative_to(ROOT)) for p in sorted((ROOT/'reconstruction/engine').glob('*.cpp'))],toolchain=subprocess.check_output(['c++','--version'],text=True).splitlines()[0]) for a in artifacts}}
(ROOT/'build/host/provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
