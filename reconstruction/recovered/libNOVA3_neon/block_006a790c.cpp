// Task #38: independently written reconstruction of the orchestration at
// ELF VA 0x006a8444 in reference SHA-256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
//
// This is a portable dependency boundary, not an ARM C++ ABI replacement.
// Addresses below are opaque 32-bit reference-domain values, never host pointers.
// The database's factory is used; the incoming CColladaFactory this-pointer is
// unused by the observed body. The name pointer is advanced by one byte without
// checking its contents, including modulo-2^32 wraparound.
//
// Only this wrapper's dispatch, branching, and temporary cleanup are recovered.
// The dependency methods must supply the real cache, renderer, material and
// intrusive-pointer operations. None is implemented with a success stub here.
// The other assigned functions (0x006a790c, 0x006b0928, 0x006c1d80) remain
// NOT RECONSTRUCTED. See the task evidence JSON for verification scope.

#include <array>
#include <cstdint>

namespace nova3::recovered::libnova3_neon {

using Address32 = std::uint32_t;

struct MaterialHandle {
    Address32 value{};
};

struct RendererHandle {
    Address32 value{};
};

// Semantic views, deliberately not serialized structs or asserted ABI layouts.
struct MaterialView {
    Address32 address{};
    Address32 id{};             // Observed SMaterial +0x00.
    Address32 effect{};         // Observed SMaterial +0x18.
    Address32 effect_uri{};     // Observed SMaterial +0x0c.
    Address32 renderer_name{};  // Observed SMaterial +0x08; precise role unresolved.
};

// Adapter for a seven-word prefix containing this wrapper's accessed fields.
// This is not a file parser and does not assert the full SMaterial size.
MaterialView material_fields_from_words(
    Address32 address, const std::array<Address32, 7>& words) noexcept {
    return {address, words[0], words[6], words[3], words[2]};
}

struct DatabaseView {
    Address32 address{};
    Address32 factory{};        // Observed CColladaDatabase +0x04.
};

// These callbacks correspond to existing, separately recoverable dependencies.
// They must write their output handles and must not throw. destroy_* represents
// invocation of an intrusive_ptr destructor, including for an empty handle;
// assign_material includes the dependency's reference-counting semantics.
class MaterialDependencies {
public:
    virtual ~MaterialDependencies() = default;

    virtual void find_material(MaterialHandle& out, Address32 root,
                               Address32 id) noexcept = 0;
    virtual void create_renderer(RendererHandle& out, Address32 factory,
                                 const DatabaseView& database, Address32 driver,
                                 Address32 effect, Address32 name_after_prefix,
                                 Address32 renderer_name, Address32 root) noexcept = 0;
    virtual void build_material(MaterialHandle& out, const DatabaseView& database,
                                Address32 driver, RendererHandle renderer,
                                const MaterialView& material, Address32 root) noexcept = 0;
    virtual void assign_material(MaterialHandle& out, MaterialHandle temporary) noexcept = 0;
    virtual void destroy_material_temporary(MaterialHandle temporary) noexcept = 0;
    virtual void destroy_renderer_temporary(RendererHandle temporary) noexcept = 0;
};

// out is fresh return storage, not an existing owned intrusive reference.
MaterialHandle& factory_create_material_006a8444(
    MaterialHandle& out, MaterialDependencies& dependencies,
    const DatabaseView& database, Address32 driver,
    const MaterialView& material, Address32 root) noexcept {
    if (root != 0) {
        dependencies.find_material(out, root, material.id);
        if (out.value != 0) {
            return out;
        }
    }

    out.value = 0;
    RendererHandle renderer;
    const Address32 name_after_prefix = static_cast<Address32>(
        static_cast<std::uint64_t>(material.effect_uri) + 1);
    dependencies.create_renderer(renderer, database.factory, database, driver,
                                 material.effect, name_after_prefix,
                                 material.renderer_name, root);
    if (renderer.value != 0) {
        MaterialHandle temporary;
        dependencies.build_material(temporary, database, driver, renderer,
                                    material, root);
        dependencies.assign_material(out, temporary);
        dependencies.destroy_material_temporary(temporary);
    }
    dependencies.destroy_renderer_temporary(renderer);
    return out;
}

}  // namespace nova3::recovered::libnova3_neon
