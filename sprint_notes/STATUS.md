<<<<<<< HEAD
# Sprint STATUS — Pass 12 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## What this pass did

Two things, both data-verified against the linked ELF:

1. **Recovered three additional glitch::video enum string tables** adjacent
   to the already-recovered E_PRIMITIVE_TYPE cluster in .data.rel.ro
   (0xd365fc..0xd36668).  Each getStringsInternal trampoline was
   disassembled, its PC-relative literal addend was extracted, and the
   resulting table base was read directly from the ELF file with xxd.
   Every string pointer was independently resolved to its .rodata target
   and read with dd.

2. **Recovered CVertexStreams::getStream** — both const (0x0077bd7c) and
   non-const (0x0077bdc0) overloads, which are byte-identical except for
   the const qualifier in the mangled name.  The function searches a
   sorted array of 16-byte SVertexStream entries by the 16-bit attribute
   at offset +8.  On no-match it falls back to the container's first
   embedded entry (this+0x10).

   A known discrepancy is NOTED: addOffset (0x0077be04) accesses the
   same offset +8 as a full 32-bit word (ldr/str), while getStream
   reads it as a 16-bit halfword (ldrh) and uses signed comparison
   (blt).  This implies a union or overlay at that field.  The
   reconstruction preserves the low half as the attribute discriminator
   and the high half as opaque data, so both access patterns coexist.

## New recovered / data-verified items

### Enum string tables (DATA-VERIFIED from .data.rel.ro)

| Enum | Helper VA | Table VA | Entries |
|------|-----------|----------|---------|
| E_BUFFER_TYPE | 0x0077bf44 | 0xd3664c | 6 (+ null) |
| E_BUFFER_USAGE | 0x0077bf54 | 0xd36634 | 4 (+ 2 nulls) |
| E_INDEX_TYPE | 0x0077bf64 | 0xd36624 | 3 (+ null) |
| E_PRIMITIVE_TYPE | 0x0077bf74 | 0xd365fc | 9 (+ null) — pass 10 |

### getStream functions (BEHAVIOURAL, host-tested)

| Function | ELF VA | Size | Result |
|----------|--------|------|--------|
| getStream (const) | 0x0077bd7c | 68 B | BEHAVIOURAL — 12 test scenarios |
| getStream (non-const) | 0x0077bdc0 | 68 B | (identical body to const) |

No ARM byte comparison performed (clang vs GCC codegen gap unchanged).

## Evidence

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Enum string tables: each pointer at the table VAs was read with xxd,
  each string at its .rodata target was read with dd.  See the
  documentation in `vertex_types.h` for exact addresses.
- getStream: disassembly compared via diff confirms const/non-const are
  byte-identical (differing only in target addresses within the function
  body).

## Host test results

CTest **28/28 passed** (was 26/26).

- `vertex_types`: 33 string-table checks (9 name-match + 9 out-of-range
  null + 15 trampoline-table checks including 3 null sentinels).
- `vertex_stream_getstream`: 24 pointer-match + value-match checks
  covering empty array, exact match, key-too-low (gap) fallback, key
  larger than all, signed negative-value sorting, const/non-const
  variants, and high-half preservation.

Negative control: modifying set_attrib to swap the halfword order causes
the sorted-search tests to fail at the "single match" check — proving
the test guards the reconstruction.

## Files changed (this pass)

New files:
1. `reconstruction/recovered/libNOVA3_neon/vertex_types.h` — NEW
   (E_INDEX_TYPE, E_BUFFER_USAGE, E_BUFFER_TYPE enums + name helpers)
2. `reconstruction/recovered/libNOVA3_neon/block_0077bf64.cpp` — NEW
   (getStringsInternal for E_INDEX_TYPE)
3. `reconstruction/recovered/libNOVA3_neon/block_0077bf54.cpp` — NEW
   (getStringsInternal for E_BUFFER_USAGE)
4. `reconstruction/recovered/libNOVA3_neon/block_0077bf44.cpp` — NEW
   (getStringsInternal for E_BUFFER_TYPE)
5. `reconstruction/recovered/libNOVA3_neon/vertex_stream_getstream.h` — NEW
   (both getStream overloads + SVertexStream layout update)
6. `reconstruction/recovered/libNOVA3_neon/block_0077bd7c.cpp` — NEW
   (const getStream trampoline)
7. `reconstruction/recovered/libNOVA3_neon/block_0077bdc0.cpp` — NEW
   (non-const getStream trampoline)
8. `reconstruction/tests/vertex_types_test.cpp` — NEW
9. `reconstruction/tests/vertex_stream_getstream_test.cpp` — NEW

Modified:
10. `CMakeLists.txt` — +2 test targets, vertex_streams block preserved

## Honest scope and limitations

- **0 new MATCHED_ARM.**  All new functions are BEHAVIOURAL or DATA-VERIFIED.
- The getStream SVertexStream layout has a known discrepancy with
  addOffset sharing offset +8 for different data types (32-bit vs 16-bit
  access).  This is documented but not resolved.
- The original struct field names (mData, mActive, mAttribIndex, etc.)
  are not recovered — our names are descriptive placeholders.
- No ARM object comparison attempted for getStream (same clang/GCC gap).
- The string tables are data, not code — no ARM instructions were
  compared.

## Known discrepancy: SVertexStream offset +8 dual access

addOffset at 0x77be04 uses `ldr r3, [r0, #24]` / `str r3, [r0, #24]`
(32-bit access at entry+8), while getStream uses `ldrh ip, [r2, #8]`
(16-bit access at entry+8).  The original likely uses a union where
the low 16 bits are the E_VERTEX_ATTRIBUTE (for search/sort) and the
full 32 bits form a data offset.  Our reconstruction preserves the high
16 bits when setting the attribute discriminator.

## Cumulative statistics

| Measure | Before pass 12 | After pass 12 |
|---------|---------------|---------------|
| Host CTest | 26/26 | 28/28 |
| MATCHED_ARM functions | 7 | 7 (unchanged) |
| COMPILED+BEHAVIOURAL | 5 | 7 (+getStream const, getStream non-const) |
| DATA-VERIFIED enum tables | 1 (E_PRIMITIVE_TYPE) | 4 (+E_INDEX_TYPE, E_BUFFER_USAGE, E_BUFFER_TYPE) |
| Total source-owned recovered functions | 21 | 23 |
| DATA-VERIFIED string tables (enums) | 1 | 4 |

## Next functions (priority order)

1. E_VERTEX_ATTRIBUTE / E_VERTEX_ATTRIBUTE_VALUE_TYPE string tables —
   these are also in the .data.rel.ro cluster (getStringsInternal at
   0x0070fe28, table at 0xd89020 in .data; and helper at 0x0070fe38,
   table at 0xd35664).  16+ entries each.
2. CVertexStreams::updateStates (0x0077bf40, wraps updateStatesInternal
   at 0x0077bea8 which was already exported) — thin wrapper.
3. SCompareType functor at 0x0077cb30 — the pointer stored at 0xd365f8
   (before E_PRIMITIVE_TYPE table) is a comparison predicate for
   E_BUFFER_TYPE entries.
4. ARM byte comparison for a small non-switch function once a viable
   GCC 4.4.3 toolchain is established (blocker unchanged).

## Current blockers (unchanged)

1. Historical GCC 4.4.3 ARM toolchain still not established.
2. Whole-`.so` SHA equality not established (not attempted).
3. Switch/jump-table functions show the largest codegen gap under clang.
4. ADB has no attached device; Android APK build not tested.
=======
# Sprint status — NOV-3 heartbeats

## 2026-11-01 (seventh run) — CMatrix4<float>::getTransposed recovered, window byte-equal

### Function under reconstruction
- `_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_`
  ELF VA 0x000eba84, 132 B, A32, STT_FUNC, STB_LOCAL (exact `.symtab` bounds)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`,
  SHA-256 `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (the pilot refuses a mismatched reference)

### Evidence summary
33 instructions, no frame, no call, no global: 16 `vldr` from r0, 16 `vstr` to
r1, `bx lr`. Destination slots `0x00..0x3c` each written once in ascending order;
destination slot k reads source slot `(k%4)*4+k/4`. The pilot proves the mapping
is self-inverse, the four diagonal slots are the only fixed points, the source is
never written, and r0/r1 are never reassigned. Row-major storage is INFERRED (a
column-major reading fits the same offsets). 27 call sites in 4 functions.

### Result (measured, not assumed)
- NDK r8e GCC 4.4.3 `-O3 -fvisibility=hidden`: candidate 132 B,
  33 EQUAL / 0 RELOCATION_FIELD / 0 encoding, `code_window_equal=True` — the
  project's first multi-instruction unmasked byte-equal window.
- `-O2`/`-Os`/`-O2 default`: 68 B rolled loops (`FUNCTION_SIZE_MISMATCH`), kept
  as the measured negative control; GCC 4.4.3 unrolls only at `-O3`.
- `source_matching_verified=False`: a byte-equal 132-byte window is not compiler
  identification, not a flag claim, not a TU recovery, not a whole-`.so` SHA.
- Behaviour: CTest `collada_matrix4_transpose_behaviour` (stub-free) and
  `collada_matrix4_transpose_link_path` (three matrix TUs, only getInverse
  stubbed) both pass; mutants (copy mapping, rotation mapping) both killed.

### Files changed
- `reconstruction/recovered/libNOVA3_neon/collada_matrix4_transpose.cpp` — new
- `reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_transpose_test.cpp` — new
- `reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_transpose_link_path_test.cpp` — new
- `tools/compare/collada_matrix4_transpose_pilot.py` — new
- `reconstruction/recovered/libNOVA3_neon/collada_skin_cache_deps.h` — getTransposed
  marked RECOVERED, getInverse still NOT RECONSTRUCTED
- `reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_copy_ctor_test.cpp` — comment
- `CMakeLists.txt`, `tools/check_all.sh`, `tools/setup/record_build_provenance.py`
- `docs/progress/{STATUS,NEXT,SESSION_LOG,HANDOFF}.md`, this file

### Commands actually run
- `.venv/bin/python tools/compare/collada_matrix4_transpose_pilot.py --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so` → rc=0
- `cmake --build build/host` → rc=0; `ctest --test-dir build/host` → 24/24 pass
- `bash tools/check_all.sh` → rc=0, 25 source-only audited artifacts, pytest 30 passed
- regression pilots re-run individually (trivial/prepareCache/multiply/ctor/copy ctor) → all rc=0

### Next ONE step
`glitch::core::CMatrix4<float>::getInverse(CMatrix4<float>&) const`
(0x000ebb08, 776 B) — the last dependency this group declares and does not own.

## 2026-11-01 (sixth run) — CMatrix4<float>(CMatrix4 const&, eConstructor) recovered

### Function under reconstruction
- `_ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE`
  ELF VA 0x00108c8c, 240 B, A32, STT_FUNC (exact `.symtab` bounds)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`,
  SHA-256 `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (the pilot refuses a mismatched reference)

### Evidence summary
`sub r2,r2,#1` / `cmp r2,#4` / `addls pc,pc,r2,lsl #2`; with ARM PC = insn+8 the
five table entries start at `addls+8`: value 1 copy (4 `ldm`/`stm` pairs = 64 B),
2 identity (`memset` stub 137 + four diagonal `vstr`), 3 `getTransposed(source,
this)` (0x000eba84), 4 `getInverse(source, this)` (0x000ebb08) with a zero-result
`memset`, 5 `getInverse` then transpose in place through a stack local built by
the plain ctor with r1=0; 0 and >= 6 fall through to the epilogue and write
nothing. 10 in-binary call sites: 8 x `r2=1`, 2 x `r2=4`.

### Files changed
- `reconstruction/recovered/libNOVA3_neon/collada_matrix4_copy_ctor.cpp` — new
- `reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_copy_ctor_test.cpp` — new
- `tools/compare/collada_matrix4_copy_ctor_pilot.py` — new
- `reconstruction/recovered/libNOVA3_neon/collada_skin_cache_deps.h` — enum values
  3/4/5, copy ctor and the two dependency declarations
- `CMakeLists.txt`, `tools/check_all.sh`, `tools/setup/record_build_provenance.py`
- `docs/progress/{STATUS,NEXT,SESSION_LOG,HANDOFF}.md`, this file

### Commands
```
.venv/bin/python tools/compare/collada_matrix4_copy_ctor_pilot.py \
  --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --out .cache/cpufeat/collada_copy_ctor/pilot.json      # 8.4 s, rc=0
cmake --build build/host && ctest --test-dir build/host   # 22/22
bash tools/check_all.sh                                   # rc=0, 23 artifacts
```

### Result
- Status: IMPLEMENTED + TESTED; window **NOT MATCHED** — 240/240 B, 28 EQUAL /
  7 RELOCATION_FIELD / 25 encoding, non-branch word multiset exactly equal
  (41/41); the difference is block placement (`4,2,3,1` vs `1,3,4,2`).
  `code_window_equal=False`, `source_matching_verified=False`.
- Four mutants run and killed; restored source passes.

### Next ONE source-recovery step
`glitch::core::CMatrix4<float>::getTransposed(CMatrix4<float>&) const` at
`0x000eba84` (132 B) — smallest remaining dependency of this constructor.

## 2026-11-01 (fifth run) — CMatrix4<float>(eConstructor) recovered, window matched modulo relocation

### Function under reconstruction
- `_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE`
  ELF VA 0x00108c54, 56 B, A32, STT_FUNC (exact `.symtab` bounds)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`,
  SHA-256 `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (checked by the pilot before it reads anything)

### Evidence summary
`cmp r1,#1` + unsigned `bls` to the epilogue => enumerator values 0 and 1 write
nothing; every value >= 2 runs `memset(this,0,0x40)` (`0xd2610` = `.plt` stub 137
-> `.rel.plt` -> `.dynsym` `memset`) and four `1.0f` stores at byte offsets 0x3c,
0x00, 0x14, 0x28, i.e. a row-major 4x4 identity; `this` is returned in r0.
74 in-binary call sites: 54 x `r1=2`, 6 x `r1=0`, 14 without a local literal.

### Files changed
- `reconstruction/recovered/libNOVA3_neon/collada_matrix4_ctor.cpp` — new
- `reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_ctor_test.cpp` — new
- `tools/compare/collada_matrix4_ctor_pilot.py` — new
- `CMakeLists.txt`, `tools/check_all.sh`, `tools/setup/record_build_provenance.py`
- `docs/progress/{STATUS,NEXT,SESSION_LOG,HANDOFF}.md`

### Commands
```
.venv/bin/python tools/compare/collada_matrix4_ctor_pilot.py \
  --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --out .cache/cm4/pilot.json
cmake --build build/host && ctest --test-dir build/host
bash tools/check_all.sh
```

### Result
- Status: IMPLEMENTED + TESTED; instruction window MATCHED modulo the one
  relocation word (`code_window_equal=False`, `source_matching_verified=False`).
- ctest 21/21; `tools/check_all.sh` rc=0 with 21 audited artifacts.

### Next ONE source-recovery step
`CMatrix4<float>::CMatrix4(CMatrix4<float> const&, eConstructor)` at
`0x00108c8c` (240 B) — the only place that can pin enumerator values 3+; the
plain constructor maps everything >= 2 to the identity.

## 2026-11-01 (third run) — prepareCache recovered, tested, measured not-matched

### Function under reconstruction
- `_ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv`
  ELF VA 0x0073f0a4, 228 B, A32, STT_FUNC, STB_LOCAL
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`,
  SHA-256 `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (both new tools refuse a mismatched reference)

### Evidence summary
Guard on bit 0 of `*(this+0x08)`; direct call to
`ISkinTechnique::preparePtrCache()`; `CMatrix4<float>(2)` built in place and
passed to `vector<CMatrix4<float>>::resize(*(this+0x04)+0x4c, value)`;
`vector<CMatrix4<float> const*>::size()` as the loop bound; per iterate
`destination[i] = *ptrs[i] * smat[i]` via `operator*<float,SMatrix>` with the
SMatrix at stride 0x40; bit 0 cleared at the end. All five call targets resolved
by address from `.symtab`; 12/12 structure checks re-derived by the pilot.

### Files changed
- `reconstruction/recovered/libNOVA3_neon/collada_skin_technique.cpp` —
  prepareCache body with instruction evidence
- `reconstruction/recovered/libNOVA3_neon/collada_skin_technique.h` — observed
  layout (+0x04 bind inputs, +0x08 matrix cache, +0x0c node) and test-only
  friend hook
- `reconstruction/recovered/libNOVA3_neon/collada_skin_cache_deps.h` — new:
  layout views, CMatrix4/SMatrix declarations, five extern declarations with the
  reference mangled names
- `reconstruction/tests/recovered/libNOVA3_neon/collada_skin_prepare_cache_test.cpp`
  — new CTest (guard, call order, counts, operands, destinations, idempotence)
- `reconstruction/tests/recovered/libNOVA3_neon/collada_skin_cache_stubs.cpp` —
  new: abort-on-call stand-ins so the leaf test links the same TU
- `tools/compare/collada_skin_prepare_cache_pilot.py` — new pilot
- `CMakeLists.txt`, `tools/check_all.sh`, `tools/setup/record_build_provenance.py`

### Commands and results
- `cmake --build build/host` — clean; `ctest --test-dir build/host` — **19/19**
- `bash tools/check_all.sh` — **rc=0**, 20 audited artifacts, pytest 30 passed
- `.venv/bin/python tools/compare/collada_skin_prepare_cache_pilot.py ...` —
  exit 0, `structure_agrees_with_source=True`, trivial leaves byte-equal 4/4
- window: 228 B reference vs 224 B candidate at -O2/-O3 (216 B at -Os);
  17 EQUAL / 5 RELOCATION_FIELD / 34 encoding; `source_matching_verified=False`
- negative controls: guard removed -> mutant crashes in a dependency stub;
  `&= ~2u` -> "only bit 0 should have been cleared"; stride `i` ->
  "multiply did not receive the SMatrix at i*0x40"; restored passes

### Result
prepareCache = IMPLEMENTED + TESTED, **NOT MATCHED**. The 4-byte gap is one
`add` (destination + i*64) scheduled before the call in the reference and after
it here; no semantic difference found, not chased. The class layout change did
not disturb the three byte-equal leaves.

### Next ONE step
Recover `glitch::core::operator*<float, collada::SMatrix>` at 0x006e7e60
(564 B), reading the `CMatrix4`/`SMatrix` element layouts out of its own
addressing first; record and stop if the layout cannot be pinned. See
`docs/progress/NEXT.md` step 1.

## 2026-11-01 (second run) — the "NEON gate in init()" question is answered NO, and three CSoftwareSkinTechnique leaves are byte-equal source

### Action taken
Two bounded actions, both evidence-first:

1. **Answered the open NEXT.md question about the NEON gate in
   `CSoftwareSkinTechnique::init`** — and the answer is that there is no gate
   there. `init` only lazily fills a TU-local 8-byte `cpuFeatures` cache; the
   engine's actual NEON gate on that cache lives in `CSoftwareSkinTechnique::skin`
   and is a `(cpuFeatures & 4) != 0` test. Both call sites sit inside
   unreconstructed functions (1028 B and 4256 B), so per the queue's own
   contingency the slice is recorded and not chased.
2. **Recovered, tested and byte-matched three 8-byte methods** of the same class
   (`checkAvailability`, `needOutputBuffer`, `attach`). These are the project's
   first *unmasked* per-function byte equalities: no relocation word differs,
   because the windows contain none.

### Function(s) under reconstruction
- `_ZN6glitch7collada6detail22CSoftwareSkinTechnique17checkAvailabilityERKNS_5video10STechniqueEj`
  ELF VA 0x00710b50, 8 B, A32, STT_FUNC, STB_LOCAL
- `_ZNK...16needOutputBufferEv` ELF VA 0x00710b48, 8 B
- `_ZN...6attachEPNS_5scene10ISceneNodeE` ELF VA 0x00710b58, 8 B
- Analysed, NOT reconstructed: `init` 0x00739630 (1028 B), `skin` 0x0072ad90
  (4256 B)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`,
  SHA-256 `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (re-verified by both new tools, which refuse a mismatched reference)

### Files read
- `docs/progress/{STATUS,NEXT}.md`, `sprint_notes/STATUS.md`, `CMakeLists.txt`
- the reference ELF's `.text` / `.bss` / `.symtab` (read only, unchanged)
- `tools/compare/{window_diff.py,driver_callback_init_pilot.py}`,
  `tools/check_all.sh`, `tools/setup/record_build_provenance.py`

### Files changed
- `tools/compare/skin_cpu_cache.py` — new: re-derives the init() lazy-fill
  structure (9 checks), the cache's consumer set (2) and the skin() mask test
  (4 checks) from the reference bytes; metadata only.
- `tools/compare/collada_skin_trivial_pilot.py` — new: compiles the recovered TU
  with NDK r8e GCC 4.4.3 at `-O2`/`-O3`/`-Os` and compares each of the three
  symbol windows byte-for-byte.
- `reconstruction/recovered/libNOVA3_neon/collada_skin_technique.{h,cpp}` — new
  recovered leaves + minimal class declarations; every unobserved member and the
  whole vtable are explicitly not reconstructed.
- `reconstruction/tests/recovered/libNOVA3_neon/collada_skin_technique_test.cpp`
  — new CTest `collada_skin_technique_behaviour`.
- `CMakeLists.txt`, `tools/check_all.sh`,
  `tools/setup/record_build_provenance.py` — target, audit entry, provenance.
- `docs/progress/{STATUS,NEXT,SESSION_LOG}.md`, `sprint_notes/STATUS.md`.

### Commands and actual results
```
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug   -> exit 0
cmake --build build/host                                     -> 85/85, clean
ctest --test-dir build/host                                  -> 18/18 pass
.venv/bin/python tools/compare/skin_cpu_cache.py --reference ...
  -> exit 0; 13/13 checks reproduce
     anchor 0x00da2950 = `cpuFeatures`, STB_LOCAL, 8 B
     pair load 8 B; orrs zero-test; beq -> 0x00739764 == getter 0x000d2f3c
     strd back at 0x00739768; rejoins fall-through 0x00739664
     pair_not_read_after_join: r2 written @0x739674, r3 written @0x73966c
     consumers: 2 (init 0x739630, skin 0x72ad90)
     gate: 0x72b280 ldrd; 0x72b288 and hi,sb (sb=#0 @0x72b268); 0x72b29c
           and lo,#4; 0x72b2a0 orrs; 0x72b2b4 bne 0x72bcd4
.venv/bin/python tools/compare/collada_skin_trivial_pilot.py --reference ...
  -> exit 0; 3/3 configs, 3/3 symbols, 8/8 bytes equal each
     code_window_equal=True (no relocation in any window)
ctest negative controls (source mutated, then restored)
  -> return false      => "FAIL: needOutputBuffer returned false" (abort 134)
     store to wrong field => "FAIL: attach changed the wrong number of bytes"
     restored            => passes
bash tools/check_all.sh                                      -> exit 0
  19 audited artifacts source-only clean; tooling pytest 30 passed
NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh             -> exit 0 (full chain)
```

### Evidence summary
- init() is not a gate: the loaded pair feeds only an `orrs` zero test, is
  overwritten before any read after the join, and is written back only on the
  first call. The object it caches is named `cpuFeatures` in `.symtab`.
- The only other consumer is `skin`, which reads the same static with `ldrd` and
  tests bit 2 (mask `#4`, high half masked with a register set to `#0`) before
  branching to 0x72bcd4. Bit 2 = NEON is the same bit `DetectNeon` extracts.
- Both consumers are inside functions far larger than the slice; no separable
  unit exists at either call site, so no source was invented for them.
- The three 8-byte methods are instruction-for-instruction equal to the
  reference under the identified toolchain; the equalities are scoped to those
  three windows and say nothing about the other ~39k functions or the `.so` SHA.

### Disposition
`checkAvailability`, `needOutputBuffer`, `attach` = IMPLEMENTED + TESTED +
instruction-window MATCHED (exact bytes, 3 configs).
`init` and `skin` cpu-features call sites = UNDERSTOOD, still NOT RECONSTRUCTED.
`android_cpuInit` remains 2648 B vs 2632 B, unexplained, not chased.

### Next ONE source-recovery step
`CSoftwareSkinTechnique::prepareCache` (ELF VA 0x0073f0a4, 228 B) — small enough
to decode and recover whole, same class, and reachable from the recovered
`init` evidence; decode → source → pilot → CTest.

---

## 2026-11-01 — DriverCallbackInterface::Init recovered, tested and measured

### Action taken
Recovered, tested and measured the next bounded source slice after `DetectNeon`:
`vox::DriverCallbackInterface::Init(void*)`, the only in-binary caller of the
NEON probe. The driver-callback chain now has two source-owned links.

### Function under reconstruction
- Symbol: `_ZN3vox23DriverCallbackInterface4InitEPv`
- ELF VA: 0x009d53f4, size 48 B, A32, STT_FUNC (not stripped)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`
- Reference SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (re-verified by the pilot, which refuses a mismatched reference)

### Files read
- `AGENTS.md`, `docs/progress/{STATUS,NEXT,SESSION_LOG}.md`, `sprint_notes/STATUS.md`
- `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so` (.text, .bss,
  .symtab, .dynsym — read only, unchanged)
- `reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp`
- `tools/compare/{detect_neon_pilot.py,window_diff.py}`, `CMakeLists.txt`,
  `tools/check_all.sh`, `tools/setup/record_build_provenance.py`

### Files changed
- `reconstruction/recovered/libNOVA3_neon/driver_callback.h` — new shared
  declarations; DetectNeon declared static on caller-side evidence; static
  objects sized from the symtab; `BufferRef` shape marked INFERRED with the
  evidence address.
- `reconstruction/recovered/libNOVA3_neon/driver_callback_init.cpp` — new
  recovered `Init`, with the resolved PIC base, the store-to-symbol mapping and
  every underdetermined point labelled.
- `reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp` —
  inline class declaration replaced by the shared header; comment corrected.
- `reconstruction/tests/recovered/libNOVA3_neon/driver_callback_init_test.cpp` —
  new behaviour test (CTest `driver_callback_init_behaviour`).
- `tools/compare/driver_callback_init_pilot.py` — new structure + window pilot.
- `CMakeLists.txt`, `tools/check_all.sh`,
  `tools/setup/record_build_provenance.py` — test, audit entry, provenance.
- `docs/progress/{STATUS,NEXT,SESSION_LOG}.md` — milestone, queue, log.

### Commands and actual results
```
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug   -> exit 0
cmake --build build/host                                     -> clean
ctest --test-dir build/host                                  -> 17/17 pass
.venv/bin/python tools/compare/driver_callback_init_pilot.py \
    --reference analysis/.../libNOVA3_neon.so
  -> exit 0; structure agrees: True
     PIC base 0x00db9d90 = s_listenerParameters
     4 word stores + 1 byte store; r0/r1 reads before the call = 0/0
     6/12 configs shape-equal with constant anchor delta 0x34
       (-fvisibility=hidden 6/6 at 48 B; -fvisibility=default 6/6 at 72 B,
        FUNCTION_SIZE_MISMATCH)
     code_window_equal=False, source_matching_verified=False
.venv/bin/python tools/compare/detect_neon_pilot.py --reference ...
  -> exit 0; 6/6 ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION, 32 B
bash tools/check_all.sh                                      -> exit 0
  (18 audited artifacts source-only clean; tooling pytest 30 passed)
negative control: one store deleted from the recovered source
  -> test aborts "FAIL: Init did not clear both buffer references"
     source restored, same test passes
```
`NOVA3_RESEARCH_ORACLES=1` is not run here: this worker worktree has no private
`research/assets/` OBB indices, a pre-existing environment gap.

### Evidence summary
- The PIC base is `s_listenerParameters` and the five reference stores land
  exactly on `m_sWorkBuffer` (+0, +4), `m_sMixingBuffer` (+0, +4) and the 1-byte
  `enableNeon`: a symtab fact, not an inference from the source comment.
- Reference 48 B; candidate 48 B in every non-preemptible configuration with the
  same instruction sequence and store order. Only the two relocation words and
  one constant 0x34 anchor offset differ, and the offset is explained by the
  original TU's anchor starting 0x34 bytes earlier (s_listenerParameters 0x30 +
  s_enable3dSimulation 4).
- Preemptible statics give a 72-byte GOT-style body, so visibility is measured
  as part of the matrix rather than tuned to fit.
- `BufferRef` field roles INFERRED from `GetWorkBuffer` (0x009d4828):
  +0 compares signed against the requested count and receives it, +4 is passed
  to `VoxFree(void*)` and replaced by `VoxAlloc(unsigned)`.

### Disposition
`Init` = IMPLEMENTED + TESTED (behaviour); NOT MATCHED (shape and store order
agree modulo relocation words and one constant anchor offset). Still NOT
RECONSTRUCTED: `android_cpuInit` (2648 B vs 2632 B residual, no longer chased)
and `glitch::collada::detail::CSoftwareSkinTechnique::init`.

### Next ONE source-recovery step
Recover the NEON gate inside `CSoftwareSkinTechnique::init` (0x00739630,
1028 B), bounded to the single direct `android_getCpuFeatures` call at
0x00739764 and the branch that consumes it — decode → source → pilot → CTest, or
record that the gate has no separable behaviour and stop.

---

## Previous heartbeat (2026-10-31)

## Action taken
Completed and verified the in-flight NEON call-site work: the recovered NEON
capability probe `DetectNeon` was finalised, its behaviour test wired into
CTest, its code window re-measured, and the engine's entire cpu-features call
surface enumerated.

## Function under reconstruction
- Symbol: `_ZN3vox29DriverCallbackSourceInterface10DetectNeonEv`
- ELF VA: 0x009dad14, size 32 B, A32, STT_FUNC (STB_LOCAL)
- Reference: `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`
- Reference SHA-256:
  `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
  (re-verified in this run with `sha256sum`)

## Files read
- `AGENTS.md`, `docs/progress/NEXT.md`, `docs/progress/STATUS.md`,
  `docs/progress/HANDOFF.md` (tail), `docs/progress/SESSION_LOG.md` (tail)
- `reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp`
- `reconstruction/tests/recovered/libNOVA3_neon/driver_callback_detect_neon_test.cpp`
- `tools/compare/detect_neon_pilot.py`, `tools/compare/cpuquery_callsites.py`
- `tools/compare/window_diff.py`, `tools/check_all.sh`,
  `tools/setup/record_build_provenance.py`, `CMakeLists.txt` (diff)

## Files changed
- `tools/compare/detect_neon_pilot.py` — fixed reference plumbing: the draft
  passed the extracted 32-byte window to `window_diff.diff_window`, which bounds
  the function itself from the whole ELF and hashes the whole file, so it always
  raised `CompareError: reference SHA-256 mismatch`. `load_reference` now returns
  `(elf_bytes, window)`; `diff_window` gets the ELF. Docstring example path typo
  fixed.
- `tools/check_all.sh` — `detect_neon_behaviour_test` added to the audited
  artifact list; `detect_neon_pilot.py` and `cpuquery_callsites.py` added to the
  oracle block.
- `tools/setup/record_build_provenance.py` — new artifact plus its own
  `link_inputs` (recovered TU, header, test).
- `docs/progress/{STATUS,NEXT,HANDOFF,SESSION_LOG}.md` — new milestone section,
  re-prioritised queue, resume directions, appended log.
- `CMakeLists.txt`, the recovered TU, its test, and both compare tools were
  already present but uncommitted from the interrupted run; kept as-is apart from
  the pilot fix.

## Commands and results (actually run)
```
sha256sum analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
  -> 84075223…baaf                                        (matches expected)
cmake -S . -B build/host                                  -> exit 0
cmake --build build/host -j4                              -> 81/81 targets
ctest --test-dir build/host --output-on-failure           -> 16/16 pass
.venv/bin/python tools/compare/detect_neon_pilot.py \
    --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
  -> exit 0; structure agrees: True; 6/6 configs
     ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION, 32 B, 6 EQUAL + 2 RELOC
     + 0 ENC; code_window_equal=False
.venv/bin/python tools/compare/cpuquery_callsites.py --reference … \
    --also 0x9dad14:DetectNeon --out .cache/cpufeat/cpuquery_callsites.json
  -> exit 0; 39,108 functions decoded, 0 undecodable, 4 direct hits
bash tools/check_all.sh                                   -> exit 0
  (17 audited artifacts source-only clean)
```
`NOVA3_RESEARCH_ORACLES=1` still stops in `tools/compare/zip_headers.py`: this
worker worktree's private `research/assets/` has no OBB indices. Pre-existing
environment gap, unchanged by this run.

## Evidence summary
- DetectNeon window: 32/32 B; 6 EQUAL + 2 RELOCATION_FIELD + 0
  ENCODING_DIFFERENCE in all six NDK r8e GCC 4.4.3 rows; the two differing words
  are the two `BL` displacements (link-time only). `code_window_equal=False`,
  `source_matching_verified=False`.
- Structure (re-derived from reference bytes, not from the source comment):
  call targets `0x000d2f6c` then `0x000d2f3c`; `cmp #1`; `ubfx` lsb 2 width 1;
  conditional return between the calls; incoming `this` never read.
- Call surface of the recovered cpu-features TU in the engine: exactly three
  direct call sites, no PLT route.
  - `0x00739764` → `android_getCpuFeatures` in
    `glitch::collada::detail::CSoftwareSkinTechnique::init` (0x00739630, 1028 B)
  - `0x009d5414` → `DetectNeon` in `vox::DriverCallbackInterface::Init(void*)`
    (0x009d53f4, 48 B)
  - `0x009dad18` / `0x009dad28` → both getters inside DetectNeon itself

## Disposition
`DetectNeon` = IMPLEMENTED + TESTED (behaviour); NOT MATCHED (no byte equality,
relocation-masked only). Call-site mapping = VERIFIED metadata from the
reference ELF. Still NOT RECONSTRUCTED: `android_cpuInit` (2648 B vs 2632 B,
layout/inlining residual), `DriverCallbackInterface::Init`, and
`CSoftwareSkinTechnique::init`.

## Next ONE source-recovery step
Recover `vox::DriverCallbackInterface::Init` at ELF VA `0x009d53f4` (48 B) — the
only in-binary caller of DetectNeon. Decode it with Capstone, write the source
with every constant/field labelled observed-or-INFERRED, add
`tools/compare/driver_callback_init_pilot.py` and a CTest behaviour target, and
record the disposition. Do not invent a type for its `void*` parameter.
>>>>>>> c840ee2
