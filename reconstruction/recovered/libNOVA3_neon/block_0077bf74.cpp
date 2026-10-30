// glitch::video::getStringsInternal(E_PRIMITIVE_TYPE*) — Premium ELF VA
// 0x0077bf74 (16 B, A32).  The original is a three-instruction helper:
//
//   ldr r0, [pc, #4]    @ literal pool word 0x005ba67c at 0x77bf80
//   add r0, pc, r0      @ r0 = 0x77bf80 + 0x005ba67c = 0x00d365fc
//   bx  lr
//
// It ignores its argument and returns the *base* of the enum's 9-entry
// `const char* const[]` display-name table (0xd365fc in .data.rel.ro, see
// primitive_types.h).  This source returns that same table of string
// literals.  Address verified 2026-10-30: 0x005ba67c addend resolves to the
// "Points" entry, and the relocation at 0xd365fc points to .rodata 0xcaf4c0.
#include "primitive_types.h"

extern "C" const char* const* nova3_recovered_get_strings_internal(
    nova3::recovered::primitive_types::E_PRIMITIVE_TYPE* /*unused*/) {
    static const char* const kNames[9] = {
        "Points",         // enum 0
        "Line Strip",     // enum 1
        "Line Loop",      // enum 2
        "Lines",          // enum 3
        "Triangle Strip", // enum 4
        "Triangle Fan",   // enum 5
        "Triangles",      // enum 6
        "Quad Strip",     // enum 7
        "Quads",          // enum 8
    };
    return kNames;
}