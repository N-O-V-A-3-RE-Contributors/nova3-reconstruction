// Source reconstruction of four Premium ARM functions; see task #31 evidence.
// This is a partial runtime layout, not an on-disk parser or the complete classes.
// Names of unknown fields and wrapper types are ours. Record contents, ownership,
// allocation and lifetime are not recovered here. Requires live pointer chains;
// the original functions have no null, count, bounds or duplicate-link checks.
#include <stddef.h>
#include <stdint.h>

namespace nova3::recovered::block_00710160 {

struct InstanceMaterial {
    unsigned char unknown_00[0x40];
    InstanceMaterial* next;
};

struct ImageRecord { unsigned char unknown[16]; };
struct MaterialRecord { unsigned char unknown[36]; };

struct ColladaRoot {
    const char* version;
    uint32_t unknown_04;
    InstanceMaterial* instance_materials;
    uint32_t unknown_0c[24];
    ImageRecord* images;
    uint32_t unknown_70[3];
    MaterialRecord* materials;
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

    void linkInstanceMaterial(InstanceMaterial* instance) const;
    const char* getVersion() const;
    ImageRecord* getImage(int index) const;
    MaterialRecord* getMaterial(int index) const;
};

static_assert(sizeof(ImageRecord) == 16);
static_assert(sizeof(MaterialRecord) == 36);
static_assert(sizeof(int) == 4, "Original index parameter is a 32-bit int");
// On a host with wider pointers the layout adapts for native behavioural tests.
// Only the 32-bit build represents the observed original ABI offsets.
#if __SIZEOF_POINTER__ == 4
static_assert(offsetof(ColladaDatabase, resource) == 0);
static_assert(offsetof(ResourceFile, header) == 0x10);
static_assert(offsetof(ResourceHeader, collada) == 0x20);
static_assert(offsetof(ColladaRoot, version) == 0);
static_assert(offsetof(ColladaRoot, instance_materials) == 8);
static_assert(offsetof(ColladaRoot, images) == 0x6c);
static_assert(offsetof(ColladaRoot, materials) == 0x7c);
static_assert(offsetof(InstanceMaterial, next) == 0x40);
#endif

// 0x00710160, 28 bytes: writes next before publishing the new list head.
void ColladaDatabase::linkInstanceMaterial(InstanceMaterial* instance) const {
    ColladaRoot* root = resource->header->collada;
    instance->next = root->instance_materials;
    root->instance_materials = instance;
}

// 0x0071017c, 20 bytes: borrowed version string pointer, no copying.
// The string interpretation is supported by prior local Collada asset evidence;
// the instructions alone establish only a word-sized return value.
const char* ColladaDatabase::getVersion() const {
    return resource->header->collada->version;
}

// 0x00710318, 24 bytes. Unsigned address arithmetic preserves ARM32 wrapping
// without signed-overflow or out-of-array C++ pointer arithmetic. No dereference
// of the returned address occurs here; callers remain responsible for validity.
ImageRecord* ColladaDatabase::getImage(int index) const {
    const uintptr_t base = reinterpret_cast<uintptr_t>(resource->header->collada->images);
    return reinterpret_cast<ImageRecord*>(base + static_cast<uintptr_t>(index) * 16u);
}

// 0x0071034c, 28 bytes; material payload fields remain opaque.
MaterialRecord* ColladaDatabase::getMaterial(int index) const {
    const uintptr_t base = reinterpret_cast<uintptr_t>(resource->header->collada->materials);
    return reinterpret_cast<MaterialRecord*>(base + static_cast<uintptr_t>(index) * 36u);
}

} // namespace nova3::recovered::block_00710160
