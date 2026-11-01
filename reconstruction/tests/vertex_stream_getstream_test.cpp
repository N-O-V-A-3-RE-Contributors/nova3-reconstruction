// Behavioral test for CVertexStreams::getStream (const and non-const).
//
// The expected semantics are transcribed from the ARM disassembly of the
// two original functions at 0x0077bd7c (const) and 0x0077bdc0 (non-const).
//
// getStream searches a sorted array of SVertexStream entries by their
// 16-bit attribute (entry->data_offset low half) and returns the matched
// entry, or falls back to the container's first embedded entry (at
// container+0x10) when no match exists.
//
// The signed comparison (blt) used by the original means entries are
// sorted ascending by signed 16-bit attribute value.

#include "vertex_stream_getstream.h"

#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>

using namespace nova3::recovered::vertex_streams;

static int g_failures = 0;

// Test-sized container matching the embedded-array layout used by
// getActiveAttributeMask / addOffset / getStream.
template <std::size_t N>
struct TestContainer {
    std::uint32_t header[4];       // +0x00
    // Embedded entries at +0x10 (stride 16).  The fallback pointer
    // [this+0x10] is the address of the first entry's field_00.
    SVertexStream entries[N];
};

static void set_attrib(SVertexStream* e, std::uint16_t attrib) {
    // The original loads the attribute as the low half of data_offset.
    // We preserve the high half so that addOffset (32-bit) and getStream
    // (16-bit) coexist on the same field — matching the dual-access
    // pattern observed in the ELF.
    e->data_offset = (e->data_offset & 0xFFFF0000u) | attrib;
}

static void check_ptr(const char* label, const void* got, const void* want) {
    if (got != want) {
        std::printf("FAIL %s: got %p, want %p\n", label,
                    static_cast<const void*>(got),
                    static_cast<const void*>(want));
        ++g_failures;
    }
}

static void check_eq(const char* label, std::uint32_t got, std::uint32_t want) {
    if (got != want) {
        std::printf("FAIL %s: got %u, want %u\n", label, got, want);
        ++g_failures;
    }
}

// Forward declarations of the recovered trampolines (C linkage).
extern "C" const SVertexStream* nova3_recovered_get_stream_const(
    const void*, std::uint32_t, const SVertexStream*, const SVertexStream*);
extern "C" SVertexStream* nova3_recovered_get_stream(
    void*, std::uint32_t, SVertexStream*, SVertexStream*);

int main() {

    // --- Test 1: empty array (begin == end) — returns fallback ---
    {
        TestContainer<2> c{};
        std::memset(&c, 0, sizeof(c));
        const SVertexStream* fallback =
            reinterpret_cast<const SVertexStream*>(
                reinterpret_cast<const std::uint8_t*>(&c) + 0x10);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 0, &c.entries[0], &c.entries[0]);
        check_ptr("empty array returns fallback", result, fallback);
    }

    // --- Test 2: single entry, exact match ---
    {
        TestContainer<2> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 5);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 5, &c.entries[0], &c.entries[0] + 1);
        check_ptr("single match", result, &c.entries[0]);
    }

    // --- Test 3: single entry, attrib too high (no match, fallback) ---
    {
        TestContainer<2> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 5);
        for (std::uint32_t key : {0u, 1u, 2u, 3u, 4u}) {
            const SVertexStream* fallback =
                reinterpret_cast<const SVertexStream*>(
                    reinterpret_cast<const std::uint8_t*>(&c) + 0x10);
            const SVertexStream* result = nova3_recovered_get_stream_const(
                &c, key, &c.entries[0], &c.entries[0] + 1);
            check_ptr("single entry, key < attrib returns fallback",
                      result, fallback);
        }
    }

    // --- Test 4: single entry, attrib too low (no match, attrib > key load fallback) ---
    {
        TestContainer<2> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 5);
        const SVertexStream* fallback =
            reinterpret_cast<const SVertexStream*>(
                reinterpret_cast<const std::uint8_t*>(&c) + 0x10);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 10, &c.entries[0], &c.entries[0] + 1);
        check_ptr("single entry, key > attrib returns fallback",
                  result, fallback);
    }

    // --- Test 5: multi-entry, search finds match in middle ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0);
        set_attrib(&c.entries[1], 3);
        set_attrib(&c.entries[2], 7);
        set_attrib(&c.entries[3], 9);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 3, &c.entries[0], &c.entries[0] + 4);
        check_ptr("multi-entry match middle", result, &c.entries[1]);
    }

    // --- Test 6: multi-entry, search finds first ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0);
        set_attrib(&c.entries[1], 3);
        set_attrib(&c.entries[2], 7);
        set_attrib(&c.entries[3], 9);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 0, &c.entries[0], &c.entries[0] + 4);
        check_ptr("multi-entry match first", result, &c.entries[0]);
    }

    // --- Test 7: multi-entry, search finds last ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0);
        set_attrib(&c.entries[1], 3);
        set_attrib(&c.entries[2], 7);
        set_attrib(&c.entries[3], 9);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 9, &c.entries[0], &c.entries[0] + 4);
        check_ptr("multi-entry match last", result, &c.entries[3]);
    }

    // --- Test 8: multi-entry, key between entries (gap) — returns fallback ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0);
        set_attrib(&c.entries[1], 5);
        set_attrib(&c.entries[2], 10);
        set_attrib(&c.entries[3], 15);
        const SVertexStream* fallback =
            reinterpret_cast<const SVertexStream*>(
                reinterpret_cast<const std::uint8_t*>(&c) + 0x10);
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 7, &c.entries[0], &c.entries[0] + 4);
        check_ptr("multi-entry gap fallback", result, fallback);
    }

    // --- Test 9: multi-entry, key larger than all — returns fallback ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0);
        set_attrib(&c.entries[1], 3);
        set_attrib(&c.entries[2], 5);
        set_attrib(&c.entries[3], 8);
        const SVertexStream* fallback =
            reinterpret_cast<const SVertexStream*>(
                reinterpret_cast<const std::uint8_t*>(&c) + 0x10);
        // key 9: entries[3]->attrib=8 < 9, skip, end reached, fallback
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 9, &c.entries[0], &c.entries[0] + 4);
        check_ptr("multi-entry key larger than all", result, fallback);
    }

    // --- Test 10: const vs non-const return types ---
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 2);
        set_attrib(&c.entries[1], 5);
        set_attrib(&c.entries[2], 8);
        set_attrib(&c.entries[3], 11);

        const SVertexStream* cr = nova3_recovered_get_stream_const(
            &c, 5, &c.entries[0], &c.entries[0] + 4);
        check_ptr("const match", cr, &c.entries[1]);

        SVertexStream* nr = nova3_recovered_get_stream(
            &c, 8, &c.entries[0], &c.entries[0] + 4);
        check_ptr("non-const match", nr, &c.entries[2]);
    }

    // --- Test 11: signed comparison — negative attrib in sorted order ---
    // The original uses blt (signed less-than).  Signed 16-bit negative
    // values should sort before small positives.
    {
        TestContainer<4> c{};
        std::memset(&c, 0, sizeof(c));
        set_attrib(&c.entries[0], 0xFFFEu);  // signed: -2
        set_attrib(&c.entries[1], 0xFFFFu);  // signed: -1
        set_attrib(&c.entries[2], 0);        // signed: 0
        set_attrib(&c.entries[3], 1);        // signed: 1

        // Search for -1 (0xFFFF as uint16)
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 0xFFFFu, &c.entries[0], &c.entries[0] + 4);

        // In signed order: -2, -1, 0, 1
        // Walking entries: attrib=-2 < key=-1, skip. attrib=-1 >= key=-1, check match → yes
        check_ptr("signed order negative match", result, &c.entries[1]);
    }

    // --- Test 12: getStream preserves data_offset high half ---
    {
        TestContainer<2> c{};
        std::memset(&c, 0, sizeof(c));
        c.entries[0].data_offset = 0xABCD0005u;  // high=0xABCD, attrib=5
        const SVertexStream* result = nova3_recovered_get_stream_const(
            &c, 5, &c.entries[0], &c.entries[0] + 1);
        check_ptr("high-half preserved match", result, &c.entries[0]);
        check_eq("high half untouched", c.entries[0].data_offset, 0xABCD0005u);
    }

    if (g_failures == 0) {
        std::printf("vertex_stream_getstream: all checks passed\n");
        return 0;
    }
    std::printf("vertex_stream_getstream: %d check(s) FAILED\n", g_failures);
    return 1;
}