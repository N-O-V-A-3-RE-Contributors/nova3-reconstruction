// Task #43: source-owned reconstruction of four Premium ARM routines.
// Reference SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// Partial in-memory layouts, NOT serialized asset definitions. Unknown payloads
// stay opaque. Names/types below are ours except the symbol-supported operations.
// Requires valid, live pointer chains; lookup arrays and NUL-terminated names
// must be valid when count > 0. No ownership transfer, allocation or validation.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace nova3::recovered::block_00710368 {

struct GeometryRecord { unsigned char unknown[16]; };
struct ImageRecord {
    const char* id;
    uint32_t unknown[3];
};
struct MaterialRecord {
    const char* id;
    uint32_t unknown[8];
};

struct ColladaRoot {
    uint32_t unknown_00[26];
    int32_t image_count;
    ImageRecord* images;
    uint32_t unknown_70[2];
    int32_t material_count;
    MaterialRecord* materials;
    uint32_t unknown_80[2];
    GeometryRecord* geometries;
};
struct ResourceHeader {
    uint32_t unknown_00[8];
    ColladaRoot* collada;
};
struct ResourceFile {
    uint32_t unknown_00[4];
    ResourceHeader* header;
};
struct ColladaDatabase {
    ResourceFile* resource;
    GeometryRecord* getGeometry(int32_t index) const;
    MaterialRecord* getMaterial(const char* name) const;
    ImageRecord* getImage(const char* name) const;
};

static_assert(sizeof(GeometryRecord) == 16);
// Wider host pointers adapt layouts for host tests. Only a 32-bit compilation
// represents the observed native offsets and image/material record strides.
#if __SIZEOF_POINTER__ == 4
static_assert(sizeof(ImageRecord) == 16);
static_assert(sizeof(MaterialRecord) == 36);
static_assert(offsetof(ImageRecord, id) == 0);
static_assert(offsetof(MaterialRecord, id) == 0);
static_assert(offsetof(ColladaDatabase, resource) == 0);
static_assert(offsetof(ResourceFile, header) == 0x10);
static_assert(offsetof(ResourceHeader, collada) == 0x20);
static_assert(offsetof(ColladaRoot, image_count) == 0x68);
static_assert(offsetof(ColladaRoot, images) == 0x6c);
static_assert(offsetof(ColladaRoot, material_count) == 0x78);
static_assert(offsetof(ColladaRoot, materials) == 0x7c);
static_assert(offsetof(ColladaRoot, geometries) == 0x88);
#endif

// 0x00710368, 0x18 bytes. Does not read or check a geometry count.
// Unsigned address arithmetic avoids signed overflow and out-of-array pointer
// arithmetic. On ARM32 this preserves modulo-2^32 addressing for every index.
// Returned addresses are not dereferenced here; caller owns their validity.
GeometryRecord* ColladaDatabase::getGeometry(int32_t index) const {
    const uintptr_t base = reinterpret_cast<uintptr_t>(resource->header->collada->geometries);
    return reinterpret_cast<GeometryRecord*>(base + static_cast<uintptr_t>(index) * 16u);
}

// 0x00710700, 8 bytes: the entire observed routine returns the word zero.
// Original symbol: CResFileManager::checkVersion(const intrusive_ptr<IReadFile>&).
// The declared C++ return type and static/member distinction are not recovered.
// This source-owned adapter exposes the observed result bits and two ignored
// argument-register slots, without inventing a version-validation algorithm.
uint32_t checkVersion(const void*, const void*) {
    return 0;
}

// 0x007146ac, 0x60 bytes. Count is signed; first exact name match wins.
MaterialRecord* ColladaDatabase::getMaterial(const char* name) const {
    const ColladaRoot* root = resource->header->collada;
    const int32_t count = root->material_count;
    if (count <= 0) return nullptr;
    MaterialRecord* const records = root->materials;
    for (int32_t index = 0; index < count; ++index) {
        if (strcmp(records[index].id, name) == 0) return records + index;
    }
    return nullptr;
}

// 0x0071476c, 0x60 bytes. Name is a borrowed C string, not a pointer identity.
ImageRecord* ColladaDatabase::getImage(const char* name) const {
    const ColladaRoot* root = resource->header->collada;
    const int32_t count = root->image_count;
    if (count <= 0) return nullptr;
    ImageRecord* const records = root->images;
    for (int32_t index = 0; index < count; ++index) {
        if (strcmp(records[index].id, name) == 0) return records + index;
    }
    return nullptr;
}

} // namespace nova3::recovered::block_00710368
