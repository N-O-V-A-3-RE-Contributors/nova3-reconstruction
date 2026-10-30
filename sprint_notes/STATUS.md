# Sprint STATUS — Pass 2 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)

## Summary

Pass 1 reconstructed `computePitch` (0x0077d37c) — behavioral source, ARM
comparison NOT MATCHED (expected ABI difference).

Pass 2 reconstructed the natural next dependency `computeSizeInBytes`
(0x0077d3cc, 112 bytes), which the original calls into from the pixel-format
module. Wrote independently compilable C++ source, added a host test that
cross-checks against the actual 40-entry descriptor table extracted from the
original ELF, ran ARM compiler-matrix comparison (NOT MATCHED, expected ABI
difference), and UBSan-clean verification.

## Evidence manifest — computeSizeInBytes

| Step | Status | Detail |
|------|--------|--------|
| DISCOVERED | DONE | ELF VA 0x0077d3cc, symbol `_ZN6glitch5video12pixel_format18computeSizeInBytesENS0_14E_PIXEL_FORMATEjj`, 112 bytes, A32 mode |
| ANALYZED | DONE | llvm-objdump trace; calls computePitch (bl 0x77d37c); reads block_height (offset 0x25) and min_size (offset 0x27) from 40-byte descriptor; size = pitch*height or pitch*ceil(height/bh); unsigned-byte min clamp. Both literal-pool entries resolve to table base 0xc7ff50. |
| SOURCE | DONE | `reconstruction/recovered/libNOVA3_neon/block_0077d3cc.cpp` — 40-byte descriptor (block_height+min_size named), compute_size_in_bytes, extern "C" wrapper |
| ARM_COMPILES | DONE | NDK Clang 21.0.0 (armv7a, -marm -O2 -fPIC -std=c++17). Candidate .o: 120 bytes, 2 relocations (__aeabi_uidiv x2). |
| COMPARISON_CHECKED | DONE | `nova3_team.compiler_matrix` → FUNCTION_SIZE_MISMATCH (120 vs 112). Expected: original uses PC-relative literal pool for global table (3-arg member fn); candidate takes table pointer in r3. |
| BEHAVIOR_TESTED | DONE | 15/15 CTest (14 existing + new `compute_size_in_bytes_arithmetic`). Table-driven cross-check vs independent reference over all 40 formats x 17x17 width/height grid; plus explicit hand-computed expected values incl. min_size clamp and zero-dimension edge cases. |
| VERIFIED_AT_SCOPED_LEVEL | BEHAVIORAL (host) | Arithmetic verified against actual 40-entry table extracted from original ELF. ARM instruction match NOT VERIFIED (ABI difference). UBSan clean. |

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/block_0077d3cc.cpp` — NEW
2. `reconstruction/tests/compute_size_in_bytes_test.cpp` — NEW
3. `CMakeLists.txt` — add compute_size_in_bytes_test + CTest registration

## Original ELF reference

- Path: /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
- SHA-256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
- Function: computeSizeInBytes at ELF VA 0x0077d3cc (7853004), 112 bytes, A32
- Descriptor table: .rodata VA 0x00c7ff50, 40-byte entries, 40 formats

## Recovered descriptor table (verified from ELF bytes)

Fields per 40-byte entry, offsets used: bytes_per_block(0x15), bits_per_pixel(0x16),
block_width(0x24), block_height(0x25), min_size(0x27). Offset 0x26 observed 1
in every entry (opaque, not consumed). Full 40-entry table embedded in the test.

Behaviour implemented:
  pitch = (bw<=1) ? (bpp*width)>>3 : bpb * ceil(width/bw)
  size  = (bh<=1) ? pitch*height    : pitch * ceil(height/bh)
  return max(size, min_size)   [unsigned byte clamp]

## ARM compiler profile (reused from pass 1)

- Compiler: /opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++ (NDK r29, Clang 21.0.0)
- Flags: --target=armv7a-linux-androideabi21 -marm -O2 -fPIC -std=c++17
- Profile: .cache/pilot-compute-pitch-profile.json
- Candidate: .cache/matrix-compute-size-v1/candidate_01.o (SHA 0ce4af59...)
- Matrix: .cache/matrix-compute-size-v1/matrix.json

## Host test results

CTest 15/15 passed (0.78s). Log: sprint dir tests-pass-2.log.
UBSan (no-recover) clean on compute_size_in_bytes_test and compute_pitch_test.
ASan could not run (host kernel/hardened ASLR shadow-interleave issue — environment,
not code).

## Next functions (priority order)

1. `computeSizeInBytes` 4-arg overload at 0x0077d43c (E_PIXEL_FORMAT, w, h, uint)
   — sibling; likely adds mip/slice handling over the same descriptor.
2. `CImageLoaderPVR::CDataInfo::getFileDataSize` at 0x007b8974 (8 bytes) — trivial accessor.
3. `CColladaDatabase::getVersion` at 0x0071017c (20 bytes) — simple accessor.

## Current blockers

1. No historical GCC 4.4.3 ARM cross-compiler; NDK Clang 21 is the only ARMv7
   Linux/Android compiler available. Byte-level ARM instruction match remains
   not possible without the historical toolchain (unchanged from pass 1).
2. research/assets/ missing from this isolated worktree → pre-existing
   check_all.sh failure (test_archive_inspection.py::test_zip_valid_and_truncated),
   unrelated to this pass.

## Limitations

- Same deliberate source-only ABI choice as pass 1: explicit table pointer
  instead of the original's embedded global descriptor address.
- Opaque descriptor bytes (0x00-0x14, 0x17-0x23, 0x26) are zeroed in our
  reconstruction; only the five fields consumed by computePitch/computeSizeInBytes
  are reproduced. They do not affect these two functions' behaviour.
- compute_size_in_bytes inlines the pitch arithmetic instead of literally
  calling the sibling computePitch source (original issues a `bl`); behaviour
  is identical and the block stays self-contained.
