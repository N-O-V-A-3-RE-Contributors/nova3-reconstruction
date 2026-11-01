// Behavioral test for CFPSCounter constructor (0x0077bb7c / 0x0077bbb4).
//
// The expected field values below are LITERAL from the disassembly:
//   - offset 0:  0 (8 bytes, strd)
//   - offset 8: 60 (target FPS)
//   - offsets 12,16,20,24,28: all 0
//
// The test constructs in-place and checks every byte of the struct against
// the expected initial pattern, catching any field-order or size error.
#include "fps_counter.h"
#include <cstdint>
#include <cstdio>
#include <cstring>

using nova3::recovered::fps_counter::CFPSCounter;

extern "C" CFPSCounter* nova3_recovered_fps_counter_ctor(CFPSCounter*);

static int g_failures = 0;

static void check_field(const char* name, std::uint64_t got, std::uint64_t want) {
    if (got != want) {
        std::printf("FAIL %s: got %llu, want %llu\n", name,
                    (unsigned long long)got, (unsigned long long)want);
        ++g_failures;
    }
}

int main() {
    // In-place construction via the recovered ctor.
    alignas(CFPSCounter) unsigned char buf[sizeof(CFPSCounter)];
    std::memset(buf, 0xFF, sizeof(buf));  // poison: catch zero-missing fields
    CFPSCounter* c = nova3_recovered_fps_counter_ctor(
        reinterpret_cast<CFPSCounter*>(buf));

    // Verify every field individually.
    check_field("time_accum", c->time_accum, 0);
    check_field("target_fps", c->target_fps, 60);
    check_field("field_12",   c->field_12,   0);
    check_field("field_16",   c->field_16,   0);
    check_field("field_20",   c->field_20,   0);
    check_field("field_24",   c->field_24,   0);
    check_field("field_28",   c->field_28,   0);

    // Entire byte-level check: every byte must match the expected pattern.
    // Byte pattern for a little-endian 32-byte struct:
    //   {0,0,0,0,0,0,0,0,   60,0,0,0,   0,0,0,0,   0,0,0,0,
    //    0,0,0,0,            0,0,0,0,   0,0,0,0}
    static const unsigned char kWant[32] = {
        0,0,0,0,0,0,0,0,   // time_accum (8 bytes)
        60,0,0,0,           // target_fps = 60 LE
        0,0,0,0,            // field_12
        0,0,0,0,            // field_16
        0,0,0,0,            // field_20
        0,0,0,0,            // field_24
        0,0,0,0,            // field_28
    };
    const unsigned char* bytes = reinterpret_cast<const unsigned char*>(c);
    for (int i = 0; i < 32; ++i) {
        if (bytes[i] != kWant[i]) {
            std::printf("FAIL byte[%d]: got 0x%02x, want 0x%02x\n",
                        i, bytes[i], kWant[i]);
            ++g_failures;
        }
    }

    // Negative control: construct a CFPSCounter normally (not via the extern "C"
    // wrapper) and confirm identical result.
    CFPSCounter direct;
    check_field("direct.time_accum", direct.time_accum, 0);
    check_field("direct.target_fps", direct.target_fps, 60);
    check_field("direct.field_12",   direct.field_12,   0);
    check_field("direct.field_16",   direct.field_16,   0);
    check_field("direct.field_20",   direct.field_20,   0);
    check_field("direct.field_24",   direct.field_24,   0);
    check_field("direct.field_28",   direct.field_28,   0);

    // Verify the poison bytes were overwritten (placement-new actually ran).
    // Bytes 0-7 must be 0, not the poison 0xFF.
    if (bytes[0] != 0 || bytes[7] != 0) {
        std::printf("FAIL poison check: bytes[0]=0x%02x bytes[7]=0x%02x (expected 0)\n",
                    bytes[0], bytes[7]);
        ++g_failures;
    }

    if (g_failures == 0) {
        std::printf("fps_counter_ctor: all checks passed\n");
        return 0;
    }
    std::printf("fps_counter_ctor: %d check(s) FAILED\n", g_failures);
    return 1;
}