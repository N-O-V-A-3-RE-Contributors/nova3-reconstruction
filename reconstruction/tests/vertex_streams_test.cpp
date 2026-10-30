// Behavioral test for CVertexStreams::getActiveAttributeMask and addOffset.
//
// The expected semantics are transcribed from the ARM disassembly of the
// two original functions at 0x0077bd28 and 0x0077be04, not from running
// the reconstructed logic backwards.
#include "vertex_streams.h"
#include <cstdint>
#include <cstdio>
#include <cstring>

using nova3::recovered::vertex_streams::CVertexStreamsN;
using nova3::recovered::vertex_streams::SVertexStream;

extern "C" std::uint32_t nova3_recovered_get_active_attribute_mask(
    const void*, std::uint32_t);
extern "C" void nova3_recovered_add_offset(
    void*, std::uint32_t, std::int32_t);

static int g_failures = 0;

static void check_eq(const char* name, std::uint32_t got, std::uint32_t want) {
    if (got != want) {
        std::printf("FAIL %s: got %u, want %u\n", name, got, want);
        ++g_failures;
    }
}

int main() {
    // Test 1: empty container — mask must be 0.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        std::uint32_t mask = nova3_recovered_get_active_attribute_mask(&c, 0);
        check_eq("empty mask", mask, 0);
    }

    // Test 2: single active entry.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].active = 1;
        c.entries[0].attrib_bit = 3;
        std::uint32_t mask = nova3_recovered_get_active_attribute_mask(&c, 1);
        check_eq("one entry mask (1<<3)", mask, 1u << 3);
    }

    // Test 3: two entries, one active at bit 0.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].active = 1;
        c.entries[0].attrib_bit = 0;
        c.entries[1].active = 0;
        c.entries[1].attrib_bit = 5;  // ignored (inactive)
        std::uint32_t mask = nova3_recovered_get_active_attribute_mask(&c, 2);
        check_eq("two entries, one active", mask, 1u << 0);
    }

    // Test 4: three entries, two active, different bit positions.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].active = 1; c.entries[0].attrib_bit = 2;
        c.entries[1].active = 0; c.entries[1].attrib_bit = 99;
        c.entries[2].active = 1; c.entries[2].attrib_bit = 7;
        std::uint32_t mask = nova3_recovered_get_active_attribute_mask(&c, 3);
        check_eq("three entries mask ((1<<2)|(1<<7))", mask, (1u << 2) | (1u << 7));
    }

    // Test 5: all bits from multiple entries (low and high overlap).
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].active = 1; c.entries[0].attrib_bit = 31;
        c.entries[1].active = 1; c.entries[1].attrib_bit = 31;  // duplicate
        c.entries[2].active = 1; c.entries[2].attrib_bit = 15;
        c.entries[3].active = 1; c.entries[3].attrib_bit = 0;
        std::uint32_t mask = nova3_recovered_get_active_attribute_mask(&c, 4);
        check_eq("overlapping bits", mask, (1u << 31) | (1u << 15) | (1u << 0));
    }

    // Test 6: addOffset on empty — no-op.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0xAB, sizeof(c));  // poison
        nova3_recovered_add_offset(&c, 0, 42);
        // All bytes should remain 0xAB (no writes occurred)
        const auto* bytes = reinterpret_cast<const unsigned char*>(&c);
        for (std::size_t i = 0; i < sizeof(c); ++i) {
            if (bytes[i] != 0xAB) {
                std::printf("FAIL addOffset empty byte[%zu]: 0x%02x\n", i, bytes[i]);
                ++g_failures;
                break;
            }
        }
    }

    // Test 7: addOffset single entry.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].data_offset = 100;
        nova3_recovered_add_offset(&c, 1, 50);
        check_eq("addOffset single +50", c.entries[0].data_offset, 150);
    }

    // Test 8: addOffset negative delta.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].data_offset = 100;
        nova3_recovered_add_offset(&c, 1, -30);
        check_eq("addOffset single -30", c.entries[0].data_offset, 70);
    }

    // Test 9: addOffset multiple entries, distinct initial values.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].data_offset = 10;
        c.entries[1].data_offset = 20;
        c.entries[2].data_offset = 30;
        nova3_recovered_add_offset(&c, 3, 100);
        check_eq("addOffset multi e0", c.entries[0].data_offset, 110);
        check_eq("addOffset multi e1", c.entries[1].data_offset, 120);
        check_eq("addOffset multi e2", c.entries[2].data_offset, 130);
    }

    // Test 10: addOffset unsigned wraparound.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].data_offset = 0xFFFFFFFFu;
        nova3_recovered_add_offset(&c, 1, 1);
        check_eq("addOffset wrap", c.entries[0].data_offset, 0u);
    }

    // Test 11: addOffset does not touch inactive/attrib fields.
    {
        CVertexStreamsN<4> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].active = 1;
        c.entries[0].attrib_bit = 7;
        nova3_recovered_add_offset(&c, 1, 99);
        check_eq("addOffset preserves active", c.entries[0].active, 1u);
        check_eq("addOffset preserves attrib_bit", c.entries[0].attrib_bit, 7u);
    }

    if (g_failures == 0) {
        std::printf("vertex_streams: all checks passed\n");
        return 0;
    }
    std::printf("vertex_streams: %d check(s) FAILED\n", g_failures);
    return 1;
}