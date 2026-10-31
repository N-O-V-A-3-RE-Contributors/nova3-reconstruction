# Sprint status — NOV-3 heartbeats

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
