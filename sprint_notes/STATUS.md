# Sprint STATUS — Pass 3 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)

## Summary

This pass repaired the host source-only regression that had been failing since
pass 1 (two environment/guard defects), then recovered the
`glitch::video::pixel_format::computeMipmapSizeInBytes` family (five- and
six-argument overloads) from Premium `libNOVA3_neon.so`, cross-checked against
the Freemium edition.

## Regression repair (gate A — prerequisite before new source)

The run.log carried "Host regression exit: 1, Failure count: 2" from passes 1-2.
Root causes and fixes (committed as 1cbc682):

1. `tools/extract/test_archive_inspection.py::test_zip_valid_and_truncated`
   used `tempfile.TemporaryDirectory(dir=.../research/assets)` without first
   creating that private (gitignored) directory, so it failed in this isolated
   worktree. Fix: `mkdir(parents=True, exist_ok=True)` immediately before use.
2. `tools/verify_source_only.py` failed on two counts:
   a. `--denylist` default `research/elf/executable_denylist.json` was absent
      (private tree not materialized). Materialized a local gitignored copy
      (SHA-256 of file 55f3f9ed...); it is a hash list, not binary.
   b. `FORBIDDEN_PARTS` was checked against *every* path component, so the
      tracked source files under `reconstruction/research/` (e.g.
      arm32_cpu_query_candidate.cpp) were falsely rejected as "forbidden".
      Fix: restrict the check to the top-level component, matching the intent
      (the private trees `originals/ analysis/ generated/ research/ Archive/`
      are all repo-root directories).

`tools/check_all.sh` now exits 0: 16/16 CTest, 13/13 pytest, all 14
`verify_source_only` artifacts `ok: true`.

## Recovered functions (this pass)

| Function | ELF VA | Size | A32 | Status |
|----------|--------|------|-----|--------|
| computeMipmapSizeInBytes (5-arg) | 0x0077d450 | 32 B | yes | BEHAVIORAL (host) |
| computeMipmapSizeInBytes (6-arg, slices/depth) | 0x0077d470 | 56 B | yes | BEHAVIORAL (host) |

Mangled symbols:
- `_ZN6glitch5video12pixel_format24computeMipmapSizeInBytesENS0_14E_PIXEL_FORMATEjjhb`
- `_ZN6glitch5video12pixel_format24computeMipmapSizeInBytesENS0_14E_PIXEL_FORMATEjjjhb`

## Recovered behaviour

  mip(v, lvl) = max(v >> lvl, 1)          ; lsrs + moveq; lvl >= 32 => 1
  5-arg(format, width, height, mipLevel, skipWidthShift):
      if (!skipWidthShift) width = mip(width, mipLevel)
      height = mip(height, mipLevel)
      return computeSizeInBytes(format, width, height)   ; tail-call 0x77d3cc
  6-arg(format, width, height, depth, mipLevel, skipWidthShift):
      as above, plus depth = mip(depth, mipLevel)
      return depth * computeSizeInBytes(format, width, height)  ; bl + mul

The trailing bool gates ONLY the width shift (height and depth are always
shifted). Its original source name is UNKNOWN (INFERRED); callers derive it
from a format-descriptor comparison. We name it `skip_width_shift` by observed
effect. Callers of the 6-arg: `CVirtualTexture::getSizeInBytes` (0x77d4fc,
bool=false), `util::atlasTextures` (0x6ad628, bool=(field_0x24>>3)&7==1),
`IImageLoader::ITextureDataLoading::getSourceStep` (0x7c80d4, bool=field_0x8==1).
The 5-arg has no direct .text caller (dead/indirect).

## Cross-edition independent confirmation

Freemium `libGloftNAHM.so` (SHA e88ccdaa...) exports the same two symbols:
- 5-arg at 0x005d2b48, 32 B — instruction-identical to Premium apart from the
  tail-call target.
- 6-arg at 0x005d2b68, 52 B — same three shifts, then tail-calls the
  four-argument slices overload instead of call-and-multiply (structural,
  behaviourally equivalent).

This is independent cross-edition evidence for the recovered semantics.

## Evidence manifest

| Step | Status | Detail |
|------|--------|--------|
| DISCOVERED | DONE | Symbols from Premium `.symtab` (nm -C); cross-checked in Freemium dynsym. |
| ANALYZED | DONE | llvm-objdump A32 traces; three callers located; bool semantics traced. |
| SOURCE | DONE | pixel_format.h (mip_shift + 2 inline funcs); block_0077d450/0077d470.cpp wrappers. |
| ARM_COMPILES | DONE | NDK Clang 21.0.0 armv7a ET_REL. 5-arg 192 B, 6-arg 232 B. |
| COMPARISON_CHECKED | DONE | compiler_matrix → FUNCTION_SIZE_MISMATCH (192/32, 232/56). Expected: header-inline design inlines the sibling computePitch/computeSizeInBytes; original keeps them out-of-line and tail-calls. |
| BEHAVIOR_TESTED | DONE | 16/16 CTest. Independent reference + explicit hand-computed values over all 40 formats × 17×17 dims × 9 mip levels × {skip,no-skip} × 5 depths. UBSan (no-recover) clean. |
| VERIFIED_AT_SCOPED_LEVEL | BEHAVIORAL (host) | Arithmetic verified against the 40-entry table from the original ELF and Freemium-confirmed semantics. ARM instruction match NOT VERIFIED. |

## Original ELF reference

- Path: /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
- SHA-256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
- Descriptor table: .rodata VA 0x00c7ff50, 40-byte entries, 40 formats (unchanged).

## ARM compiler profile

- Compiler: /opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-21 (NDK r29, Clang 21.0.0)
- Flags: --target=armv7a-linux-androideabi21 -marm -O2 -fPIC -std=c++17
- Matrices: .cache/matrix-mip5-v1, .cache/matrix-mip6-v1 (private, gitignored)

## Files changed (this pass)

1. `tools/extract/test_archive_inspection.py` — create research/assets before use
2. `tools/verify_source_only.py` — top-level-only forbidden-path check
3. `reconstruction/recovered/libNOVA3_neon/pixel_format.h` — +mip_shift, +2 mipmap funcs, updated header comment
4. `reconstruction/recovered/libNOVA3_neon/block_0077d450.cpp` — NEW 5-arg wrapper
5. `reconstruction/recovered/libNOVA3_neon/block_0077d470.cpp` — NEW 6-arg wrapper
6. `reconstruction/tests/compute_mipmap_size_in_bytes_test.cpp` — NEW
7. `CMakeLists.txt` — add compute_mipmap_size_in_bytes_test target

## Host test results

CTest 16/16 passed; pytest 13/13 passed; verify_source_only 14/14 ok.
UBSan (no-recover) clean on the mipmap test.

## Next functions (priority order)

1. `CVirtualTexture::getSizeInBytes(unsigned char)` at 0x0077d4a8 — the primary
   6-arg caller; needs CVirtualTexture field layout (+0x10 format, +0x14 width,
   +0x18 height, +0x1c mip count). Bounded, single caller chain mapped.
2. `CImageLoaderPVR::CDataInfo::getFileDataSize` at 0x007b8974 — `ldr r0,[r0,#0xc]` accessor.
3. `CColladaDatabase::getVersion` at 0x0071017c — 4-level pointer chain.

## Current blockers

1. No historical GCC 4.4.3 ARM cross-compiler; NDK Clang 21 is the only ARMv7
   compiler available. Byte-level ARM instruction match remains impossible
   without the historical toolchain (unchanged from passes 1-2).
2. The `research/` private tree is only partially materialized in this isolated
   worktree; only `research/elf/executable_denylist.json` was copied (needed by
   the source-only guard). Oracle compare scripts require `NOVA3_RESEARCH_ORACLES=1`.

## Limitations

- Deliberate source-only ABI choice: explicit table pointer instead of the
  original's embedded global descriptor address (unchanged from prior passes).
- Header-inline design inlines computePitch/computeSizeInBytes into the mipmap
  wrappers, so ARM candidates (192/232 B) are far larger than the originals
  (32/56 B) which keep the siblings out-of-line and tail-call. This is a
  structural difference, not a behavioural one.
- The trailing bool's original source name is UNKNOWN; recorded as INFERRED.
