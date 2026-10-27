// Task #44; reference SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// 0x7b8974/0x7b8984: two ARM32 metadata accessors, with explicit storage layout.
// 0x7b1dec: portable public texture lookup policy; original object/string ABI,
// intrusive ownership and exception unwinding are outside this interface.
// 0x7b13e0 remains NOT RECONSTRUCTED. The dependency below has no default
// implementation: ZIP handling, decoding, caching and GPU work need recovery.

namespace nova3::recovered::texture_lookup_007b13e0 {

// Only these two fields are understood. The preceding twelve bytes include
// native object state; they are deliberately opaque, not host pointer fields.
// This is a view of ARM32 storage, not the complete original C++ class.
struct PvrDataInfoArm32 {
    unsigned int opaque_prefix[3];
    unsigned int file_data_size;
    unsigned int face_start_offset;
};
static_assert(sizeof(unsigned int) == 4);
static_assert(sizeof(PvrDataInfoArm32) == 20);
static_assert(__builtin_offsetof(PvrDataInfoArm32, file_data_size) == 12);
static_assert(__builtin_offsetof(PvrDataInfoArm32, face_start_offset) == 16);

// Non-null, aligned, readable object required, as for the native methods.
// C linkage supplies stable names for the independently compiled ARM object.
extern "C" unsigned int nova3_007b8974(const PvrDataInfoArm32* info) noexcept {
    return info->file_data_size;
}

extern "C" unsigned int nova3_007b8984(const PvrDataInfoArm32* info) noexcept {
    return info->face_start_offset;
}

} // namespace nova3::recovered::texture_lookup_007b13e0

// Allows a freestanding ARM build of precisely the two accessors without an
// Android C++ sysroot. The normal production build includes the lookup policy.
#ifndef NOVA3_TASK44_ACCESSORS_ONLY
#include <memory>
#include <string>

namespace nova3::recovered::texture_lookup_007b13e0 {

class Texture {
public:
    virtual ~Texture() = default;
};

class ReadFile {
public:
    virtual ~ReadFile() = default;
};

using TexturePtr = std::shared_ptr<Texture>;
using FilePtr = std::shared_ptr<ReadFile>;

// Required engine services, not substitute implementations. In particular,
// get_hash_name is the manager's 0x79a944, not CCustomResFactory::GetHashName.
class LookupServices {
public:
    virtual ~LookupServices() = default;
    virtual std::string get_hash_name(const char* filename) = 0;
    virtual TexturePtr find_texture(const char* key) = 0;
    virtual FilePtr open_file(const char* filename) = 0;
    virtual TexturePtr get_texture_internal(const FilePtr& file,
                                            const std::string& key,
                                            bool flag,
                                            const TexturePtr& first,
                                            const TexturePtr& second) = 0;
    virtual void report_open_failure(const char* filename, unsigned int level) = 0;
};

// 0x7b1dec: filename must be a valid NUL-terminated string. A null key asks the
// manager to generate a key; a non-null empty key stays empty. Cache hits skip
// opening/loading altogether. The internal loader receives false and two null
// textures. Its result, including failure, is propagated without a retry.
TexturePtr get_texture(const char* filename, const char* explicit_key,
                       LookupServices& services) {
    const std::string key = explicit_key ? std::string(explicit_key)
                                         : services.get_hash_name(filename);
    auto texture = services.find_texture(key.c_str());
    if (texture) return texture;

    auto file = services.open_file(filename);
    if (!file) {
        services.report_open_failure(filename, 3);
        return {};
    }
    return services.get_texture_internal(file, key, false, {}, {});
}

} // namespace nova3::recovered::texture_lookup_007b13e0
#endif
