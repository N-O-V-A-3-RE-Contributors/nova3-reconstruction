# Sprint status — NOV-3 heartbeats

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
