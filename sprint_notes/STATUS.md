# Sprint STATUS — Pass 1 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30T16:52 AEDT

## Summary

Selected `computePitch` (ELF VA 0x0077d37c, 80 bytes, A32) for proof-of-process.
Wrote independently compilable C++ source, added host test (14/14 CTest pass),
ran through ARM compiler matrix pipeline.  ARM comparison result: NOT MATCHED
(expected — ABI differs: original has literal-pool global table, ours takes
explicit table pointer).  Behavioral tests pass on host.

## Evidence manifest

| Step | Status | Detail |
|------|--------|--------|
| DISCOVERED | DONE | ELF VA 0x0077d37c, symbol `_ZN6glitch5video12pixel_format12computePitchENS0_14E_PIXEL_FORMATEj`, 80 bytes, A32 mode |
| ANALYZED | DONE | Ghidra decompilation, instruction trace, data-table extraction at ELF VA 0xc7ff50 (40-byte entries). Behavior: uncompressed pitch = (bpp * width) >> 3; compressed pitch = block_bytes * ceil(width / block_width). Calls __aeabi_uidiv. |
| SOURCE | DONE | `reconstruction/recovered/libNOVA3_neon/block_0077d37c.cpp` — 40-byte PixelFormatDescriptor struct, compute_pitch function, extern "C" wrapper |
| ARM_COMPILES | DONE | Compiled with Android NDK Clang 21.0.0 (target armv7a-linux-androideabi21, -marm -O2 -fPIC -std=c++17). Candidate .o: 72 bytes, 1 relocation. |
| COMPARISON_CHECKED | DONE | `nova3_team.compiler_matrix` → FUNCTION_SIZE_MISMATCH (candidate 72 vs original 80 bytes). Code window not equal. Expected: original uses PC-relative literal pool for global table; candidate takes table pointer as 3rd arg. |
| BEHAVIOR_TESTED | DONE | 14/14 CTest pass (13 existing + new `compute_pitch_arithmetic`). Tests uncompressed (8/16/24/32/48/64/96bpp) and compressed (4x4/8x4 block, 8/16 byte blocks) pitch computation. |
| VERIFIED_AT_SCOPED_LEVEL | BEHAVIORAL (host) | Behavioral arithmetic verified against known format table entries extracted from original ELF. ARM instruction match: NOT VERIFIED (ABI mismatch expected). |

## Files changed

1. `reconstruction/recovered/libNOVA3_neon/block_0077d37c.cpp` — NEW: computePitch reconstruction
2. `reconstruction/tests/compute_pitch_test.cpp` — NEW: 30 assertion tests
3. `CMakeLists.txt` — add compute_pitch_test executable and CTest registration

## Original ELF reference

- Path: `/home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`
- SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Function: computePitch at ELF VA 0x0077d37c, 80 bytes, A32 (Thumb=0)
- Symbol: `_ZN6glitch5video12pixel_format12computePitchENS0_14E_PIXEL_FORMATEj`
- Data table: .rodata at ELF VA 0x00c7ff50, 40-byte entries, 40 formats identified

## ARM compiler profile

- Compiler: `/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++` (Android NDK r29, Clang 21.0.0)
- Flags: `--target=armv7a-linux-androideabi21 -marm -O2 -fPIC -std=c++17`
- Profile: `.cache/pilot-compute-pitch-profile.json`
- Candidate object: `.cache/matrix-compute-pitch-v2/candidate_01.o`
- Candidate SHA-256: recorded in matrix.json

## Comparison result

- Tool: `python3 -m nova3_team.compiler_matrix`
- Status: `FUNCTION_SIZE_MISMATCH` (72 vs 80 bytes)
- `code_window_equal`: false
- Root cause: Original uses PC-relative literal pool to load global table address
  (2-arg function). Candidate takes table as explicit 3rd arg (r2). ABI differs
  by design — we do not embed original global data addresses.
- Not a bug: this is a clean behavioral reconstruction with different ABI.

## Host test results

```
14/14 tests passed (0.80s)
  crc32_vectors, scramble_vectors, zip_local_headers, zip_payload_decode,
  bres_relocation, zip_native_lookup, zip_archive_resources,
  collada_mesh_decode, pvr_texture_headers, texture_split_bundle,
  etc1_pixels, texture_filename_policy, scrambled_zip_headers,
  compute_pitch_arithmetic  ← NEW
```

`tools/check_all.sh` has a pre-existing failure in
`test_archive_inspection.py::test_zip_valid_and_truncated` because
`research/assets/` directory is missing from this isolated worktree.
This is NOT introduced by this pass; CTest 14/14 passes cleanly.

## Next functions (priority order)

1. `computeSizeInBytes` at 0x0077d3cc (112 bytes) — calls computePitch,
   adds height/clamp logic. Natural next step.
2. `CImageLoaderPVR::CDataInfo::getFileDataSize` at 0x007b8974 (8 bytes) —
   trivial accessor, good for another ABI-comparison attempt.
3. `CColladaDatabase::getVersion` at 0x0071017c (20 bytes) — simple accessor,
   no external dependencies beyond object layout.

## Current blockers

1. No historical GCC 4.4.3 ARM cross-compiler available. NDK Clang 21 is the
   only ARMv7 compiler. GCC 16.2 (arm-none-eabi) has incorrect ABI (bare-metal,
   not Linux/Android). Byte-level ARM instruction match is NOT POSSIBLE without
   the historical compiler.
2. research/assets/ directory missing in worktree — pre-existing `check_all.sh`
   failure unrelated to this pass.

## Limitations

- computePitch reconstruction uses explicit table parameter; original uses
  embedded global address. This is a deliberate source-only design choice.
- The 37 reserved bytes in PixelFormatDescriptor are zeroed; original table
  contains additional metadata (format IDs, masks, block heights, min sizes).
- No integration with existing engine/pvr_texture module yet. The recovered
  block is standalone with its own test.
- ARM comparison is negative (expected), not a byte match.