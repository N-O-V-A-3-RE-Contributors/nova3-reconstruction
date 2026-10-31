# Next tasks

## Tooling is set up; two things remain (2026-11-01)

The decompilation toolset is finished as a usable product: `./nova3` with
`doctor|index|decompile|status|show|search|build|test|verify|push`, documented in
`docs/DECOMPILE.md`, tested by `reconstruction/tests/test_workbench.py` (10
hermetic cases) and wired into `tools/check_all.sh` + `nova-ci.yml`. Ghidra/JDK
discovery no longer hardcodes this laptop's paths.

1. **Publish the branch** — the only outstanding setup item, and it is a
   credential grant, not code. `./nova3 push` (= `bash tools/setup/push_branch.sh`)
   does the push and verifies the remote head; it exits `3` and prints the two
   supported routes when the run has no GitHub capability. Nothing else is
   missing: remote, identity and the branch are in place.
2. **Resume source work** with `vox::DriverCallbackInterface::Init(void*)`
   (`0x009d53f4`, 48 B), unchanged below. Use `./nova3 decompile --address
   0x9d53f4` then `./nova3 show 0x9d53f4` for the first pass.
3. **When the early-development codebase is supplied**: treat it as candidate
   source under `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; see
   "When the early-development source arrives" in `docs/DECOMPILE.md`. It does
   not upgrade any completion state by itself.

## Immediate: publish the branch, then resume the caller slice (2026-10-31)

1. **Publish `automation/paperclip-worker`** once a GitHub credential source
   exists. `bash tools/setup/push_branch.sh` now does the whole action and
   verifies the remote head; without capability it exits `3` and prints the two
   supported routes (grant the GitHub connection to the run so the wrapper can
   broker a token, or run it from the maintainer's own terminal with `GH_TOKEN`).
   Nothing else about the repo is missing: remote, `user.name`, `user.email` and
   the 3-commit branch are all in place.
2. **Continue the caller slice** with `vox::DriverCallbackInterface::Init(void*)`
   (0x009d53f4, 48 B) — unchanged from the previous queue entry below.
3. **Use the new pipeline for the next slice's evidence**, bounded:
   `.venv/bin/python tools/pipeline/auto_decompile.py --max-functions 8 --batch-size 4`
   then read the exports under `generated/ghidra/exports/<sha>/`. Do not run it to
   completion: at the measured ~11 s per function warm rate the full 39,123 would
   be ~100 hours, and an export is `DECOMPILED`, never a completion state.

## Immediate: DetectNeon is closed; the probe's single caller is the next slice (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp` is
`vox::DriverCallbackSourceInterface::DetectNeon` (ELF VA `0x009dad14`, 32 B).
It is IMPLEMENTED, TESTED (`detect_neon_behaviour`, CTest) and measured
`ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION` 6/6 configs at 32/32 B with the
two `BL` words as the only differences; `code_window_equal` stays false.
The call-site search (`tools/compare/cpuquery_callsites.py`, 39,108 functions
decoded, 0 undecodable) found exactly one in-binary caller and one other
cpu-features consumer:

* `vox::DriverCallbackInterface::Init(void*)` — `0x009d53f4`, **48 B**, calls
  DetectNeon at `0x009d5414`.
* `glitch::collada::detail::CSoftwareSkinTechnique::init(...)` — `0x00739630`,
  1028 B, calls `android_getCpuFeatures` directly at `0x00739764` (no family
  gate, no helper).

1. **Next bounded step: recover `vox::DriverCallbackInterface::Init` (48 B).**
   It is the smallest remaining caller in the chain and it consumes the probe's
   boolean, so it turns DetectNeon from a leaf into a linked source path. Do it
   the same way this slice was done and in this order: decode the window with
   `tools/compare/cpuquery_callsites.py --also 0x9d53f4:DriverCallbackInterfaceInit`
   plus a small Capstone structure pass, write the source with every
   constant/field INFERRED-or-observed labelled, then add
   `tools/compare/<name>_pilot.py` and a CTest behaviour target. Do not guess the
   `void*` parameter's type: 48 B is unlikely to prove it, and an unproven cast
   is worse than an opaque parameter.
2. **Then the Collada gate**, if the 48 B function has no source-visible
   behaviour worth owning: `CSoftwareSkinTechnique::init` is where the engine
   actually branches on NEON (1028 B, one direct cpu-features read). Recovering
   the gate condition and the two paths is a real rendering decision, but it is
   a much larger slice — bound it by the branch around `0x00739764` only, not by
   the whole function.
3. **Stop chasing `android_cpuInit`'s 12-byte residual.** It is 2648 B against
   the reference 2632 B with char signedness settled (`-fsigned-char`), every
   feature-check permutation measured and every relevant flag probed and
   rejected (see the section below). Only a different GCC 4.4.3 patchlevel
   would test the remaining hypothesis; if one is never tried, record that and
   keep the function IMPLEMENTED + TESTED.
4. **Keep the rejected hypotheses visible**: `-fno-toplevel-reorder` rows in
   `tools/compare/cpufeatures_pilot.py`, the `-noToplevelReorder` objects under
   `.cache/cpufeat/order/`, and the `-fno-*` measurements in STATUS.md. Do not
   delete them.

## Previous: finish the cpu-features TU (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/cpu_features.c` is now the whole
recovered TU. All three exported getters match the reference instruction stream
modulo the two unavoidable relocation words under NDK r8e GCC 4.4.3 at `-O2`,
`-O3` and `-Os`, with the reference `.bss` layout, and the helpers have
behavioural tests (`cpu_features_behaviour`, CTest). Two bounded gaps remain.

1. **Close `android_cpuInit`'s 32-byte gap** (candidate 2600 B vs reference
   2632 B at `-O3 -fstack-protector`). Known structural differences: the
   reference sign-extends the cpuinfo-field delimiters (`LDRSB` where this
   compile uses `LDRB`) and orders the `Features` block differently. Compare
   `.cache/cpufeat/rec/cpuinit.txt` (reference listing) with the candidate
   listing function-by-function, starting from the first divergence the pilot
   reports. `.cache/cpufeat/cpuinit_refs.json` already resolves every literal.
   Do not synthesise instructions to close the gap; if the source cannot
   produce it, record the divergence and stop.
2. **Behavioural test for the initializer end-to-end.** `android_cpuInit` reads
   fixed paths, so an integration test needs either a fixture root mounted into
   the process (e.g. a test-only build with redirected paths behind a macro that
   is itself documented as not present in the original) or a host test that
   runs against a synthetic cpuinfo in a `chroot`/mount namespace. Decide and
   document which; do not silently change the recovered source's paths.
3. Keep the rejected hypotheses visible: the pilot's `-noToplevelReorder` rows
   and `.cache/cpufeat/order/` exist so the definition-order conclusion stays
   measured. Do not delete them.

## Previous: close the two measured gaps in the cpu-features TU (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/cpu_features.c` now covers the whole
recovered TU and is measured by `tools/compare/cpufeatures_pilot.py`. Two
concrete, bounded gaps remain; both are measured, not speculative.

1. **Reproduce the reference `.bss` ordering.** With the local NDK r8e GCC
   4.4.3 the two int-sized objects always land `g_cpuFamily@+4`,
   `g_cpuCount@+16`, while the reference has `g_cpuCount@+4`,
   `g_cpuFamily@+16`. Declaration order, object type and symbol names were
   varied with no effect. Test, in this order: (a) another 4.4.3 build (the
   `.comment` string only proves GCC 4.4.3, not the patchlevel or NDK
   revision); (b) `-fdata-sections` plus an explicit linker script, and measure
   the *linked* result instead of the ET_REL object; (c) GCC 4.6/4.7 from the
   same r8e NDK as a bound. Do not force the layout with padding or attributes
   and call it recovered. Closing this removes the single divergent word in the
   `android_getCpuCount` and `android_getCpuFamily` windows.
2. **Close `android_cpuInit`'s 32-byte gap.** The reconstructed body is 2600 B
   at `-O3 -fstack-protector` against the reference's 2632 B. Known structural
   differences: the reference sign-extends the field delimiters (`LDRSB`
   against `LDRB`) and orders the `Features` block differently. Work from
   `.cache/cpufeat/rec/cpuinit.txt` (reference listing) and the candidate
   listing side by side; the `tools/compare/literal_map.py` output in
   `.cache/cpufeat/cpuinit_refs.json` already resolves every literal.
3. **Behavioural tests for the TU.** The helpers (`parse_decimal`,
   `cpulist_parse`, `has_list_item`, `extract_cpuinfo_field`) are pure functions
   over caller buffers. Add a host test target with fixture buffers (cpuinfo
   with/without `Features`, `vfpv3d16`-only, `(v6l)` misreport, `0-3`/`2,4-7`
   cpulists, truncated/invalid input) so the TU reaches TESTED for behaviour
   independently of the byte-match question.

## Previous: relocate the thunks, then recover the initializer (2026-10-31)

The Premium CPU-query thunks now match instruction-for-instruction under three
different compilers, so the open question is no longer "which compiler shape"
but "can the relocation and literal-pool words be resolved to the reference
values". NDK r8e GCC 4.4.3 is installed locally at
`/home/jason/.local/opt/android-ndk-r8e`; the pilot picks it up automatically
(override with `NOVA3_R8E_NDK`).

1. **Relocation/link experiment.** Compile the matching-context variant with
   r8e GCC 4.4.3 and expose the `.bss`/`.text` layout the reference implies, so
   `R_ARM_PLT32` and `R_ARM_REL32` resolve to the observed displacements. This
   is the only measured remaining difference; until it closes, no byte-match
   claim is permissible. Keep any linker script or synthetic object under
   `.cache` and out of production targets.
2. **Recover `android_cpuInit` (0x000d2f98, 2,632 B)** at least far enough to
   own its writes to the state block, which also removes the need for the
   matching-context stand-in and restores direct (non-preemptible) binding of
   the initializer.
3. **Settle the alignment inference.** The two-`LDR` feature load is modelled as
   a word-aligned aggregate. Cross-check it against other 64-bit field accesses
   in the reference: if other 64-bit fields at provable 8-byte alignment use
   `LDRD`, the word-alignment reading gains support; if not, the model is wrong
   and the candidate struct must be corrected rather than kept.
4. Keep `reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp` (context
   ABI) alongside the corrected candidate but do not present it as the
   original interface.

Existing source-engine and asset milestones remain valuable and are retained.

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
