# Sprint STATUS — Pass 8 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## Summary

This pass added 2 new source-owned C++ implementations: Collada
named-parameter accessors getMaterial by name (0x007146ac) and
getImage by name (0x0071476c). Both are 96-byte strcmp-based linear
search functions with the same 3-level database -> resource -> header ->
collada_root pointer walk shared by the pass-7 accessor cluster.

Both functions compile under the pass-6 clang-21/-marm/-O2/-fomit-frame-pointer
profile, pass host behavioral tests (22 new checks), and produce
structurally similar but larger instruction sequences (116 vs 96 bytes)
due to clang-21 loop-unrolling and down-counter optimization differences
vs the historical GCC 4.4.3 compiler. No byte-window match.

## Recovered functions (this pass)

All functions are A32 (ARM), confirmed from `.symtab`.

| Function | ELF VA | Size | Mangled symbol | Result |
|----------|--------|------|----------------|--------|
| CColladaDatabase::getMaterial by name | 0x007146ac | 96 B | `_ZNK6glitch7collada16CColladaDatabase11getMaterialEPKc` | COMPILED+BEHAVIOURAL; bytes differ (116 vs 96, clang unrolls loop) |
| CColladaDatabase::getImage by name | 0x0071476c | 96 B | `_ZNK6glitch7collada16CColladaDatabase8getImageEPKc` | COMPILED+BEHAVIOURAL; bytes differ (116 vs 96, clang down-counter) |

## Recovered behaviour

Both functions share the pattern established in pass 7:
- 3-level pointer walk: this+0 -> file+0x10 -> header+0x20 -> collada_root
- getMaterial: count at collada[+0x78] (i32), records at collada[+0x7c], stride 36
- getImage: count at collada[+0x68] (i32), records at collada[+0x6c], stride 16
- Count <= 0 returns nullptr
- Linear scan: strcmp(record[0] (id pointer), name) for each element
- First exact match returns pointer to record; exhaustion returns nullptr
- Original GCC loop structure: non-header entry, pre-increment stride,
  up-counter. Equivalent to standard counted for-loop.

## ARM comparison evidence

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Compiler: NDK r29 clang-21, `--target=armv7a-linux-androideabi21 -marm -O2 -fomit-frame-pointer -fPIC -std=c++17`
- Reports in `.cache/om-getMaterialByName/matrix.json` and `.cache/om-getImageByName/matrix.json`

Both functions: FUNCTION_SIZE_MISMATCH (116 vs 96 B), 2 strcmp relocations each.
Clang-21 differences vs GCC 4.4.3:
- **getMaterial by name**: clang unrolls loop body (two strcmp per iteration),
  uses signed count<1 (blt) guard and down-counter; original GCC uses simple
  up-counter with single strcmp per iteration.
- **getImage by name**: clang uses down-counter with separate sub/cmp/bne;
  original GCC uses up-counter with cmp/add/beq.
- **Both**: clang walks chain through r0 (return register); GCC uses r3 (scratch).
  Clang saves r11 instead of r8 (frame pointer register preference).

## Host test results

CTest **23/23 passed** (22 prior + 1 new ccollada_by_name).

New test: `ccollada_by_name` (22 checks):
- get_material_by_name: 11 checks (count 0/null, count -1/null, 3-element find
  each, not-found, duplicate first-match, empty string, single element)
- get_image_by_name: 11 checks (same categories, stride-16 verification)
- All 32-bit addressable static chain, verified pointer arithmetic.

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/ccollada_database.h` — extended
   (get_material_by_name, get_image_by_name inline functions + 2 extern "C" decls)
2. `reconstruction/recovered/libNOVA3_neon/block_007146ac.cpp` — NEW
   (getMaterial by name extern "C" wrapper)
3. `reconstruction/recovered/libNOVA3_neon/block_0071476c.cpp` — NEW
   (getImage by name extern "C" wrapper)
4. `reconstruction/tests/ccollada_by_name_test.cpp` — NEW (22 behavioral checks)
5. `CMakeLists.txt` — +ccollada_by_name test target

## Honest scope and limitations

- **0 new MATCHED_ARM** this pass. Both functions differ in size and instruction
  bytes due to compiler codegen differences (clang-21 vs GCC 4.4.3).
  Behavioural equivalence is independently host-tested.
- The `_walk_to_collada` helper refactoring changes register allocation from
  the original per-function inline walks, preventing byte-window match for
  the three-level walk portion.
- Both functions call strcmp, creating 2 relocations in ARM ET_REL. The
  original PLT entry vs ET_REL relocation difference prevents byte-window
  match for the BL strcmp instructions.
- Collada struct layouts at +0x68 (image_count), +0x6c (image_base),
  +0x78 (material_count), +0x7c (material_base) are now recovered and tested
  with actual strcmp semantics.

## Cumulative statistics

| Measure | Before pass 8 | After pass 8 |
|---------|--------------|--------------|
| Host CTest | 22/22 | 23/23 |
| MATCHED_ARM functions | 7 | 7 (unchanged) |
| COMPILED+BEHAVIOURAL (bytes differ) | 6 | 8 (+2) |
| Total source-owned funcs in pass-6 pipeline | 13 | 15 |
| Collada struct offsets recovered | +0x00, +0x08, +0x6c, +0x7c, +0x88 | + +0x68 (image_count), +0x78 (material_count) |

## Next functions (priority order)

1. **CVirtualTexture::getSizeInBytes(unsigned char)** (0x0077d4a8): already has
   source in block_0077d4a8.cpp with behavioral tests. Try ARM compilation
   and comparison. This function calls getMipmapCount and computeMipmapSizeInBytes
   internally — needs all sub-functions compiled together or relocation handling.
2. **Remaining non-trivial functions**: move from accessors to functions with
   branches, loops, and data dependencies. Target functions that are 48–128 bytes
   with clear control flow.
3. **GCC 4.4.3 toolchain**: still the core blocker for byte-window matches.
   Clang-21 consistently generates different register allocation and loop
   optimization patterns.
4. **Functions with explicit GOT/data relocations**: start the relocation
   worksheet approach described in docs/DISCORD_IMPLEMENTATION.md.

## Current blockers (unchanged)

1. Historical GCC 4.4.3 ARM toolchain still not established.
2. Whole-`.so` SHA equality not established (not attempted).
3. Functions with data references carry relocations and need a worksheet.
4. Clang-21 consistently generates larger/different code for loops and
   strcmp-based functions compared to GCC 4.4.3.