// CColladaDatabase accessor cluster.
//
// Reference ELF: Premium retail lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// All accessors share a three-level walk from the database object:
//   r3 = *(this+0x00)       ; ResourceFile*
//   r3 = *(r3+0x10)         ; ResourceHeader* (word index 4)
//   r3 = *(r3+0x20)         ; ColladaRoot*     (word index 8)
//
// Then each accessor reads/writes a specific field at a documented offset:
//
//   getVersion          0x0071017c  r3[0]  (word index 0)
//   linkInstanceMaterial 0x00710160 r3[2] read/write; instance->next at +0x40
//   getImage            0x00710318  r3[27] base, element stride 16
//   getMaterial         0x0071034c  r3[31] base, element stride 36
//   getGeometry         0x00710368  r3[34] base, element stride 16
//   getMaterial by name 0x007146ac r3[0x78/4] count, r3[0x7c/4] base; stride 36
//   getImage by name    0x0071476c r3[0x68/4] count, r3[0x6c/4] base; stride 16
//
// All pointer offsets are expressed as uint32_t word indices so the
// exact integer arithmetic matches the ARM32 A32 instructions.

#pragma once

#include <cstdint>
#include <cstring>

namespace nova3::recovered::collada {

// Walk the three-level chain: database -> resource(+0x00) ->
// header(+0x10) -> collada_root(+0x20).  Returns a pointer to the
// collada root word array.
inline const std::uint32_t* _walk_to_collada(const void* database) noexcept {
    const auto* root = static_cast<const std::uint32_t*>(database);
    const auto* node1 = reinterpret_cast<const std::uint32_t*>(root[0]);   // +0x00
    const auto* node2 = reinterpret_cast<const std::uint32_t*>(node1[4]);  // +0x10
    return reinterpret_cast<const std::uint32_t*>(node2[8]);               // +0x20
}

// Returns the version word at collada_root[0] (offset +0x00).
inline std::uint32_t get_version(const void* database) noexcept {
    const auto* collada = _walk_to_collada(database);
    return collada[0];
}

// linkInstanceMaterial (0x00710160, 28 B): push-front insert.
// Reads collada_root[2] (head), writes to instance[16] (+0x40),
// then writes instance back to collada_root[2].
inline void link_instance_material(void* database, void* instance) noexcept {
    const auto* collada_c = _walk_to_collada(database);
    auto* collada = const_cast<std::uint32_t*>(collada_c);
    auto* inst = static_cast<std::uint32_t*>(instance);
    inst[16] = collada[2];                     // instance+0x40 = old head
    collada[2] = static_cast<std::uint32_t>(   // head = instance
        reinterpret_cast<std::uintptr_t>(instance));
}

// getImage (0x00710318, 24 B): indexed array access.
// collada_root[27] (+0x6c) base, element stride 16.
inline std::uint32_t get_image(const void* database, std::int32_t index) noexcept {
    const auto* collada = _walk_to_collada(database);
    return collada[27] + static_cast<std::uint32_t>(index) * 16u;
}

// getMaterial (0x0071034c, 28 B): indexed array access.
// collada_root[31] (+0x7c) base, element stride 36.
inline std::uint32_t get_material(const void* database, std::int32_t index) noexcept {
    const auto* collada = _walk_to_collada(database);
    return collada[31] + static_cast<std::uint32_t>(index) * 36u;
}

// getGeometry (0x00710368, 24 B): indexed array access.
// collada_root[34] (+0x88) base, element stride 16.
inline std::uint32_t get_geometry(const void* database, std::int32_t index) noexcept {
    const auto* collada = _walk_to_collada(database);
    return collada[34] + static_cast<std::uint32_t>(index) * 16u;
}

// getMaterial by name (0x007146ac, 96 B): linear strcmp search, stride 36.
// collada[0x78] = material count (signed i32); collada[0x7c] = materials ptr.
// Returns pointer to MaterialRecord (36-byte stride, id ptr at +0x00) or null.
inline std::uint32_t get_material_by_name(const void* database, const char* name) noexcept {
    const auto* collada = _walk_to_collada(database);
    const std::int32_t count = static_cast<std::int32_t>(collada[0x78u / 4u]);
    if (count <= 0) return 0;
    const std::uint32_t* rec = reinterpret_cast<const std::uint32_t*>(collada[0x7cu / 4u]);
    std::int32_t i = 0;
    // Original non-loop-header entry tests element 0, then increments i,
    // advances rec by stride, and checks i==count at the top of the loop.
    goto first;
loop:
    if (i == count) return 0;
    rec = reinterpret_cast<const std::uint32_t*>(
        reinterpret_cast<const unsigned char*>(rec) + 36u);
first:
    {
        const char* const* id_ptr = reinterpret_cast<const char* const*>(rec);
        if (std::strcmp(*id_ptr, name) == 0)
            return static_cast<std::uint32_t>(reinterpret_cast<std::uintptr_t>(rec));
    }
    ++i;
    goto loop;
}

// getImage by name (0x0071476c, 96 B): linear strcmp search, stride 16.
// collada[0x68] = image count (signed i32); collada[0x6c] = images ptr.
// Returns pointer to ImageRecord (16-byte stride, id ptr at +0x00) or null.
inline std::uint32_t get_image_by_name(const void* database, const char* name) noexcept {
    const auto* collada = _walk_to_collada(database);
    const std::int32_t count = static_cast<std::int32_t>(collada[0x68u / 4u]);
    if (count <= 0) return 0;
    const std::uint32_t* rec = reinterpret_cast<const std::uint32_t*>(collada[0x6cu / 4u]);
    std::int32_t i = 0;
    goto first;
loop:
    if (i == count) return 0;
    rec = reinterpret_cast<const std::uint32_t*>(
        reinterpret_cast<const unsigned char*>(rec) + 16u);
first:
    {
        const char* const* id_ptr = reinterpret_cast<const char* const*>(rec);
        if (std::strcmp(*id_ptr, name) == 0)
            return static_cast<std::uint32_t>(reinterpret_cast<std::uintptr_t>(rec));
    }
    ++i;
    goto loop;
}

}  // namespace nova3::recovered::collada

// Source-owned extern "C" wrappers for controlled ARM ET_REL compilation
// and comparison.  Each wrapper is a trivial pass-through so the inline
// C++ is what the compiler actually processes.
extern "C" void         nova3_recovered_collada_link_instance_material(void*, void*);
extern "C" std::uint32_t nova3_recovered_collada_get_image(const void*, std::int32_t);
extern "C" std::uint32_t nova3_recovered_collada_get_material(const void*, std::int32_t);
extern "C" std::uint32_t nova3_recovered_collada_get_geometry(const void*, std::int32_t);
extern "C" std::uint32_t nova3_recovered_collada_database_version(const void*);
extern "C" std::uint32_t nova3_recovered_collada_get_material_by_name(const void*, const char*);
extern "C" std::uint32_t nova3_recovered_collada_get_image_by_name(const void*, const char*);