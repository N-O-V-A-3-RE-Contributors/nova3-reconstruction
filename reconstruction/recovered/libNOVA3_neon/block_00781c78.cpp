// Task #33: data computation in CImageLoaderPVR::loadTextureHeader (0x781e08).
// Local ARM differential tests cover the return and all 32 descriptor bytes.
// NOT MATCHED: this is portable C++, not the original ARM/C++ object ABI.
// IReadFile seek/read/size/name dispatch and diagnostic text are excluded.
// Header validation reuses the existing 0x781c78 predicate; do not duplicate it.
// The 0x7a5088 coordination below delegates the two real engine dependencies;
// it does not implement ITexture upload or the generic IImageLoader::loadData.
// 0x7914a4 remains the partial ETC1 branch in engine/etc1.cpp, unchanged here.

#include "../../engine/pvr_texture.hpp"

#include <cstddef>
#include <cstdint>

namespace nova3::recovered::pvr {

// Names of code fields are deliberately neutral: their native enum names are
// not recovered. Reserved storage is left intact, including on partial failure.
struct TextureDescription {
    std::uint32_t type_code;
    std::uint32_t format_code;
    std::uint32_t layout_code;
    std::uint32_t reserved;
    std::uint32_t width;
    std::uint32_t height;
    std::uint32_t depth;
    std::uint8_t mipmapped;
    std::uint8_t padding[3];
};
static_assert(sizeof(TextureDescription) == 32);
static_assert(offsetof(TextureDescription, width) == 16);
static_assert(offsetof(TextureDescription, mipmapped) == 28);

namespace {
std::uint32_t little_word(const std::uint8_t* p) noexcept {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}

// PVR on-disk format -> engine format. These numeric codes are observed
// metadata, not a claim that all of the corresponding converters exist here.
bool translate_format(std::uint32_t flags, std::uint32_t& format) noexcept {
    const bool alpha = (flags & 0x8000u) != 0;
    switch (flags & 0xffu) {
    case 0x00: format = 6; break;
    case 0x01: format = 8; break;
    case 0x02: case 0x13: format = 5; break;
    case 0x04: case 0x15: format = 10; break;
    case 0x05: case 0x1a: format = 13; break;
    case 0x07: case 0x16: format = 0; break;
    case 0x08: case 0x17: format = 4; break;
    case 0x0c: case 0x18: format = alpha ? 22 : 21; break;
    case 0x0d: case 0x19: format = alpha ? 24 : 23; break;
    case 0x10: format = 7; break;
    case 0x11: format = 9; break;
    case 0x12: format = 14; break;
    case 0x1c: format = alpha ? 26 : 25; break;
    case 0x1d: format = alpha ? 28 : 27; break;
    case 0x20: format = alpha ? 18 : 17; break;
    case 0x21: case 0x22: format = 19; break;
    case 0x23: case 0x24: format = 20; break;
    case 0x2a: format = 16; break;
    case 0x36: format = 29; break;
    case 0x39: format = 2; break;
    case 0x3b: format = 1; break;
    case 0x50: format = 40; break;
    case 0x53: format = 39; break;
    case 0x56: format = 38; break;
    case 0x60: format = alpha ? 31 : 30; break;
    default: return false;
    }
    return true;
}
} // namespace

// header points to a previously read, possibly unaligned file prefix.
// file_size is the original 32-bit IReadFile size result, not available bytes.
// Only the prefix is read here; the caller must separately validate payloads.
// The native routine accepts zero dimensions, inconsistent bpp/masks and
// wrapped file lengths. Preserve those metadata rules without allocating or
// reading a payload. Errors can leave part of description updated, as observed.
bool load_texture_header(const std::uint8_t* header, std::size_t available,
                         std::uint32_t file_size,
                         TextureDescription& description) noexcept {
    if (!valid_pvr_v2_header(header, available)) return false;

    const auto flags = little_word(header + 16);
    const bool cube = (flags & 0x1000u) != 0;
    const bool volume = !cube && (flags & 0x4000u) != 0;
    description.type_code = cube ? 3u : (volume ? 2u : 1u);
    description.width = little_word(header + 8);
    description.height = little_word(header + 4);
    description.depth = volume ? little_word(header + 48) : 1u;
    description.layout_code = (flags & 0x200u) ? 3u : 0u;
    description.mipmapped = (flags & 0x100u) ? 1u : 0u;

    // Unsigned arithmetic intentionally retains the observed ARM32 wraparound.
    const std::uint32_t payload = little_word(header + 20);
    const std::uint32_t expected_size = 52u + payload * (cube ? 6u : 1u);
    if (file_size != expected_size) return false;
    if (!translate_format(flags, description.format_code)) return false;
    if (description.layout_code == 3u &&
        (description.format_code < 17u || description.format_code > 31u))
        return false;
    return true;
}

// Observable CImageLoaderPVR::CDataInfo results passed to loadData. The offset
// is within each face's payload, excluding the PVR header. File pitch is zero,
// isLittleEndian is true and needsFlip is false in the probed implementation.
struct DataInfo {
    std::uint32_t file_data_size;
    std::uint32_t face_start_offset;
    constexpr std::uint32_t file_pitch(unsigned /*mip*/) const noexcept { return 0; }
    constexpr bool little_endian() const noexcept { return true; }
    constexpr bool needs_flip() const noexcept { return false; }
};

// Portable dependency boundary for the real 0x77f604 and 0x7a4988 callees.
// The owner supplies mip selection and actual file/texture loading. There is
// no default success implementation. Selection returns an ARM32 mip count;
// 0xffffffff signals failure. Other counts must be reasonably bounded: like
// the original, this routine iterates the supplied count without clamping it.
class DataLoadServices {
public:
    virtual ~DataLoadServices() = default;
    virtual std::uint32_t choose_mipmap(std::uint32_t source_width,
                                       std::uint32_t source_height,
                                       const TextureDescription& target) = 0;
    virtual bool load_data(const DataInfo& info, const TextureDescription& target) = 0;
};

// 0x7a5088, after modeled IReadFile header I/O, with explicit engine services.
// Header acceptance, dimensions, dispatch order, DataInfo results and delegate
// result are testable independently of a GPU. No payload is read by this code.
// Like the native wrapper, this uses header bpp directly and does not validate
// the file length or pixel-format code before delegating to generic loadData.
bool load_texture_data(const std::uint8_t* header, std::size_t available,
                       std::uint32_t file_size,
                       const TextureDescription& target, DataLoadServices& services) {
    if (!valid_pvr_v2_header(header, available)) return false;
    auto width = little_word(header + 8);
    auto height = little_word(header + 4);
    std::uint32_t skipped_mips = 0;
    // This wrapper tests the count, unlike the descriptor's mip flag above.
    // A nonzero count without flag 0x100 still invokes the selector.
    if (little_word(header + 12) != 0) {
        skipped_mips = services.choose_mipmap(width, height, target);
        if (skipped_mips == 0xffffffffu) return false;
    } else if (width != target.width || height != target.height) {
        return false;
    }

    const auto bits_per_pixel = little_word(header + 24);
    std::uint32_t offset = 0;
    for (std::uint32_t mip = 0; mip < skipped_mips; ++mip) {
        // Multiplication wraps BEFORE division, as observed on ARM32. Tiny
        // compressed levels can consequently contribute zero bytes here.
        const std::uint32_t bits = width * height * bits_per_pixel;
        offset += bits >> 3;
        width = width > 1u ? width >> 1 : 1u;
        height = height > 1u ? height >> 1 : 1u;
    }
    const DataInfo info{file_size - 52u - offset, offset};
    return services.load_data(info, target);
}

} // namespace nova3::recovered::pvr
