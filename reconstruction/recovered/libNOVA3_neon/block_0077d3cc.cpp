// Source-owned recovery of glitch::video::pixel_format::computeSizeInBytes
// Premium ELF VA 0x0077d3cc, 112 bytes, A32 mode.
// Symbol: _ZN6glitch5video12pixel_format18computeSizeInBytesENS0_14E_PIXEL_FORMATEjj
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: see sprint_notes/STATUS.md (ABI differs by design, as
// in the sibling computePitch block — we pass the descriptor table explicitly).
//
// Disassembly summary (llvm-objdump, A32):
//   push {r4-r8,lr}; mov r4,r0(format); mov r7,r2(height)
//   bl  0x77d37c (computePitch)            -> r0 = pitch
//   ldrb r1,[r3,#0x25]                     -> block_height (byte)
//   cmp  r1,#1 ; mov r6,r0 ; muls r0,r7,r0 -> size = pitch*height
//   bls  clamp                             -> if block_height<=1 skip
//   sub r0,r1,#1 ; add r0,r0,r7 ; bl __udivsi3 -> ceil(height/block_height)
//   mul r0,r0,r6                           -> size = pitch*ceil(height/bh)
//   ldrb r3,[r3,#0x27]                     -> min_size (byte)
//   cmp r0,r3 ; movlo r0,r3                -> if size<min_size, size=min_size
//   pop {r4-r8,pc}
//
// Both literal-pool entries resolve to the same descriptor table base at
// .rodata VA 0x00c7ff50 (verified: 0x77d3f0+0x502b60 == 0x77d424+0x502b2c
// == 0xc7ff50).
//
// The original calls computePitch (0x0077d37c) through a `bl`; this source
// inlines the identical arithmetic so the block stays self-contained.  The
// pitch formula is byte-for-byte behaviourally identical to the sibling
// block_0077d37c.cpp (see its header comment for the descriptor layout).

#include <cstdint>

namespace nova3::recovered::block_0077d3cc {

// PixelFormatDescriptor mirrors the 40-byte entries at ELF VA 0xc7ff50.
// Layout is identical to block_0077d37c.cpp; the two fields newly used by
// this function are block_height (offset 0x25) and min_size (offset 0x27).
struct alignas(4) PixelFormatDescriptor {
    // Bytes 0x00–0x14: opaque (format identifier, component count, masks).
    std::uint8_t _reserved_00_14[0x15];

    // Offset 0x15 (21): bytes per compressed block.  Used when
    // block_width > 1.  Reserved/zero for uncompressed formats.
    std::uint8_t bytes_per_block;

    // Offset 0x16 (22): bits per pixel for uncompressed formats.
    std::uint8_t bits_per_pixel;

    // Bytes 0x17–0x23: opaque (component counts / masks).
    std::uint8_t _reserved_17_23[0x24 - 0x17];

    // Offset 0x24 (36): block width for compressed formats.
    std::uint8_t block_width;

    // Offset 0x25 (37): block height for compressed formats.  Value 1 for
    // uncompressed.  The original branch is `cmp r1, #1; bls`.
    std::uint8_t block_height;

    // Offset 0x26 (38): opaque.  Observed 1 in every table entry; not
    // consumed by computeSizeInBytes or computePitch.
    std::uint8_t _reserved_26;

    // Offset 0x27 (39): minimum size in bytes.  When the computed size is
    // smaller, this value is returned (unsigned byte, 0..255).
    std::uint8_t min_size;
};

static_assert(sizeof(PixelFormatDescriptor) == 0x28,
              "Descriptor must be exactly 40 bytes for original-table compatibility");

// Inlined pitch computation, behaviourally identical to computePitch at
// ELF VA 0x0077d37c (see block_0077d37c.cpp).  The original issues a `bl`
// to that function; keeping the arithmetic here avoids a cross-block ABI
// dependency on the sibling source file's struct type.
inline std::uint32_t compute_pitch(const PixelFormatDescriptor& desc,
                                   std::uint32_t width) noexcept {
    if (desc.block_width <= 1) {
        return (static_cast<std::uint32_t>(desc.bits_per_pixel) * width) >> 3;
    }
    const std::uint32_t bw = static_cast<std::uint32_t>(desc.block_width);
    const std::uint32_t blocks = (bw - 1u + width) / bw;
    return blocks * static_cast<std::uint32_t>(desc.bytes_per_block);
}

// computeSizeInBytes  —  ELF VA 0x0077d3cc, 112 bytes, A32.
//
// Behaviour:
//   pitch = computePitch(format, width)
//   bh    = desc[format].block_height
//   size  = (bh <= 1) ? pitch * height
//                      : pitch * ceil(height / bh)   // ceil(a/b) = (a+b-1)/b
//   if (size < desc[format].min_size) size = min_size  // unsigned byte clamp
//
// The original computes `height * pitch` first and only recomputes via the
// block-height ceil division when block_height > 1; the result is identical.
inline std::uint32_t compute_size_in_bytes(
    const PixelFormatDescriptor* table,
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height) noexcept
{
    const auto& desc = table[format_index];
    const std::uint32_t pitch = compute_pitch(desc, width);

    std::uint32_t size;
    const std::uint32_t bh = static_cast<std::uint32_t>(desc.block_height);
    if (bh <= 1) {
        size = pitch * height;
    } else {
        const std::uint32_t blocks = (bh - 1u + height) / bh; // ceil(height/bh)
        size = pitch * blocks;
    }

    const std::uint32_t min_size = static_cast<std::uint32_t>(desc.min_size);
    if (size < min_size) {
        size = min_size;
    }
    return size;
}

// Original-ABI-compatible wrapper for comparison and oracle testing.
// r0 = format_index, r1 = width, r2 = height, r3 = table base pointer.
// Returns size in r0.  The original is a three-argument C++ member function
// with the table address embedded via a PC-relative literal pool; this
// signature differs only in taking the table explicitly.
extern "C" std::uint32_t nova3_recovered_compute_size_in_bytes(
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    const PixelFormatDescriptor* table)
{
    return compute_size_in_bytes(table, format_index, width, height);
}

} // namespace nova3::recovered::block_0077d3cc
