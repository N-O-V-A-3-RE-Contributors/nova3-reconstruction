// Task #39: portable reconstruction of the pixel sizing subset.
// Reference ELF SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// ARM entries: computePitch 0x0077d37c (80 bytes),
//              computeSizeInBytes(format,width,height) 0x0077d3cc (112 bytes).
// NOT MATCHED: no target compiler/ABI or machine-code identity is claimed.
//
// Numeric format IDs 0..49 retain the reference table's ordering. Names for
// individual enumerators have not been recovered. This is a portable API, not
// a declaration of the original C++ ABI. The bounds exception is added policy;
// reference behaviour outside the table is deliberately not reproduced.
//
// The other assigned entries, 0x0074b248 (Collada postLoadProcess) and
// 0x0076c3f4 (res::File::Init(void*)), remain NOT RECONSTRUCTED. In particular,
// this file provides no success stubs for their unresolved dependencies.
#include <cstdint>
#include <stdexcept>

namespace nova3::recovered::block_0074b248 {
namespace {
struct PixelLayout {
    std::uint32_t bits_per_pixel;
    std::uint32_t block_width;
    std::uint32_t block_height;
    std::uint32_t bytes_per_block;
    std::uint32_t minimum_bytes;
};

PixelLayout layout_for(std::uint32_t format) {
    // Grouped sizing properties, not a copy of the original 40-byte records.
    // Memory-access observations locate the reference properties at record
    // offsets +22, +36, +37, +21, +39 respectively (table VA 0x00c7ff50).
    switch (format) {
    case 0: case 2: case 48: return {8, 1, 1, 1, 0};
    case 1: case 3: case 4: case 5: case 6: case 7: case 8: case 9:
    case 42: return {16, 1, 1, 2, 0};
    case 10: case 11: return {24, 1, 1, 3, 0};
    case 12: case 13: case 14: case 15: case 16:
    case 43: case 44: case 45: return {32, 1, 1, 4, 0};
    case 17: case 18: return {4, 4, 4, 8, 0};
    case 19: case 20: return {8, 4, 4, 16, 0};
    case 21: case 22: case 25: case 26: return {2, 8, 4, 8, 32};
    case 23: case 24: case 27: case 28: return {4, 4, 4, 8, 32};
    case 29: case 30: case 32: case 35: return {4, 4, 4, 8, 8};
    case 31: case 33: case 34: case 36: return {8, 4, 4, 16, 16};
    case 37: return {48, 1, 1, 6, 0};
    case 38: return {64, 1, 1, 8, 0};
    case 39: return {96, 1, 1, 12, 0};
    case 40: return {128, 1, 1, 16, 0};
    case 41: return {16, 1, 1, 4, 0};
    case 46: return {1, 1, 1, 1, 0};
    case 47: return {4, 1, 1, 1, 0};
    case 49: return {0, 0, 0, 0, 0};
    default: throw std::out_of_range("pixel format ID must be in [0,49]");
    }
}

std::uint32_t pitch_for(const PixelLayout& layout, std::uint32_t width) {
    if (layout.block_width > 1) {
        // Addition must wrap BEFORE division, including UINT32_MAX inputs.
        const std::uint32_t rounded = width + (layout.block_width - 1);
        return (rounded / layout.block_width) * layout.bytes_per_block;
    }
    // Preserve multiplication overflow before the right shift. Formats with
    // fewer than eight bits per pixel truncate; they do not round up a byte.
    const std::uint32_t bits = width * layout.bits_per_pixel;
    return bits >> 3;
}
} // namespace

std::uint32_t compute_pitch(std::uint32_t format, std::uint32_t width) {
    return pitch_for(layout_for(format), width);
}

std::uint32_t compute_size_in_bytes(std::uint32_t format, std::uint32_t width,
                                    std::uint32_t height) {
    const auto layout = layout_for(format);
    const auto pitch = pitch_for(layout, width);
    std::uint32_t rows = height;
    if (layout.block_height > 1) {
        const std::uint32_t rounded = height + (layout.block_height - 1);
        rows = rounded / layout.block_height;
    }
    const std::uint32_t size = pitch * rows;
    // Apply the minimum after multiplication, even if the product wrapped.
    return size < layout.minimum_bytes ? layout.minimum_bytes : size;
}
} // namespace nova3::recovered::block_0074b248
