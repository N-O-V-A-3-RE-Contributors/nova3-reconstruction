// Host behavioural tests for Collada named-parameter accessors:
// getMaterial by name (0x007146ac) and getImage by name (0x0071476c).
//
// Reference ELF: Premium retail lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Both functions share the 3-level chain from database -> collada_root,
// then do a linear strcmp scan.  Strides: 36 (material), 16 (image).
// Linked non-PIE so 32-bit pointer walks are addressable on LP64.
//
// Record layouts are stored as uint32_t arrays to enforce 32-bit strides
// regardless of host pointer size. The first word of each record stores
// a pointer to a C string name.
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
static std::uint32_t database[1];
// ResourceFile: +0x00..+0x0c opaque, +0x10 = header pointer
static std::uint32_t resource_file[5];        // [4] = &resource_header
// ResourceHeader: +0x00..+0x1c opaque, +0x20 = collada ptr
static std::uint32_t resource_header[9];       // [8] = &collada_root

// Collada root word array.  Indices:
//   [0x68/4]=26 image_count, [0x6c/4]=27 image_base
//   [0x78/4]=30 material_count, [0x7c/4]=31 material_base
static std::uint32_t collada_root[35];

// --- Record storage: uint32_t word arrays with 32-bit strides ---
// Material records: 36 bytes = 9 words.  Word[0] = id pointer.
static constexpr int kMatRecWords = 9;   // 36 / 4
static std::uint32_t mat_records[10 * kMatRecWords];

// Image records: 16 bytes = 4 words.  Word[0] = id pointer.
static constexpr int kImgRecWords = 4;   // 16 / 4
static std::uint32_t img_records[10 * kImgRecWords];

// String storage for names
static const char name_a[] = "DiffuseVC";
static const char name_b[] = "Specular";
static const char name_c[] = "NormalMap";
static const char name_z[] = "Z_Not_There";
static const char name_empty[] = "";

static void init_chain() {
    std::memset(database, 0, sizeof(database));
    std::memset(resource_file, 0, sizeof(resource_file));
    std::memset(resource_header, 0, sizeof(resource_header));
    std::memset(collada_root, 0, sizeof(collada_root));

    // Chain: database -> resource_file -> resource_header -> collada_root
    database[0] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&resource_file[0]));
    resource_file[4] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&resource_header[0]));
    resource_header[8] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&collada_root[0]));
}

static void setup_materials(int count, const char** names) {
    std::memset(mat_records, 0, sizeof(mat_records));
    for (int i = 0; i < count && i < 10; ++i) {
        mat_records[i * kMatRecWords] = static_cast<std::uint32_t>(
            reinterpret_cast<std::uintptr_t>(names[i]));
    }
    // collada[30] = material_count (at byte offset 0x78)
    collada_root[30] = static_cast<std::uint32_t>(count);
    // collada[31] = materials base (at byte offset 0x7c)
    collada_root[31] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&mat_records[0]));
}

static void setup_images(int count, const char** names) {
    std::memset(img_records, 0, sizeof(img_records));
    for (int i = 0; i < count && i < 10; ++i) {
        img_records[i * kImgRecWords] = static_cast<std::uint32_t>(
            reinterpret_cast<std::uintptr_t>(names[i]));
    }
    // collada[26] = image_count (at byte offset 0x68)
    collada_root[26] = static_cast<std::uint32_t>(count);
    // collada[27] = images base (at byte offset 0x6c)
    collada_root[27] = static_cast<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(&img_records[0]));
}

int main() {
    // Guard: all static arrays must be 32-bit addressable for pointer walks.
    const auto addr_database = reinterpret_cast<std::uintptr_t>(&database[0]);
    if (addr_database > 0xFFFFFFFFu) {
        std::fprintf(stderr, "SKIP: static chain not 32-bit addressable\n");
        return 0;
    }

    const void* db = &database[0];

    // ===== get_material_by_name =====

    // -- Count <= 0 returns nullptr --
    init_chain();
    setup_materials(0, nullptr);
    CHECK(get_material_by_name(db, name_a) == 0u,
          "material: count 0 returns 0");
    init_chain();
    setup_materials(-1, nullptr);
    CHECK(get_material_by_name(db, name_a) == 0u,
          "material: count -1 returns 0");

    // -- Positive count, find first --
    {
        init_chain();
        const char* names[] = {name_a, name_b, name_c};
        setup_materials(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&mat_records[0]);

        // Find element 0
        CHECK(get_material_by_name(db, name_a) == base,
              "material: find first element");
        // Find element 1 (stride 36)
        CHECK(get_material_by_name(db, name_b) == base + 36u,
              "material: find second element (stride 36)");
        // Find element 2
        CHECK(get_material_by_name(db, name_c) == base + 72u,
              "material: find third element");

        // Not found
        CHECK(get_material_by_name(db, name_z) == 0u,
              "material: not found returns 0");

        // Re-setup with duplicate name: first match wins
    }
    {
        init_chain();
        const char* names[] = {name_a, name_b, name_a};
        setup_materials(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&mat_records[0]);
        CHECK(get_material_by_name(db, name_a) == base,
              "material: duplicate: first match (element 0) wins");
    }

    // -- Empty string lookup --
    {
        init_chain();
        const char* names[] = {name_empty, name_a, name_empty};
        setup_materials(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&mat_records[0]);
        CHECK(get_material_by_name(db, name_empty) == base,
              "material: empty string returns first empty element");
    }

    // -- Single element --
    {
        init_chain();
        const char* names[] = {name_a};
        setup_materials(1, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&mat_records[0]);
        CHECK(get_material_by_name(db, name_a) == base,
              "material: single element found");
        CHECK(get_material_by_name(db, name_b) == 0u,
              "material: single element not found");
    }

    // ===== get_image_by_name =====

    // -- Count <= 0 --
    init_chain();
    setup_images(0, nullptr);
    CHECK(get_image_by_name(db, name_a) == 0u,
          "image: count 0 returns 0");
    init_chain();
    setup_images(-5, nullptr);
    CHECK(get_image_by_name(db, name_a) == 0u,
          "image: count -5 returns 0");

    // -- Positive count, find first --
    {
        init_chain();
        const char* names[] = {name_a, name_b, name_c};
        setup_images(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&img_records[0]);

        CHECK(get_image_by_name(db, name_a) == base,
              "image: find first element");
        CHECK(get_image_by_name(db, name_b) == base + 16u,
              "image: find second element (stride 16)");
        CHECK(get_image_by_name(db, name_c) == base + 32u,
              "image: find third element");

        CHECK(get_image_by_name(db, name_z) == 0u,
              "image: not found returns 0");
    }

    // -- Duplicate names --
    {
        init_chain();
        const char* names[] = {name_b, name_a, name_b};
        setup_images(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&img_records[0]);
        CHECK(get_image_by_name(db, name_b) == base,
              "image: duplicate: first match (element 0) wins");
        CHECK(get_image_by_name(db, name_a) == base + 16u,
              "image: second unique name found");
    }

    // -- Empty string --
    {
        init_chain();
        const char* names[] = {name_a, name_empty, name_b};
        setup_images(3, names);
        const std::uint32_t base = reinterpret_cast<std::uintptr_t>(&img_records[0]);
        CHECK(get_image_by_name(db, name_empty) == base + 16u,
              "image: empty string at element 1");
    }

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("ccollada_by_name: all tests passed\n");
    return 0;
}