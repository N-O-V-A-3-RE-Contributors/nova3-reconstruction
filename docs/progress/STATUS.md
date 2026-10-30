# Status — source resource, mesh and texture reconstruction

## All three cpu-features getters now match modulo relocation; TU tested for behaviour (2026-10-31)

The one remaining source-level difference in the Premium `cpu-features.c` TU
(see the section below) is closed, and it was a source-ordering effect, not an
unreproduced toolchain.

**What was measured.** GCC 4.4.3 emits the exported functions of a TU in the
reverse of their definition order, and lays out the TU's `.bss` objects in the
order in which those emitted functions first reference them. The reference
layout (`g_cpuCount +0x4`, `g_cpuFeatures +0x8`, `g_cpuFamily +0x10`) and the
reference function order (`getCpuCount`, `getCpuFeatures`, `getCpuFamily`,
then `cpuInit`) are both reproduced by defining the three getters in the
reverse of that order, after `android_cpuInit`. Four other declaration
permutations left the layout unchanged (`g_cpuFamily` always at `+0x4`);
`-fno-toplevel-reorder` also reproduces the offsets but destroys the address
caching the reference visibly uses (candidate getters grow to 48/52 B and
address `.bss` through `ldr rX, [pc, rY]`), so that hypothesis is recorded as
rejected rather than adopted.

Measured with `tools/compare/cpufeatures_pilot.py`, NDK r8e GCC 4.4.3, 12
configurations (base and rejected-flag rows both kept in the report):

| window | reference | candidate | words |
| --- | --- | --- | --- |
| `android_getCpuCount` 0xd2f10 | 44 B | 44 B | 9 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_getCpuFeatures` 0xd2f3c | 48 B | 48 B | 10 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_getCpuFamily` 0xd2f6c | 44 B | 44 B | 9 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_cpuInit` 0xd2f98 | 2632 B | 2600 B | still `FUNCTION_SIZE_MISMATCH` |

This holds at `-O2`, `-O3` and `-Os`, with and without `-fstack-protector`, at
`-O3 -fstack-protector` the sizes are 44/48/44/2600. The two differing words
per getter are exactly the `BL pthread_once` PLT relocation and the `.bss`
`R_ARM_REL32` addend, i.e. link-time values an ET_REL object cannot contain.
No byte-equality claim is made (`code_window_equal` is false for every window,
`source_matching_verified=False`), and `android_cpuInit` remains the only
unmatched member of the TU.

**Behavioural tests exist now.** `reconstruction/tests/recovered/libNOVA3_neon/cpu_features_test.c`
compiles the TU into itself and exercises the file-scope helpers over fixture
buffers: token matching (`vfpv3` must not match inside `vfpv3d16`, `(v6l)`
against a v7l Processor string, tab/blank separators, NULL), `parse_decimal`
(digit runs, limit truncation, leading zeros, non-digit), `cpulist_parse` /
`cpulist_set` / `cpulist_and` / `cpulist_count` (`0-3`, `2,4-7`, `0-31`,
indices ≥ 32 dropped, first-newline stop, malformed input) and
`extract_cpuinfo_field` (first occurrence wins, field must start a line,
`:`+space required, empty value, value at end of buffer, absent field). It is
wired into CMake/CTest (`cpu_features_behaviour`), into
`tools/check_all.sh`'s source-only audit list and into
`tools/setup/record_build_provenance.py`. `ctest --test-dir build/host` passes
14/14 and `bash tools/check_all.sh` exits 0.

Not claimed: behaviour of `android_cpuInit`/the getters end-to-end (`/proc` and
`/sys` paths are fixed in the source, so that test needs a fixture root), the
original compiler/flags/linker, and any full-`.so` equality.

## cpu-features.c identified from the symbol table; struct hypothesis retracted (2026-10-31)

The Premium symbol table settles what decompiler views could not. The
`.symtab` symbol group that begins with STT_FILE `cpu-features.c` (the first
object file in the link order) and ends at STT_FILE `app.cpp` contains exactly
these symbols:

| symbol | VA | size | type/bind |
| --- | --- | --- | --- |
| `android_getCpuCount` | 0x000d2f10 | 44 | FUNC GLOBAL |
| `android_getCpuFeatures` | 0x000d2f3c | 48 | FUNC GLOBAL |
| `android_getCpuFamily` | 0x000d2f6c | 44 | FUNC GLOBAL |
| `android_cpuInit` | 0x000d2f98 | 2632 | FUNC LOCAL |
| `g_once` | 0x00d96598 | 4 | OBJECT LOCAL |
| `g_cpuCount` | 0x00d9659c | 4 | OBJECT LOCAL |
| `g_cpuFeatures` | 0x00d965a0 | 8 | OBJECT LOCAL |
| `g_cpuFamily` | 0x00d965a8 | 4 | OBJECT LOCAL |

Consequences, all from the binary rather than inference:

1. **The earlier `CpuQueryState` struct model is wrong and is retracted.** The
   four state fields are four separate file-scope objects, and `.bss` itself
   begins at `g_once`, which is why all three getters reach `+4`, `+8`/`+12`
   and `+16` from one PC-relative anchor. The word-alignment argument that was
   used to justify the struct is therefore moot: `g_cpuFeatures` is an 8-byte
   object at an 8-byte-aligned address, and the reference still loads it as two
   `LDR`s. `android_cpuInit` stores it with `STRD`, so the same object is
   stored as a pair and loaded as two words in this build.
2. **The TU is the AOSP cpufeatures `cpu-features.c`, BSD-3-Clause.** Resolving
   every PC-relative literal in `android_cpuInit` shows the exact strings and
   paths: `/proc/cpuinfo`, `/sys/devices/system/cpu/present`,
   `/sys/devices/system/cpu/possible`, `CPU architecture`, `Processor`,
   `(v6l)`, `Features`, `vfpv3`, `vfpv3d16`, `neon`. The feature-bit OR values
   (2 = VFPv3, 6 = VFPv3|NEON, 1 = ARMv7, 8 = LDREX_STREX), the cpulist
   bitmask/popcount counting and the `parse_decimal` range loop all match the
   pre-r8c revision of that library.
3. **Revision bound.** No `/proc/self/auxv` access, no `android_setCpu`, no
   `g_inited` object and `Features`-based detection place it at the r7c
   generation (r7c added the present/possible count; r8c replaced Features
   parsing with auxv; r8d added `android_setCpu`).
4. **Reconstruction written:** `reconstruction/recovered/libNOVA3_neon/cpu_features.c`
   plus a local `cpu_features.h` (public API constants only). It compiles to
   `android_cpuInit` = 2600 B at `-O3 -fstack-protector` (reference 2632) and to
   the exact reference sizes for all three getters (44/48/44).
5. **Measured window result (r8e GCC 4.4.3, 6 configurations):** — *superseded
   by the section above: all three getters now match modulo the two relocation
   words once the getter definition order is reversed.* At the time of this
   measurement `android_getCpuFeatures` matched the reference instruction
   stream word for word modulo relocations and literal-pool words, while
   `android_getCpuCount` and `android_getCpuFamily` matched in size and shape
   but carried one differing word each: the offset immediate of the final load,
   because this compiler ordered the two int-sized objects the other way round
   (`g_cpuFamily@+4`, `g_cpuCount@+16` versus the reference's `g_cpuCount@+4`,
   `g_cpuFamily@+16`). Declaration order of the objects did not change that;
   getter *definition* order does. `android_cpuInit` is NOT matched (structural
   divergence, 32 bytes short).
6. **New tooling.** `tools/compare/literal_map.py` resolves every PC-relative
   literal in an A32 function to a symbol, section or string (this is what
   produced the string inventory above); `tools/compare/cpufeatures_pilot.py`
   compiles the TU under a compiler/flags matrix and reports per-window status,
   the `.bss` layout of the candidate and `source_matching_verified=False`.

Nothing here claims an original compiler, flags, linker or `.so` hash. The
getters moved from "shape-only, guessed ABI" to "recovered object layout plus
one measured instruction match", and `android_cpuInit` moved from NOT
RECONSTRUCTED to IMPLEMENTED (not TESTED for behaviour, not byte-matched).

## CPU-query thunk ABI correction and compiler-shape measurement (2026-10-31)

The older recovered block for Premium `0x000d2f10` used an invented
context-pointer ABI, so its mismatch was uninformative. A corrected candidate
now exists: `reconstruction/recovered/libNOVA3_neon/cpufeatures_queries.cpp`
implements `android_getCpuCount` (0x000d2f10, 44 B, A32),
`android_getCpuFeatures` (0x000d2f3c, 48 B) and `android_getCpuFamily`
(0x000d2f6c, 44 B) with the observed no-argument interface, the once-service
call, one shared 20-byte state object and the 4/8/4-byte field reads;
`android_cpuInit` (0x000d2f98, 2,632 B) stays declared-only and NOT
RECONSTRUCTED. Signedness of the 32-bit results and the struct grouping remain
INFERRED from the observed offsets and the public cpufeatures interface.

New tooling: `tools/compare/window_diff.py` (relocation-aware per-word diff
classifying EQUAL / RELOCATION_FIELD / LITERAL_POOL / ENCODING_DIFFERENCE) and
`tools/compare/cpuquery_pilot.py` (compiles the candidate plus a privately
generated matching-context variant with each discovered ARM compiler — NDK
clang 21, `arm-none-eabi-g++` 16.2 and the locally installed NDK r8e
`arm-linux-androideabi-g++` 4.4.3 — and runs both the strict
`object_compare` verdict and the per-word diff). The pilot is wired into
`tools/check_all.sh` behind `NOVA3_RESEARCH_ORACLES=1`.

Measured outcome — **no byte-equality candidate, no compiler identified**:

| Compiler / variant | getCpuCount (44 B) | getCpuFeatures (48 B) | getCpuFamily (44 B) |
| --- | --- | --- | --- |
| r8e GCC 4.4.3, committed candidate | 44 B, 6 eq / 3 reloc / 2 enc | 48 B, 7 eq / 3 reloc / 2 enc | 44 B, 6 eq / 3 reloc / 2 enc |
| r8e GCC 4.4.3, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 48 B, 9 eq / 2 reloc / 1 lit | 44 B, 8 eq / 2 reloc / 1 lit |
| NDK clang 21, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 44 B, size mismatch | 44 B, 8 eq / 2 reloc / 1 lit |
| arm-none-eabi-g++ 16.2, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 44 B, size mismatch | 44 B, 8 eq / 2 reloc / 1 lit |
| NDK clang 21 / GCC 16.2, committed candidate | `FUNCTION_BYTES_DIFFERENT` | size mismatch | `FUNCTION_BYTES_DIFFERENT` |

Measured conclusions, each backed by the report rather than assumed:

1. **No candidate is byte-equal.** Every compile leaves the `bl` displacement
   and the literal-pool words different — link-time values an ET_REL object
   cannot contain. `object_compare` never returns `LINKED_FUNCTION_WINDOW_EQUAL`.
2. **Instruction-stream agreement is source-driven, not compiler-identifying.**
   Once the state object is contiguous, word-aligned and the initializer binds
   locally, clang 21 and GCC 16.2 reproduce every instruction word of
   `getCpuCount` and `getCpuFamily`, and GCC 4.4.3 reproduces every word of all
   three. Only relocation and literal words differ. An earlier draft of this
   note attributed the shape to GCC alone — the pilot refutes that, so the
   claim is withdrawn.
3. **The 64-bit load is the discriminator, and it is an alignment effect.**
   The reference loads `features` with two `LDR`s (48-byte body). The local
   GCC 4.4.3 reproduces exactly that pair when the state aggregate is modelled
   as 4-byte aligned, and emits `LDRD` when it is 8-byte aligned; clang 21 and
   GCC 16.2 emit `LDRD` at either alignment. So the reference's state object was
   only word-aligned, which is INFERRED from the codegen and not independently
   known. A first draft read the two-`LDR` form as proof of an old compiler;
   that reading is withdrawn — the reference contains 1,789 `LDRD` and 1,576
   `STRD` instructions elsewhere, so its compiler clearly emits `LDRD`.
4. **The initializer is translation-unit local in the original.** The committed
   candidate declares `android_cpuInit` extern, so GCC 4.4.3 addresses it
   through a GOT-style indirect literal (`LDR` of a literal plus `LDR [pc, r3]`,
   two divergent words); the context variant with a file-local initializer
   removes exactly those two words. The original TU therefore bound the
   initializer locally, which this module cannot reproduce until the initializer
   itself is reconstructed.

Isolated compilation of the committed source still constant-folds under clang
21 (immediate-zero returns) because nothing in the TU writes the state; that is
why the pilot generates the labelled context variant privately.

Also repaired this session: an uncommitted edit had loosened
`tools/verify_source_only.py` so that only the first path component was checked
against the forbidden trees. That edit is required — the repository's own
committed source under `reconstruction/research/` contains a path component
named `research` — and it is not a weakening, because absolute paths and `..`
components are already rejected, so the first component is the only component
that can name a private root tree. It is restored with that reasoning recorded,
and the suite below was run against it.

## Compiler-aware historical revalidation (2026-10-28)

A read-only inventory over 25 existing `reconstruction/recovered/` C++ blocks
and 25 historical evidence JSON records completed successfully:
8 source blocks have explicit evidence-path associations, 17 have inferred
address associations, none lack an evidence record, and no stale recorded
source SHA was found. **Zero blocks are yet independently reverified as an
original ARM source-code match under the new compiler/objdiff standard.**
Two historical evidence records assert short billing-forwarder instruction
window matches; those claims have been retained for independent review.
Previously passing behavioural tests remain meaningful within their scope.

The new local tools for Cyrus/Premium donor hypotheses, ARM ET_REL code-window
comparison and sequential compiler-candidate experiments are source-only
research infrastructure, not a completed original-linker reconstruction.
Observed `GCC 4.4.3` strings are still an incomplete compiler fingerprint.
Original linker inputs, source-built JNI/Irrlicht integration and a playable
APK remain unverified. See `docs/DISCORD_IMPLEMENTATION.md` and
`docs/LEGACY_REVALIDATION.md`. The worker now preserves private streaming
AI logs and quarantines a timed-out provider after one bounded attempt.

Current source milestone: **named loading from complete Premium OBBs, checked BRES relocation, real inline mesh extraction, actual ETC1 texture pixel decoding, and game-specific ETC filename routing**. No playable game or Android APK exists; no original executable enters production targets.

| Measure | Actual coverage |
| --- | --- |
| Native inventory |76,344 function-symbol rows /73,318 distinct binary+address pairs; four input ELFs |
| DWARF |20 compiler-runtime CUs,276 type-tag records; no recovered game class DWARF |
| Ghidra |62 byte-validated scoped exports; scrambled-archive follow-up completed; persistent project and SQLite saved |
| Complete native mappings |3 original utility functions verified in bounded domains: ComputeCRC,glf CRC32 wrapper,unscramble;317 differential cases |
| Additional source |Checked seekable ZIP index/read regions, native logical filename/heap/mount rules, contiguous BRES byte relocation, inline Collada geometry, PVR header/mip reader, split RGB/alpha texture containers, ETC1 RGB8 decoder, and bounded mode4 texture filename rewrite |
| Package source verification |4,281 index entries and60 named reads match independent inventory/Python ZIP bytes;32 BRES images relocate; first complete archive→name→inflate→CRC→BRES bytes match original ARM golden |
| Mesh source verification |31 first geometries from32 sampled BDAE assets;37,522 vertices/25,289 triangles; positions/indices match native-backed field reader and stored bounds. Remaining sampled resource has0 geometries |
| Native-backed branch evidence |184 lookup/normalization cases+3 duplicate orders;4 BRES bases+7 header variants;12 Collada accessor cases+version;40 GL enum/argument slices;30 stream-base slices |
| Texture source verification |388 original ARM/source header predicate cases; all1,332 texture assets parsed,359 split bundles,1,691 planes,17,505 mip regions independently crosschecked. All17,505 mip regions decode (1,829,884,023 pixels);272 original ARM/source pixel cases match. Rendering pending |
| Texture routing |165 original ARM/source filename cases;6 synthetic ZIP fallback cases and2 actual mesh image names resolve and decode identically to explicit .etc reads |
| Nested SB data source verification |30 archives,4,885 entries,2,703,534,940 decoded bytes; all source header fields and payload size/CRC checks agree with independent Python reader. First mesh effect/shader references located; SDIR/19other GLA variants pending |
| Game functionality |No world/scene renderer, materials/shaders, gameplay, controls, audio, campaign, saves or multiplayer |

Source modules live under reconstruction/engine/; source-built audited zlib under reconstruction/third_party/. Production builds need no original APK or native game library. Actual assets remain data supplied locally. New tools include nova3_resource, nova3_mesh, nova3_package_probe, nova3_bres_relocate and nova3_texture. First real geometry artifact build/room_a.obj has12vertices/6triangles; build/room_a_preview.svg is an explicitly diagnostic projection, not a game frame or recovered materials.

Validation at this checkpoint: host CTest13/13; Python guard/archive13/13; sanitizer ASan/UBSan13/13; clean source-only release build/tests13/13; latest ARMv7 source build succeeded, no device run. Extended native oracles and source-only artifact checks pass (exit0). See .cache/logs/scrambled-final-{checks,clean,sanitizer,armv7}.log. Source guard rejects both actual original APKs and all4 ELF inputs. Final APK/DEX/linker/source provenance and standard-runtime source-build/license audit remain pending; current guard is a packaging regression check, not final certification.

Native-equivalence scopes are limited deliberately: ZIP logic does not reproduce COW string/allocator ABI; BRES covers contiguous byte transformation and rejects control-field aliases; mesh reader covers inline tag0/interleaved primary float streams/triangle lists/serialized buffers, excludes skin/controller and shared runtime ownership. Shader attribute roles and normalized byte semantics remain unverified. Exact evidence: research/architecture/RESOURCE_SLICE.md, research/assets/{BRES,COLLADA,GL_ENUMS}.md, research/elf/*verification.json.

Premium package/version mismatch remains: APK GAND.GloftN3LT105/v1.0.8e versus expansions ANMP.GloftN3HM1050/1070. Resource-byte compatibility has concrete samples, but whole-game edition parity is unverified. ADB has no attached device. rev.ng probes timed out with0 successful C output. Original hashes remain preserved; optional asset OBB copies live only in ignored .cache/assets.

Resource reader milestone committed as bbb7447. Texture decoder extension has passing tests and inspectable raw RGB/alpha PPM/PNG outputs under build/; these are texture data, not game frames. ETC1 tiny-mip clipping and invalid differential rejection are explicit source policies beyond the tested native branch.

ETC1 decoder checkpoint committed as d57b632. Texture request routing is now implemented for an explicit ETC profile; full device quality/normal/shader aliases remain incomplete. Nested SB data now has source header/payload recovery; first-mesh effect references found. Next dependencies are actual material/technique/semantic binding, original scene transforms, and a newly implemented renderer.

Source implementation accounting:13 owned engine .cpp modules, comprising3 complete native utility mappings and17 additional scoped address-to-source mappings; mapping counts are not whole subsystem completion. Ghidra62 scoped exports are decompilation evidence. No end-to-end game functionality exists. Current source milestone builds independently for host and Android ARMv7, with no original executable inputs.
