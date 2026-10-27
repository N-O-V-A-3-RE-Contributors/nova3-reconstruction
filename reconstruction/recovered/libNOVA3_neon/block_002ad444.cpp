// Task 29: portable recovery of CCustomResFactory name/filter policy.
// ELF VAs: GetHashName 0x2af114, getTexture 0x2af2ac.
// Reference: SHA256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// NOT MATCHED: this API deliberately does not reproduce the ARM custom-allocator
// std::string ABI, copy-on-write storage, or intrusive engine object layouts.
// The reference name says "HashName", but the observed operation is a string
// normalization, not a numerical hash. ASCII case conversion is locale-free.
//
// Other assigned entry points remain NOT RECONSTRUCTED here:
//   0x2ad444: CCustomFileSystem::addScrambledZipFileArchive
//   0x2ad998: CCustomFileSystem::createAndOpenFile
// Their archive ownership and filesystem policies are not represented here.
// get_texture below requires a real texture manager supplied by its caller;
// rendering, decoding, native intrusive ownership and ABI are not recovered.
// See the task evidence JSON for the bounded handoff and oracle limitations.

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

namespace nova3::recovered::libnova3_neon {

std::string get_hash_name(std::string_view path) {
    // Native reverse scans at 0x2af138..0x2af190 take the later of '/' and '\\'.
    const auto separator = path.find_last_of("/\\");
    const auto begin = separator == std::string_view::npos ? 0 : separator + 1;
    const auto dot = path.find_last_of('.');

    // The native 32-bit substring count wraps when the final dot precedes the
    // basename, then substr clamps it to the remaining length. Express that
    // result directly rather than depending on unsigned underflow.
    const auto end = dot != std::string_view::npos && dot >= begin ? dot : path.size();
    std::string result(path.substr(begin, end - begin));
    for (char& byte : result) {
        if (byte >= 'A' && byte <= 'Z') {
            byte = static_cast<char>(byte + ('a' - 'A'));
        }
    }
    return result;
}

// Source-owned dependency boundary. Numeric filter values are intentionally
// retained: their original enum names have not been established. The descriptor
// byte at native texture->0x10->0x2f is INFERRED to count mip levels; only its
// unsigned comparison with 1 is established by the differential tests.
class Texture {
public:
    virtual ~Texture() = default;
    virtual std::uint8_t filter_level_count() const = 0;
    virtual void set_mag_filter(std::uint32_t value) = 0;
    virtual void set_min_filter(std::uint32_t value) = 0;
    virtual void set_anisotropy(float value) = 0;
};

class TextureManager {
public:
    virtual ~TextureManager() = default;
    virtual std::shared_ptr<Texture> load(std::string_view path,
                                          std::string_view cache_key) = 0;
};

// A snapshot of the native globals/options; concurrent mutation is outside this
// portable API's contract. device_anisotropy corresponds to the signed word at
// DeviceOptions::Instance()+0x38, converted to float by the reference ARM body.
struct TexturePolicy {
    bool use_one_texture = false;
    std::uint8_t texture_tests = 0;
    bool use_anisotropy = false;
    std::int32_t device_anisotropy = 0;
};

std::shared_ptr<Texture> get_texture(std::string_view path, bool configure,
                                     const TexturePolicy& policy,
                                     TextureManager& manager) {
    // The original takes const char*: an embedded NUL ends the input name.
    const auto nul = path.find('\0');
    if (nul != std::string_view::npos) path = path.substr(0, nul);
    const auto cache_key = get_hash_name(path);
    // With configure enabled, g_UseOneTex changes the asset, but retains the key
    // computed from the caller's name (0x2af328..0x2af35c).
    auto texture = manager.load(configure && policy.use_one_texture ? "HeatDistort.tga" : path,
                                cache_key);
    if (!texture || !configure) return texture;

    // Order matters: these setters can change renderer state. In particular,
    // test mode clears anisotropy BEFORE setting the minification filter.
    if (policy.texture_tests != 0) {
        texture->set_mag_filter(0);
        if (texture->filter_level_count() > 1) {
            texture->set_anisotropy(0.0f);
            texture->set_min_filter(2);
        } else {
            texture->set_min_filter(1);
        }
    } else {
        texture->set_mag_filter(1);
        if (policy.use_anisotropy) {
            if (texture->filter_level_count() > 1) {
                texture->set_min_filter(4);
                texture->set_anisotropy(4.0f);
            } else {
                texture->set_min_filter(1);
            }
        } else {
            texture->set_min_filter(texture->filter_level_count() > 1 ? 2 : 1);
            texture->set_anisotropy(static_cast<float>(policy.device_anisotropy));
        }
    }
    return texture;
}

} // namespace nova3::recovered::libnova3_neon
