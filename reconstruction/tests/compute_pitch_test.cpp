// Tests for nova3::recovered::block_0077d37c::compute_pitch.
// Validates arithmetic against known format table entries from the
// original ELF (Premium retail,
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf).
// Only the three used fields are set; the 37 reserved bytes are zero.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace nova3::recovered::block_0077d37c {
struct alignas(4) PixelFormatDescriptor {
    std::uint8_t _reserved_00_14[0x15];
    std::uint8_t bytes_per_block;
    std::uint8_t bits_per_pixel;
    std::uint8_t _reserved_17_23[0x24 - 0x17];
    std::uint8_t block_width;
    std::uint8_t _reserved_25_27[3];
};
static_assert(sizeof(PixelFormatDescriptor) == 0x28, "size check");
std::uint32_t compute_pitch(
    const PixelFormatDescriptor* table,
    std::uint32_t format_index,
    std::uint32_t width) noexcept;
}

using namespace nova3::recovered::block_0077d37c;

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

static void set_entry(PixelFormatDescriptor* desc,
                      std::uint8_t bpb, std::uint8_t bpp, std::uint8_t bw) {
    std::memset(desc, 0, sizeof(*desc));
    desc->bytes_per_block = bpb;
    desc->bits_per_pixel = bpp;
    desc->block_width = bw;
}

int main() {
    // Allocate a table large enough for the maximum format index we test.
    // Each entry is 40 bytes; the stride is sizeof(PixelFormatDescriptor).
    PixelFormatDescriptor table[40];
    std::memset(table, 0, sizeof(table));

    // Populate entries using our helper.  Indices match the original
    // E_PIXEL_FORMAT enum values (verified from Ghidra export of
    // computeSizeInBytes and the original table at ELF VA 0xc7ff50).

    // [0]  uncompressed  8bpp
    set_entry(&table[0], 0, 8, 1);
    // [1]  uncompressed 16bpp
    set_entry(&table[1], 0, 16, 1);
    // [2]  uncompressed  8bpp
    set_entry(&table[2], 0, 8, 1);
    // [3]  uncompressed 16bpp
    set_entry(&table[3], 0, 16, 1);
    // [10] uncompressed 24bpp
    set_entry(&table[10], 0, 24, 1);
    // [12] uncompressed 32bpp
    set_entry(&table[12], 0, 32, 1);
    // [17] compressed: 4x4 blocks, 8 bytes/block (ETC1 / DXT1 style)
    set_entry(&table[17], 8, 0, 4);
    // [18] compressed: 4x4 blocks, 8 bytes/block
    set_entry(&table[18], 8, 0, 4);
    // [19] compressed: 4x4 blocks, 16 bytes/block (8bpp DXT5 style)
    set_entry(&table[19], 16, 0, 4);
    // [21] compressed: 8x4 blocks, 8 bytes/block (2bpp)
    set_entry(&table[21], 8, 0, 8);
    // [37] uncompressed 48bpp
    set_entry(&table[37], 0, 48, 1);
    // [38] uncompressed 64bpp
    set_entry(&table[38], 0, 64, 1);
    // [39] uncompressed 96bpp
    set_entry(&table[39], 0, 96, 1);

    // --- Uncompressed tests ---
    // 8bpp: pitch = width
    CHECK(compute_pitch(table, 0, 1) == 1, "8bpp w=1");
    CHECK(compute_pitch(table, 0, 4) == 4, "8bpp w=4");
    CHECK(compute_pitch(table, 0, 64) == 64, "8bpp w=64");
    CHECK(compute_pitch(table, 0, 127) == 127, "8bpp w=127");
    CHECK(compute_pitch(table, 0, 3) == 3, "8bpp w=3 odd");

    // 16bpp: pitch = width * 2
    CHECK(compute_pitch(table, 1, 1) == 2, "16bpp w=1");
    CHECK(compute_pitch(table, 1, 4) == 8, "16bpp w=4");
    CHECK(compute_pitch(table, 1, 64) == 128, "16bpp w=64");
    CHECK(compute_pitch(table, 1, 3) == 6, "16bpp w=3");

    // 24bpp: pitch = width * 3
    CHECK(compute_pitch(table, 10, 1) == 3, "24bpp w=1");
    CHECK(compute_pitch(table, 10, 4) == 12, "24bpp w=4");
    CHECK(compute_pitch(table, 10, 3) == 9, "24bpp w=3");

    // 32bpp: pitch = width * 4
    CHECK(compute_pitch(table, 12, 1) == 4, "32bpp w=1");
    CHECK(compute_pitch(table, 12, 4) == 16, "32bpp w=4");
    CHECK(compute_pitch(table, 12, 3) == 12, "32bpp w=3");

    // 48bpp: pitch = width * 6
    CHECK(compute_pitch(table, 37, 4) == 24, "48bpp w=4");

    // 64bpp: pitch = width * 8
    CHECK(compute_pitch(table, 38, 4) == 32, "64bpp w=4");

    // 96bpp: pitch = width * 12
    CHECK(compute_pitch(table, 39, 4) == 48, "96bpp w=4");

    // --- Compressed tests ---
    // 4x4 blocks, 8 bytes/block: pitch = 8 * ceil(w/4)
    CHECK(compute_pitch(table, 17, 1) == 8, "4x4b8 w=1");
    CHECK(compute_pitch(table, 17, 4) == 8, "4x4b8 w=4");
    CHECK(compute_pitch(table, 17, 5) == 16, "4x4b8 w=5");
    CHECK(compute_pitch(table, 17, 8) == 16, "4x4b8 w=8");
    CHECK(compute_pitch(table, 17, 64) == 128, "4x4b8 w=64");
    CHECK(compute_pitch(table, 17, 0) == 0, "4x4b8 w=0");

    // 4x4 blocks, 16 bytes/block: pitch = 16 * ceil(w/4)
    CHECK(compute_pitch(table, 19, 4) == 16, "4x4b16 w=4");
    CHECK(compute_pitch(table, 19, 5) == 32, "4x4b16 w=5");
    CHECK(compute_pitch(table, 19, 64) == 256, "4x4b16 w=64");

    // 8x4 blocks, 8 bytes/block: pitch = 8 * ceil(w/8)
    CHECK(compute_pitch(table, 21, 8) == 8, "8x4b8 w=8");
    CHECK(compute_pitch(table, 21, 9) == 16, "8x4b8 w=9");
    CHECK(compute_pitch(table, 21, 16) == 16, "8x4b8 w=16");
    CHECK(compute_pitch(table, 21, 64) == 64, "8x4b8 w=64");

    // --- Edge cases ---
    // Width 0 uncompressed
    CHECK(compute_pitch(table, 0, 0) == 0, "8bpp w=0");
    // Width 0 compressed
    CHECK(compute_pitch(table, 17, 0) == 0, "compressed w=0");
    // Large width, no overflow on 32-bit
    CHECK(compute_pitch(table, 17, 4096) == 8192, "4x4b8 w=4096");

    // Verify stride: reading entry[1] should NOT see entry[0]'s fields
    // (regression test for struct sizing)
    CHECK(table[0].bytes_per_block == 0, "entry0 bpb unchanged");
    CHECK(table[1].bytes_per_block == 0, "entry1 bpb");
    CHECK(table[1].bits_per_pixel == 16, "entry1 bpp");

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("compute_pitch: all tests passed\n");
    return 0;
}