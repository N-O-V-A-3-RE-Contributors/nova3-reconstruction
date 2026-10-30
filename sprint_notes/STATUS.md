# Sprint STATUS — Pass 6 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## Summary

This pass filled the ARM-compilation + comparison gates that prior passes
deferred, and produced the project's **first independently verified ARM
instruction-window matches** (7 functions, `LINKED_FUNCTION_WINDOW_EQUAL`),
plus one behaviourally correct 4-level pointer-walk function that compiles to
20 identical instructions but differs only in register allocation.

## Key finding: original is compiled WITHOUT a frame pointer

The trivial `CDataInfo` accessors in the original are single `ldr / bx lr`
(or `mov / bx lr`) sequences with **no `push {r11,lr}` prologue**. The NDK
clang-21 default (with `-marm -O2`) emitted a frame pointer, doubling every
function size and preventing a byte match. Adding `-fomit-frame-pointer`
yielded exact 8/4-byte matches. This is a concrete, evidence-backed compiler
flag observation (the original was built with frame-pointer omission, as is
GCC's `-O2` default), not a fabricated profile.

## Recovered functions (this pass)

All functions are A32 (ARM), confirmed from `$a` mapping symbols + `.symtab`.

| Function | ELF VA | Size | Mangled symbol | Result |
|----------|--------|------|----------------|--------|
| DDS CDataInfo::getFileDataSize | 0x007b892c | 8 B | `_ZNK6glitch5video15CImageLoaderDDS9CDataInfo15getFileDataSizeEv` | MATCHED_ARM |
| PVR CDataInfo::getFileDataSize | 0x007b8974 | 8 B | `_ZNK6glitch5video15CImageLoaderPVR9CDataInfo15getFileDataSizeEv` | MATCHED_ARM |
| DDS CDataInfo::getFaceStartOffset | 0x007b8958 | 8 B | `_ZNK6glitch5video15CImageLoaderDDS9CDataInfo18getFaceStartOffsetEv` | MATCHED_ARM |
| DDS CDataInfo::isLittleEndian | 0x007b8960 | 8 B | `_ZNK6glitch5video15CImageLoaderDDS9CDataInfo14isLittleEndianEv` | MATCHED_ARM |
| DDS CDataInfo::needsFlip | 0x007b8968 | 8 B | `_ZNK6glitch5video15CImageLoaderDDS9CDataInfo9needsFlipEv` | MATCHED_ARM |
| DDS CDataInfo::~CDataInfo | 0x007b8970 | 4 B | `_ZN6glitch5video15CImageLoaderDDS9CDataInfoD1Ev` | MATCHED_ARM |
| PVR CDataInfo::getFilePitch(h) | 0x007b897c | 8 B | `_ZNK6glitch5video15CImageLoaderPVR9CDataInfo12getFilePitchEh` | MATCHED_ARM |
| CColladaDatabase::getVersion | 0x0071017c | 20 B | `_ZNK6glitch7collada16CColladaDatabase10getVersionEv` | COMPILED + BEHAVIOURAL; bytes differ (regalloc) |

## Recovered behaviour

**CDataInfo (DDS/PVR shared image-info record):**
- +0x0c `file_data_size`: `getFileDataSize` returns it (both DDS and PVR).
- +0x10 `face_start_offset`: `getFaceStartOffset` returns it (DDS).
- +0x04 `header_word`: pointer word (evidenced by the longer DDS getFilePitch
  at 0x007b8934 performing `ldr r3,[r0,#0x4] ... ldr r0,[r3,#0x10]`).
- `isLittleEndian` -> 1; `needsFlip` -> 0; PVR `getFilePitch(h)` -> 0 (stub).

**CColladaDatabase::getVersion:** four-level 32-bit pointer walk
`*(*(*(root+0x00)+0x10)+0x20)+0x00` (root at +0x00 in the object).

The `static_assert(offsetof(...))` assertions in `cimage_datainfo.h` and the
sentinel values in the test are the "catch a wrong reconstruction" guard: a
mislocated field fails at compile time or produces a wrong read at run time.

## ARM comparison evidence (compiler_matrix -> object_compare)

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Compiler: `/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-21`
  (Android NDK r29, clang 21.0.0).
- Flags: `--target=armv7a-linux-androideabi21 -marm -O2 -fomit-frame-pointer -fPIC -std=c++17`
- Profile: `.cache/armv7-clang-omitfp.json`
- Per-function reports: `.cache/om-*/matrix.json` (JSON, private local cache).

Each MATCHED_ARM entry has: reference window SHA == candidate code SHA, same
size, A32 == A32, and **0 relocations**. Example (DDS getFileDataSize):
`reference_size=8 candidate_size=8 candidate_relocation_count=0`
`reference_window_sha256 == candidate_code_sha256 = 687b83fbb1b1084c4b61325547b5f8fadfb354f0d8aa39fcef5ecb6ba6b86ed1`.

## Host test results

CTest **21/21 passed** (19 prior + 2 new). New tests:
- `cimage_datainfo` (7 CDataInfo accessors: field offsets, sentinel reads,
  constant/zero returns, no-op destructor).
- `ccollada_get_version` (4-level walk; linked `-no-pie` so the 32-bit walk is
  addressable on LP64; skips cleanly if static storage exceeds 4 GiB).

## Honest scope and limitations

- **7 MATCHED_ARM** are `LINKED_FUNCTION_WINDOW_EQUAL`, not
  `MATCHED_LIBRARY_SHA256`, not proof of the historical GCC 4.4.3 compiler,
  original `.o` TU boundaries, relocations, or full source equivalence. They
  are real, independently-written C++ whose ARM code bytes equal the original
  linked window with a pinned, documented compiler profile and no relocations.
- These functions are **trivial leaf accessors** where codegen is effectively
  compiler-independent; the match is real but narrowly scoped.
- `getVersion` compiles to the same 5-instruction/20-byte shape but clang-21
  threads the walk through r0 while the original uses r3 as the walk register
  (register-allocation variance). Marked `FUNCTION_BYTES_DIFFERENT`, behaviour
  independently host-tested.
- `-fomit-frame-pointer` matches observed bytes but is **not** proof the
  historical toolchain/NDK used the same flags; it is one required flag among
  an unknown full set.

## Correction to prior pass-5 STATUS

Pass 5 listed `0x0078668c` as "CVirtualTexture::getSizeInBytes(unsigned int)".
The actual `.symtab` symbol at `0x0078668c` is
`_ZNK6glitch5video15CImageWriterPVR16writeTextureDescE...` (CImageWriterPVR::
writeTextureDesc, a large non-trivial writer), not getSizeInBytes. That
assumption is retracted; no such unsigned-int overload exists at that address.

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/cimage_datainfo.h` — NEW (CDataInfo layout + inline accessors + extern-"C" decls)
2. `reconstruction/recovered/libNOVA3_neon/ccollada_database.h` — NEW (getVersion walk)
3. `reconstruction/recovered/libNOVA3_neon/block_007b892c.cpp` — NEW (DDS getFileDataSize)
4. `reconstruction/recovered/libNOVA3_neon/block_007b8958.cpp` — NEW (DDS getFaceStartOffset)
5. `reconstruction/recovered/libNOVA3_neon/block_007b8960.cpp` — NEW (DDS isLittleEndian)
6. `reconstruction/recovered/libNOVA3_neon/block_007b8968.cpp` — NEW (DDS needsFlip)
7. `reconstruction/recovered/libNOVA3_neon/block_007b8970.cpp` — NEW (DDS ~CDataInfo)
8. `reconstruction/recovered/libNOVA3_neon/block_007b8974.cpp` — NEW (PVR getFileDataSize)
9. `reconstruction/recovered/libNOVA3_neon/block_007b897c.cpp` — NEW (PVR getFilePitch)
10. `reconstruction/recovered/libNOVA3_neon/block_0071017c.cpp` — NEW (Collada getVersion)
11. `reconstruction/tests/cimage_datainfo_test.cpp` — NEW
12. `reconstruction/tests/ccollada_get_version_test.cpp` — NEW
13. `CMakeLists.txt` — +2 test targets (cimage_datainfo, ccollada_get_version)

## Next functions (priority order)

1. Remaining DDS CDataInfo `getFilePitch` (0x007b8934, ~30 B, non-trivial):
   the only CDataInfo accessor left; records the +0x04 header pointer semantics.
2. DDS CDataInfo accessor cluster continuation: `getFaceStartOffset`
   neighbours, or the PVR/DDS `getFilePitch` family fully.
3. More trivial leaf accessors in the 0x00710160..0x00710368 Collada range
   (`linkInstanceMaterial`, `getImage`, `getMaterial` from existing evidence
   task 31) — multi-level pointer/offset walks of the getVersion shape.
4. Revisit pass-5 `ITexture::computeSizeInBytes` / `CVirtualTexture` blocks
   with `-fomit-frame-pointer` now that the flag is characterized; these have
   data-table relocations so full byte equality will require relocation
   handling, but the flag removes the frame-pointer size inflation.

## Current blockers (unchanged + new)

1. Historical GCC 4.4.3 ARM toolchain still not established; modern clang-21
   matches trivial leaves but diverges on register allocation for walks.
2. Whole-`.so` SHA equality not established (not attempted; requires original
   compiler/linker/link-order/padding).
3. Functions with data references (pixel_format table, vtable[0x14]) carry
   relocations and cannot byte-match trivially; need a relocation worksheet.
4. CDataInfo layout beyond +0x10 is UNKNOWN (only four fields evidenced).

## Evidence manifest

See `sprint_notes/evidence_pass6.json` for the per-function status chain
(DISCOVERED -> ANALYZED -> SOURCE -> ARM_COMPILES -> COMPARISON_CHECKED
-> BEHAVIOR_TESTED -> VERIFIED_AT_SCOPED_LEVEL).