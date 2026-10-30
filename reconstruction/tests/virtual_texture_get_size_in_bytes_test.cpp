// Tests for CVirtualTexture::getSizeInBytes(unsigned char) at 0x0077d4a8
// and the subordinate getMipmapCount at 0x007b84e0.
//
// Reference ELF SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// getMipmapCount(width, height, mipCount) behaviour (A32 at 0x7b84e0):
//   if mipCount == 0: return 0
//   else: return max(floor(log2(max(w,h))), floor(log2(mipCount))) + 1
//
// CVirtualTexture::getSizeInBytes(unsigned char):
//   level = getMipmapCount(width, height, mipCount)
//   level = min(level, targetLevel)
//   format = (descriptor[+0x24] >> 6) & 0x3F
//   return computeMipmapSizeInBytes_slices(format, w, h, mipCount, level, false)

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "pixel_format.h"

using namespace nova3::recovered::pixel_format;

// --- getMipmapCount tests --------------------------------------------------

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

static void test_get_mipmap_count() {
    // Zeros: mipCount=0 returns 0 regardless of dimensions
    CHECK(get_mipmap_count(0, 0, 0) == 0, "gmc(0,0,0) == 0");
    CHECK(get_mipmap_count(1024, 1024, 0) == 0, "gmc(1024,1024,0) == 0");

    // Single values: floor(log2(v)) + 1 = level count
    CHECK(get_mipmap_count(1, 1, 1) == 1, "gmc(1,1,1) == 1");       // max(0,0,0)+1=1
    CHECK(get_mipmap_count(2, 2, 1) == 2, "gmc(2,2,1) == 2");       // max(1,1,0)+1=2
    CHECK(get_mipmap_count(4, 4, 1) == 3, "gmc(4,4,1) == 3");       // max(2,2,0)+1=3
    CHECK(get_mipmap_count(16, 16, 1) == 5, "gmc(16,16,1) == 5");   // max(4,4,0)+1=5
    CHECK(get_mipmap_count(255, 255, 1) == 8, "gmc(255,255,1) == 8"); // floor(log2(255))=7
    CHECK(get_mipmap_count(256, 256, 1) == 9, "gmc(256,256,1) == 9"); // floor(log2(256))=8
    CHECK(get_mipmap_count(1024, 1024, 1) == 11, "gmc(1024,1024,1) == 11");

    // mipCount dominates when it has more bits than max dimension
    CHECK(get_mipmap_count(1, 1, 16) == 5, "gmc(1,1,16) == 5");     // max(0,0,4)+1=5
    CHECK(get_mipmap_count(4, 4, 64) == 7, "gmc(4,4,64) == 7");     // max(2,2,6)+1=7

    // Asymmetric dimensions: max(w_bits, h_bits)
    CHECK(get_mipmap_count(1024, 16, 1) == 11, "gmc(1024,16,1) == 11");
    CHECK(get_mipmap_count(16, 1024, 1) == 11, "gmc(16,1024,1) == 11");

    // mipCount between: does not override dimension bits
    CHECK(get_mipmap_count(1024, 1024, 8) == 11, "gmc(1024,1024,8) == 11");
}

// --- CVirtualTexture::getSizeInBytes(unsigned char) tests ------------------

// Minimal descriptor struct: we only need the uint32_t at +0x24.
// The 6-bit format index lives at bits 6-11.
struct alignas(4) TestDescriptor {
    std::uint8_t _pad[0x24];
    std::uint32_t config_word;
};

// CVirtualTextureFields matches the layout in block_0077d4a8.cpp.
struct CVirtualTextureFields {
    std::uint32_t width;      // +0x14
    std::uint32_t height;     // +0x18
    std::uint32_t mip_count;  // +0x1c
};

extern "C" std::uint32_t nova3_recovered_cvirtual_texture_get_size_in_bytes(
    const CVirtualTextureFields* vt,
    const void* descriptor,
    std::uint8_t target_level,
    const PixelFormatDescriptor* table);

// Independent reference: same logic as original, no shared code.
static std::uint32_t ref_get_size_in_bytes(
    const CVirtualTextureFields& vt,
    const void* descriptor,
    std::uint8_t target_level,
    const PixelFormatDescriptor* table)
{
    const std::uint32_t level = get_mipmap_count(vt.width, vt.height, vt.mip_count);
    const std::uint32_t clamped = (level <= static_cast<std::uint32_t>(target_level))
                                      ? level : static_cast<std::uint32_t>(target_level);

    const auto* words = static_cast<const std::uint32_t*>(descriptor);
    const std::uint32_t config = words[0x24 / sizeof(std::uint32_t)];
    const std::uint32_t format_index = (config >> 6) & 0x3Fu;

    return compute_mipmap_size_in_bytes_slices(
        table, format_index,
        vt.width, vt.height, vt.mip_count,
        static_cast<std::uint8_t>(clamped), false);
}

// 40-entry format table (same as other tests)
struct Entry { std::uint8_t bpb, bpp, bw, bh, min; };
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

static void test_virtual_texture_get_size_in_bytes() {
    build_table();

    TestDescriptor desc;
    std::memset(&desc, 0, sizeof(desc));

    CVirtualTextureFields vt;

    // Scenario 1: fmt0 (8bpp), 256x256, 1 mip level, target=0
    desc.config_word = (0 << 6);  // format index 0
    vt.width = 256; vt.height = 256; vt.mip_count = 1;
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 0, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 0, g_table);
        // getMipmapCount(256,256,1) = max(8,8,0)+1 = 9, clamped to 0.
        // computeMipmapSizeInBytes_slices(fmt0,256,256,1,0,false) = 1*256*256*8/8 = 65536
        CHECK(got == exp, "vt fmt0 256x256 mip1 target0");
        CHECK(got == 65536u, "vt fmt0 256x256 mip1 target0 value");
    }

    // Scenario 2: target=1 (mip level 1)
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 1, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 1, g_table);
        CHECK(got == exp, "vt fmt0 256x256 mip1 target1");
        CHECK(got == 16384u, "vt fmt0 256x256 mip1 target1 value");
    }

    // Scenario 3: target=255 (clamp to max level, 8 for 256x256)
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 255, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 255, g_table);
        CHECK(got == exp, "vt fmt0 256x256 mip1 target255");
        CHECK(got == 1u, "vt fmt0 256x256 mip1 target255 value");
    }

    // Scenario 4: compressed fmt17 (4x4 blocks, bpb=8), 256x256, mipCount=4
    desc.config_word = (17 << 6);  // format index 17
    vt.width = 256; vt.height = 256; vt.mip_count = 4;
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 0, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 0, g_table);
        CHECK(got == exp, "vt fmt17 256x256 mip4 target0");
        CHECK(got == 131072u, "vt fmt17 256x256 mip4 target0 value");
    }

    // Scenario 5: compressed fmt17, target=1
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 1, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 1, g_table);
        CHECK(got == exp, "vt fmt17 256x256 mip4 target1");
        CHECK(got == 16384u, "vt fmt17 256x256 mip4 target1 value");
    }

    // Scenario 6: small texture, target out of range
    desc.config_word = (0 << 6);
    vt.width = 1; vt.height = 1; vt.mip_count = 1;
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 0, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 0, g_table);
        CHECK(got == exp, "vt fmt0 1x1 mip1 target0");
        CHECK(got == 1u, "vt fmt0 1x1 mip1 target0 value");
    }

    // Scenario 7: mipCount=0 — getMipmapCount returns 0, but
    // computeMipmapSizeInBytes clamps depth to 1 via mip_shift, so the
    // result is one slice's worth of data (not zero).
    desc.config_word = (0 << 6);
    vt.width = 256; vt.height = 256; vt.mip_count = 0;
    {
        std::uint32_t got = nova3_recovered_cvirtual_texture_get_size_in_bytes(
            &vt, &desc, 0, g_table);
        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, 0, g_table);
        CHECK(got == exp, "vt fmt0 256x256 mip0 target0");
        CHECK(got == 65536u, "vt fmt0 256x256 mip0 target0 value"); // depth clamps to 1
    }

    // --- Table-driven cross-check across formats, dimensions, levels ----
    const std::uint32_t dims[] = {0, 1, 2, 3, 4, 8, 15, 16, 31, 32, 64, 128, 256};
    const std::uint8_t  lvls[] = {0, 1, 2, 3, 5, 8, 255};
    const std::uint32_t cnts[] = {0, 1, 2, 4, 6};

    for (int fmt = 0; fmt < 40; ++fmt) {
        desc.config_word = static_cast<std::uint32_t>(fmt) << 6;
        for (std::uint32_t w : dims) {
            for (std::uint32_t h : dims) {
                for (std::uint32_t c : cnts) {
                    vt.width = w; vt.height = h; vt.mip_count = c;
                    for (std::uint8_t lvl : lvls) {
                        std::uint32_t got =
                            nova3_recovered_cvirtual_texture_get_size_in_bytes(
                                &vt, &desc, lvl, g_table);
                        std::uint32_t exp = ref_get_size_in_bytes(vt, &desc, lvl, g_table);
                        if (got != exp) {
                            std::fprintf(stderr,
                                "FAIL: vt fmt%d w=%u h=%u cnt=%u lvl=%u got=%u exp=%u\n",
                                fmt, w, h, c, static_cast<unsigned>(lvl), got, exp);
                            ++failures;
                        }
                    }
                }
            }
        }
    }
}

int main() {
    test_get_mipmap_count();
    test_virtual_texture_get_size_in_bytes();

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("CVirtualTexture::getSizeInBytes(unsigned char): all tests passed\n");
    return 0;
}