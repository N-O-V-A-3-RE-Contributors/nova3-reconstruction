// Task 41: portable control-policy subsets, NOT whole recovered entry points.
// Reference SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// All four original functions remain PARTIAL / NOT MATCHED. See the task's
// coordination/evidence/functions-c9e13b3c562667c083cda1.json for scope/tests.
// No original engine ABI, renderer, intrusive ownership, or cache is supplied.

#include <cstdint>
#include <optional>

namespace nova3::recovered::block_006ca8a0 {

// 0x006ca8a0: select one of the count/pointer pairs in SInstanceMaterial.
// The meanings of the five flag families/table names are not established.
// Earlier families take precedence even if their selected table is empty.
// The caller must obtain flags from the original renderer dependency and must
// handle the material's existing cached-map/force-rebuild path separately.
[[nodiscard]] std::optional<std::uint32_t>
select_instance_material_binding_table(std::uint32_t flags) noexcept {
    if ((flags & 0x007u) != 0) return 0x1cu;
    if ((flags & 0x018u) != 0) return 0x24u;
    if ((flags & 0x460u) != 0) return 0x14u;
    if ((flags & 0x300u) != 0) return 0x2cu;
    if ((flags & 0x080u) != 0) return 0x34u;
    return std::nullopt;
}

enum class MaterialRendererRoute : std::uint8_t {
    null_shader_manager,
    pink_wireframe,
    cached_renderer,
    effect_builder,
};

// 0x006cad1c: dispatch after the driver's virtual +0x48 query. The precise
// driver-query API/name is unresolved; only zero versus nonzero is known.
// cached_id is the result of the manager's getId lookup. That lookup is only
// relevant when driver_mode != 0 and an effect exists. This policy does not
// perform the lookup or build the default shader/render states/effect list.
[[nodiscard]] MaterialRendererRoute select_material_renderer_route(
    std::uint32_t driver_mode, bool has_effect,
    std::uint16_t cached_id) noexcept {
    if (driver_mode == 0) return MaterialRendererRoute::null_shader_manager;
    if (!has_effect) return MaterialRendererRoute::pink_wireframe;
    if (cached_id != 0xffffu) return MaterialRendererRoute::cached_renderer;
    return MaterialRendererRoute::effect_builder;
}

enum class MeshBufferLookup : std::uint8_t {
    create_private,
    reuse_shared,
    lock_and_recheck,
};

// Shared by 0x006d7f60 and the inlined construction in 0x006d8888.
// The serialized SMeshBuffer record stride is 56, and its runtime cache slot
// is +0x34. Private construction ignores that slot, including a populated one.
// A cache miss must be checked again after acquiring the engine's lock.
[[nodiscard]] MeshBufferLookup select_mesh_buffer_lookup(
    bool share, bool initially_cached) noexcept {
    if (!share) return MeshBufferLookup::create_private;
    if (initially_cached) return MeshBufferLookup::reuse_shared;
    return MeshBufferLookup::lock_and_recheck;
}

enum class LockedMeshBufferLookup : std::uint8_t {
    unlock_and_reuse_shared,
    create_and_publish_shared,
};

// Valid only while the caller holds the shared-cache lock. A value may have
// been published between the initial lookup and this second observation.
// This returns the next operation, not a buffer or a claim that it was built.
// Allocation, construction, publication, barriers, unlock, and refcounts are
// deliberately outside this reconstructed control-policy subset.
[[nodiscard]] LockedMeshBufferLookup recheck_mesh_buffer_cache(
    bool cached_under_lock) noexcept {
    return cached_under_lock
        ? LockedMeshBufferLookup::unlock_and_reuse_shared
        : LockedMeshBufferLookup::create_and_publish_shared;
}

} // namespace nova3::recovered::block_006ca8a0
