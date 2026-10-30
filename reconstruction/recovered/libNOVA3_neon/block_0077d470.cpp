// Source-owned recovery of glitch::video::pixel_format::computeMipmapSizeInBytes
// (six-argument slices/depth overload) — Premium ELF VA 0x0077d470, 56 bytes, A32.
// Symbol: _ZN6glitch5video12pixel_format24computeMipmapSizeInBytesENS0_14E_PIXEL_FORMATEjjjhb
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: NOT MATCHED (expected — ABI differs by design, as above).
//
// Disassembly (llvm-objdump, A32):
//   77d470: push {r4, lr}
//   77d474: ldrb r12, [sp, #0xc] ; skipWidthShift (6th arg)
//   77d478: cmp  r12, #0
//   77d47c: ldrb r12, [sp, #0x8] ; mipLevel (5th arg)
//   77d480: bne  0x77d48c        ; if set, skip the width shift
//   77d484: lsrs r1, r1, r12     ; width >>= mipLevel
//   77d488: moveq r1, #1
//   77d48c: lsrs r2, r2, r12     ; height >>= mipLevel
//   77d490: moveq r2, #1
//   77d494: lsrs r4, r3, r12     ; depth >>= mipLevel
//   77d498: moveq r4, #1
//   77d49c: bl   0x77d3cc        ; computeSizeInBytes(format, width, height)
//   77d4a0: mul  r0, r4, r0      ; return depth * size
//   77d4a4: pop  {r4, pc}
//
// The arithmetic and descriptor layout live in pixel_format.h; this file
// provides the extern "C" wrapper for controlled ARM compilation/comparison.

#include "pixel_format.h"

extern "C" std::uint32_t nova3_recovered_compute_mipmap_size_in_bytes_slices(
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t depth,
    std::uint8_t mip_level,
    bool skip_width_shift,
    const nova3::recovered::pixel_format::PixelFormatDescriptor* table)
{
    return nova3::recovered::pixel_format::compute_mipmap_size_in_bytes_slices(
        table, format_index, width, height, depth, mip_level, skip_width_shift);
}
