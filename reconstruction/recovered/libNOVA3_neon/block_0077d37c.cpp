// Source-owned recovery of glitch::video::pixel_format::computePitch
// Premium ELF VA 0x0077d37c, 80 bytes, A32 mode.
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: NOT MATCHED.  No historical compiler/flags claim.
//
// The arithmetic and the 40-byte descriptor layout live in pixel_format.h
// (shared with the sibling computeSizeInBytes blocks).  This file provides
// the original-ABI-compatible extern "C" wrapper used for controlled ARM
// compilation and comparison experiments; the original is a two-argument C++
// member function with the table base loaded via a PC-relative literal pool,
// whereas the wrapper takes the table pointer explicitly (r2).

#include "pixel_format.h"

extern "C" std::uint32_t nova3_recovered_compute_pitch(
    std::uint32_t format_index,
    std::uint32_t width,
    const nova3::recovered::pixel_format::PixelFormatDescriptor* table)
{
    return nova3::recovered::pixel_format::compute_pitch(table, format_index, width);
}
