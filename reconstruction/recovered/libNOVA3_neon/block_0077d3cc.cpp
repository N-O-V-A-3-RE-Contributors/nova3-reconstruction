// Source-owned recovery of glitch::video::pixel_format::computeSizeInBytes
// Premium ELF VA 0x0077d3cc, 112 bytes, A32 mode.
// Symbol: _ZN6glitch5video12pixel_format18computeSizeInBytesENS0_14E_PIXEL_FORMATEjj
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: see sprint_notes/STATUS.md (ABI differs by design, as
// in the sibling computePitch block — the descriptor table is passed explicitly).
//
// Disassembly summary (llvm-objdump, A32):
//   push {r4-r8,lr}; mov r4,r0(format); mov r7,r2(height)
//   bl  0x77d37c (computePitch)            -> r0 = pitch
//   ldrb r1,[r3,#0x25]                     -> block_height
//   cmp r1,#1 ; mov r6,r0 ; muls r0,r7,r0 -> size = pitch*height
//   bls clamp                              -> if block_height<=1 skip
//   sub r0,r1,#1 ; add r0,r0,r7 ; bl __udivsi3 -> ceil(height/bh)
//   mul r0,r0,r6                           -> size = pitch*ceil(height/bh)
//   ldrb r3,[r3,#0x27]                     -> min_size
//   cmp r0,r3 ; movlo r0,r3                -> clamp
//
// The arithmetic and descriptor layout live in pixel_format.h; this file
// provides the extern "C" wrapper for ARM comparison experiments.

#include "pixel_format.h"

extern "C" std::uint32_t nova3_recovered_compute_size_in_bytes(
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    const nova3::recovered::pixel_format::PixelFormatDescriptor* table)
{
    return nova3::recovered::pixel_format::compute_size_in_bytes(
        table, format_index, width, height);
}
