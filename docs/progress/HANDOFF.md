# Resume without chat history

Work at /home/jason/Projects/nova3_re; read AGENTS.md, STATUS.md, NEXT.md and docs/decisions/{ARCHITECTURE,BLOCKERS}.md. Owner jason(uid1000), shell may be root: use runuser -u jason for builds/analysis. Original inputs are read-only references, no originals changed, nothing uploaded. Do not execute original binaries at runtime or bundle original shader programs. Local Git identity works; commit verified milestones.

## Source-recovered native chain (2026-11-01, seventh run)

Eleven source-owned units across ten files in
`reconstruction/recovered/libNOVA3_neon/`, all IMPLEMENTED + TESTED:

- the `cpu-features.c` TU (`cpu_features.c`) and its no-argument query thunks
  (`cpufeatures_queries.cpp`);
- `DriverCallbackSourceInterface::DetectNeon` (0x009dad14);
- `DriverCallbackInterface::Init` (0x009d53f4);
- `CSoftwareSkinTechnique::{checkAvailability,needOutputBuffer,attach}`
  (0x00710b50 / 0x00710b48 / 0x00710b58) — these three are also **byte-equal**
  per function (no relocation in the windows), measured by
  `tools/compare/collada_skin_trivial_pilot.py`;
- `CSoftwareSkinTechnique::prepareCache` (0x0073f0a4, 228 B) — IMPLEMENTED +
  TESTED, **NOT MATCHED**: 224 B candidate, 17 words equal, 5 relocation words,
  the rest register scheduling. Its five dependencies are declared with the
  reference mangled names in `collada_skin_cache_deps.h` and are NOT
  reconstructed; the class layout at +0x04/+0x08/+0x0c is declared there;
- `glitch::core::operator*<float, collada::SMatrix>(CMatrix4<float> const&,
  SMatrix const&)` (0x006e7e60, 564 B) — IMPLEMENTED + TESTED, **NOT MATCHED**:
  identical instruction mix at `-O3` (12 `vmul`/24 `vmla`/3 `vadd`/16 `vstr`) but
  540 B, the difference being 6 `vldr` (the SMatrix's middle component kept live
  across a row). `collada_matrix_multiply.{h,cpp}`; the SMatrix is declared there
  as 64 B of four rows of four floats with only three components per row read;
- `glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float>::eConstructor)`
  (0x00108c54, 56 B) — IMPLEMENTED + TESTED and instruction-window MATCHED
  (56/56 B, 13 equal + the one `bl memset` relocation word, 4/4 configs):
  values 0/1 write nothing, values >= 2 zero the 64 bytes and set the row-major
  diagonal (the identity). Measured: 74 call sites, 54 x `r1=2`, 6 x `r1=0`.
- `glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float> const&, eConstructor)`
  (0x00108c8c, 240 B) — IMPLEMENTED + TESTED, **NOT MATCHED** (240/240 B, 28
  equal + 7 relocation words + 25 displacement words; the non-branch instruction
  multiset is exactly equal 41/41, the residue is the four trailing case blocks
  laid out `4,2,3,1` in the reference versus `1,3,4,2` here). Five enumerated
  paths: 1 copy, 2 identity, 3 `getTransposed(source,this)`, 4
  `getInverse(source,this)` with zero-on-failure, 5 `transpose(inverse(source))`
  in place; 0 and >= 6 write nothing. Call sites: 10, 8 x `r2=1`, 2 x `r2=4`.
- `glitch::core::CMatrix4<float>::getTransposed(CMatrix4<float>&) const`
  (0x000eba84, 132 B, `collada_matrix4_transpose.cpp`) — IMPLEMENTED + TESTED and
  instruction-window **MATCHED unmasked**: 33/33 words equal, no relocation or
  encoding word, at `-O3 -fvisibility=hidden`. Destination slot k <- source slot
  `(k%4)*4 + k/4`, self-inverse, source never written, no frame and no call.
  CTest `collada_matrix4_transpose_behaviour` (stub-free) and
  `collada_matrix4_transpose_link_path` (all three matrix TUs, only getInverse
  stubbed). 27 call sites in 4 functions. Row-major storage is INFERRED; the
  offset pairing and the arithmetic are observed.

`getInverse` (0x000ebb08, 776 B) is declared in `collada_skin_cache_deps.h` and
NOT reconstructed — the only remaining dangling dependency of this group.

The cpu-features consumer surface is closed: `CSoftwareSkinTechnique::init`
(0x00739630) only lazily fills the TU-local 8-byte `cpuFeatures` static
(0x00da2950); the NEON gate is `(cpuFeatures & 4) != 0` inside
`CSoftwareSkinTechnique::skin` (0x0072ad90, test at 0x72b280..0x72b2b4).
Reproduce with `tools/compare/skin_cpu_cache.py`. Both call sites live inside
1 KB / 4 KB functions and are NOT reconstructed.

Next ONE step: `glitch::core::CMatrix4<float>::getInverse(CMatrix4<float>&) const`
at `0x000ebb08` (776 B) — the last dependency this group declares and does not
own; bound it by the determinant path and record-and-stop if 776 B cannot pin the
algorithm.

Machine-code evidence per unit: `tools/compare/{cpufeatures,detect_neon,
driver_callback_init,skin_cpu_cache,collada_skin_trivial,collada_skin_prepare_cache,
collada_matrix_multiply,collada_matrix4_ctor,collada_matrix4_copy_ctor,
collada_matrix4_transpose}_pilot.py` (also `skin_cpu_cache.py`).
`ctest` is 24/24; `bash tools/check_all.sh` is rc=0.

## Front door for everything analysis-related (2026-11-01)

`./nova3` (thin shell wrapper) → `tools/workbench.py` (tested). Guide:
`docs/DECOMPILE.md`.

```sh
./nova3 doctor     # PASS/WARN/FAIL per dependency + the fix; exit 1 on FAIL
./nova3 index      # analysis_db/index.sqlite (once, ~45 s)
./nova3 decompile  # bounded resumable Ghidra batch (default 8 fns)
./nova3 decompile --address 0x9dad14
./nova3 status | show 0x9dad14 | search DetectNeon
./nova3 build | test | verify
./nova3 push       # wraps tools/setup/push_branch.sh (needs credentials)
```

Ghidra/JDK discovery lives in `tools/pipeline/ghidra_env.py` (no hardcoded
`/opt/ghidra`/`java-21-openjdk` any more); the exporter and `doctor` share it.
Exports: `generated/ghidra/exports/<sha256>/<addr>.json`, private and gitignored.
Measured 2026-11-01: doctor 16 pass / 0 warn / 0 fail; exports 4 → 7 → 9;
CTest 16/16; tooling pytest 16 passed. `show` subtracts the Ghidra image base
(0x10000) when resolving call targets — that is deliberate, not a fudge.

The decompile driver holds a single-writer lock
(`.cache/pipeline/auto_decompile.lock`): a second live run exits `4` doing
nothing, a lock whose PID is gone is replaced, so `nohup ./nova3 decompile --all`
can be left running (`docs/DECOMPILE.md` §2) and restarted safely.

## Two setup facts to know before touching Ghidra or Git (2026-10-31)

- **Unattended decompilation is now one command**:
  `.venv/bin/python tools/pipeline/auto_decompile.py [--max-functions N --batch-size M]`,
  `--status` for coverage only. It bootstraps `analysis_db/index.sqlite`
  (gitignored, absent from a clean checkout), then drives
  `tools/ghidra/export_batch.py --all-symbols` in bounded, checkpointed batches.
  The Ghidra project must NOT live under a `.`-prefixed path (`~/nova3-analysis/ghidra`
  by default); Ghidra's `ProjectLocator` rejects those outright, which is what
  used to make every worktree import fail. Measured on Premium
  `84075223…baaf`: 39,123 indexed functions, cold batch 68.7 s (+2 exports),
  warm batch 22.9 s (+2), checkpoint `.cache/pipeline/auto_decompile_state.json`.
- **Push is a capability grant, not a repo problem.**
  `bash tools/setup/push_branch.sh` publishes and verifies the branch; it exits
  `3` when no credential source exists. The Paperclip `git` wrapper strips
  `GH_TOKEN`/`GITHUB_TOKEN`/`SSH_AUTH_SOCK` and brokers credentials only with
  `PAPERCLIP_GITHUB_BROKER_TOKEN`; that is absent in automated runs today, and
  `origin/main` was at `43f3502` when last read. Do not bypass the wrapper with
  the host's personal credentials.

## GCC 4.4.3 is installed; start here (2026-11-02)

The engine's compiler is now available and verified:
`bash tools/setup/fetch_gcc443.sh` -> `.cache/toolchains/android-ndk-r8e/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++`
(GCC 4.4.3; ELF attributes of the original: ARM v7-A, VFPv3, softfp, wchar 4).

Do not cite "clang-21 vs GCC 4.4.3 codegen" as the reason a function differs any
more: rebuild it with the real compiler:

```sh
.venv/bin/python tools/compare/gcc443_sweep.py \
  --gcc .cache/toolchains/android-ndk-r8e/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++ \
  --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --case reconstruction/recovered/libNOVA3_neon/<file>.cpp:0x<VA>:<mangled>\;reconstruction/recovered/libNOVA3_neon \
  --out .cache/gcc443-sweep/<name>.json
.venv/bin/python tools/compare/gcc443_explain.py --gcc <same> --reference <same> \
  --source <file> --address 0x<VA> --symbol <mangled> --flags "<flags>"
```

Current findings are in `docs/progress/NEXT.md` (top section): CMatrix4
(eConstructor) already matches size 56 B; operator*<float,SMatrix> has an exact
instruction-class profile at `-mfpu=vfpv3-d16 -O2 -funroll-loops` and a 6-load
reload-pattern gap; `__restrict` rejected.

The worker worktree `/.local/automation/worktrees/worker` is rebased onto main
and has `tools/compare/gcc443_{sweep,explain}.py`, a `WORKER-MISSION.md` and a
seeded `.local/dsh-loop/BACKLOG.md`; the DeepSeek Harness loop can run there with
`nova3-dsh loop --workspace <that dir>` once an API key is available.

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
