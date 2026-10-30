# Sprint STATUS — Pass 2 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)

## Summary

Recovered the `glitch::video::pixel_format` size-computation family from
Premium `libNOVA3_neon.so`, now consolidated behind one shared header
(`pixel_format.h`) with one descriptor struct and three behavioural functions:

| Function | ELF VA | Size | A32 | Status |
|----------|--------|------|-----|--------|
| computePitch (pass 1) | 0x0077d37c | 80 B | yes | BEHAVIORAL (host) |
| computeSizeInBytes (3-arg) | 0x0077d3cc | 112 B | yes | BEHAVIORAL (host) |
| computeSizeInBytes (slices) | 0x0077d43c | 20 B | yes | BEHAVIORAL (host) |

All three compile to ARMv7 ET_REL and pass host + UBSan tests; ARM
instruction-window comparison is NOT MATCHED for every one (expected — the
recovered C++ takes the descriptor table as an explicit pointer, while the
original loads it via PC-relative literal pool; see Limitations).

## Evidence manifest

| Step | Status | Detail |
|------|--------|--------|
| DISCOVERED | DONE | Three symbols recovered from Premium ELF symbol table (nm). 40-entry descriptor table at .rodata VA 0x00c7ff50 (40-byte entries) dumped and field-mapped. |
| ANALYZED | DONE | llvm-objdump traces for all three. 3-arg: bl computePitch, block_height(0x25) ceil division, min_size(0x27) unsigned clamp. 4-arg: mov r4,r3; bl 0x77d3cc; mul r0,r4,r0 (pure slices multiplier). |
| SOURCE | DONE | pixel_format.h (shared struct + 3 inline funcs); block_0077d37c/0077d3cc/0077d43c.cpp thin extern-C wrappers. |
| ARM_COMPILES | DONE | NDK Clang 21.0.0 (armv7a, -marm -O2 -fPIC -std=c++17). pitch 72 B, size3 120 B, slices 132 B candidates. |
| COMPARISON_CHECKED | DONE | compiler_matrix → FUNCTION_SIZE_MISMATCH for all (72/80, 120/112, 132/20). Expected ABI difference + inlining of the sibling calls. |
| BEHAVIOR_TESTED | DONE | 15/15 CTest. Table-driven cross-check vs independent reference over all 40 formats x 17x17 dim grid; explicit hand-computed clamp/zero-dim/slices cases. |
| VERIFIED_AT_SCOPED_LEVEL | BEHAVIORAL (host) | Arithmetic verified against the actual 40-entry table extracted from the original ELF. ARM instruction match NOT VERIFIED. UBSan clean. |

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/pixel_format.h` — NEW shared header
2. `reconstruction/recovered/libNOVA3_neon/block_0077d37c.cpp` — refactored to thin wrapper
3. `reconstruction/recovered/libNOVA3_neon/block_0077d3cc.cpp` — refactored to thin wrapper
4. `reconstruction/recovered/libNOVA3_neon/block_0077d43c.cpp` — NEW slices overload
5. `reconstruction/tests/compute_pitch_test.cpp` — use shared header
6. `reconstruction/tests/compute_size_in_bytes_test.cpp` — use shared header + slices tests
7. `CMakeLists.txt` — add block_0077d43c.cpp to the size test target

## Original ELF reference

- Path: /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
- SHA-256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
- Descriptor table: .rodata VA 0x00c7ff50, 40-byte entries, 40 formats.
  Fields used: bytes_per_block(0x15), bits_per_pixel(0x16), block_width(0x24),
  block_height(0x25), min_size(0x27). Offset 0x26 observed 1 in every entry
  (opaque). Full 40-entry table embedded in the size test.

## Recovered behaviour

  pitch  = (bw<=1) ? (bpp*width)>>3 : bpb * ceil(width/bw)
  size   = (bh<=1) ? pitch*height    : pitch * ceil(height/bh)
  size   = max(size, min_size)        [unsigned byte clamp]
  slices = slices * size

## ARM compiler profile

- Compiler: /opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++ (NDK r29, Clang 21.0.0)
- Flags: --target=armv7a-linux-androideabi21 -marm -O2 -fPIC -std=c++17
- Matrices: .cache/matrix-pitch-v2, matrix-size-v2, matrix-compute-size-slices-v1

## Host test results

CTest 15/15 passed. UBSan (no-recover) clean on both test executables.
ASan could not run (host kernel/hardened ASLR shadow-interleave issue — env, not code).

## Next functions (priority order)

1. `computeMipmapSizeInBytes` 5-arg at 0x0077d450 — shifts width/height by mip
   level with ==0 clamp-to-1, then tail-calls the 3-arg (real mip logic).
2. `CImageLoaderPVR::CDataInfo::getFileDataSize` at 0x007b8974 — `ldr r0,[r0,#0xc]` trivial accessor.
3. `CColladaDatabase::getVersion` at 0x0071017c — 4-level pointer chain, needs CColladaDatabase layout.

## Current blockers

1. No historical GCC 4.4.3 ARM cross-compiler; NDK Clang 21 is the only ARMv7
   Linux/Android compiler available. Byte-level ARM instruction match remains
   impossible without the historical toolchain (unchanged).
2. research/assets/ missing from this isolated worktree → pre-existing
   check_all.sh failure (test_archive_inspection.py::test_zip_valid_and_truncated),
   unrelated to this pass.

## Limitations

- Deliberate source-only ABI choice: explicit table pointer instead of the
  original's embedded global descriptor address.
- Opaque descriptor bytes (0x00-0x14, 0x17-0x23, 0x26) zeroed; only the five
  consumed fields reproduced. They do not affect these three functions.
- The slices overload inlines the 3-arg computation in our header-inline
  design, so its ARM candidate is 132 B vs the original 20 B `bl`-and-multiply
  caller. This is a structural (inlining) difference, not a behavioural one;
  the original's sibling call is represented by our shared inline helper.
