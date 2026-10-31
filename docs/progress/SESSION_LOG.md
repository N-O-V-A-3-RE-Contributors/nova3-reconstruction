# Session log

## 2026-11-01 — fourth run: `glitch::core::operator*<float, SMatrix>` recovered

- Decoded `0x006e7e60` (564 B, A32) with Capstone from the Premium ELF (SHA-256
  `84075223...048abbaaf`); resolved the single `bl` to
  `_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE` (`0x00108c54`) from `.symtab`.
- Established the element mapping from the store offsets (16 `vstr`, byte
  0x00..0x3c) rather than from a matrix convention: row-major, rows 0..2 linear,
  row 3 translation, fourth column literal 0.0f/1.0f; the CMatrix4's floats
  3/7/11/15 and the SMatrix's fourth float per row are never loaded.
- Wrote `collada_matrix_multiply.{h,cpp}` plus CTest
  `collada_matrix_multiply_behaviour`; four mutants run and killed; wired into
  `tools/check_all.sh`'s audit list, `record_build_provenance.py` and the oracle
  block.
- Measured with NDK r8e GCC 4.4.3: `-O3` 540 B vs 564 B with an identical
  instruction mix apart from 6 `vldr` (the SMatrix's middle component kept live);
  `-O2`/`-Os` stay looped (264/260 B). `source_matching_verified=False`.
- `ctest` 20/20, `./nova3 verify` rc=0. Regression pilots: three leaves still
  byte-equal, `prepareCache` still 224 B. The full
  `NOVA3_RESEARCH_ORACLES=1 check_all.sh` exceeded the foreground time limit and
  was not completed this session; the three Collada pilots were run individually.

## 2026-10-25 — initial source reconstruction checkpoint

- Read actual AGENTS.md; verified /home/jason account via getent and workspace path. Root shell used runuser -u jason for analysis/build work. Initial disk38GiB free, RAM7.6GiB total/~4GiB available; no attached ADB device. No host package installs, service/security/global configuration changes intentionally performed.
- Input APK hashes exactly matched both expected values. tools/index/inventory.py hashes all four ELF extracts and confirms APK member hashes match extracts. Archive hashes recorded independently research/assets/archive_sha256.txt. Inputs never written.
- Parallel bounded agents inventoried Android/JNI and archives; inspected their reports against ELF, asset specimen, hash and source tests. Reports research/android/STARTUP.md, research/assets/ARCHIVE_REPORT.md.
- Installed tools already include Ghidra12.1.2, Java21/27, JADX1.5.6, Apktool3.0.3, CMake4.4.4, Ninja1.13.2, LLVM, Android SDK/NDK. Used Java21 for Ghidra. Existing .venv reused; installed research-only unicorn2.1.4 from PyPI (log .cache/logs/unicorn-install.log). Record pinned requirements in tools/setup/.
- tools/index/inventory.py ran exit0: 76,344 defined-function symbol records across four binaries; Premium39,123. DWARF20 CUs,276 type-tag records,91 subprogram DIEs; all CUs compiler-runtime, no game class DIEs. research/elf/inventory.json and analysis_db/index.sqlite are local durable outputs.
- Persistent PyGhidra import generated/ghidra/premium.gpr. First attempt hit concurrent SQLite writer; second discovered Ghidra image base offset. Invalid first unvalidated JSON moved .cache/invalid-ghidra, script corrected to compare input bytes. Function-body/disassembly export also refined; logs .cache/logs/ghidra-batch*.log preserve defects and corrections.
- New C++ CRC module compiled with GCC16.2.1/C++17/Ninja; known vectors, empty/null source semantics and chaining tests pass. CLI on real5204-byte BRES asset prints d8c3c07e, matching ZIP header CRC. Offline isolated ARM oracle plus C++ plus zlib agree on122 cases; original256-entry table agrees with generated polynomial. Report research/elf/crc_verification.json.
- New repeating-XOR module from ARM0x411610 compiled and tested;48 original ARM/source/independent-expression cases pass (research/elf/scramble_verification.json). External strlen alone is hooked; original libgcc uidivmod executes inside isolated research emulator. Research oracle never enters source runtime build.
- Source-only packaging guard added with original .so/.dex hash denylist; disguised original, nested unknown DEX and forbidden-link/source mismatch regressions tested. Host provenance record built under build/host/. No APK certification claimed.
- Official rev.ng docs consulted; installer downloaded and inspected without piping to shell. Pinned official demo archive544,913,104 bytes downloaded into .cache, hash a1e7d2a622ea48a8c8888d0bdbed7b9ed965a38ebfe16fcc4582a7957e96cc94; bounded safe extraction by agent. Details/failures in research/architecture/REVNG.md when completed.
- Git initialized without prior history; existing user identity jason is configured. Proprietary originals/extracts/generated evidence, .venv, build caches and SQLite excluded; metadata reports and source retained. No claim of recovered gameplay.
- Final initial-checkpoint expansion:12 byte-validated Ghidra exports, including4 startup routines and CZipReader::openFile0x819878; latest batches exit0/failures=0. Ghidra function bodies are bounded by ELF symbol sizes and false first annotation/old call edges cleaned. Exporter skips existing pending batches and returns nonzero on requested failures.
- CRC wrapper oracle expanded to147 seeded/aligned/unaligned/null cases; all agree with original ARM and source outputs.3 full original native functions now VERIFIED in bounded domains. ZIP source mappings remain PARTIAL, not whole class reconstruction.
- New C++ stream scanner traversed all4,281 real Premium entries; every8 metadata fields match separately generated Python local/central inventory (research/elf/zip_index_verification.json). Full outer streams drained/CRC checked, no full OBB materialization.
- Pinned zlib1.3.2 tar SHA256 verified against official homepage,17 unchanged source/header/license files vendored; STATIC/PIC source build, standalone test1/1 passes, no dynamic system libz. Third-party provenance/license audit reconstruction/third_party/README.md and zlib_manifest.json; fetch tools/setup/fetch_zlib.py.
- Recovered native openFile method8/raw inflateInit2(-15) path from ARM0x8199fc/0x819ab8/0x819ae0. New source decoder uses bounded zlib source plus own CRC, verifies exact compressed/output sizes, checksum and output cap.10 actual BDAE payloads byte-match separately invoked Python inflater and known first SHA256;40 corrupt/truncated/size/CRC variants return normal exit3/4. No whole original routine/class equivalence claim.
- Final host CTest4/4, Python guard/archive13/13, ASan+UBSan non-PIE CTest4/4 (20,000 malformed headers), disposable owned-source-only clean build/tests4/4 all pass. Initial PIE ASan shadow-map initialization failure retained; resolved with local build flags, no host setting changes.
- Final Android NDK ARMv7 Release rebuild succeeds including new source-built inflater. New libnova3_core.so DT_NEEDED only libc/libm/libdl. No APK/device test. Final source guard rejects both actual original APKs and all4 ELF inputs (research/elf/source_only_rejection_verification.json).
- Independent reviewer found prefixed-ZIP detection bypass and recommended cumulative recursive budget, nonzero exporter failure status, and avoiding crash-as-rejection tests. All corrected with regressions/rechecks. Source payload review found no count/memory bug; logs research/android/logs/payload-review-*.txt. Python and C++ decompression use zlib-family implementations; their match is independently invoked output evidence, not an independent algorithm/native execution oracle.

## 2026-10-25 — next resource-loading stage

- Resumed clean commit57efa86, read actual AGENTS/saved state, confirmed33GiB free disk and~4.6GiB available RAM; no installs or system configuration changes. Independent agents assigned bounded ZIP lookup and BRES evidence work; lead retained sole Ghidra DB access/main integration.
- Native lookup evidence recovered ASCII-only folding, separator asymmetry, lower_bound and custom unstable heap duplicate ordering; earlier mounted ZIP precedence and later-match decode-failure fallback. Added source utilities and matched184 logical ARM cases plus3 duplicate orders; modeled string/allocator helper scope retained explicitly.
- Ghidra ran additional batches for ZIP lookup/initialization and BRES File::Init/FileReader methods. All requested functions byte-validated and exported successfully; logs stage2 ghidra-*. More Collada constructor/accessor work queued separately.
- Added source seekable ZipArchive/FileRegion indexing with central/local validation, immutable asset reads, bounded resources and transactional errors; ResourcePackages named lookup/mount order. Unit tests cover duplicates, case/path rules, nonzero regions, EOCD comment shadows, CRC mismatch, traversal, bounds, empty ZIP, mount precedence and fallback.
- Materialized only supplied asset OBBs under ignored .cache/assets: main1980258754 bytes SHA2560e5d025cbe1a4c51d6071993cf402166191bf69901a0e0d9896235aeda2b2c14; patch339002046 SHA2563b1806a3180070f76695be3fe052b40429ab6dc8c9d33fd9c4baf6db2219e738. Outer member CRCs fully checked; original archives untouched. No original executable needed by source loader.
- Genuine contiguous BRES initializer branch recovered from ARM0x69c6f0/0x69ccf8. Original isolated emulator has no code hooks/external calls for this path. New C++ byte relocation matches4 real bases,7 header variants, idempotence and zero/duplicate slot outputs. Source safety rejects truncated input/control aliases/table bounds beyond original unchecked API; full shared/split buffer/object side effects excluded.
- Source end-to-end resource chain now validates all4,281 indices,60 named asset bytes and32 structurally relocated BRES images. First full package→name→inflate→CRC→BRES output hash matches original ARM golden. This is real asset loading, not gameplay/level rendering.
- Independent review caught EOCD comment-shadowing, inaccurate serialization wording, NUL-query domain and header-control alias behavior. All addressed with code/policy/tests; corrupted matching archive now tries later mounts. See RESOURCE_SLICE.md and ZIP_LOOKUP.md review appendix.
- Extended evidence to actual SCollada root header+0x20 (correcting initial+0x24 guess), indexed geometry/image/material native accessors, mesh attributes/buffers/tail serialization. Actual native accessor oracle12cases +versionpointer, nohooks. Reports COLLADA.md/COLLADA_native_accessors.json.
- Recovered exact serialized→runtime→GL primitive/type mappings with40 original ARM instruction-slice cases, noGLcalls/hooks. Critical raw6temporary+8→runtime+10 copy established before calling it GL_FLOAT. Serialized primitive0 maps throughruntime6 toGL_TRIANGLES4. Shader semantic roles/byte normalization remain unknown.
- Added independently compiled checked inline Collada mesh decoder and OBJ tool. Initial19mesh successes/13rejections expanded after real addStream minimum-vertex evidence;30 original ARM arithmetic slices confirm minVertex*stride+offset. Source adds that base to relative indices forglobalOBJ export.
- Final sampled mesh coverage:31 real meshes/37,522 vertices/25,289 triangles matched position bytes and indices against a separate native-backed field reader, allwithin storedbounds. One remaining sampled resource V_Boss_Lava_anim.bdae haszero geometries andreturns explicitbounds error. First real model build/room_a.obj contains12vertices/6triangles. No fabricated geometry, materials, shader roles, rendered frame or gameplay claimed.
- Collada parser unit suite adds synthetic validmesh, per-buffer base offsets, bounds/limits/index failures and10,000 deterministic malformed inputs. Native CTests now8; final sanitizer/clean-source/ARM checks pending latest extension completion.
- Agent quota interruption occurred during final independent mesh review. Already delivered review found no immediate memory-safety defect; descriptor-rescan complexity concern addressed by a source count cap before final tests. No further agents spawned to work around quota; lead continued integration and durable checkpointing.

## Stage2 texture and combined verification checkpoint

Continued independently after account usage interrupted parallel review. Implemented owned memory-backed ZIP input, native-policy SPLIT/rgb/alpha selection and checked PVRv2 ETC1 mip layout. All1,332 texture resources pass corpus crosscheck:359 split bundles,1,691 planes,17,505 mip regions (research/elf/texture_corpus_verification.json). Header predicate matches388 original ARM cases; native texture-manager0x7b13e0 decompilation completed. No decoded pixels/rendering claim.

Final combined checks exit0: NOVA3_RESEARCH_ORACLES=1 tools/check_all.sh (10native/13Python plus oracle/source-only checks); tools/compare/clean_build.py (10tests from source-only tree); build/sanitize-nopie ASan/UBSan10/10; build/android-armv7 successful. Logs .cache/logs/stage2-final-{checks,clean,sanitizer,armv7}.log. No system tool installations. Started bounded Ghidra conversion follow-up0x7914a4 and computePitch/computeSize0x77d37c/0x77d3cc; persistent project log .cache/logs/ghidra-pixel-convert.log.

## ETC1 source pixels

Committed resource-reader milestone bbb7447, then implemented independent C++17 ETC1 RGB8 decoding from actual convert0x7914a4 branch and constants0xc80720/0xc807a0.272 complete native ARM/source image cases pass, without hooks or external helper models. Unit tests include known blocks, tiny-mip crop/flip, bounds/limits and1,000 malformed cases. Release assertions are explicitly enabled. Named source loader writes real RGB/alpha PPM data; standard-library PNG encoder added after PIL was found absent. Viewed actual B1_miscObjectsA_NRM.rgb.png; this is a texture plane, not a game render.

Full Debug corpus scan intentionally terminated for cost; isolated Release build completed full scan. All1,332 assets/1,691planes/17,505mips decode1,829,884,023RGB pixels successfully; hashes/layout summary research/elf/etc1_corpus_verification.json. No persistent multi-gigabyte pixel dump. All final commands exit0: host11/11, Python13/13, sanitizer11/11, clean-source Release11/11, ARMv7 source build; logs .cache/logs/etc1-final-*. No device run. Ghidra completed convert/pitch, generic+custom material batches (53exports); custom-filesystem4-function follow-up underway. Actual custom image factory bypasses generic image factory; inspect custom FS before defining texture extension policy. No host packages installed;31GiB disk remains.

## Game texture request integration

Source mode4 texture path rewrite added from native CCustomFileSystem0x2ad998/UseTextureETC0x29e7b0: ordered case-sensitive substring replacements, original-name open fallback.165 native branch/source cases pass;8 source integration cases cover6 synthetic ZIP/error/fallback tests and2 actual first-mesh image names ending.tga, which resolve to genuine .etc resources and produce identical decoded pixels. Host12/12, sanitizer12/12, clean-source Release12/12, Python13/13, ARMv7 source build all exit0; logs .cache/logs/texture-path-final-*.62 validated Ghidra exports after5-function scrambled-package batch. Next major actual dependency is nestedSB .gla format, whose decoded headers match26-byte ZIP local bodies XORed with archive basename; investigating actual records before implementing.

## SB package source checkpoint

Committed game filename routing1e64037. Added independently compiled scrambled_zip.* header parser from native scanner0x411fc0/init0x412730, reusing verified basename-XOR and ZIP header/payload modules. Actual SB body is26-byte ZIP local body, no PK signature; same four-byte prefix length preserves offsets.30packages(19main/11patch),4,885records,2,703,534,940decoded bytes match independent Python metadata/inflate/CRC. All flags0, methods0/8; one emptySEND package; SDIR metadata and19otherGLAvariants remain unparsed. Original NV_DiffuseVC effect and2shader source references found inside effects.gla and kept under ignored .cache/assets only. These3files match between main/patch; no production use of original shader code.

Final verification exit0: tools/check_all.sh(host13/13,Python13/13,source-only artifact checks); clean source-only Release13/13; ASan/UBSan13/13; ARMv7 source rebuild. Logs .cache/logs/scrambled-final-*. SQLite source mapping update/check complete,62Ghidra exports,17stage2 scoped source mappings,3full native utility mappings;13source engine modules. No new tool installs/host config changes. Next genuine milestone is effect/material/vertex semantic/sampler binding and own renderer; source SB mount integration/SDIR/other PAK types still missing.

## 2026-10-31 — CPU-query thunk ABI correction and matching-context pilot

- Read AGENTS/STATUS/NEXT/HANDOFF and docs/OBJECT_MATCHING.md; confirmed Premium SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf unchanged. Disk 19 GiB free, ~2 GiB available RAM, one lightweight compile pipeline only.
- Ran the existing `tools/run_compiler_pilot.sh`: recorded `FUNCTION_SIZE_MISMATCH` for the legacy context-pointer candidate (`nova3_recovered_cpu_count`), as its own comments predict; evidence in `.cache/revalidation/compiler-pilot-*/matrix.json`.
- Wrote `reconstruction/recovered/libNOVA3_neon/cpufeatures_queries.cpp`: all three Premium CPU-query thunks with the observed no-argument ABI, one 20-byte word-aligned state object, `android_cpuInit` left as an unresolved dependency. Covers 0x000d2f10 (44 B), 0x000d2f3c (48 B), 0x000d2f6c (44 B).
- Added `tools/compare/window_diff.py` (relocation-aware per-word diff with A32 PC-relative literal detection) and `tools/compare/cpuquery_pilot.py`; wired the pilot into `tools/check_all.sh` behind NOVA3_RESEARCH_ORACLES=1.
- Found and used the local NDK r8e GCC 4.4.3 at `/home/jason/.local/opt/android-ndk-r8e` (the historical pilot documented it). Compiler candidates measured: NDK clang 21, arm-none-eabi-g++ 16.2, r8e GCC 4.4.3.
- Measurements: no candidate is byte-equal (`LINKED_FUNCTION_WINDOW_EQUAL` never occurs); with a matching-context variant, r8e GCC 4.4.3 reproduces every instruction word of all three windows and clang/GCC 16.2 reproduce two of three. Divergence left: `R_ARM_PLT32` call displacement and literal-pool addends.
- Two inferences recorded then withdrawn after measurement: (a) "GCC-only codegen shape" — clang/GCC 16.2 reproduce the same shape once the state object and initializer binding are right; (b) "pre-LDRD compiler age" — the reference contains 1,789 LDRD and 1,576 STRD instructions elsewhere. The two-LDR feature load is instead modelled as a word-aligned aggregate, which GCC 4.4.3 reproduces exactly.
- `tools/verify_source_only.py`: an uncommitted edit limited the forbidden-path check to the first component. This is correct (absolute paths and `..` are rejected already, so only the root component can name a private tree) but was undocumented; restored with an explanatory comment, a `forbidden()` helper and a new regression test covering both acceptance of committed `reconstruction/research/*` source and rejection of `research/*`, `analysis/*` and `reconstruction/../research/*`.
- Private evidence copy `research/elf/cpuquery_matching_verification.json` (18 compiler/variant/function results). No original code bytes are printed by any tool; full disassembly stayed in ignored `.cache/logs/premium-full-disasm.txt`.

## 2026-10-31 — cpu-features getter definition order closes the last source difference

- Verified the interrupted run's state: `cpu_features.c` (whole TU), `cpu_features.h`,
  `tools/compare/cpufeatures_pilot.py`, `window_diff.py` present and the pilot
  reproducible (12-config matrix; nothing uploaded).
- Root-caused the `.bss` ordering "toolchain mystery" to a source-ordering effect:
  GCC 4.4.3 emits exported functions in reverse definition order and lays out `.bss`
  in the order the emitted functions first reference it. Four declaration
  permutations of the four objects left `g_cpuFamily@+4` unchanged; defining the
  getters in reverse order (Family, Features, Count, after `android_cpuInit`) gives
  the reference layout `g_cpuCount +0x4 / g_cpuFeatures +0x8 / g_cpuFamily +0x10`
  *and* the reference function order.
- Rejected, measured: `-fno-toplevel-reorder` also reproduces the offsets but grows
  the getters to 48/52 B and switches them to `ldr rX, [pc, rY]` addressing, which
  the reference does not use. Kept as an explicit matrix row so the conclusion stays
  measured.
- Result (r8e GCC 4.4.3, `-O2`/`-O3`/`-Os` × `-fstack-protector` on/off): all three
  getters `ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION` (9/10/9 EQUAL, 2
  RELOCATION_FIELD each, 0 ENCODING_DIFFERENCE), sizes 44/48/44. `android_cpuInit`
  still 2600 B vs 2632 B (`FUNCTION_SIZE_MISMATCH`). No byte-match or compiler claim.
- Added `reconstruction/tests/recovered/libNOVA3_neon/cpu_features_test.c`:
  fixture-driven behaviour tests for `has_list_item`, `parse_decimal`, the cpulist
  helpers and `extract_cpuinfo_field`; wired into CMake/CTest as
  `cpu_features_behaviour`, into `tools/check_all.sh`'s source-only audit and into
  `tools/setup/record_build_provenance.py` (with its own `link_inputs`).
- Verification: `cmake --build build/host && ctest` 14/14 pass; `bash tools/check_all.sh`
  exit 0 (source-only audit now includes the new binary). With
  `NOVA3_RESEARCH_ORACLES=1` the run stops in `tools/compare/zip_headers.py` because
  this worker worktree's private `research/assets/` is empty (2 OBB index files exist
  only in the main tree) — pre-existing local-environment gap, not a regression.

## 2026-10-31 — cpu-features: end-to-end initializer tests + char-signedness evidence

- Compared the `android_cpuInit` window instruction by instruction (reference
  `arm-none-eabi-objdump --start-address=0xd2f98 --stop-address=0xd3050` over the
  Premium ELF vs the r8e GCC 4.4.3 ET_REL candidate) with a small normalizer that
  strips addresses, `@` annotations, literal-pool words and relocations. First
  structural divergence: the reference sign-extends every cpuinfo byte
  comparison (`sxtb` in the inlined `has_list_item`, `ldrsb` for the `:` and ` `
  delimiters) and the candidate emitted none.
- Counted the effect: reference window = 11 `sxtb` + 18 `ldrsb`; default
  (`unsigned char`) candidate = 0/0, `-fsigned-char` candidate = 11/18. Sizes at
  `-O3 -fstack-protector`: 2600 → 2648 B against the reference 2632 B. Getters
  stay word-equal in every signed-char row.
- Bound the residual: all six orders of the three `has_list_item` feature checks
  compile to 2648 B; flag probes measured and rejected — `-fno-crossjumping`
  2720, `-fno-reorder-blocks` 2612, `-fno-guess-branch-probability` 2612,
  `-fno-tree-dominator-opts` 2800, `-fno-if-conversion2` 2652,
  `-fno-omit-frame-pointer` 2672, `-fno-schedule-insns2`/`-fno-ipa-cp` 2648,
  `-fno-tree-tail-merge`/`-fno-inline-functions` not usable (740 B). No
  configuration reproduces 2632 B, so the remaining difference is recorded as
  toolchain-patchlevel inlining/scheduling, never forced.
- Added `-fsigned-char` rows to `tools/compare/cpufeatures_pilot.py` so the
  effect stays in every report (18 rows now; getters EQ everywhere, cpuInit SIZE).
- New `reconstruction/tests/recovered/libNOVA3_neon/cpu_features_init_test.c`:
  the recovered TU is compiled unchanged and `/proc/cpuinfo`,
  `/sys/devices/system/cpu/present` and `.../possible` are served from an
  in-memory fixture table through `-Wl,--wrap=open/read/close`. Eight scenarios:
  v7+NEON (count 4, features 15), `(v6l)` correction, `vfpv3d16`-only, missing
  cpuinfo (count 1 and sysfs never opened), present∩possible intersection,
  unreadable cpulists → clamp to 1, missing `Features` field, and
  pthread_once caching (cpuinfo opened exactly once across three getters).
- Negative control: a copy of the test with `neon` removed from the fixture
  aborts on the feature assertion, proving the wrappers supply the data.
- Wired as CTest `cpu_features_behaviour_init`, added to `tools/check_all.sh`
  and to `tools/setup/record_build_provenance.py` (own `link_inputs`).
- Verification: `cmake --build build/host` clean, `ctest` 15/15 pass,
  `bash tools/check_all.sh` exit 0 (16 audited artifacts). Local scratch objects
  and listings stay under `.cache/cpufeat/`; nothing proprietary was written
  outside `.cache` or committed.

## 2026-10-31 — DetectNeon recovered, tested; caller map resolved

Resumed the interrupted DetectNeon slice from the worktree (partial TU, test,
and two compare tools were uncommitted; nothing was discarded).

- Restored reference identity first: `sha256sum analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`
  = `84075223…baaf`, NDK r8e GCC 4.4.3 present at
  `/home/jason/.local/opt/android-ndk-r8e`.
- `cmake -S . -B build/host` + `cmake --build build/host -j4`: 81/81 targets,
  including the new `detect_neon_behaviour_test`.
- `ctest --test-dir build/host --output-on-failure`: **16/16 pass**, including
  `detect_neon_behaviour`.
- `tools/compare/detect_neon_pilot.py` first failed with
  `nova3_team.object_compare.CompareError: reference SHA-256 mismatch`. Cause
  was the draft's own caller bug: it passed the extracted 32-byte window to
  `window_diff.diff_window`, which bounds the reference function itself from
  the whole ELF and therefore hashes the whole file. Fixed to pass the full
  ELF bytes after the SHA check (`load_reference` now returns both). This is a
  tool defect, not a matching result.
- Re-run: `structure agrees with recovered source: True`; calls
  `0x000d2f6c → 0x000d2f3c`, `cmp [1]`, `ubfx (2,1)`, short-circuit return
  between calls = True, incoming `this` read = False. Window: 6/6 configs
  `ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION`, 32 B, 6 EQUAL + 2 RELOC + 0
  ENC, `code_window_equal=False`, `source_matching_verified=False`.
- `tools/compare/cpuquery_callsites.py --also 0x9dad14:DetectNeon`: 39,108
  functions decoded, 0 undecodable, **4 direct hits, no PLT route** —
  `0x00739764` in `CSoftwareSkinTechnique::init` (0x00739630, 1028 B),
  `0x009d5414` in `DriverCallbackInterface::Init` (0x009d53f4, 48 B), and the
  two getter calls inside DetectNeon itself. Naming the caller side was the
  open NEXT.md item; it is now closed with addresses.
- Wired the new target into `tools/check_all.sh` (audited artifacts) and
  `tools/setup/record_build_provenance.py` (own `link_inputs`); added both
  compare tools to the oracle block.
- Verification: `bash tools/check_all.sh` → exit 0, 17 audited artifacts
  source-only-clean, `detect_neon_behaviour_test` included. Oracle block still
  stops in `tools/compare/zip_headers.py` in this worker worktree because the
  private `research/assets/` OBB indices exist only in the maintainer tree
  (pre-existing environment gap, not a regression).
- No APK/ELF/OBB bytes, assembly or pseudocode written outside ignored
  `.cache/`; docs carry addresses, sizes, symbol names and counts only.

## 2026-10-31 — unattended decompilation pipeline; two clean-checkout defects fixed

Reported blocker: `git push` "no GitHub credentials" (fourth consecutive run) and
no way to start decompilation unattended. Both were investigated to root cause
instead of retried.

- `tools/index/inventory.py` → `sqlite3.OperationalError: unable to open
  database file`; cause: `analysis_db/` is gitignored, so a clean worktree has no
  such directory. Fixed by creating `analysis_db/` and `research/elf/` at start.
  Re-ran: exit 0, 43.3 s, 4 binaries / 76,344 function rows / 39,123 rows for
  Premium `libNOVA3_neon.so` (SHA-256 `84075223…baaf`).
- `tools/ghidra/export_batch.py` → Ghidra rejected the project path:
  `java.lang.IllegalArgumentException: Path element starting with '.' is not
  permitted`, because the project lived under the repo (this worktree is under
  `.local/automation/worktrees/worker`). Fixed with `--project-dir` (default
  `~/nova3-analysis/ghidra`, env `NOVA3_GHIDRA_PROJECT_DIR`).
- New `tools/pipeline/auto_decompile.py`: hash verification → index bootstrap →
  repeated bounded `export_batch` runs (`--all-symbols`) → JSON checkpoint after
  every batch → coverage summary; `--status` for a cheap read-only report.
- Measured: batch 1 `+2 exports in 68.7 s` (cold import), batch 2 `+2 exports in
  22.9 s`; `indexed=39123 exported=4 remaining=39119`. Exports:
  `_Z10ComputeCRCPhi` 0x000de2cc (20 instructions, 5 basic blocks),
  `_ZN3glf10CrcChecker5CRC32EPKvij` 0x008d890c, `android_getCpuCount` 0x000d2f10,
  `android_getCpuFeatures` 0x000d2f3c — all `byte_mapping_validated=true`.
- First attempt exported the *default two addresses* again and gained nothing:
  `export_batch.py` defaults to `--addresses 0xde2cc,0x8d890c`, so the driver must
  pass `--all-symbols`. After the fix a re-run advanced to the next two
  functions, which is the resumability proof.
- New hermetic test `reconstruction/tests/test_auto_decompile.py` (4 cases:
  missing reference exits 2, hash mismatch exits 2, status schema/coverage
  invariant, exporter arguments still wired). Passes in 0.71 s.
- GitHub: `git ls-remote origin HEAD` → `43f3502` unauthenticated, so the remote
  is fine; the Paperclip `git` wrapper deletes `GH_TOKEN`/`GITHUB_TOKEN`/
  `SSH_AUTH_SOCK` and only brokers credentials when
  `PAPERCLIP_GITHUB_BROKER_TOKEN` is set, which it is not in this run. New
  `tools/setup/push_branch.sh` performs and verifies the push when capability
  exists; run here it exits `3` with the two supported remediations. Nothing was
  pushed; `automation/paperclip-worker` stays 3 commits ahead of `origin/main`.
- `research/assets` symlinked to the maintainer tree locally (gitignored) so
  `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` can run the whole oracle
  block in this worktree; previously it aborted at `tools/compare/zip_headers.py`.
- Full suite re-run after linking private references:
  `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` → EXIT=0, CTest 16/16,
  9 oracle PASS lines, `tools/index/check_index.py` clean. The earlier run of the
  same command failed at `tools/compare/texture_path_source.py` (exit 3 from
  `build/host/nova3_texture`) purely because `.cache/assets/premium-main.obb`
  was absent from the worktree; new `tools/setup/link_private_references.sh`
  links `research/assets`, `.cache/assets` and (when missing) `analysis/`.
- `tools/check_all.sh` oracle block now prints
  `tools/pipeline/auto_decompile.py --status` before the first oracle, so index
  coverage is recorded in every full run (this run: `indexed: 39123`).

## 2026-11-01 — the toolset becomes one documented command (`./nova3`)

- Added `./nova3` (shell wrapper) → `tools/workbench.py`: `doctor`, `index`,
  `decompile` (bounded, `--address`, `--all`), `status`, `show`, `search`,
  `build`, `test`, `verify`, `push`. `docs/DECOMPILE.md` is the single guide and
  `README.md` points at it from the top.
- `doctor` measured on this host: **16 pass, 0 warn, 0 fail** — reference SHA,
  venv, pyelftools/capstone, Ghidra 12.1.2 (`/opt/ghidra`), JDK 27, pyghidra
  3.2.0, project dir, 17.7 GiB free, 39,123 indexed rows, cmake/ninja,
  jadx/apktool/7z, NDK r8e. Exit 1 when anything FAILs.
- Fixed a portability defect that made the documented "start it and walk away"
  command host-specific: `tools/ghidra/export_batch.py` hardcoded
  `install_dir='/opt/ghidra'` and `JAVA_HOME=/usr/lib/jvm/java-21-openjdk`. New
  `tools/pipeline/ghidra_env.py` resolves Ghidra (explicit arg →
  `NOVA3_GHIDRA_HOME` → `GHIDRA_INSTALL_DIR` → system dirs → versioned `ghidra*`
  dirs, requiring `support/analyzeHeadless`) and the JDK; an explicit bad path is
  an error, not a silent substitution.
- Exercised end to end: `./nova3 decompile --address 0x9dad14` exported
  `DetectNeon` (found Ghidra/JDK itself, printed both); `./nova3 decompile
  --max-functions 2 --batch-size 2` advanced coverage 4 → **7** exports
  (`indexed=39123`, `remaining=39116`); `./nova3 status` per-binary table;
  `./nova3 show 0x9dad14` resolved both call edges to
  `android_getCpuFamily`/`android_getCpuFeatures` (needed the image-base
  subtraction, 0x10000) and printed the body; `./nova3 search DetectNeon` hit.
- `reconstruction/tests/test_workbench.py` — 10 hermetic cases (help surface,
  doctor JSON + "every non-PASS check has a fix", fabricated ELF rejected by
  hash, status counts, image-base call resolution, missing-export hint,
  unknown-symbol exit 1, search totals, resolver semantics). One test caught a
  real gap (optional WARN lines had no fix text) which is now fixed.
  `pytest` on `test_workbench.py` + `test_auto_decompile.py`: **14 passed**.
- Wiring: both tooling test files added to `tools/check_all.sh`; new
  `nova-ci.yml` step runs them on every PR/push.
- Verification: `./nova3 verify` → build + CTest **16/16** + source-only audit
  exit 0. No APK/ELF/OBB bytes, assembly or pseudocode committed; docs carry
  metadata and source facts only. Branch still unpublished (no GitHub capability
  in this harness; `./nova3 push` exits 3 with the two supported routes).

## 2026-11-01 — unattended decompilation hardened; publish routes measured

- `tools/pipeline/auto_decompile.py`: added a single-writer lock
  (`.cache/pipeline/auto_decompile.lock`, JSON `{pid, started_at, binary}`)
  acquired before the index/Ghidra project are touched, released in a `finally`
  with an ownership check. Live holder → exit `4` with one line and the foreign
  lock untouched; dead-PID lock → replaced. `tools/workbench.py` maps that to
  "another decompilation run owns the lock; this one did nothing".
- `./nova3 index` no longer hides its result: prints per-binary counts read back
  from `analysis_db/index.sqlite`, returns 1 if the DB is missing despite rc=0,
  and no longer discards stdout on failure.
- Live probes (not unit tests only): `./nova3 decompile --max-functions 2
  --batch-size 2` → exports 7 → 9, 33.2 s, `indexed=39123 remaining=39114`;
  lock probe through the CLI → exit 4 + `foreign lock preserved: True`;
  stale-PID acquire → True; release → lock removed.
- `tools/setup/push_branch.sh`: added opt-in `NOVA3_PUSH_VIA_SSH=1` (derives the
  ssh URL from the https remote, never rewrites the configured remote) and
  `NOVA3_PUSH_DRY_RUN=1`; remediation text now carries measured facts.
- Publish routes measured in this run: broker token absent; `gh` "not logged into
  any GitHub hosts"; host SSH key authenticates as `UmbraSecurity` but a
  `git push --dry-run` to the SSH URL returns `Permission ... denied`. Anonymous
  `git ls-remote` works, so this is a write-credential gap only. No push was
  attempted from the wrapper and no credential was configured.
- Verification: `ctest --test-dir build/host` 16/16; `pytest
  reconstruction/tests/test_workbench.py reconstruction/tests/test_auto_decompile.py`
  16 passed; `./nova3 doctor` 16 pass/0 warn/0 fail; CI-equivalent local run
  (guard 507 files, verification-gate 28 tests, legacy validation, `bash -n`
  checks, `nova3_easy --self-test`) all rc=0; `bash tools/check_all.sh` exit 0.
- Committed `4d7a1b0` (lock + index UX) and this docs/tooling commit on
  `automation/paperclip-worker`. Exports stay under gitignored
  `generated/`/`.cache/`; no APK/ELF/OBB bytes or decompiler output committed.

## 2026-11-01 — DriverCallbackInterface::Init recovered and tested

- Decoded `_ZN3vox23DriverCallbackInterface4InitEPv` (ELF VA 0x009d53f4, 48 B,
  A32): 11 instructions + literal. PIC addend 0x003e4988 + `add` PC resolves to
  0x00db9d90 = `DriverCallbackSourceInterface::s_listenerParameters`; the four
  32-bit zero stores hit `m_sWorkBuffer`/`m_sMixingBuffer` (+0/+4) and the
  `strb` hits the 1-byte `enableNeon`. r0 (`this`) and r1 (`void*`) are never
  read before the BL to DetectNeon at 0x009dad14.
- Field shape for the two 8-byte buffers INFERRED from
  `DriverCallbackInterface::GetWorkBuffer` (0x009d4828): +0 compares signed
  against the requested count and receives it, +4 goes to `VoxFree(void*)` and
  is replaced by `VoxAlloc(unsigned)`.
- New shared `driver_callback.h` (both classes, DetectNeon static on caller-side
  evidence), new `driver_callback_init.cpp`, new
  `driver_callback_init_behaviour` CTest, new
  `tools/compare/driver_callback_init_pilot.py`.
- Pilot (12 configs): `-fvisibility=hidden` 6/6 → 48 B, 5 EQUAL + 2
  RELOCATION_FIELD + 5 encoding (store displacements), instruction shape and
  store order equal, anchor delta constant 0x34. `-fvisibility=default` 6/6 →
  72 B `FUNCTION_SIZE_MISMATCH` (per-symbol GOT-style loads for preemptible
  statics), which is the negative control that the addressing choice matters.
  `code_window_equal=False`, `source_matching_verified=False`.
- DetectNeon re-measured after moving its declaration into the header: 6/6
  `ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION`, 32 B, 6 EQUAL + 2 RELOC.
- Tests: `cmake --build build/host` clean; `ctest` 17/17; negative control
  (one store deleted from the source) aborts with "Init did not clear both
  buffer references"; `bash tools/check_all.sh` exit 0, 18 audited artifacts,
  tooling pytest 30 passed.

## 2026-11-01 (second run) — cpu-features consumer surface closed; three byte-equal leaves

- Decoded the init() window and resolved the PIC anchor to `0x00da2950`, which
  `.symtab` names `cpuFeatures` (STB_LOCAL, 8 B). The pair load is followed by one
  `orrs` whose only consumer is `beq 0x00739764` → `bl 0x000d2f3c`
  (`android_getCpuFeatures`) → `strd r0,r1,[anchor]` @0x00739768 → `b 0x00739664`.
  The loaded pair is overwritten (r3 @0x73966c, r2 @0x739674) before any read
  after the join, so init() lazily fills a cache and does not gate anything.
- Consumer sweep over all bounded A32 STT_FUNCs: exactly two resolutions of that
  anchor — `init` (0x739640) and `skin` (0x72b278, `0x0072ad90`, 4256 B). In
  `skin` the cache is tested as `(cpuFeatures & 4) != 0`: `ldrd` @0x72b280,
  `mov sb,#0` @0x72b268 + `and r1,r7,sb` @0x72b288, `mov r8,#4` @0x72b294 +
  `and r0,r6,r8` @0x72b29c, `orrs sb,r0,r1` @0x72b2a0, `bne 0x72bcd4` @0x72b2b4.
  New tool `tools/compare/skin_cpu_cache.py` re-derives all of it, 13/13 checks,
  exit 0.
- Two tool defects found and fixed during the run: reading a literal through
  `index = va - text_vaddr` against whole-file bytes (wrong base) and a
  linear sweep across `.text` that desynchronised on literal pools and also
  captured the wrong `bl`. Final version decodes per symbol and follows the
  branch target out of line.
- Recovered three 8-byte methods of the same class from their exact bytes:
  `checkAvailability` 0x00710b50 (`mov r0,#1; bx lr`), `needOutputBuffer`
  0x00710b48 (same), `attach(ISceneNode*)` 0x00710b58 (`str r1,[r0,#0xc]; bx lr`).
  New pilot `tools/compare/collada_skin_trivial_pilot.py` compiles the TU with
  NDK r8e GCC 4.4.3 at `-O2`/`-O3`/`-Os`: 3/3 symbols × 3/3 configs, 8/8 bytes
  equal, no relocation in any window → `code_window_equal=True`. Scope stated in
  the report: per-function windows only, not compiler identification, not a
  whole-`.so` SHA match.
- New CTest `collada_skin_technique_behaviour`. The +0x0c offset is an ARM32 ABI
  fact, so on the 64-bit host the test asserts instead that `attach` changes
  exactly one pointer-sized slot holding its argument and nothing else; the
  absolute offset is asserted only when `sizeof(void*) == 4`. Negative controls
  run both ways: `return false` aborts on the predicate assertion, a wrong-field
  store aborts with "attach changed the wrong number of bytes".
- Verification: clean configure + build 85/85, `ctest` 18/18, `bash
  tools/check_all.sh` exit 0 with 19 audited artifacts and tooling pytest 30
  passed, and — unlike the previous run's note — `NOVA3_RESEARCH_ORACLES=1 bash
  tools/check_all.sh` now completes with exit 0 because this worktree's
  `research/assets/` is populated.

## 2026-11-01 (third run) — CSoftwareSkinTechnique::prepareCache recovered, tested, measured

- Decoded `_ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv`
  (ELF VA 0x0073f0a4, 228 B, A32, STT_FUNC) with Capstone; resolved all five `bl`
  targets by address against `.symtab` (0x731c58 ISkinTechnique::preparePtrCache,
  0x108c54 CMatrix4<float>::CMatrix4(eConstructor), 0x775520 vector<CMatrix4>
  ::resize, 0x752470 vector<CMatrix4 const*>::size, 0x6e7e60
  operator*<float,SMatrix>). No guessing: each was matched as the enclosing
  symbol of the branch target.
- Wrote `reconstruction/recovered/libNOVA3_neon/collada_skin_technique.cpp`
  (+prepareCache) and the new `collada_skin_cache_deps.h` (layout views + five
  extern declarations carrying the reference mangled names). Named the class
  layout at +0x04/+0x08/+0x0c; the three 8-byte leaves stayed byte-equal.
- Measured codegen experiments (NDK r8e GCC 4.4.3, scratch under
  `.cache`/TMPDIR, nothing committed): a by-value `CMatrix4` parameter always
  produces a 64-byte copy and a 0xbc frame (268 B function), at -O2/-O3/-Os and
  with a visible inline constructor alike, so the reference's in-place fill is
  reproduced by declaring the parameter by const reference (ABI-identical on
  ARM32: pointer in r2). Hoisting the loop's member reads into locals was also
  measured and rejected (it changed nothing) — keeping the accesses in the loop
  body is what reproduces the reference's per-iteration reloads.
- New `tools/compare/collada_skin_prepare_cache_pilot.py`: 12/12 structure facts
  re-derived from the reference, exit 0; window diff 17 EQUAL / 5 RELOCATION_FIELD
  / 34 encoding at -O2 and -O3, 224 B vs 228 B, `source_matching_verified=False`.
- New CTest `collada_skin_prepare_cache_behaviour` (20 scenarios) plus
  `collada_skin_cache_stubs.cpp` abort-on-call stand-ins for the older leaf test.
- Negative controls actually run: guard deleted -> mutant crashes inside a
  dependency stub; `&= ~2u` -> "FAIL: only bit 0 should have been cleared";
  stride `i` instead of `i*0x40` -> "FAIL: multiply did not receive the SMatrix at
  i*0x40". Restored source passes.
- Verification: `cmake --build build/host` clean; `ctest` 19/19; 
  `bash tools/check_all.sh` rc=0 with 20 audited artifacts, pytest 30 passed;
  `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` rc=0 (full private chain).
- No APK/ELF/OBB bytes, assembly or pseudocode committed; all scratch objects
  lived outside the repository.

## 2026-11-01 (fifth run) — CMatrix4<float>(eConstructor) recovered, 56/56 B modulo its memset relocation

- Read `git status`, `docs/progress/NEXT.md` and the two recovered Collada units;
  NEXT.md step 1 was the constructor at `0x00108c54`.
- Decoded the 56-byte window with Capstone (A32): `cmp r1,#1` / `push {r4,lr}` /
  `mov r4,r0` / `bls 0x108c84` / `mov r1,#0` / `mov r2,#0x40` / `bl 0xd2610` /
  `vmov.f32 s15,#1.0` / `vstr` 0x3c,0x00,0x14,0x28 / `mov r0,r4` / `pop {r4,pc}`.
- Resolved `bl 0xd2610` rather than guessing: `.plt` is a 20-byte PLT0 plus
  12-byte stubs, so stub index = (0xd2610-(0xd21f90+0x14))/12 = 137;
  `.rel.plt[137]` -> `.dynsym` = `memset`. (First decode attempt started mid-stub
  and produced a nonsense helper; the corrected index and the stub shape
  `add ip,pc,#12 / add ip,ip,#got / ldr pc,[ip,#slot]` are both asserted by the
  pilot.)
- Wrote `collada_matrix4_ctor.cpp`: `if ((unsigned)e > 1u) { memset(this,0,64);
  diag = 1; }`, with `__builtin_memset` because this toolchain has no `--sysroot`
  and therefore no `<string.h>` — the spelling that still emits the reference's
  `bl memset` instead of inline stores.
- `collada_matrix4_ctor_pilot.py`: structure facts re-derived (predicate, unsigned
  `bls`, saved `this`, single call -> `memset`, args 0/0x40, four diagonal stores
  of one `vmov #1.0` register, no other store), plus a whole-library call-site
  sweep: 74 direct calls, `r1 = 2` in 54, `r1 = 0` in 6, 14 with no local literal.
- Measured window: 4/4 configs (-O2/-O3/-Os hidden, -O2 default) give 56 B and
  13 EQUAL / 1 RELOCATION_FIELD / 0 encoding, `instruction_stream_equal=True`,
  `code_window_equal=False` (the one word is the `bl memset` displacement).
- New CTest `collada_matrix4_ctor_behaviour`, including the TU unchanged, stub-free.
  Negative controls actually run: predicate `> 1u` -> `> 0u`, diagonal `[10]`
  deleted, `[15]` -> `[14]`; each mutant fails, restored source passes.
- Verification: `cmake --build build/host` clean; `ctest` 21/21;
  `bash tools/check_all.sh` rc=0 with 21 audited artifacts.
- Scratch objects under `.cache/cm4/` only; no reference bytes or disassembly
  committed.
