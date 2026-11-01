// Host behavioural test for CColladaDatabase::getVersion (ELF VA 0x0071017c).
// The original is a four-level 32-bit pointer walk.  To exercise the exact
// recovered code path on an LP64 host, the chain nodes are placed in static
// storage of a non-PIE executable, keeping every pointer below 4 GiB so the
// 32-bit loads reproduce the original addresses faithfully.
#include <cstdint>
#include <cstdio>

#include "ccollada_database.h"

using nova3::recovered::collada::get_version;

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

// node3 holds the version word at offset 0.
static std::uint32_t node3[] = { 0x00C0FFEEu, 0, 0, 0, 0, 0, 0, 0 };
// node2 points to node3 at offset +0x20 (word index 8).
static std::uint32_t node2[9];
// node1 points to node2 at offset +0x10 (word index 4).
static std::uint32_t node1[5];
// CColladaDatabase root points to node1 at offset +0x00 (word index 0).
static std::uint32_t database_root[1];

static void init_chain() {
    database_root[0] = (std::uint32_t)(std::uintptr_t)&node1[0];
    node1[4] = (std::uint32_t)(std::uintptr_t)&node2[0];
    node2[8] = (std::uint32_t)(std::uintptr_t)&node3[0];
}

int main() {
    // Guard: this test requires 32-bit-addressable static storage.
    if ((std::uintptr_t)&node1[0] > 0xFFFFFFFFu ||
        (std::uintptr_t)&node2[0] > 0xFFFFFFFFu ||
        (std::uintptr_t)&node3[0] > 0xFFFFFFFFu) {
        std::fprintf(stderr, "SKIP: static chain not 32-bit addressable\n");
        return 0;
    }

    init_chain();
    CHECK(get_version(&database_root[0]) == 0x00C0FFEEu,
          "get_version walks 00 -> 10 -> 20 -> 00");

    // A wrong reconstruction that, say, walks +0x20 first would read a zero
    // word (node2[8] is the only initialized slot) and fail the check above;
    // here we additionally confirm the intermediate slots remain untouched.
    CHECK(node1[0] == 0u, "node1[0] untouched");
    CHECK(node2[0] == 0u, "node2[0] untouched");

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("ccollada_get_version: all tests passed\n");
    return 0;
}