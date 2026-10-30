// Tests for CVirtualTexture::getSizeInBytes() const (no-arg overload)
// Premium ELF VA 0x007863e4, 680 bytes, A32.
//
// Reference ELF SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Behaviour:
//   Fast path (descriptor[0x2c] & 2):
//     return vtable[0x14](this, 0) * cubemap_factor
//   Slow path:
//     level = getMipmapCount(w, h, mipCount)
//     if level == 0: return 0
//     for lvl in 0..level-1:
//       size = computeSizeInBytes(fmt, w>>lvl, h>>lvl)
//       total += size * max(1, depth>>lvl)
//     return total * cubemap_factor
//
//   cubemap_factor = (descriptor[0x24] & 7) == 3 ? 6 : 1

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "pixel_format.h"

using namespace nova3::recovered::pixel_format;

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

// --- Extern the function under test ----------------------------------------

// Matches CVirtualTextureHeader in block_007863e4.cpp.
struct CVirtualTextureHeader {
    std::uint32_t width;      // +0x14
    std::uint32_t height;     // +0x18
    std::uint32_t mip_count;  // +0x1c — also depth parameter
};

extern "C"
std::uint32_t nova3_recovered_cvirtual_texture_get_size_in_bytes_noarg(
    const CVirtualTextureHeader* vt,
    std::uint32_t descriptor_config_word,
    bool use_external_size,
    std::uint32_t external_data_size,
    const PixelFormatDescriptor* table);

// --- Independent reference: same logic, separate code path -----------------

static std::uint32_t ref_get_size_in_bytes_noarg(
    const CVirtualTextureHeader& vt,
    std::uint32_t config_word,
    bool use_external,
    std::uint32_t ext_size,
    const PixelFormatDescriptor* table)
{
    if (use_external) {
        std::uint32_t factor = ((config_word & 7u) == 3u) ? 6u : 1u;
        return ext_size * factor;
    }

    std::uint32_t level_count = get_mipmap_count(vt.width, vt.height, vt.mip_count);
    if (level_count == 0) return 0;

    std::uint32_t fmt_idx = (config_word >> 6) & 0x3Fu;
    std::uint32_t factor  = ((config_word & 7u) == 3u) ? 6u : 1u;

    std::uint32_t total = 0;
    for (std::uint32_t lvl = 0; lvl < level_count; ++lvl) {
        std::uint32_t w_s = mip_shift(vt.width,  static_cast<std::uint8_t>(lvl));
        std::uint32_t h_s = mip_shift(vt.height, static_cast<std::uint8_t>(lvl));
        std::uint32_t d_s = mip_shift(vt.mip_count,
                                      static_cast<std::uint8_t>(lvl));

        std::uint32_t sz = compute_size_in_bytes(table, fmt_idx, w_s, h_s);
        total += sz * d_s;
    }
    return total * factor;
}

// --- 40-entry format table (same as other pixel_format tests) --------------

struct Entry { std::uint8_t bpb, bpp, bw, bh, min; };
static const Entry G_TABLE[40] = {
    //  0    1    2    3    4    5    6    7    8    9
    {1,8,1,1,0},  {2,16,1,1,0}, {1,8,1,1,0},  {2,16,1,1,0}, {2,16,1,1,0},
    {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0}, {2,16,1,1,0},
    // 10    11    12    13    14    15    16    17    18    19
    {3,24,1,1,0}, {3,24,1,1,0}, {4,32,1,1,0}, {4,32,1,1,0}, {4,32,1,1,0},
    {4,32,1,1,0}, {4,32,1,1,0}, {8,4,4,4,0},  {8,4,4,4,0},  {16,8,4,4,0},
    // 20    21    22    23    24    25    26    27    28    29
    {16,8,4,4,0},  {8,2,8,4,32}, {8,2,8,4,32}, {8,4,4,4,32}, {8,4,4,4,32},
    {8,2,8,4,32},  {8,2,8,4,32}, {8,4,4,4,32}, {8,4,4,4,32}, {8,4,4,4,8},
    // 30    31    32    33    34    35    36    37    38    39
    {8,4,4,4,8},   {16,8,4,4,16},{8,4,4,4,8},  {16,8,4,4,16}, {16,8,4,4,16},
    {8,4,4,4,8},   {16,8,4,4,16},{6,48,1,1,0}, {8,64,1,1,0}, {12,96,1,1,0}
};

static PixelFormatDescriptor g_table[40];

static void build_table() {
    std::memset(g_table, 0, sizeof(g_table));
    for (int i = 0; i < 40; ++i) {
        g_table[i].bytes_per_block = G_TABLE[i].bpb;
        g_table[i].bits_per_pixel   = G_TABLE[i].bpp;
        g_table[i].block_width      = G_TABLE[i].bw;
        g_table[i].block_height     = G_TABLE[i].bh;
        g_table[i].min_size         = G_TABLE[i].min;
    }
}

// Helper to invoke the recovered function.
static std::uint32_t call_recovered(
    const CVirtualTextureHeader& vt,
    std::uint32_t config_word,
    bool use_ext, std::uint32_t ext_size)
{
    return nova3_recovered_cvirtual_texture_get_size_in_bytes_noarg(
        &vt, config_word, use_ext, ext_size, g_table);
}

// --- Fast-path tests -------------------------------------------------------

static void test_fast_path() {
    CVirtualTextureHeader vt = {256, 256, 1};

    // Fast path with factor 1 (desc & 7 != 3)
    // external_size=100, factor=1 → 100
    {
        std::uint32_t got = call_recovered(vt, 0, true, 100);
        CHECK(got == 100u, "fast: ext=100 cfg=0 → 100");
    }

    // Fast path with factor 6 (desc & 7 == 3)
    // external_size=100, factor=6 → 600
    {
        std::uint32_t got = call_recovered(vt, 3, true, 100);
        CHECK(got == 600u, "fast: ext=100 cfg=3 → 600");
    }

    // Fast path, factor 6, zero external size
    {
        std::uint32_t got = call_recovered(vt, 3, true, 0);
        CHECK(got == 0u, "fast: ext=0 cfg=3 → 0");
    }

    // Fast path does NOT use format index bits — only low 3 bits matter
    {
        std::uint32_t got = call_recovered(vt, 0x43, true, 50); // (fmt=1, flag=3) & 7 = 3
        CHECK(got == 300u, "fast: ext=50 cfg=0x43(flag=3) → 300");
    }
    {
        std::uint32_t got = call_recovered(vt, 0x44, true, 50); // (fmt=1, flag=4) & 7 = 4
        CHECK(got == 50u, "fast: ext=50 cfg=0x44(flag=4) → 50");
    }
}

// --- Slow-path: hand-computed scenarios ------------------------------------

static void test_slow_path_hand_computed() {
    CVirtualTextureHeader vt;

    // --- Uncompressed format 0 (8 bpp, 1x1 blocks) ---
    std::uint32_t cfg0 = 0;  // fmt=0, flag=0

    // 1x1 pixel, mipCount=1 → level_count = max(log2(1),log2(1),log2(1))+1=1
    // one level: computeSizeInBytes(fmt0, 1, 1) = 1*1*8/8 = 1, depth=1, total=1*1=1
    vt = {1, 1, 1};
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg0, false, 0, g_table);
        CHECK(got == exp, "slow: 1x1 mip1 fmt0");
        CHECK(got == 1u, "slow: 1x1 mip1 fmt0 value");
    }

    // 256x256, mipCount=1 → level_count=9
    // Levels: 256→128→64→32→16→8→4→2→1
    // size(256)=65536, size(128)=16384, size(64)=4096, size(32)=1024,
    // size(16)=256, size(8)=64, size(4)=16, size(2)=4, size(1)=1
    // total = 65536+16384+4096+1024+256+64+16+4+1 = 87381
    vt = {256, 256, 1};
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg0, false, 0, g_table);
        CHECK(got == exp, "slow: 256x256 mip1 fmt0");
        CHECK(got == 87381u, "slow: 256x256 mip1 fmt0 value");
    }

    // 256x256, mipCount=4 → depth starts at 4, shifts down per level
    // level_count = max(log2(256),0,log2(4))+1 = max(8,0,2)+1 = 9
    // Levels 0-8: d_s = 4,2,1,1,1,1,1,1,1
    vt = {256, 256, 4};
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg0, false, 0, g_table);
        CHECK(got == exp, "slow: 256x256 mip4 fmt0");
        // l0: 65536*4=262144, l1: 16384*2=32768,
        // l2:4096, l3:1024, l4:256, l5:64, l6:16, l7:4, l8:1
        // = 262144+32768+4096+1024+256+64+16+4+1 = 300373
        CHECK(got == 300373u, "slow: 256x256 mip4 fmt0 value");
    }

    // --- Compressed format 17 (ETC1: 4x4 blocks, 8 bpb) ---
    // size(256,256) = 8 * ceil(256/4) * ceil(256/4) = 8*64*64 = 32768
    // size(128,128) = 8 * 32 * 32 = 8192
    // size(64,64) = 8 * 16 * 16 = 2048
    // size(32,32) = 8 * 8 * 8 = 512
    // size(16,16) = 8 * 4 * 4 = 128
    // size(8,8) = 8 * 2 * 2 = 32 (but min_size=0, so 32)
    // size(4,4) = 8 * 1 * 1 = 8
    // size(2,2) = 8 * ceil(0.5) * ceil(0.5) = 8*1*1 = 8 (dim clamped to 1)
    // size(1,1) = 8
    // total = 32768+8192+2048+512+128+32+8+8+8 = 43704
    std::uint32_t cfg17 = static_cast<std::uint32_t>(17) << 6;
    vt = {256, 256, 1};
    {
        std::uint32_t got = call_recovered(vt, cfg17, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg17, false, 0, g_table);
        CHECK(got == exp, "slow: 256x256 mip1 fmt17");
        CHECK(got == 43704u, "slow: 256x256 mip1 fmt17 value");
    }

    // Compressed fmt17 with cubemap factor 6
    std::uint32_t cfg17_cube = (static_cast<std::uint32_t>(17) << 6) | 3;
    vt = {256, 256, 1};
    {
        std::uint32_t got = call_recovered(vt, cfg17_cube, false, 0);
        CHECK(got == 43704u * 6u, "slow: 256x256 mip1 fmt17 cube6");
    }

    // --- Asymmetric dimensions: 512x64 ---
    // level_count = max(log2(512),log2(64),log2(1))+1 = max(9,6,0)+1 = 10
    // With mipCount=2: depth starts at 2, shifts to 1 after first level
    cfg0 = 0;
    vt = {512, 64, 2};
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg0, false, 0, g_table);
        CHECK(got == exp, "slow: 512x64 mip2 fmt0");
    }

    // --- Zero mipCount → level_count=0 → return 0 ---
    vt = {256, 256, 0};
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        CHECK(got == 0u, "slow: 256x256 mip0 → 0");
    }

    // --- Zero dimensions, mipCount=1 ---
    vt = {0, 0, 1};
    // getMipmapCount(0,0,1) = max(log2(0),log2(0),log2(1))+1
    // log2(0) in our implementation returns 0xFFFFFFFF, so max = 0xFFFFFFFF, +1 wraps to 0
    // Actually: level_count = max(0xFFFFFFFF, 0xFFFFFFFF, 0) + 1 = 0xFFFFFFFF + 1 = 0 (wrapping)
    // Wait, that can't be right for the original... The original uses shift loops:
    // for w=0: lsrs r0, r0, #1; add r1, r1, #1; bne loop → first lsrs sets zero flag, then adds 1
    // Actually the shift loop counts until value becomes zero. For input 0:
    //   shift: 0 >> 1 = 0, Z=1, then after the loop the count is whatever was accumulated...
    // Wait, let me re-examine getMipmapCount logic.
    //
    // Actually from the disassembly at 0x7b84e0:
    // For each argument, it does a shift-count loop. If the arg is 0, it immediately
    // hits lsrs, Z flag is set, and the loop doesn't execute. The count remains at its
    // initial value (which is -1/all-ones from mvn rN, #0).
    // So for w=0: log2_result = 0xFFFFFFFF (-1).
    // Then max(log2(w), log2(h), log2(mipCount)) = max(0xFFFFFFFF, 0xFFFFFFFF, 0) = 0
    // (the max of 0xFFFFFFFF and 0 in unsigned comparison is 0xFFFFFFFF, not 0)
    //
    // Wait, getMipmapCount does: max(max_dim, log2(mipCount)) + 1
    // If w=0: log2=0xFFFFFFFF, so max_dim = 0xFFFFFFFF. Then max(0xFFFFFFFF, 0) = 0xFFFFFFFF.
    // Then 0xFFFFFFFF + 1 = 0 (wrapping). So level_count = 0.
    // Then level_count == 0 → return 0.
    //
    // Hmm, but the original ARM code doesn't have wrapping — 0xFFFFFFFF + 1 in 32-bit = 0.
    // That is indeed wrapping. So for (0,0,1): level_count = 0 → return 0.
    // Let me verify my implementation handles this: get_mipmap_count(0,0,1):
    //   mip_count = 1, not 0, so we don't return early.
    //   log2(0) = 0xFFFFFFFF (all ones)
    //   max_dim = max(0xFFFFFFFF, 0xFFFFFFFF) = 0xFFFFFFFF
    //   max_count = max(0xFFFFFFFF, 0) = 0xFFFFFFFF
    //   return 0xFFFFFFFF + 1 = 0x00000000
    // So level_count = 0 → return 0.
    //
    // For the original ARM: this is actually undefined behavior in C++ (unsigned overflow),
    // but it wraps in practice and the ARM code would also wrap (0xFFFFFFFF + 1 = 0 in 32-bit).
    // So our implementation should match.
    {
        std::uint32_t got = call_recovered(vt, cfg0, false, 0);
        CHECK(got == 0u, "slow: 0x0 mip1 → 0");
    }

    // --- Small texture, min_size clamping ---
    // fmt21: 8bpb, 8x4 blocks, min_size=32
    // 4x4 tex, mipCount=1 → level_count=3 (4→2→1)
    // each level < 32, clamped to 32; 3 * 32 = 96
    std::uint32_t cfg21 = (static_cast<std::uint32_t>(21) << 6);
    vt = {4, 4, 1};
    {
        std::uint32_t got   = call_recovered(vt, cfg21, false, 0);
        std::uint32_t exp   = ref_get_size_in_bytes_noarg(vt, cfg21, false, 0, g_table);
        CHECK(got == exp, "slow: 4x4 mip1 fmt21 (min_size clamp)");
        CHECK(got == 96u, "slow: 4x4 mip1 fmt21 value");
    }

    // --- fmt38: 64bpp uncompressed (table idx 38), 256x256, mipCount=2 ---
    // level_count = max(8,8,1)+1 = 9
    // pitch = (64 * w) >> 3 = 8*w
    // l0: 8*256*256*2 = 1048576
    // l1: 8*128*128*1 = 131072
    // l2-l8: 8*(64²+32²+16²+8²+4²+2²+1²)=8*5461=43688
    // total = 1048576+131072+43688 = 1223336
    std::uint32_t cfg38 = static_cast<std::uint32_t>(38) << 6;
    vt = {256, 256, 2};
    {
        std::uint32_t got = call_recovered(vt, cfg38, false, 0);
        std::uint32_t exp = ref_get_size_in_bytes_noarg(vt, cfg38, false, 0, g_table);
        CHECK(got == exp, "slow: 256x256 mip2 fmt38");
        CHECK(got == 1223336u, "slow: 256x256 mip2 fmt38 value");
    }
}

// --- Table-driven cross-check across formats, dims, mip counts -------------

static void test_table_driven() {
    const std::uint32_t dims[]  = {0, 1, 2, 3, 4, 8, 16, 32, 64, 128, 256};
    const std::uint32_t cnts[]  = {0, 1, 2, 4, 6};
    const std::uint32_t flags[] = {0, 1, 2, 3, 4, 5, 6, 7}; // cubemap flags

    for (int fmt = 0; fmt < 40; ++fmt) {
        for (std::uint32_t w : dims) {
            for (std::uint32_t h : dims) {
                for (std::uint32_t c : cnts) {
                    CVirtualTextureHeader vt = {w, h, c};
                    for (std::uint32_t flag : flags) {
                        std::uint32_t cfg = (static_cast<std::uint32_t>(fmt) << 6) | flag;

                        // Slow path (use_external=false)
                        std::uint32_t got = call_recovered(vt, cfg, false, 0);
                        std::uint32_t exp =
                            ref_get_size_in_bytes_noarg(vt, cfg, false, 0, g_table);
                        if (got != exp) {
                            std::fprintf(stderr,
                                "FAIL: slow fmt%d w=%u h=%u cnt=%u flag=%u got=%u exp=%u\n",
                                fmt, w, h, c, flag, got, exp);
                            ++failures;
                        }

                        // Fast path: external_size=42, factor varies with flag
                        // factor = (flag==3) ? 6 : 1; expected = 42 * factor
                        std::uint32_t got_f = call_recovered(vt, cfg, true, 42);
                        std::uint32_t exp_f =
                            ref_get_size_in_bytes_noarg(vt, cfg, true, 42, g_table);
                        if (got_f != exp_f) {
                            std::fprintf(stderr,
                                "FAIL: fast fmt%d w=%u h=%u cnt=%u flag=%u got=%u exp=%u\n",
                                fmt, w, h, c, flag, got_f, exp_f);
                            ++failures;
                        }
                    }
                }
            }
        }
    }
}

// --- Asymmetric dimension stress -------------------------------------------

static void test_asymmetric() {
    // Wide textures, tall textures, non-square compressed
    const std::uint32_t w_vals[] = {1, 3, 5, 7, 8, 9, 10, 15, 16, 17, 31, 32, 33, 63, 64,
                                    65, 127, 128, 129, 255, 256, 257, 511, 512};
    const std::uint32_t h_vals[] = {1, 2, 3, 4, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64,
                                    127, 128, 255, 256, 512};

    // Test a subset of format/dimension combinations to keep runtime reasonable
    const int test_fmts[] = {0, 17, 21, 37, 38, 39};

    for (int fi = 0; fi < 6; ++fi) {
        int fmt = test_fmts[fi];
        std::uint32_t cfg = static_cast<std::uint32_t>(fmt) << 6;

        for (std::uint32_t w : w_vals) {
            for (std::uint32_t h : h_vals) {
                for (std::uint32_t c : {1u, 2u, 4u}) {
                    CVirtualTextureHeader vt = {w, h, c};
                    std::uint32_t got = call_recovered(vt, cfg, false, 0);
                    std::uint32_t exp =
                        ref_get_size_in_bytes_noarg(vt, cfg, false, 0, g_table);
                    if (got != exp) {
                        std::fprintf(stderr,
                            "FAIL: asymmetric fmt%d w=%u h=%u c=%u got=%u exp=%u\n",
                            fmt, w, h, c, got, exp);
                        ++failures;
                    }
                }
            }
        }
    }
}

// --- Main ------------------------------------------------------------------

int main() {
    build_table();

    test_fast_path();
    test_slow_path_hand_computed();
    test_table_driven();
    test_asymmetric();

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf(
        "CVirtualTexture::getSizeInBytes() no-arg: all tests passed\n");
    return 0;
}