// Source-owned recovery of glitch::video::pixel_format::computeSizeInBytes
// (slices overload) — Premium ELF VA 0x0077d43c, 20 bytes, A32 mode.
// Symbol: _ZN6glitch5video12pixel_format18computeSizeInBytesENS0_14E_PIXEL_FORMATEjjj
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Disassembly (llvm-objdump, A32):
//   77d43c: push {r4, lr}
//   77d440: mov  r4, r3      ; r4 = slices (4th argument)
//   77d444: bl   0x77d3cc    ; r0 = computeSizeInBytes(format, width, height)
//   77d448: mul  r0, r4, r0  ; r0 = slices * size
//   77d44c: pop  {r4, pc}
//
// A pure multiplier over the three-argument overload (0x0077d3cc); the
// arithmetic and descriptor layout live in pixel_format.h.  The extern "C"
// wrapper below is for controlled ARM compilation/comparison only.

#include "pixel_format.h"

extern "C" std::uint32_t nova3_recovered_compute_size_in_bytes_slices(
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t slices,
    const nova3::recovered::pixel_format::PixelFormatDescriptor* table)
{
    return nova3::recovered::pixel_format::compute_size_in_bytes_slices(
        table, format_index, width, height, slices);
}
