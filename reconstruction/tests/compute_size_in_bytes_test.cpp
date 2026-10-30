// Tests for nova3::recovered::block_0077d3cc::compute_size_in_bytes.
//
// Expected values are derived from the actual 40-entry pixel-format
// descriptor table extracted from the Premium retail ELF (.rodata VA
// 0x00c7ff50, SHA 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf),
// so every assertion cross-checks our reconstruction against the original
// data.  Each entry's five fields used here are {bytes_per_block(0x15),
// bits_per_pixel(0x16), block_width(0x24), block_height(0x25),
// min_size(0x27)}.
//
// Behaviour under test:
//   pitch = (bw<=1) ? (bpp*w)>>3 : bpb * ceil(w/bw)
//   size  = (bh<=1) ? pitch*h      : pitch * ceil(h/bh)
//   size  = max(size, min_size)     [unsigned byte min]
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace nova3::recovered::block_0077d3cc {
struct alignas(4) PixelFormatDescriptor {
    std::uint8_t _reserved_00_14[0x15];
    std::uint8_t bytes_per_block;
    std::uint8_t bits_per_pixel;
    std::uint8_t _reserved_17_23[0x24 - 0x17];
    std::uint8_t block_width;
    std::uint8_t block_height;
    std::uint8_t _reserved_26;
    std::uint8_t min_size;
};
static_assert(sizeof(PixelFormatDescriptor) == 0x28, "size check");
std::uint32_t compute_size_in_bytes(
    const PixelFormatDescriptor* table,
    std::uint32_t format_index,
    std::uint32_t width,
    std::uint32_t height) noexcept;
}

using namespace nova3::recovered::block_0077d3cc;

struct Entry { std::uint8_t bpb, bpp, bw, bh, min; };

// The 40 entries extracted directly from Premium .rodata VA 0xc7ff50.
// (bpb=bytes_per_block, bpp=bits_per_pixel, bw=block_width, bh=block_height,
//  min=min_size)
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

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s (got %u)\n", msg, (unsigned)_last); \
        ++failures; \
    } \
} while (0)

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

// Independent reference implementation, written directly from the behavioural
// spec (not sharing any code with the recovered source under test).
static std::uint32_t ref_size(const Entry* e, std::uint32_t w, std::uint32_t h) {
    std::uint32_t pitch;
    if (e->bw <= 1) {
        pitch = ((std::uint32_t)e->bpp * w) / 8;
    } else {
        std::uint32_t bw = e->bw;
        std::uint32_t blocks_w = (w + bw - 1) / bw;
        pitch = blocks_w * e->bpb;
    }
    std::uint32_t size;
    if (e->bh <= 1) {
        size = pitch * h;
    } else {
        std::uint32_t bh = e->bh;
        size = pitch * ((h + bh - 1) / bh);
    }
    if (size < e->min) size = e->min;
    return size;
}

int main() {
    build_table();

    // ---- Explicit expected-value assertions (hand-computed from the table) ----
    // Uncompressed formats (bh=1, min=0): size = bpp*w*h/8
    _last = compute_size_in_bytes(table, 0, 16, 16);   CHECK(_last == 16*16,      "fmt0 8bpp 16x16");
    _last = compute_size_in_bytes(table, 0, 4, 3);     CHECK(_last == 4*3,        "fmt0 8bpp 4x3");
    _last = compute_size_in_bytes(table, 1, 16, 16);   CHECK(_last == 512,        "fmt1 16bpp 16x16");
    _last = compute_size_in_bytes(table, 10, 8, 8);    CHECK(_last == 192,        "fmt10 24bpp 8x8");
    _last = compute_size_in_bytes(table, 12, 4, 4);    CHECK(_last == 64,         "fmt12 32bpp 4x4");
    _last = compute_size_in_bytes(table, 37, 4, 4);    CHECK(_last == 96,         "fmt37 48bpp 4x4");
    _last = compute_size_in_bytes(table, 38, 4, 4);    CHECK(_last == 128,        "fmt38 64bpp 4x4");
    _last = compute_size_in_bytes(table, 39, 4, 4);    CHECK(_last == 192,        "fmt39 96bpp 4x4");

    // Compressed formats (bh=4) with min=0: size = pitch * ceil(h/4)
    _last = compute_size_in_bytes(table, 17, 16, 16);  CHECK(_last == 128,        "fmt17 4x4 b8 16x16");
    _last = compute_size_in_bytes(table, 17, 4, 4);    CHECK(_last == 8,          "fmt17 4x4 b8 4x4");
    _last = compute_size_in_bytes(table, 17, 5, 5);    CHECK(_last == 32,         "fmt17 4x4 b8 5x5");
    _last = compute_size_in_bytes(table, 17, 1, 1);    CHECK(_last == 8,          "fmt17 4x4 b8 1x1");
    _last = compute_size_in_bytes(table, 19, 16, 16);  CHECK(_last == 256,        "fmt19 4x4 b16 16x16");

    // Compressed formats with min_size clamp
    _last = compute_size_in_bytes(table, 21, 8, 8);    CHECK(_last == 32,         "fmt21 b8x8 min32 8x8 (clamped)");
    _last = compute_size_in_bytes(table, 21, 32, 16);  CHECK(_last == 128,        "fmt21 b8x8 min32 32x16");
    _last = compute_size_in_bytes(table, 21, 1, 1);    CHECK(_last == 32,         "fmt21 b8x8 min32 1x1 (clamped)");

    // min_size=8: height=0 collapses to 0, then clamped up to 8
    _last = compute_size_in_bytes(table, 29, 4, 0);    CHECK(_last == 8,          "fmt29 min8 h0 (clamped)");
    // min_size=16: height=0 -> clamped to 16
    _last = compute_size_in_bytes(table, 31, 4, 0);    CHECK(_last == 16,         "fmt31 min16 h0 (clamped)");

    // Zero dimensions, uncompressed (min=0) -> 0
    _last = compute_size_in_bytes(table, 0, 0, 16);    CHECK(_last == 0,          "fmt0 w0");
    _last = compute_size_in_bytes(table, 0, 16, 0);    CHECK(_last == 0,          "fmt0 h0");
    _last = compute_size_in_bytes(table, 0, 0, 0);     CHECK(_last == 0,          "fmt0 0x0");

    // ---- Table-driven cross-check against the independent reference ----
    // Covers all 40 formats over a width/height grid, including the opaque
    // cases not enumerated above.
    const std::uint32_t dims[] = {0, 1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 64, 127, 256};
    for (int fmt = 0; fmt < 40; ++fmt) {
        for (std::uint32_t w : dims) {
            for (std::uint32_t h : dims) {
                std::uint32_t got = compute_size_in_bytes(table, fmt, w, h);
                std::uint32_t exp = ref_size(&G_TABLE[fmt], w, h);
                if (got != exp) {
                    std::fprintf(stderr,
                        "FAIL: fmt%d w=%u h=%u got=%u exp=%u\n", fmt, w, h, got, exp);
                    ++failures;
                }
            }
        }
    }

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("compute_size_in_bytes: all tests passed\n");
    return 0;
}