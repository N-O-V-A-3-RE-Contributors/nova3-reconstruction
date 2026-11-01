// Tests for nova3::recovered::pixel_format::compute_mipmap_size_in_bytes
// (five-argument) and compute_mipmap_size_in_bytes_slices (six-argument).
//
// Expected values are derived from the actual 40-entry pixel-format
// descriptor table extracted from the Premium retail ELF (.rodata VA
// 0x00c7ff50, SHA 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf).
// Each entry's five consumed fields are {bytes_per_block(0x15),
// bits_per_pixel(0x16), block_width(0x24), block_height(0x25), min_size(0x27)}.
//
// Behaviour under test (A32 disassembly at 0x0077d450 / 0x0077d470, confirmed
// instruction-identical to the Freemium libGloftNAHM.so exports):
//   mip(v, lvl) = max(v >> lvl, 1)          [logical shift, lvl>=32 => 1]
//   5-arg: if (!skipWidth) width = mip(width);  height = mip(height);
//          return computeSizeInBytes(format, width, height)
//   6-arg: additionally depth = mip(depth); return depth * that size
//
// The independent reference below is written directly from the disassembly
// spec and shares no code with the recovered source under test.
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "pixel_format.h"

using nova3::recovered::pixel_format::PixelFormatDescriptor;
using nova3::recovered::pixel_format::compute_mipmap_size_in_bytes;
using nova3::recovered::pixel_format::compute_mipmap_size_in_bytes_slices;

struct Entry { std::uint8_t bpb, bpp, bw, bh, min; };

// The 40 entries extracted directly from Premium .rodata VA 0xc7ff50.
static const Entry G_TABLE[40] = {
    {1,8,1,1,0}, {2,16,1,1,0}, {1,8,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0},
    {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0},
    {3,24,1,1,0}, {3,24,1,1,0}, {4,32,1,1,0}, {4,32,1,1,0}, {4,32,1,1,0},
    {4,32,1,1,0}, {4,32,1,1,0}, {8,4,4,4,0}, {8,4,4,4,0}, {16,8,4,4,0},
    {16,8,4,4,0}, {8,2,8,4,32}, {8,2,8,4,32}, {8,4,4,4,32}, {8,4,4,4,32},
    {8,2,8,4,32}, {8,2,8,4,32}, {8,4,4,4,32}, {8,4,4,4,32}, {8,4,4,4,8},
    {8,4,4,4,8}, {16,8,4,4,16}, {8,4,4,4,8}, {16,8,4,4,16}, {16,8,4,4,16},
    {8,4,4,4,8}, {16,8,4,4,16}, {6,48,1,1,0}, {8,64,1,1,0}, {12,96,1,1,0}
};

static int failures = 0;
static std::uint32_t _last = 0;
static PixelFormatDescriptor table[40];

static void build_table() {
    std::memset(table, 0, sizeof(table));
    for (int i = 0; i < 40; ++i) {
        table[i].bytes_per_block = G_TABLE[i].bpb;
        table[i].bits_per_pixel   = G_TABLE[i].bpp;
        table[i].block_width      = G_TABLE[i].bw;
        table[i].block_height     = G_TABLE[i].bh;
        table[i].min_size         = G_TABLE[i].min;
    }
}

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s (got %u)\n", msg, (unsigned)_last); \
        ++failures; \
    } \
} while (0)

// ---- Independent reference (from disassembly spec, no shared code) ----
static std::uint32_t ref_size(const Entry* e, std::uint32_t w, std::uint32_t h) {
    std::uint32_t pitch;
    if (e->bw <= 1) pitch = ((std::uint32_t)e->bpp * w) / 8;
    else pitch = ((w + e->bw - 1) / e->bw) * e->bpb;
    std::uint32_t size;
    if (e->bh <= 1) size = pitch * h;
    else size = pitch * ((h + e->bh - 1) / e->bh);
    if (size < e->min) size = e->min;
    return size;
}

static std::uint32_t ref_mip(std::uint32_t v, std::uint8_t lvl) {
    std::uint32_t s = (lvl < 32u) ? (v >> lvl) : 0u;
    return s ? s : 1u;
}

static std::uint32_t ref_mip5(const Entry* e, std::uint32_t w, std::uint32_t h,
                              std::uint8_t lvl, bool skip) {
    if (!skip) w = ref_mip(w, lvl);
    h = ref_mip(h, lvl);
    return ref_size(e, w, h);
}

static std::uint32_t ref_mip6(const Entry* e, std::uint32_t w, std::uint32_t h,
                              std::uint32_t d, std::uint8_t lvl, bool skip) {
    if (!skip) w = ref_mip(w, lvl);
    h = ref_mip(h, lvl);
    d = ref_mip(d, lvl);
    return d * ref_size(e, w, h);
}

int main() {
    build_table();

    // ---- Explicit hand-computed values ----
    // fmt0 (8bpp, uncompressed), 16x16.
    _last = compute_mipmap_size_in_bytes(table, 0, 16, 16, 0, false);
    CHECK(_last == 256, "fmt0 16x16 mip0 skip0");       // no shift
    _last = compute_mipmap_size_in_bytes(table, 0, 16, 16, 0, true);
    CHECK(_last == 256, "fmt0 16x16 mip0 skip1");       // no shift either way
    _last = compute_mipmap_size_in_bytes(table, 0, 16, 16, 1, false);
    CHECK(_last == 64,  "fmt0 16x16 mip1 skip0");       // 8x8 = 64
    _last = compute_mipmap_size_in_bytes(table, 0, 16, 16, 1, true);
    CHECK(_last == 128, "fmt0 16x16 mip1 skip1");       // width kept: 16x8 = 128

    // Clamp-to-1: 1x1 at mip1 collapses to 1x1 (not 0).
    _last = compute_mipmap_size_in_bytes(table, 0, 1, 1, 1, false);
    CHECK(_last == 1, "fmt0 1x1 mip1 clamp");

    // Compressed fmt17 (4x4 block, bpb=8), 16x16 at mip1.
    // width=8,height=8 -> pitch=8*ceil(8/4)=16, size=16*ceil(8/4)=32.
    _last = compute_mipmap_size_in_bytes(table, 17, 16, 16, 1, false);
    CHECK(_last == 32, "fmt17 16x16 mip1");

    // mip level >= 32: shifted dimension is 0 -> clamped to 1.
    _last = compute_mipmap_size_in_bytes(table, 0, 16, 16, 32, false);
    CHECK(_last == 1, "fmt0 16x16 mip32 clamp");

    // ---- Six-argument (depth) ----
    _last = compute_mipmap_size_in_bytes_slices(table, 0, 16, 16, 6, 0, false);
    CHECK(_last == 6 * 256, "slices fmt0 16x16 d6 mip0");
    _last = compute_mipmap_size_in_bytes_slices(table, 0, 16, 16, 6, 1, false);
    CHECK(_last == 3 * 64,  "slices fmt0 16x16 d6 mip1");   // depth halves too: 6>>1=3
    _last = compute_mipmap_size_in_bytes_slices(table, 0, 16, 16, 6, 1, true);
    CHECK(_last == 3 * 128, "slices fmt0 16x16 d6 mip1 skip1"); // width kept; depth still 3
    // depth clamps to 1
    _last = compute_mipmap_size_in_bytes_slices(table, 0, 16, 16, 1, 1, false);
    CHECK(_last == 64, "slices fmt0 16x16 d1 mip1");        // depth 1>>1 -> 1

    // ---- Table-driven cross-check against independent reference ----
    const std::uint32_t dims[] = {0, 1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 64, 127, 256};
    const std::uint8_t  lvls[] = {0, 1, 2, 3, 4, 5, 8, 16, 31};
    const std::uint32_t dvals[] = {0, 1, 2, 3, 6};
    for (int fmt = 0; fmt < 40; ++fmt) {
        for (std::uint32_t w : dims) {
            for (std::uint32_t h : dims) {
                for (std::uint8_t lvl : lvls) {
                    for (int s = 0; s < 2; ++s) {
                        bool skip = (s == 1);
                        std::uint32_t got = compute_mipmap_size_in_bytes(
                            table, fmt, w, h, lvl, skip);
                        std::uint32_t exp = ref_mip5(&G_TABLE[fmt], w, h, lvl, skip);
                        if (got != exp) {
                            std::fprintf(stderr,
                                "FAIL: fmt%d w=%u h=%u lvl=%u skip=%d got=%u exp=%u\n",
                                fmt, w, h, lvl, s, got, exp);
                            ++failures;
                        }
                        for (std::uint32_t d : dvals) {
                            std::uint32_t gs = compute_mipmap_size_in_bytes_slices(
                                table, fmt, w, h, d, lvl, skip);
                            std::uint32_t es = ref_mip6(&G_TABLE[fmt], w, h, d, lvl, skip);
                            if (gs != es) {
                                std::fprintf(stderr,
                                    "FAIL(slices): fmt%d w=%u h=%u d=%u lvl=%u skip=%d got=%u exp=%u\n",
                                    fmt, w, h, d, lvl, s, gs, es);
                                ++failures;
                            }
                        }
                    }
                }
            }
        }
    }

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("compute_mipmap_size_in_bytes: all tests passed\n");
    return 0;
}
