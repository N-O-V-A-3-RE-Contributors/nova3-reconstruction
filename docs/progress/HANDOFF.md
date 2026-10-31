# Resume without chat history

Work at /home/jason/Projects/nova3_re; read AGENTS.md, STATUS.md, NEXT.md and docs/decisions/{ARCHITECTURE,BLOCKERS}.md. Owner jason(uid1000), shell may be root: use runuser -u jason for builds/analysis. Original inputs are read-only references, no originals changed, nothing uploaded. Do not execute original binaries at runtime or bundle original shader programs. Local Git identity works; commit verified milestones.

## Current source and exact limits

Newest (2026-10-31): the caller side of the cpu-features work now exists.
`reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp` is
`vox::DriverCallbackSourceInterface::DetectNeon` (ELF VA `0x009dad14`, 32 B, A32):
family != 1 → false, else one bit (bit 2) of `android_getCpuFeatures()`. It is
IMPLEMENTED, behaviour-tested (`detect_neon_behaviour`, CTest) and measured
`ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION` in 6/6 NDK r8e 4.4.3 configs at
32/32 B, with the two `BL` displacements as the only differing words;
`code_window_equal` is false, no byte/compiler claim. `tools/compare/cpuquery_callsites.py`
decoded all 39,108 bounded engine functions (0 undecodable) and found the whole
cpu-features API surface: DetectNeon's only caller is
`vox::DriverCallbackInterface::Init(void*)` (0x009d53f4, 48 B, call at 0x009d5414),
and the only other consumer is a direct read in
`glitch::collada::detail::CSoftwareSkinTechnique::init` (0x00739630, 1028 B, call
at 0x00739764). Next slice is that 48-byte Init.

Earlier (2026-10-31): `reconstruction/recovered/libNOVA3_neon/cpu_features.c` is the
whole Premium `cpu-features.c` TU (getters + `android_cpuInit` + helpers) and its
three exported getters (0x000d2f10/0x000d2f3c/0x000d2f6c, 44/48/44 B, A32) now match
the reference instruction stream modulo the two unavoidable relocation words under
NDK r8e GCC 4.4.3 at `-O2`/`-O3`/`-Os`, with the reference `.bss` layout — the last
source difference was getter *definition* order, not a missing toolchain flag.
`android_cpuInit` (0x000d2f98) is IMPLEMENTED and TESTED but still not matched:
with `-fsigned-char` (settled by the reference's 11 `sxtb` / 18 `ldrsb`) it is
2648 B against the reference 2632 B; the residual is inlining/scheduling, and no
measured NDK r8e 4.4.3 configuration reproduces 2632 B. Behaviour is covered by two
CTest targets — `cpu_features_behaviour` (pure helpers over fixtures) and
`cpu_features_behaviour_init` (android_cpuInit end-to-end, with `/proc/cpuinfo` and
the two sysfs cpulists served from an in-memory table via link-time
`-Wl,--wrap=open/read/close`, so the recovered paths are untouched).
`tools/compare/cpufeatures_pilot.py` reproduces the comparison with
`source_matching_verified=False` and now carries `-fsigned-char` rows; the older
`cpufeatures_queries.cpp` context ABI candidate and `tools/compare/cpuquery_pilot.py`
remain as a separate, superseded measurement. Nothing claims an original compiler,
`.o`, relocation set or `.so` hash.

13 independently compiled engine .cpp modules: CRC32, repeating XOR, ZIP local headers/payloads, native logical ZIP lookup/heap policy, seekable ZIP archive/resource mounts, contiguous BRES relocation, bounded inline Collada mesh reading, PVR header/mips, SPLIT RGB/alpha bundles, ETC1 pixels, game ETC filename rewrite, SB scrambled headers. zlib1.3.2 is vendored source with pinned provenance/license. No original ELF/APK needed to build. Assets remain separate local data. No Android APK, scene renderer, controls/gameplay/audio/campaign or networking yet.

Only3 complete native utility mappings are verified (ComputeCRC, CRC wrapper, unscramble),317 original differential cases.17 additional stage2 address-to-source mappings are explicitly partial. Native lookup184cases+3orders, BRES4bases+7headers, PVR header388cases, ETC1 pixels272cases, texture rewrite165cases have scoped original-ARM evidence; see individual reports for hooks/exclusions. 31 sampled first meshes/37,522vertices/25,289triangles match independent field reading; one sampled resource has0geometries. All1,332 texture assets/1,691planes/17,505mips decode1,829,884,023RGB pixels. SB30packages/4,885entries/2,703,534,940bytes independently match fields/inflate/CRC. SB SDIR/trailer validation, descriptor forms and19otherGLA variants remain unsupported.

Native analyzed inventory:4ELFs,76,344symbol rows/73,318distinct binary+addresses. DWARF20compiler-runtime CUs/276type tags, no game class DWARF. SQLite analysis_db/index.sqlite saved;62byte-validated scoped Ghidra exports (59function rows DECOMPILED,3VERIFIED; remaining76,282INVENTORIED). source_units has22records including5initial+17stage2 partial mappings. Do not count exports as reconstructed game modules.

## Build and verification

`NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` runs host tests, Python guard/archive checks, source-only artifact checks and selected original oracles. Latest host13/13, Python13/13, ASan/UBSan13/13, clean source-only Release13/13; ARMv7 source rebuild successful. Actual exit0 logs .cache/logs/scrambled-final-{checks,clean,sanitizer,armv7}.log. The 2026-10-31 cpu-features and DetectNeon work added three CTest targets, so the
current host count is 16/16 (`ctest --test-dir build/host`, Debug/Ninja) and
`bash tools/check_all.sh` exits 0 with `cpu_features_init_test` and
`detect_neon_behaviour_test` in the audited-artifact list (17 artifacts); the ASan/Release counts above predate that and were not re-run. Source-only guard rejects both original APKs and4ELFs, but full final APK/DEX/linker/license certification remains pending. No device run; ADB has no attached device.

`cmake -S . -B build/host -G Ninja && cmake --build build/host -j2 && ctest --test-dir build/host --output-on-failure` works from a source-only checkout. Clean disposable build: `.venv/bin/python tools/compare/clean_build.py`. Existing build/sanitize-nopie has clang ASan/UBSan/non-PIE flags needed on this host (docs/toolchain.md). Android NDK29 /opt/android-ndk, SDK ready, output build/android-armv7/libnova3_core.so; no APK packaging yet. No new host installs this continuation.

## Local reference data and reproduction

APKs originals/*.apk, original native extracts analysis/*/lib/*/*.so, RAR-named input archives Archive/*.rar. Exact hashes research/elf/inventory.json and research/assets/archive_sha256.txt. Premium .rar actuallyZIP; full OBB data cached .cache/assets/premium-{main,patch}.obb, complete CRC/SHA verified in adjacentJSON. Restore `.venv/bin/python tools/extract/materialize_premium.py`. Premium APK GAND.GloftN3LT105 differs OBB ANMP.GloftN3HM1050/1070: sampled data compatibility is verified, whole-game edition parity is not.

Core data checks: tools/compare/{package_resources,mesh_source,texture_corpus,etc1_oracle,texture_path_source,scrambled_zip_source}.py. SB full corpus checker decodes2.7GB and recreates selected offline effect references under .cache/assets/{main,patch}-NV_DiffuseVC{.bdae,_FS.glsl,_VS.glsl}. These are original code/data analysis fixtures ONLY, never production runtime dependencies. All3selected files match between main/patch. Full reports research/elf/*verification.json; native/format notes research/assets/{BRES,COLLADA,GL_ENUMS,TEXTURES,ETC1,TEXTURE_PATHS,SCRAMBLED_GLA}.md.

`build/host/nova3_mesh .cache/assets/premium-main.obb 01_room_a.bdae build/room_a.obj` exports actual first geometry, no materials/transforms. `build/host/nova3_texture .cache/assets/premium-main.obb TS_ground.tga build/ground` routes native ETC-profile name and writes .rgb.ppm/.alpha.ppm if present. PNG via `python3 tools/compare/ppm_to_png.py INPUT.ppm OUTPUT.png`; PIL absent, not needed. These previews are data diagnostics, not game frames. ETC full corpus scans should use build/release/nova3_texture OBB --pixels for speed; then etc1_corpus.py checks completion.

## Next genuine source task

Newest bounded task (NEXT.md): recover `vox::DriverCallbackInterface::Init`
(ELF VA `0x009d53f4`, 48 B), the only in-binary caller of the recovered
DetectNeon probe, using the same decode → source → pilot → CTest pattern; the
alternative is bounding the NEON branch at `0x00739764` inside
`CSoftwareSkinTechnique::init` (1028 B, much larger).

Then: integrate SB readers into owned seekable/memory archive mounts, preserving actual custom filesystem precedence (customCreateAndOpenFile0x2a3afc; not exported yet). Header parser and CLI exist, not a mounted SB archive class. Then recover effect techniques/defines, material parameters, vertex semantic/byte normalization, samplers and scene/camera transforms for a newly implemented renderer. Actual first mesh buffer+4 references _2_-_Default; SMaterial uses24-byte SNewParam entries, count+16/array+20, type+8/value+20 per native createMaterial0x6a790c. First mesh images SF_Intro_Building_02_Wall.tga/TS_ground.tga resolve and source-decode. Effect dependency NV_DiffuseVC.bdae/#DiffuseVC-fx now found inside effects.gla; do not invent shader semantics or copy original GLSL into production. See NEXT.md for priority/details.

## Persistent analysis tools

Saved Ghidra generated/ghidra/premium.gpr/.rep, exports generated/ghidra/exports/<ELFhash>/<ELFVA>.json. Run tools/ghidra/export_batch.py with JAVA_HOME=/usr/lib/jvm/java-21-openjdk JAVA_TOOL_OPTIONS=-Xmx1400m and --addresses/--limit. Only one process may open project; no current job remains. Exporter skips completed records and validates original bytes; Ghidra image base+0x10000 is stored separately from ELFVA. Full autoanalysis/other ELF imports pending. Do not re-export valid batches unnecessarily. Last .cache/logs/ghidra-scrambled-archive.log DONE failures0.

Index checks `.venv/bin/python tools/index/check_index.py`; record scoped source evidence tools/index/record_stage2.py. Inventory regeneration preserves function states; never run concurrently with Ghidra writes. JNI/lifecycle map research/android/STARTUP.md, JADX/apktool offline outputs generated/. rev.ng pinned public-demo installed .cache/revng-c865bc3 but90/30sec actual probes timed out with0C outputs (research/architecture/REVNG.md); don't blindly retry. Agent quota interrupted independent review; lead continued, no replacement agents spawned to evade quota. Keep concurrency moderate. Disk31GiB free at latest check.
