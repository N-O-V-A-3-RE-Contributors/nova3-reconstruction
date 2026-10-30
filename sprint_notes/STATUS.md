# Sprint STATUS — Pass 9 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## Summary

This pass recovered a coherent cluster of three `glitch::video` engine
primitive-counting functions, fully behavioural-tested against literal
disassembly-derived truth tables, and ARM-compared under the pinned clang-21
profile. No byte-window match (consistent with the historical GCC 4.4.3
blocker documented since pass 6).

## Recovered functions (this pass)

All three are A32 (ARM), verified from `.symtab` and linked-ELF disassembly.

| Function | ELF VA | Size | Mangled symbol | Result |
|----------|--------|------|----------------|--------|
| getPrimitiveCount | 0x0077bf84 | 152 B | `_ZN6glitch5video17getPrimitiveCountENS0_16E_PRIMITIVE_TYPEEj` | BEHAVIOURAL; cand 144 vs ref 152 |
| getTriangleCount  | 0x0077c1e0 | 112 B | `_ZN6glitch5video16getTriangleCountENS0_16E_PRIMITIVE_TYPEEj` | BEHAVIOURAL; same 112 B size, bytes differ |
| getIndexCount     | 0x0077c250 | 124 B | `_ZN6glitch5video13getIndexCountENS0_16E_PRIMITIVE_TYPEEj` | BEHAVIOURAL; cand 112 vs ref 124 |

## Recovered behaviour

Each function is a 9-entry run-time switch (`cmp #8 / addls pc,pc,rN,lsl#2`
jump table; values > 8 fall through to `return 0`). Recovered enum semantics
(values 0..8) cross-checked across all three functions:

| value | primitiveCount(N) | indexCount(P) | triangleCount(N) | name |
|-------|-------------------|---------------|------------------|------|
| 0 | N | P | 0 | Points (string "Points" confirmed at rodata 0xcaf4c0) |
| 1 | max(N-1,0) | P+1 | 0 | Line Strip |
| 2 | N | P | 0 | Line Loop (INFERRED) |
| 3 | N/3 | 0 (quirk) | 0 (quirk) | legacy Triangles (INFERRED) |
| 4 | max(N-2,0) | P+2 | max(N-2,0) | Triangle Strip |
| 5 | max(N-2,0) | P+2 | max(N-2,0) | Triangle Fan |
| 6 | N/3 | 3P | N/3 | Triangle List |
| 7 | max((N-2)/2,0) | 2P+2 | 2*max((N-2)/2,0) | Quad Strip |
| 8 | N/4 | 4P | 2*floor(N/4) | Quad List |

Original `/3` and `/4` cases compile as magic-constant multiplies
(`0xaaaaaaab >> 33`) which equal integer floor division for unsigned inputs;
the recovery uses portable `/` and documents this. Enum value 3 is an
observed inconsistency (getPrimitiveCount divides by 3 yet getIndexCount and
getTriangleCount return 0); reproduced verbatim, not normalized.

## Evidence

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Disassembled with arm-none-eabi-objdump at the three VAs above; full
  semantics transcribed into `reconstruction/recovered/libNOVA3_neon/primitive_types.h`.
- Compiler: NDK r29 clang-21 (`--target=armv7a-linux-androideabi21 -marm -O2
  -fomit-frame-pointer -fPIC -std=c++17`). Comparison matrices in
  `.cache/om-getPrimitiveCount|getTriangleCount|getIndexCount/matrix.json`.

## Host test results

CTest **24/24 passed** (23 prior + 1 new `primitive_count`).

New test `primitive_count` (243 truth-table checks + 15 out-of-range +
4 floor/saturate edge cases):
- Literal 9x9 expected tables for getPrimitiveCount / getTriangleCount /
  getIndexCount, values transcribed directly from disassembly (not re-derived
  by calling recovered logic).
- Out-of-range discriminant -> 0 for all three functions.
- Floor-division and wrap-saturation edge cases.
- Negative control executed: flipping one table cell made the test fail with
  a precise message (exit 1), confirming the test actually guards the
  reconstruction.

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/primitive_types.h` — NEW
   (enum + 3 inline functions with INFERRED names and documented quirk)
2. `reconstruction/recovered/libNOVA3_neon/block_0077bf84.cpp` — NEW (getPrimitiveCount)
3. `reconstruction/recovered/libNOVA3_neon/block_0077c1e0.cpp` — NEW (getTriangleCount)
4. `reconstruction/recovered/libNOVA3_neon/block_0077c250.cpp` — NEW (getIndexCount)
5. `reconstruction/tests/primitive_count_test.cpp` — NEW (literal truth tables)
6. `CMakeLists.txt` — +primitive_count test target

## Honest scope and limitations

- **0 new MATCHED_ARM this pass.** All three differ in bytes; getTriangleCount
  is the closest (identical 112-byte size) but differs in jump-table scheme
  (clang PIC LUT `ldr [r2,r0,lsl#2]` vs GCC relative branch table `addls
  pc,pc,rN,lsl#2`) and saturation idiom (clang `subs/movcc` vs GCC `sub/bic`).
- Enum symbolic names for values 2 and 3 are INFERRED from arithmetic; only
  value 0 ("Points") is confirmed by a surviving string. Value 3's anomaly is
  an observed original quirk, not a reconstruction bug.
- Behavioural equivalence is independently host-tested (literal truth tables,
  not self-referential).

## Cumulative statistics

| Measure | Before pass 9 | After pass 9 |
|---------|--------------|--------------|
| Host CTest | 23/23 | 24/24 |
| MATCHED_ARM functions | 7 | 7 (unchanged) |
| COMPILED+BEHAVIOURAL (bytes differ) | 8 | 11 (+3) |
| Total source-owned funcs (pass-6 pipeline) | 15 | 18 |

## Next functions (priority order)

1. `getStringsInternal(E_PRIMITIVE_TYPE)` (0x0077bf74) + the enum->string
   lookup it feeds: recovering the actual E_PRIMITIVE_TYPE string names would
   upgrade values 2/3 from INFERRED to VERIFIED.
2. `CVertexStreams::getActiveAttributeMask` (0x0077bd28, 84 B) and
   `addOffset(int)` (0x0077be04, 64 B): small, symbol-bearing, clear semantics.
3. `CFPSCounter::CFPSCounter` (0x0077bb7c, 56 B): field-zeroing constructor.
4. Continue the existing `computeMipmapSizeInBytes(6-arg)` / getSizeInBytes
   (unsigned char) ARM comparison (0x0077d4a8) once sub-call relocations are
   handled.

## Current blockers (unchanged)

1. Historical GCC 4.4.3 ARM toolchain still not established; clang-21 emits
   different jump-table and conditional-move idioms for switch-based code.
2. Whole-`.so` SHA equality not established (not attempted).
3. Switch/jump-table functions show the largest codegen gap under clang; the
   relative-branch (GCC) vs PIC-LUT (clang) split implies the original was
   built without strict PIC jump tables for local switches.