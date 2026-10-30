// Behavioral tests for glitch::video primitive-counting functions.
//
// The expected values below are LITERAL truth tables transcribed from the
// linked ARM disassembly of the three reference functions (not produced by
// re-running the recovered logic), so any transcription or arithmetic error
// in the reconstruction is caught as a mismatch.
#include <cstdint>
#include <cstdio>

// Extern "C" dispatch symbols compiled from the recovered source blocks.
extern "C" std::uint32_t nova3_recovered_get_primitive_count(std::uint32_t, std::uint32_t);
extern "C" std::uint32_t nova3_recovered_get_triangle_count(std::uint32_t, std::uint32_t);
extern "C" std::uint32_t nova3_recovered_get_index_count(std::uint32_t, std::uint32_t);

static int g_failures = 0;

static void check(const char* fn, std::uint32_t type, std::uint32_t in,
                  std::uint32_t got, std::uint32_t want) {
    if (got != want) {
        std::printf("FAIL %s(type=%u, in=%u): got %u, want %u\n",
                    fn, type, in, got, want);
        ++g_failures;
    }
}

// getPrimitiveCount(type, indexCount) — literal expected table.
// Rows ordered by indexCount 0..8; each column is a type 0..8.
static const std::uint32_t kPrimCount[9][9] = {
    //         t0 t1 t2 t3 t4 t5 t6 t7 t8
    /*N=0*/ {  0, 0, 0, 0, 0, 0, 0, 0, 0 },
    /*N=1*/ {  1, 0, 1, 0, 0, 0, 0, 0, 0 },
    /*N=2*/ {  2, 1, 2, 0, 0, 0, 0, 0, 0 },
    /*N=3*/ {  3, 2, 3, 1, 1, 1, 1, 0, 0 },
    /*N=4*/ {  4, 3, 4, 1, 2, 2, 1, 1, 1 },
    /*N=5*/ {  5, 4, 5, 1, 3, 3, 1, 1, 1 },
    /*N=6*/ {  6, 5, 6, 2, 4, 4, 2, 2, 1 },
    /*N=7*/ {  7, 6, 7, 2, 5, 5, 2, 2, 1 },
    /*N=8*/ {  8, 7, 8, 2, 6, 6, 2, 3, 2 },
};

// getTriangleCount(type, indexCount) — literal expected table.
static const std::uint32_t kTriCount[9][9] = {
    //         t0 t1 t2 t3 t4 t5 t6 t7 t8
    /*N=0*/ {  0, 0, 0, 0, 0, 0, 0, 0, 0 },
    /*N=1*/ {  0, 0, 0, 0, 0, 0, 0, 0, 0 },
    /*N=2*/ {  0, 0, 0, 0, 0, 0, 0, 0, 0 },
    /*N=3*/ {  0, 0, 0, 0, 1, 1, 1, 0, 0 },
    /*N=4*/ {  0, 0, 0, 0, 2, 2, 1, 2, 2 },
    /*N=5*/ {  0, 0, 0, 0, 3, 3, 1, 2, 2 },
    /*N=6*/ {  0, 0, 0, 0, 4, 4, 2, 4, 2 },
    /*N=7*/ {  0, 0, 0, 0, 5, 5, 2, 4, 2 },
    /*N=8*/ {  0, 0, 0, 0, 6, 6, 2, 6, 4 },
};

// getIndexCount(type, primitiveCount) — literal expected table.
static const std::uint32_t kIndexCount[9][9] = {
    //         t0 t1 t2 t3  t4  t5 t6  t7  t8
    /*P=0*/ {  0, 1, 0, 0,  2,  2, 0,  2,  0 },
    /*P=1*/ {  1, 2, 1, 0,  3,  3, 3,  4,  4 },
    /*P=2*/ {  2, 3, 2, 0,  4,  4, 6,  6,  8 },
    /*P=3*/ {  3, 4, 3, 0,  5,  5, 9,  8, 12 },
    /*P=4*/ {  4, 5, 4, 0,  6,  6, 12,10, 16 },
    /*P=5*/ {  5, 6, 5, 0,  7,  7, 15,12, 20 },
    /*P=6*/ {  6, 7, 6, 0,  8,  8, 18,14, 24 },
    /*P=7*/ {  7, 8, 7, 0,  9,  9, 21,16, 28 },
    /*P=8*/ {  8, 9, 8, 0, 10, 10, 24,18, 32 },
};

int main() {
    for (std::uint32_t t = 0; t <= 8; ++t) {
        for (std::uint32_t n = 0; n <= 8; ++n) {
            check("getPrimitiveCount", t, n,
                  nova3_recovered_get_primitive_count(t, n), kPrimCount[n][t]);
            check("getTriangleCount", t, n,
                  nova3_recovered_get_triangle_count(t, n), kTriCount[n][t]);
            check("getIndexCount", t, n,
                  nova3_recovered_get_index_count(t, n), kIndexCount[n][t]);
        }
    }

    // Out-of-range discriminants must return 0 for all three functions.
    const std::uint32_t kBadTypes[5] = {9u, 10u, 0xffffffffu, 100u, 0xdeadu};
    for (std::uint32_t bad : kBadTypes) {
        check("getPrimitiveCount(default)", bad, 5,
              nova3_recovered_get_primitive_count(bad, 5), 0);
        check("getTriangleCount(default)", bad, 5,
              nova3_recovered_get_triangle_count(bad, 5), 0);
        check("getIndexCount(default)", bad, 5,
              nova3_recovered_get_index_count(bad, 5), 0);
    }

    // Division floors (never rounds/carries) — would catch a >>/ceil error.
    check("getPrimitiveCount(floor /3)", 6, 2,
          nova3_recovered_get_primitive_count(6, 2), 0);
    check("getPrimitiveCount(floor /4)", 8, 3,
          nova3_recovered_get_primitive_count(8, 3), 0);
    // Quad strip/triangle-strip subtraction saturates at zero (no wraparound).
    check("getPrimitiveCount(saturate)", 4, 1,
          nova3_recovered_get_primitive_count(4, 1), 0);
    check("getTriangleCount(saturate)", 7, 1,
          nova3_recovered_get_triangle_count(7, 1), 0);

    if (g_failures == 0) {
        std::printf("primitive_count: all checks passed\n");
        return 0;
    }
    std::printf("primitive_count: %d check(s) FAILED\n", g_failures);
    return 1;
}