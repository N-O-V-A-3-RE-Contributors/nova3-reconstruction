// Host behavioural tests for CColladaDatabase accessor cluster.
// Covers: getVersion, linkInstanceMaterial, getImage, getMaterial, getGeometry.
//
// Reference ELF: Premium retail lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// All functions share a 3-level chain:
//   database[0] -> resource[4] -> header[8] -> collada_root
// Offsets: version@0, instance_head@2, images@27(0x6c), materials@31(0x7c),
//          geometries@34(0x88).
//
// Linked non-PIE so the 32-bit pointer walks are addressable on LP64.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "ccollada_database.h"

using namespace nova3::recovered::collada;

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

// --- Static chain structures ---

// Database object: +0x00 = pointer to ResourceFile
static std::uint32_t database[1];              // [0] = &resource_file
// ResourceFile: +0x00..+0x0c opaque, +0x10 = header pointer
static std::uint32_t resource_file[5];        // [4] = &resource_header
// ResourceHeader: +0x00..+0x1c opaque, +0x20 = collada ptr
static std::uint32_t resource_header[9];       // [8] = &collada_root
// ColladaRoot:
//   [0] = version, [2] = instance list head,
//   [27] = image base (0x6c), [31] = material base (0x7c),
//   [34] = geometry base (0x88)
static std::uint32_t collada_root[35];

// Instance record layout: +0x00..+0x3c opaque, +0x40 = next pointer
static std::uint32_t instance_a[17];           // index 16 = next
static std::uint32_t instance_b[17];

// Dummy base arrays (just valid addresses for arithmetic)
static std::uint32_t image_array[32];          // 16 bytes/elem
static std::uint32_t material_array[72];       // 36 bytes/elem
static std::uint32_t geometry_array[32];       // 16 bytes/elem

static void init_chain() {
    std::memset(database, 0, sizeof(database));
    std::memset(resource_file, 0, sizeof(resource_file));
    std::memset(resource_header, 0, sizeof(resource_header));
    std::memset(collada_root, 0, sizeof(collada_root));
    std::memset(instance_a, 0, sizeof(instance_a));
    std::memset(instance_b, 0, sizeof(instance_b));

    // Chain: database -> resource_file -> resource_header -> collada_root
    database[0] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&resource_file[0]));
    resource_file[4] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&resource_header[0]));
    resource_header[8] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&collada_root[0]));

    // Collada root fields
    collada_root[0] = 0x00C0FFEEu;   // version word
    collada_root[27] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&image_array[0]));
    collada_root[31] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&material_array[0]));
    collada_root[34] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&geometry_array[0]));
}

int main() {
    // Guard: all static arrays must be 32-bit addressable for pointer walks.
    const auto addr_database = reinterpret_cast<std::uintptr_t>(&database[0]);
    const auto addr_resource_file = reinterpret_cast<std::uintptr_t>(&resource_file[0]);
    if (addr_database > 0xFFFFFFFFu ||
        addr_resource_file > 0xFFFFFFFFu) {
        std::fprintf(stderr, "SKIP: static chain not 32-bit addressable\n");
        return 0;
    }

    init_chain();

    // Only the database root is passed from here; the rest is opaque.
    const void* db = &database[0];

    // getVersion: walks the 3-level chain, reads word at collada[0].
    CHECK(get_version(db) == 0x00C0FFEEu,
          "getVersion returns collada_root[0]");

    // --- getImage (0x00710318): base at collada[27], stride 16 ---
    {
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&image_array[0]);
        CHECK(get_image(db, 0) == base,
              "getImage idx=0 returns base address");
        CHECK(get_image(db, 1) == base + 16u,
              "getImage idx=1 returns base+16");
        CHECK(get_image(db, 3) == base + 48u,
              "getImage idx=3 returns base+48");
        CHECK(get_image(db, 5) == base + 80u,
              "getImage idx=5 returns base+80");
    }

    // --- getMaterial (0x0071034c): base at collada[31], stride 36 ---
    {
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&material_array[0]);
        CHECK(get_material(db, 0) == base,
              "getMaterial idx=0 returns base address");
        CHECK(get_material(db, 1) == base + 36u,
              "getMaterial idx=1 returns base+36");
        CHECK(get_material(db, 2) == base + 72u,
              "getMaterial idx=2 returns base+72");
        CHECK(get_material(db, 10) == base + 360u,
              "getMaterial idx=10 returns base+360");
    }

    // --- getGeometry (0x00710368): base at collada[34], stride 16 ---
    {
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&geometry_array[0]);
        CHECK(get_geometry(db, 0) == base,
              "getGeometry idx=0 returns base address");
        CHECK(get_geometry(db, 2) == base + 32u,
              "getGeometry idx=2 returns base+32");
        CHECK(get_geometry(db, 7) == base + 112u,
              "getGeometry idx=7 returns base+112");
    }

    // --- linkInstanceMaterial (0x00710160): linked-list push-front ---
    {
        // Start with empty list
        collada_root[2] = 0;

        // Push instance A
        link_instance_material(
            const_cast<void*>(static_cast<const void*>(db)),
            &instance_a[0]);
        CHECK(collada_root[2] == static_cast<std::uint32_t>(
                  reinterpret_cast<std::uintptr_t>(&instance_a[0])),
              "push A: head == &instance_a");
        CHECK(instance_a[16] == 0u,
              "push A: instance_a->next == NULL");

        // Push instance B
        link_instance_material(
            const_cast<void*>(static_cast<const void*>(db)),
            &instance_b[0]);
        CHECK(collada_root[2] == static_cast<std::uint32_t>(
                  reinterpret_cast<std::uintptr_t>(&instance_b[0])),
              "push B: head == &instance_b");
        CHECK(instance_b[16] == static_cast<std::uint32_t>(
                  reinterpret_cast<std::uintptr_t>(&instance_a[0])),
              "push B: instance_b->next == &instance_a");
        // instance_a->next should still be 0
        CHECK(instance_a[16] == 0u,
              "after push B: instance_a->next still NULL");

        // Push instance A again (re-inserts)
        link_instance_material(
            const_cast<void*>(static_cast<const void*>(db)),
            &instance_a[0]);
        CHECK(collada_root[2] == static_cast<std::uint32_t>(
                  reinterpret_cast<std::uintptr_t>(&instance_a[0])),
              "push A again: head == &instance_a");
        CHECK(instance_a[16] == static_cast<std::uint32_t>(
                  reinterpret_cast<std::uintptr_t>(&instance_b[0])),
              "push A again: instance_a->next == &instance_b");
    }

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("ccollada_accessors: all tests passed\n");
    return 0;
}