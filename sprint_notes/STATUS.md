# Sprint STATUS — Pass 5 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)

## Summary

This pass recovered two additional virtual texture size-computation functions from Premium `libNOVA3_neon.so`, extending the pixfmt descriptor/mipmap infrastructure.

## Recovered functions (this pass)

| Function | ELF VA | Size | A32 | Status |
|----------|--------|------|-----|--------|
| CVirtualTexture::getSizeInBytes() no-arg | 0x007863e4 | 680 B | yes | BEHAVIORAL (host) |
| ITexture::computeSizeInBytes() const | 0x0077d99c | 128 B | yes | BEHAVIORAL (host) |

Mangled symbols:
- `_ZNK6glitch5video15CVirtualTexture14getSizeInBytesEv`
- `_ZNK6glitch5video8ITexture18computeSizeInBytesEv`

## Recovered behaviour

**CVirtualTexture::getSizeInBytes() const (no-arg):**
Sums mipmap chain sizes across all levels.
- Fast path (descriptor[0x2c] & 2): returns vtable[0x14](this, 0) * cubemap_factor
- Slow path: getMipmapCount() determines level count, then for each level computes computeSizeInBytes(fmt, w>>lvl, h>>lvl) * max(1, depth>>lvl), sums, multiplies by cubemap_factor
- Cubemap factor: (descriptor[0x24] & 7) == 3 ? 6 : 1
- Four inner-loop variants match the original: both dims compressed (bpb*ceil(w/bw)*ceil(h/bh)), one compressed (bpb or bpp paths), both uncompressed (bpp>>3 path)

**ITexture::computeSizeInBytes() const:**
128-byte alignment + cubemap weight dispatch.
- Path A (descriptor[0x2c] & 2 == 0): inner table lookup, raw value rounded up to 128, formula: weight*aligned + raw
- Path B: two vtable[0x14] calls (A, B), formula: weight*align_up(B,128) + A
- Cubemap weight: (descriptor[0x24] & 7) == 3 ? 5 : 0

Descriptor struct fields observed:
- +0x10: pointer to inner uint32_t array (Path A)
- +0x24: uint32_t — bits [2:0] = cubemap flag
- +0x2c: uint8_t — bit 1 = fast path selector
- +0x2f: uint8_t — index into inner array (Path A)

## Evidence manifest

| Step | CVirtualTexture no-arg | ITexture::computeSizeInBytes |
|------|----------------------|---------------------------|
| DISCOVERED | Symbol from .symtab; 0x7863e4, 680B | Symbol from .symtab; 0x77d99c, 128B |
| ANALYZED | Capstone A32; 4 loop variants traced; control flow mapped | Capstone A32; two-path dispatch, align/cubemap traced |
| SOURCE | block_007863e4.cpp (needs pixel_format.h headers) | block_0077d99c.cpp (standalone inline helpers) |
| ARM_COMPILES | Not done (time constraint) | Not done (time constraint) |
| COMPARISON_CHECKED | Not done | Not done |
| BEHAVIOR_TESTED | Hand-computed values + table-driven cross-check (40 fmts, 11 dims, 5 cnts, 8 flags) | Hand-computed + table-driven (raw/A/B vals, all flags) |
| VERIFIED_AT_SCOPED_LEVEL | BEHAVIORAL (host) | BEHAVIORAL (host) |

## Original ELF reference

- Path: /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
- SHA-256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf

## Files changed (this pass)

1. `reconstruction/recovered/libNOVA3_neon/block_007863e4.cpp` — NEW (CVirtualTexture no-arg getSizeInBytes)
2. `reconstruction/recovered/libNOVA3_neon/block_0077d99c.cpp` — NEW (ITexture computeSizeInBytes)
3. `reconstruction/tests/virtual_texture_get_size_in_bytes_noarg_test.cpp` — NEW
4. `reconstruction/tests/itexture_compute_size_test.cpp` — NEW
5. `CMakeLists.txt` — +2 test targets (virtual_texture_get_size_in_bytes_noarg, itexture_compute_size)

## Host test results

CTest 19/19 passed. No regressions. Python tests unchanged (13/13 verified in prior passes).

## Cumulative recovered functions

| Function | ELF VA | Bytes | Pass |
|----------|--------|-------|------|
| computePitch | 0x0077d37c | 80 | 2 |
| computeSizeInBytes | 0x0077d3cc | 112 | 2 |
| computeSizeInBytes_slices | 0x0077d43c | 20 | 2 |
| computeMipmapSizeInBytes | 0x0077d450 | 32 | 3 |
| computeMipmapSizeInBytes_slices | 0x0077d470 | 56 | 3 |
| getMipmapCount | 0x007b84e0 | 108 | 4 |
| CVirtualTexture::getSizeInBytes(tgt) | 0x0077d4a8 | 96 | 4 |
| CVirtualTexture::getSizeInBytes() no-arg | 0x007863e4 | 680 | 5 |
| ITexture::computeSizeInBytes() const | 0x0077d99c | 128 | 5 |

Total: 9 functions, all BEHAVIORAL (host), 0 MATCHED_ARM.

## Next functions (priority order)

1. CVirtualTexture::getSizeInBytes(unsigned int) — the overload at 0x0078668c (2484 B).
   Directly follows the no-arg overload; likely the same loop + offset/stride computation.
   Large but approachable since all dependencies (descriptor layout, compute helpers,
   cubemap factors) are now established.

2. CImageLoaderPVR::CDataInfo::getFileDataSize (0x007b8974, 8 B) — trivial accessor,
   `ldr r0,[r0,#0xc]; bx lr`.  Too simple to merit a standalone test, but useful
   for completeness.

3. CColladaDatabase::getVersion (0x0071017c, 20 B) — 4-level pointer chain,
   `ldr r3,[r0]; ldr r3,[r3,#0x10]; ldr r3,[r3,#0x20]; ldr r0,[r3]; bx lr`.
   Also trivial accessor.

4. Additional glitch::video functions in 0x0077d500–0x0077da00 range,
   several in 16–196 byte range (descendant accessors, format queries).

## Current blockers

1. No historical GCC 4.4.3 ARM cross-compiler (unchanged).
2. Byte-level ARM instruction match remains impossible without historical toolchain.
3. CVirtualTexture vtable layout beyond slot 0x14 is UNKNOWN.
4. Descriptor struct layout beyond +0x2f is UNKNOWN.

## Limitations

- CVirtualTexture no-arg function uses compute_size_in_bytes inline (header design),
  so generated ARM will be structurally different from original's 4-path loop unrolling.
- ITexture function requires vtable[0x14] mock for testing; its real implementation
  is not independently recovered.
- All recovered functions are behavioral (host); 0 MATCHED_ARM.
- Field names are INFERRED (descriptor, width, height, mipCount); original names UNKNOWN.