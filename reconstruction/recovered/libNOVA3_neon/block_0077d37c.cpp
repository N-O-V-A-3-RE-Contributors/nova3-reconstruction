// Source-owned recovery of glitch::video::pixel_format::computePitch
// Premium ELF VA 0x0077d37c, 80 bytes, A32 mode.
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: NOT MATCHED.  No historical compiler/flags claim.
//
// This function computes the byte pitch (row stride) for a pixel format
// and width.  It uses a pixel-format descriptor table indexed by the
// E_PIXEL_FORMAT enum.  The original table lives in .rodata at ELF VA
// 0x00c7ff50 with exactly 40-byte entries; this source preserves that
// layout so that the same raw table can be used as an oracle.
//
// Preconditions: table refers to a valid PixelFormatDescriptor array
// with at least (format_index + 1) entries.  Width may be any uint32_t;
// the result is defined for all inputs but overflow for unrealistic
// widths is the caller's concern (the original has no guard).
//
// The original calls __aeabi_uidiv for unsigned division on compressed
// block widths > 1.  We use standard C++ unsigned division, which
// ARMv7-A without hardware divide delegates to __aeabi_uidiv when
// compiled with an appropriate target that lacks hwdiv.

#include <cstdint>

namespace nova3::recovered::block_0077d37c {

// PixelFormatDescriptor mirrors the 40-byte entries at ELF VA 0xc7ff50.
// Only offsets 0x15, 0x16, and 0x24 are used by computePitch; the
// remaining bytes are reserved for computeSizeInBytes and other
// consumers.  The 40-byte stride is mandatory for raw-table oracle
// compatibility.
struct alignas(4) PixelFormatDescriptor {
    // Bytes 0x00–0x14: opaque (format identifier, masks, other metadata).
    std::uint8_t _reserved_00_14[0x15];

    // Offset 0x15 (21): bytes per compressed block.  Used when
    // block_width > 1.  Reserved/zero for uncompressed formats.
    std::uint8_t bytes_per_block;

    // Offset 0x16 (22): bits per pixel for uncompressed formats
    // (block_width <= 1).  Reserved/zero for compressed.
    std::uint8_t bits_per_pixel;

    // Bytes 0x17–0x23: opaque (block height at 0x25, min size at 0x27).
    std::uint8_t _reserved_17_23[0x24 - 0x17];

    // Offset 0x24 (36): block width for compressed formats.
    // Value 0 or 1 for uncompressed.  The threshold in the original
    // is `cmp r1, #1; bls` (branch if lower-or-same, i.e. <= 1).
    std::uint8_t block_width;

    // Bytes 0x25–0x27: opaque (used by computeSizeInBytes).
    std::uint8_t _reserved_25_27[3];
};

static_assert(sizeof(PixelFormatDescriptor) == 0x28,
              "Descriptor must be exactly 40 bytes for original-table compatibility");

// computePitch  —  ELF VA 0x0077d37c, 80 bytes, A32.
//
// Behaviour:
//   if block_width <= 1  →  pitch = (bits_per_pixel * width) >> 3
//   else                  →  pitch = bytes_per_block *
//                                    ceil(width / block_width)
//          where ceil(a/b) = (a + b - 1) / b
//
// The original function loads the table base via PC-relative literal
// pool entry, computes index * 40 (multiply by 5, then shift-left 3),
// and selects the computation branch based on the byte at offset 0x24.
// The arithmetic is identical; register allocation, literal-pool layout
// and the __aeabi_uidiv call site are not reproduced.
inline std::uint32_t compute_pitch(
    const PixelFormatDescriptor* table,
    std::uint32_t format_index,
    std::uint32_t width) noexcept
{
    const auto& desc = table[format_index];
    if (desc.block_width <= 1) {
        // Uncompressed: (bpp * width) >> 3
        return (static_cast<std::uint32_t>(desc.bits_per_pixel) * width) >> 3;
    }
    // Compressed: block_bytes * ceil(width / block_width)
    const std::uint32_t bw = static_cast<std::uint32_t>(desc.block_width);
    const std::uint32_t blocks = (bw - 1 + width) / bw;
    return blocks * static_cast<std::uint32_t>(desc.bytes_per_block);
}

// Original-ABI-compatible wrapper for comparison and oracle testing.
// r0 = format_index, r1 = width, r2 = table base pointer.
// Returns pitch in r0.  This signature differs from the original
// two-argument C++ member function; it exists only for controlled
// compilation and comparison experiments.
extern "C" std::uint32_t nova3_recovered_compute_pitch(
    std::uint32_t format_index,
    std::uint32_t width,
    const PixelFormatDescriptor* table)
{
    return compute_pitch(table, format_index, width);
}

} // namespace nova3::recovered::block_0077d37c