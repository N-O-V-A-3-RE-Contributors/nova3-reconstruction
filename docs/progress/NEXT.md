# Next tasks

## Immediate evidence/worker priorities (2026-10-28)

**Pre-start checkpoint:** Jason's local six-stage audit succeeded. It
inventoried 25 old recovered C++ blocks, identified no stale source hashes,
and established no new ARM source-byte matches. A donor shortlist of 64
four-byte thunks was unrepresentative; the updated planner now stratifies
nontrivial sizes and consumes existing-source worklist hints. The dedicated
worker-presence issue is #92; it is not a queued coding issue.

Next on the private laptop: `bash tools/run_compiler_pilot.sh`. Review its
actual ARM object comparison, then assign one tightly scoped native ABI
validation task to the Codex worker. Do not queue broad decompilation until
a reproducible source C++ comparison has been demonstrated. A code-window
match is not necessarily proof of the original compiler/linker or full `.so`.

1. Run `python -m nova3_team.legacy_validation --repo . --out .cache/legacy-source-revalidation.json` against all previous decompilation work; review each historical match assertion before assigning new grades.
2. Run `nova3_team.version_compare` and `nova3_team.donor_mapping` locally on the Cyrus debug and Premium retail ELF, then stage a small `object_matching` unit. Validate its boundaries in IDA or Ghidra and retain the full ELF hashes locally.
3. Establish a reproducible GCC 4.4.3 *candidate* alongside modern cross-compilers; test one genuine C++ ARM candidate with `nova3_team.compiler_matrix` and `nova3_team.object_compare`. Record code-window/relocation and behavioural results; do not claim original compiler flags, TU objects or full `.so` SHA without evidence.
4. Investigate the saved issue #82 timeout logs and re-scope the broad architecture task before requeueing it. It was moved from queued/attention to blocked to prevent automatic repeat attempts. Worker provider logs are private; the updated runner emits elapsed/byte-count progress and stops after one provider timeout.
5. Independently confirm the Irrlicht version/fork and the JNI registration/lifecycle before sourcing and building third-party engine/JNI adapters. Preserve Ghidra as the free analysis backend and the source-only render/material vertical slice below.

Existing source-engine and asset milestones remain valuable and are retained.

1. Use recovered effect/material data to build the next genuine render dependency. SB source reader now parses4,885 entries across30 packages, decodes2.70GB with independent size/CRC agreement, and locates first mesh NV_DiffuseVC.bdae/#DiffuseVC-fx plus originalGLSL references. Native key=original archive basename, XORed26-byte ZIP local-header body. See research/assets/SCRAMBLED_GLA.md. Ignored effect fixtures .cache/assets/{main,patch}-NV_DiffuseVC*. Reconstruct own shader source from verified equations; original shaders remain offline references. Recover effect technique/defines, actual material parameter values, vertex selector semantic IDs/normalization and sampler states before claiming faithful rendering. Native material6a790c uses24-byte SNewParam records, type+8/value+20; mesh buffer+4 references material ID (firstmesh _2_-_Default). Main effect document matches patch byte-for-byte; global edition parity remains unverified.
2. Source mesh decoding works on31 sampled assets. Extend resource/scene handling using native CCDB/SCollada fields and callers; current tool extracts first geometry only. CMesh ctor0x6d7f60/createGeometry0x6d8888/addStream0x81bd20 exported. Multi-buffer minimum vertex offsets are supported and30ARM arithmetic slices confirm them. Materials, transforms, shader semantic mapping and skin/controller geometry are the next real dependencies.
3. Build actual rendering only after original data/material/shader dependencies are recovered. Primitive/type mapping is established: serialized0→runtime6→GL_TRIANGLES4; rawtype6→FLOAT,rawtype1→UNSIGNED_BYTE. Byte normalization is shader-dependent. Do not substitute diagnostic OBJ/SVG previews for game-frame fidelity.
4. Minimal own Android asset inspector can now use real named loading/mesh bytes; SDK/NDK ready, ARMv7 source core builds. No APK/UI yet. Recheck ADB environment, complete standard-runtime/DEX/source-provenance auditing; no device test assumed.
5. Resolve Premium APK/OBB naming mismatch using expected GameInstaller DownloadComponents/config, then level/native paths. No default patch-overrides-main policy is invented: recovered ZIP mount order is insertion order, earlier successful match wins; failed decode tries later mounts.
6. Source file-region ZIP subset intentionally rejects ZIP64/descriptors/gaps/unsafe names and bounds metadata/output; indexed native ownership not reconstructed. Other ELF Ghidra imports/global/vtable/fulltypes still pending. Prefer next executable dependency over mass decompilation.
7. Do not blindly retry rev.ng; reports record90/30second timeouts. Parallel-agent quota interrupted a review this session; lead continued existing authorized work and persisted state.

Reproduce:
`NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh`
`.venv/bin/python tools/extract/materialize_premium.py` #2.3GiB ignored asset-data cache, verified hashes
`.venv/bin/python tools/compare/package_resources.py`
`.venv/bin/python tools/compare/mesh_source.py`
`.venv/bin/python tools/compare/pvr_header_oracle.py`
`.venv/bin/python tools/compare/texture_corpus.py`
`.venv/bin/python tools/compare/etc1_oracle.py`
`.venv/bin/python tools/compare/etc1_corpus.py`
`.venv/bin/python tools/compare/texture_path_oracle.py`
`.venv/bin/python tools/compare/texture_path_source.py`
`.venv/bin/python tools/compare/scrambled_zip_source.py` # full30-package CRC/size comparison; extracts selected offline effects
`.venv/bin/python tools/index/record_stage2.py`
`.venv/bin/python tools/index/check_index.py`
`build/host/nova3_resource 01_room_a.bdae build/first.bres --bres .cache/assets/premium-main.obb`
`build/host/nova3_mesh .cache/assets/premium-main.obb 01_room_a.bdae build/room_a.obj`

Nested archive integration: implement a source-owned seekable/memory SB archive class using scrambled_zip.* and existing payload decoder. SDIR is not yet validated; descriptors/other custom PAK variants unsupported. Mount precedence must follow actual CCustomFileSystem::customCreateAndOpenFile0x2a3afc rather than assuming generic ZIP policy applies across archive classes. Five new Ghidra exports completed successfully; total62. No Ghidra process should remain.
