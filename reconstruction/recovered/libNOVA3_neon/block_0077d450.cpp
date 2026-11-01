// Source-owned recovery of glitch::video::pixel_format::computeMipmapSizeInBytes
// (five-argument overload) — Premium ELF VA 0x0077d450, 32 bytes, A32 mode.
// Symbol: _ZN6glitch5video12pixel_format24computeMipmapSizeInBytesENS0_14E_PIXEL_FORMATEjjhb
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: NOT MATCHED (expected — ABI differs by design, as in the
// sibling computePitch/computeSizeInBytes blocks: the descriptor table is passed
// explicitly rather than loaded via a PC-relative literal pool).
//
// Disassembly (llvm-objdump, A32):
//   77d450: ldrb r12, [sp]      ; skipWidthShift (5th arg, stack)
//   77d454: cmp  r12, #0
//   77d458: bne  0x77d464       ; if set, skip the width shift
//   77d45c: lsrs r1, r1, r3     ; width >>= mipLevel
//   77d460: moveq r1, #1        ; clamp width to 1
//   77d464: lsrs r2, r2, r3     ; height >>= mipLevel
//   77d468: moveq r2, #1        ; clamp height to 1
//   77d46c: b    0x77d3cc       ; tail-call computeSizeInBytes
//
// The arithmetic and descriptor layout live in pixel_format.h; this file
// provides the extern "C" wrapper for controlled ARM compilation/comparison.

#include "pixel_format.h"

extern "C" std::uint32_t nova3_recovered_compute_mipmap_size_in_bytes(
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    std::uint8_t mip_level,
    bool skip_width_shift,
    const nova3::recovered::pixel_format::PixelFormatDescriptor* table)
{
    return nova3::recovered::pixel_format::compute_mipmap_size_in_bytes(
        table, format_index, width, height, mip_level, skip_width_shift);
}
