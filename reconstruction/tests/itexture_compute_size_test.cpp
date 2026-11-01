// Tests for ITexture::computeSizeInBytes() const — ELF VA 0x0077d99c, 128 B
#include <cstdint>
#include <cstdio>

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { std::fprintf(stderr, "FAIL: %s\n", msg); ++failures; } \
} while (0)

// Match the signature in block_0077d99c.cpp
using vtable_call_fn = std::uint32_t (*)(const void*, std::uint32_t);

extern "C"
std::uint32_t nova3_recovered_itexture_compute_size_in_bytes(
    const void* self,
    vtable_call_fn call1,
    vtable_call_fn call2,
    std::uint32_t descriptor_word,
    std::uint8_t  descriptor_byte_2c,
    const std::uint32_t* inner_table,
    std::uint8_t  inner_index);

// --- Independent reference implementation --------------------------------

static std::uint32_t align_up_128(std::uint32_t x) {
    return (x + 0x7fu) & ~0x7fu;
}

static std::uint32_t ref_compute(vtable_call_fn call1,
                                 vtable_call_fn call2,
                                 const void* self,
                                 std::uint32_t desc_word,
                                 std::uint8_t  desc_byte_2c,
                                 const std::uint32_t* inner_table,
                                 std::uint8_t  inner_idx)
{
    std::uint32_t weight = ((desc_word & 7u) == 3u) ? 5u : 0u;

    if (!(desc_byte_2c & 2u)) {
        // Path A
        std::uint32_t raw = inner_table[inner_idx];
        return weight * align_up_128(raw) + raw;
    }

    // Path B
    std::uint32_t A = call1(self, 0);
    std::uint32_t B = call2(self, 0);
    return weight * align_up_128(B) + A;
}

// --- Mock vtable call functions for testing -----------------------------

// Stateful mock: returns different values on successive calls.
struct MockState {
    std::uint32_t call1_val;
    std::uint32_t call2_val;
    bool was_called;
};

static MockState g_mock;

static std::uint32_t mock_call1(const void*, std::uint32_t) {
    return g_mock.call1_val;
}

static std::uint32_t mock_call2(const void*, std::uint32_t) {
    // second call — returns call2_val
    return g_mock.call2_val;
}

static std::uint32_t same_call(const void*, std::uint32_t) {
    // returns same value both times
    return 42;
}

// --- Tests ---------------------------------------------------------------

static void test_path_a() {
    // Path A: inner table lookup, alignment, cubemap weight
    std::uint32_t table[16] = {};
    const void* dummy_self = reinterpret_cast<const void*>(0x1000);

    // Non-cubemap (flag != 3): weight=0, result = raw only
    table[0] = 0;
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, nullptr, nullptr,
              0,     // desc_word: flag=0
              0,     // desc_byte_2c: no fast path
              table, 0) == 0u,
          "pathA: raw=0 flag=0 → 0");

    table[0] = 100;
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, nullptr, nullptr, 0, 0, table, 0) == 100u,
          "pathA: raw=100 flag=0 → 100");

    table[0] = 200;
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, nullptr, nullptr, 0, 0, table, 0) == 200u,
          "pathA: raw=200 flag=0 → 200");

    // Cubemap (flag == 3): weight=5
    // aligned = align_up_128(raw)
    // result = 5 * aligned + raw
    table[0] = 50;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, nullptr, nullptr,
            3,     // desc_word: flag=3 (cubemap)
            0,     // not fast path
            table, 0);
        std::uint32_t exp = 5 * align_up_128(50) + 50;  // 5*128+50=690
        CHECK(got == exp, "pathA: raw=50 flag=3 → 690");
        CHECK(got == 690u, "pathA: raw=50 flag=3 value");
    }

    table[0] = 128;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, nullptr, nullptr, 3, 0, table, 0);
        std::uint32_t exp = 5 * 128 + 128;  // = 768
        CHECK(got == exp, "pathA: raw=128 flag=3 → 768");
    }

    table[0] = 129;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, nullptr, nullptr, 3, 0, table, 0);
        // aligned = 256, 5*256+129 = 1409
        CHECK(got == 1409u, "pathA: raw=129 flag=3 → 1409");
    }

    // Different inner_index
    table[3] = 77;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, nullptr, nullptr, 0, 0, table, 3);
        CHECK(got == 77u, "pathA: idx=3 raw=77 flag=0 → 77");
    }

    // Flag 7 (not cubemap): weight=0
    table[0] = 99;
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, nullptr, nullptr, 7, 0, table, 0) == 99u,
          "pathA: flag=7 (non-cubemap) weight=0");
}

static void test_path_b() {
    std::uint32_t table[16] = {};
    const void* dummy_self = reinterpret_cast<const void*>(0x2000);

    // Path B: two vtable calls, align second, weight * aligned_B + A
    // Non-cubemap: weight=0, result = A (second call ignored)
    g_mock.call1_val = 100;
    g_mock.call2_val = 999;
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, mock_call1, mock_call2,
              0,     // flag=0, non-cubemap
              2,     // byte_2c: bit 1 set → fast path
              table, 0) == 100u,
          "pathB: A=100 B=999 flag=0 → 100 (weight=0, only A)");

    // Cubemap: weight=5, result = 5 * align_up_128(B) + A
    g_mock.call1_val = 10;
    g_mock.call2_val = 50;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, mock_call1, mock_call2,
            3,     // flag=3 (cubemap)
            2,     // fast path
            table, 0);
        std::uint32_t exp = 5 * align_up_128(50) + 10;  // 5*128+10=650
        CHECK(got == 650u, "pathB: A=10 B=50 flag=3 → 650");
    }

    // B already aligned
    g_mock.call1_val = 5;
    g_mock.call2_val = 256;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy_self, mock_call1, mock_call2, 3, 2, table, 0);
        CHECK(got == 5*256 + 5u, "pathB: A=5 B=256 flag=3 → 1285");
    }

    // Same call both times
    CHECK(nova3_recovered_itexture_compute_size_in_bytes(
              dummy_self, same_call, same_call, 3, 2, table, 0) ==
          5u * align_up_128(42) + 42u,
          "pathB: same_call 42 flag=3 → 5*128+42=682");
}

static void test_table_driven() {
    const void* dummy = reinterpret_cast<const void*>(0x3000);
    std::uint32_t table[16];

    const std::uint32_t raw_vals[] = {0, 1, 63, 64, 127, 128, 129, 255, 256, 1000};
    const std::uint32_t flags[]   = {0, 1, 2, 3, 4, 5, 6, 7};
    const std::uint32_t A_vals[]  = {0, 1, 100, 1000};
    const std::uint32_t B_vals[]  = {0, 1, 50, 128, 129, 500};

    // Path A
    for (std::uint32_t flag : flags) {
        for (std::uint32_t raw : raw_vals) {
            for (std::uint8_t idx = 0; idx < 8; ++idx) {
                table[idx] = raw;
                std::uint32_t got =
                    nova3_recovered_itexture_compute_size_in_bytes(
                        dummy, nullptr, nullptr, flag, 0, table, idx);
                std::uint32_t exp = ref_compute(nullptr, nullptr, dummy, flag, 0, table, idx);
                if (got != exp) {
                    std::fprintf(stderr,
                        "FAIL: pathA raw=%u flag=%u idx=%u got=%u exp=%u\n",
                        raw, flag, idx, got, exp);
                    ++failures;
                }
            }
        }
    }

    // Path B
    for (std::uint32_t flag : flags) {
        for (std::uint32_t A : A_vals) {
            for (std::uint32_t B : B_vals) {
                g_mock.call1_val = A;
                g_mock.call2_val = B;
                std::uint32_t got =
                    nova3_recovered_itexture_compute_size_in_bytes(
                        dummy, mock_call1, mock_call2, flag, 2, table, 0);
                std::uint32_t exp =
                    ref_compute(mock_call1, mock_call2, dummy, flag, 2, table, 0);
                if (got != exp) {
                    std::fprintf(stderr,
                        "FAIL: pathB A=%u B=%u flag=%u got=%u exp=%u\n",
                        A, B, flag, got, exp);
                    ++failures;
                }
            }
        }
    }

    // Edge: byte_2c has bit 1 set but also high bits set
    g_mock.call1_val = 77;
    g_mock.call2_val = 88;
    {
        std::uint32_t got = nova3_recovered_itexture_compute_size_in_bytes(
            dummy, mock_call1, mock_call2, 0, 0xFF, table, 0);
        // 0xFF & 2 = 2 → fast path, flag bit irrelevant (not 3)
        CHECK(got == 77u, "pathB: byte_2c=0xFF flag=0 → A=77");
    }
}

int main() {
    test_path_a();
    test_path_b();
    test_table_driven();

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("ITexture::computeSizeInBytes(): all tests passed\n");
    return 0;
}