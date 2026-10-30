// Source-owned recovery of glitch::video::CFPSCounter constructor.
//
// Reference: Premium libNOVA3_neon.so (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Constructor: _ZN6glitch5video11CFPSCounterC2Ev at 0x0077bb7c (56 B, ARM).
// Also: _ZN6glitch5video11CFPSCounterC1Ev at 0x0077bbb4 (56 B, byte-identical).
//
// The original zeroes all fields except offset 8, which is initialised to 60
// (the target FPS).  The 8-byte zero at offset 0 uses a 64-bit store (strd),
// and the remaining five 32-bit fields are individually zeroed.
//
// Struct layout inferred from the constructor's store offsets:
//
//   offset  size  value   instruction
//   ------  ----  -----   -----------
//     0      8     0      strd r4,[r0]        (r4=r5=0)
//     8      4    60      str r1,[r0,#8]      (r1=0x3c)
//    12      4     0      str r2,[r0,#12]     (r2=0)
//    16      4     0      str r2,[r0,#16]
//    20      4     0      str r2,[r0,#20]
//    24      4     0      str r2,[r0,#24]
//    28      4     0      str r2,[r0,#28]
//
// The class name and target FPS value are from the ELF symbol table and the
// literal 0x3c immediate; field semantics beyond the initialisation pattern
// are INFERRED (FPS counter with a time accumulator and sample windows).

#ifndef NOVA3_RECOVERED_FPS_COUNTER_H
#define NOVA3_RECOVERED_FPS_COUNTER_H

#include <cstdint>

namespace nova3 {
namespace recovered {
namespace fps_counter {

struct CFPSCounter {
    std::uint64_t time_accum;     // offset 0: 8 bytes, zeroed
    std::uint32_t target_fps;     // offset 8: initialised to 60
    std::uint32_t field_12;       // offset 12: zeroed
    std::uint32_t field_16;       // offset 16: zeroed
    std::uint32_t field_20;       // offset 20: zeroed
    std::uint32_t field_24;       // offset 24: zeroed
    std::uint32_t field_28;       // offset 28: zeroed

    // Value-initialises all fields; target_fps is set to 60.
    // Equivalent to the original base-object constructor at 0x0077bb7c
    // and the complete-object constructor at 0x0077bbb4 (both 56 B, ARM).
    CFPSCounter() noexcept
        : time_accum(0)
        , target_fps(60)
        , field_12(0)
        , field_16(0)
        , field_20(0)
        , field_24(0)
        , field_28(0) {}
};

// Size guard: the original constructor writes exactly 32 bytes of fields.
static_assert(sizeof(CFPSCounter) == 32, "CFPSCounter size must be 32 bytes");

}  // namespace fps_counter
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_FPS_COUNTER_H