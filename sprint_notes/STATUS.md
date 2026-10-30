# Sprint STATUS — Pass 10 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## What this pass did

Two things, both verified against the linked ELF before any change:

1. **Corrected a pass-9 reconstruction error in `getPrimitiveCount`.**
   Pass 9 recorded enum value 3 as `indexCount / 3` and named it
   "legacy Triangles". The linked ARM jump table says otherwise: case 3
   (0x77bf9c) branches to 0x77bfcc = `lsr r1,r1,#1`, i.e. `N / 2`, while
   case 6 ("Triangles") is the only case that uses the `umull` divide-by-3
   chain. Value 3 is genuinely **"Lines"** (GL_LINES): N/2 primitives.

2. **Verified all nine `E_PRIMITIVE_TYPE` names from the original string
   table.** `getStringsInternal(E_PRIMITIVE_TYPE*)` (0x0077bf74, 16 B) loads
   literal `0x005ba67c` and returns `0x77bf80 + 0x005ba67c = 0x00d365fc`,
   the base of a 9-entry `const char* const[]` in `.data.rel.ro`. The nine
   relocated pointers name the enum 0..8 as: Points, Line Strip, Line Loop,
   Lines, Triangle Strip, Triangle Fan, Triangles, Quad Strip, Quads. This
   *confirms* the previously INFERRED values 2 and 3 and corrects the pass-9
   labels for 6 ("Triangle List" -> "Triangles") and 8 ("Quad List" ->
   "Quads"). The pass-9 note that "Points" sits at rodata 0xcaf4c0 was
   accidentally right in address; the relocation/pointer link was missing and
   is now established (R_ARM_RELATIVE at 0xd365fc, addend 0xcaf4c0).

## Recovered / corrected functions

| Function | ELF VA | Size | Result |
|----------|--------|------|--------|
| getPrimitiveCount | 0x0077bf84 | 152 B | BEHAVIOURAL; now cand 152 B == ref 152 B (was 144 vs 152) — FUNCTION_BYTES_DIFFERENT |
| getStringsInternal(E_PRIMITIVE_TYPE*) | 0x0077bf74 | 16 B | DATA-VERIFIED string table (9 names) |
| getTriangleCount | 0x0077c1e0 | 112 B | unchanged (enum names corrected) |
| getIndexCount | 0x0077c250 | 124 B | unchanged ("Lines" -> 0 quirk re-confirmed) |

## Corrected behaviour table (verified against disassembly)

| value | getPrimitiveCount | getIndexCount | getTriangleCount | name (verified) |
|-------|-------------------|---------------|------------------|-----------------|
| 0 | N | P | 0 | Points |
| 1 | max(N-1,0) | P+1 | 0 | Line Strip |
| 2 | N | P | 0 | Line Loop |
| 3 | **N/2** | 0 (quirk) | 0 | **Lines** |
| 4 | max(N-2,0) | P+2 | max(N-2,0) | Triangle Strip |
| 5 | max(N-2,0) | P+2 | max(N-2,0) | Triangle Fan |
| 6 | N/3 | 3P | N/3 | Triangles |
| 7 | max((N-2)/2,0) | 2P+2 | 2*max((N-2)/2,0) | Quad Strip |
| 8 | N/4 | 4P | 2*floor(N/4) | Quads |
| else | 0 | 0 | 0 | (default) |

The "Lines" asymmetry (N/2 primitives, but 0 index/triangle counts) is an
observed original quirk reproduced verbatim, now with its true name attached.

## Evidence

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Disassembly: `arm-none-eabi-objdump -d` at the four VAs; see
  `sprint_notes/evidence_pass10.json` for the jump-table trace and the full
  string-table entry list.
- Compiler: NDK r29 clang-21 (`--target=armv7a-linux-androideabi21 -marm -O2
  -fomit-frame-pointer -fPIC -std=c++17`). New comparison report at
  `.cache/om-getPrimitiveCount-v2/matrix.json`.

## Host test results

CTest **24/24 passed**. `primitive_count` now carries 243 literal truth-table
checks + out-of-range + floor/saturate edges **plus 9 string-name checks**
(transcribed from the ELF string table).

Negative control executed: flipping value 3 back to N/3 makes
`primitive_count` fail with 6 precise mismatches (type=3, N in {2,4,5,6,7,8}),
exit 1 — proving the test guards the reconstruction, and proving the pass-9
table really was wrong.

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/primitive_types.h` — corrected
   value 3 (N/2, "Lines"), renamed 6/8 to "Triangles"/"Quads", added
   string-table evidence header and `getPrimitiveTypeName()`.
2. `reconstruction/recovered/libNOVA3_neon/block_0077bf74.cpp` — NEW
   (getStringsInternal string-table reconstruction).
3. `reconstruction/tests/primitive_count_test.cpp` — fixed t3 column to N/2,
   added 9-name check.
4. `CMakeLists.txt` — +block_0077bf74.cpp to primitive_count target.
5. `sprint_notes/evidence_pass10.json` — NEW manifest.

## Honest scope and limitations

- **0 new MATCHED_ARM.** getPrimitiveCount improved from size-mismatch to
  same-size-bytes-differ; the residual diff is clang-21 PIC LUT
  (`ldr [r2,r0,lsl#2] ; add pc`) vs GCC 4.4.3 relative branch table
  (`addls pc,pc,rN,lsl#2`) and `subs/movcc` vs `sub/bic` saturation.
- The compiler_matrix `source_sha256` hashes only the `.cpp` wrapper, not the
  included header, so it did not change despite the header correction; the
  candidate object/code hashes DID change (recorded in the v2 matrix).
- The string table is data, not a switch-function; block_0077bf74.cpp returns
  the literals and is DATA-VERIFIED at the 9-entry scope, not ARM byte-compared.
- Historical GCC 4.4.3 toolchain still unestablished (unchanged blocker).

## Cumulative statistics

| Measure | Before pass 10 | After pass 10 |
|---------|---------------|---------------|
| Host CTest | 24/24 | 24/24 |
| MATCHED_ARM functions | 7 | 7 (unchanged) |
| COMPILED+BEHAVIOURAL (same-size, bytes differ) | 1 | 2 (+getPrimitiveCount) |
| Total source-owned funcs in primitive cluster | 3 | 4 (+getStringsInternal) |
| Enum names VERIFIED (was 1 confirmed / 8 inferred) | 1 | 9 |

## Next functions (priority order)

1. `CFPSCounter::CFPSCounter` (0x0077bb7c, 56 B): field-zeroing constructor,
   small and unambiguous.
2. `CVertexStreams::getActiveAttributeMask` (0x0077bd28, 84 B) and
   `addOffset(int)` (0x0077be04, 64 B): symbol-bearing, clear semantics.
3. The `E_INDEX_TYPE` / `E_BUFFER_USAGE` / `E_BUFFER_TYPE` string tables
   (adjacent in `.data.rel.ro` at 0xd36624 / 0xd36634 / 0xd365f8): recover
   the neighbouring enums the same way, using getStringsInternal as the
   accessor.
4. Continue `computeMipmapSizeInBytes(6-arg)` / `getSizeInBytes(unsigned char)`
   ARM comparison once sub-call relocations are handled.

## Current blockers (unchanged)

1. Historical GCC 4.4.3 ARM toolchain still not established; clang-21 emits
   different jump-table/conditional idioms for switch code.
2. Whole-`.so` SHA equality not established (not attempted).
3. Switch/jump-table functions show the largest codegen gap under clang; the
   relative-branch (GCC) vs PIC-LUT (clang) split implies the original was
   built without strict PIC jump tables for local switches.