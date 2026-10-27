// Standalone because task #31 does not permit build-system/header edits:
// Compile this file using C++17, then run the resulting executable.
// Exact commands and results are recorded in task #31's evidence JSON.
#include "../../../recovered/libNOVA3_neon/block_00710160.cpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

using namespace nova3::recovered::block_00710160;

static unsigned checks = 0;
static void require(bool condition) {
    ++checks;
    if (!condition) {
        std::fprintf(stderr, "FAIL: check %u\n", checks);
        std::abort();
    }
}

int main() {
    char version[] = "synthetic-version";
    ImageRecord images[8]{};
    MaterialRecord materials[8]{};
    ColladaRoot root{};
    root.version = version;
    root.images = images + 3;
    root.materials = materials + 3;
    root.unknown_04 = 0x12345678;
    for (auto& word : root.unknown_0c) word = 0xdeadbeef;
    for (auto& word : root.unknown_70) word = 0xabcd1234;
    ResourceHeader header{};
    header.collada = &root;
    ResourceFile file{};
    file.header = &header;
    const ColladaDatabase database{&file};

    unsigned char before[sizeof(root)];
    std::memcpy(before, &root, sizeof(root));
    require(database.getVersion() == version);
    for (int32_t i = -3; i <= 4; ++i) {
        require(database.getImage(i) == &images[i + 3]);
        require(database.getMaterial(i) == &materials[i + 3]);
    }
    // ARM instructions also accept extreme signed indices. Compare returned
    // address bits only, never dereference a deliberately invalid result.
    const int32_t extremes[] = {std::numeric_limits<int32_t>::min(),
                               std::numeric_limits<int32_t>::max()};
    for (int32_t i : extremes) {
        const uintptr_t image_offset = static_cast<uintptr_t>(i) << 4;
        const uintptr_t material_offset = (static_cast<uintptr_t>(i) +
                                          (static_cast<uintptr_t>(i) << 3)) << 2;
        require(reinterpret_cast<uintptr_t>(database.getImage(i)) ==
                reinterpret_cast<uintptr_t>(root.images) + image_offset);
        require(reinterpret_cast<uintptr_t>(database.getMaterial(i)) ==
                reinterpret_cast<uintptr_t>(root.materials) + material_offset);
    }
    require(std::memcmp(before, &root, sizeof(root)) == 0);

    InstanceMaterial first{}, second{};
    std::memset(first.unknown_00, 0x35, sizeof(first.unknown_00));
    std::memset(second.unknown_00, 0xa7, sizeof(second.unknown_00));
    first.next = &second; // insertion must replace a pre-existing next value
    database.linkInstanceMaterial(&first);
    require(root.instance_materials == &first && first.next == nullptr);
    database.linkInstanceMaterial(&second);
    require(root.instance_materials == &second && second.next == &first);
    require(first.next == nullptr);
    database.linkInstanceMaterial(&second); // no deduplication in original
    require(root.instance_materials == &second && second.next == &second);
    for (unsigned char byte : first.unknown_00) require(byte == 0x35);
    for (unsigned char byte : second.unknown_00) require(byte == 0xa7);
    // Only the head field may change in the root.
    root.instance_materials = nullptr;
    require(std::memcmp(before, &root, sizeof(root)) == 0);
    require(header.collada == &root && file.header == &header);

    root.version = nullptr;
    require(database.getVersion() == nullptr);
    root.images = nullptr;
    root.materials = nullptr;
    require(database.getImage(0) == nullptr);
    require(database.getMaterial(0) == nullptr);
    std::printf("PASS: %u checks (synthetic fixtures; host semantics only)\n", checks);
}
