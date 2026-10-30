// Shared descriptor and inline helpers for the glitch::video::pixel_format
// family recovered from Premium libNOVA3_neon.so.
//
// Reference ELF SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// The original 40-byte per-format descriptor table lives in .rodata at
// ELF VA 0x00c7ff50.  This header reproduces the layout exactly; the three
// functions recovered from it are:
//
//   compute_pitch                    ELF VA 0x0077d37c  (block_0077d37c.cpp)
//   compute_size_in_bytes            ELF VA 0x0077d3cc  (block_0077d3cc.cpp)
//   compute_size_in_bytes_slices     ELF VA 0x0077d43c  (block_0077d43c.cpp)
//   compute_mipmap_size_in_bytes     ELF VA 0x0077d450  (block_0077d450.cpp)
//   compute_mipmap_size_in_bytes
//     (slices/depth overload)        ELF VA 0x0077d470  (block_0077d470.cpp)
//
// Only five descriptor fields are consumed by these functions:
// bytes_per_block (0x15), bits_per_pixel (0x16), block_width (0x24),
// block_height (0x25), min_size (0x27).  The remaining bytes are opaque.
// All functions take the descriptor table as an explicit pointer; the
// original loads the table base through a PC-relative literal pool.  This is
// a deliberate source-only ABI choice (no embedded global data addresses).
//
// No original executable bytes are reproduced here — only the behavioural
// arithmetic and the observed field layout.

#pragma once

#include <cstdint>

namespace nova3::recovered::pixel_format {

// Mirrors the 40-byte entries at ELF VA 0x00c7ff50.
struct alignas(4) PixelFormatDescriptor {
    // Bytes 0x00–0x14: opaque (format identifier, component counts, masks).
    std::uint8_t opaque_00_14[0x15];

    // Offset 0x15 (21): bytes per compressed block.  Used when block_width > 1.
    std::uint8_t bytes_per_block;

    // Offset 0x16 (22): bits per pixel for uncompressed formats.
    std::uint8_t bits_per_pixel;

    // Bytes 0x17–0x23: opaque.
    std::uint8_t opaque_17_23[0x24 - 0x17];

    // Offset 0x24 (36): block width for compressed formats (0/1 = uncompressed).
    std::uint8_t block_width;

    // Offset 0x25 (37): block height for compressed formats (1 = uncompressed).
    std::uint8_t block_height;

    // Offset 0x26 (38): opaque.  Observed 1 in every table entry.
    std::uint8_t opaque_26;

    // Offset 0x27 (39): minimum size in bytes (unsigned byte clamp).
    std::uint8_t min_size;
};

static_assert(sizeof(PixelFormatDescriptor) == 0x28,
              "Descriptor must be exactly 40 bytes for original-table compatibility");

// computePitch  —  ELF VA 0x0077d37c, 80 bytes, A32.
//   block_width <= 1 -> (bits_per_pixel * width) >> 3
//   else             -> bytes_per_block * ceil(width / block_width)
inline std::uint32_t compute_pitch(const PixelFormatDescriptor* table,
                                   std::uint32_t format_index,
                                   std::uint32_t width) noexcept {
    const auto& d = table[format_index];
    if (d.block_width <= 1) {
        return (static_cast<std::uint32_t>(d.bits_per_pixel) * width) >> 3;
    }
    const std::uint32_t bw = static_cast<std::uint32_t>(d.block_width);
    const std::uint32_t blocks = (bw - 1u + width) / bw; // ceil(width / bw)
    return blocks * static_cast<std::uint32_t>(d.bytes_per_block);
}

// computeSizeInBytes  —  ELF VA 0x0077d3cc, 112 bytes, A32.
//   size = (block_height <= 1) ? pitch * height
//                              : pitch * ceil(height / block_height)
//   return max(size, min_size)      [unsigned byte clamp]
inline std::uint32_t compute_size_in_bytes(const PixelFormatDescriptor* table,
                                           std::uint32_t format_index,
                                           std::uint32_t width,
                                           std::uint32_t height) noexcept {
    const auto& d = table[format_index];
    const std::uint32_t pitch = compute_pitch(table, format_index, width);

    std::uint32_t size;
    const std::uint32_t bh = static_cast<std::uint32_t>(d.block_height);
    if (bh <= 1) {
        size = pitch * height;
    } else {
        const std::uint32_t blocks = (bh - 1u + height) / bh; // ceil(height / bh)
        size = pitch * blocks;
    }

    const std::uint32_t mn = static_cast<std::uint32_t>(d.min_size);
    if (size < mn) {
        size = mn;
    }
    return size;
}

// computeSizeInBytes (slices overload)  —  ELF VA 0x0077d43c, 20 bytes, A32.
//   return slices * computeSizeInBytes(format, width, height)
// (original: mov r4,r3; bl 0x77d3cc; mul r0,r4,r0)
inline std::uint32_t compute_size_in_bytes_slices(const PixelFormatDescriptor* table,
                                                  std::uint32_t format_index,
                                                  std::uint32_t width,
                                                  std::uint32_t height,
                                                  std::uint32_t slices) noexcept {
    return slices * compute_size_in_bytes(table, format_index, width, height);
}

// ARM `lsrs` shift + `moveq #1` clamp, reproduced exactly.  The original
// shifts by a register whose low 8 bits are used; for a 32-bit destination a
// shift amount >= 32 yields 0, which is then clamped to 1.
inline std::uint32_t mip_shift(std::uint32_t value, std::uint8_t level) noexcept {
    const std::uint32_t shifted = (level < 32u) ? (value >> level) : 0u;
    return shifted != 0u ? shifted : 1u;
}

// computeMipmapSizeInBytes  —  ELF VA 0x0077d450, 32 bytes, A32.
//   (format, width, height, mipLevel, skipWidthShift)
//   if (!skipWidthShift) width  = mip_shift(width,  mipLevel)
//   height = mip_shift(height, mipLevel)
//   return computeSizeInBytes(format, width, height)
//
// Cross-edition check: the Freemium libGloftNAHM.so export of the same
// mangled symbol (0x005d2b48, 32 bytes) is instruction-identical apart from
// the tail-call target, confirming the behaviour below.
//
// The trailing bool gates only the width shift.  Its original source name is
// UNKNOWN (INFERRED): callers derive it from a format-descriptor comparison
// (e.g. `(descriptor word >> 3) & 7 == 1`, or `descriptor.field_0x8 == 1`).
// We name it by its observed effect.
inline std::uint32_t compute_mipmap_size_in_bytes(const PixelFormatDescriptor* table,
                                                  std::uint32_t format_index,
                                                  std::uint32_t width,
                                                  std::uint32_t height,
                                                  std::uint8_t mip_level,
                                                  bool skip_width_shift) noexcept {
    if (!skip_width_shift) {
        width = mip_shift(width, mip_level);
    }
    height = mip_shift(height, mip_level);
    return compute_size_in_bytes(table, format_index, width, height);
}

// computeMipmapSizeInBytes (slices/depth overload)  —  ELF VA 0x0077d470,
// 56 bytes, A32.  (format, width, height, depth, mipLevel, skipWidthShift)
//   if (!skipWidthShift) width = mip_shift(width,  mipLevel)
//   height = mip_shift(height, mipLevel)
//   depth  = mip_shift(depth,  mipLevel)
//   return depth * computeSizeInBytes(format, width, height)
//
// Cross-edition check: Freemium libGloftNAHM.so (0x005d2b68, 52 bytes)
// instead tail-calls the four-argument slices overload after applying the
// same three shifts — behaviourally equivalent, structurally different.
inline std::uint32_t compute_mipmap_size_in_bytes_slices(
    const PixelFormatDescriptor* table,
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t depth,
    std::uint8_t mip_level,
    bool skip_width_shift) noexcept {
    if (!skip_width_shift) {
        width = mip_shift(width, mip_level);
    }
    height = mip_shift(height, mip_level);
    depth = mip_shift(depth, mip_level);
    return depth * compute_size_in_bytes(table, format_index, width, height);
}

} // namespace nova3::recovered::pixel_format
