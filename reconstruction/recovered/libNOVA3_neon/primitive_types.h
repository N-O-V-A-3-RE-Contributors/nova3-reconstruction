// Source-owned recovery of the glitch::video primitive counting and naming.
//
// Reference: Premium libNOVA3_neon.so  (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Three public free functions (verified against linked ELF disassembly):
//   getPrimitiveCount(E_PRIMITIVE_TYPE, unsigned indexCount)    0x0077bf84 (152 B)
//   getTriangleCount(E_PRIMITIVE_TYPE, unsigned indexCount)     0x0077c1e0 (112 B)
//   getIndexCount   (E_PRIMITIVE_TYPE, unsigned primitiveCount) 0x0077c250 (124 B)
//
// Enum value -> name mapping (VERIFIED, not inferred): the original
// `glitch::video::getStringsInternal(E_PRIMITIVE_TYPE*)` helper at 0x0077bf74
// loads the base of a 9-entry `const char* const[]` table whose address is
// 0x00d365fc in .data.rel.ro.  Each entry is an R_ARM_RELATIVE-relocated
// pointer to a NUL-terminated .rodata string:
//
//   entry VA   -> target VA -> string       (enum value 0..8)
//   ------------------------------------------------------------
//   0xd365fc   -> 0xcaf4c0   -> "Points"          (0)
//   0xd36600   -> 0xc7d488   -> "Line Strip"      (1)
//   0xd36604   -> 0xc7d494   -> "Line Loop"       (2)
//   0xd36608   -> 0xc7d4a0   -> "Lines"           (3)
//   0xd3660c   -> 0xc7d4a8   -> "Triangle Strip"  (4)
//   0xd36610   -> 0xc7d4b8   -> "Triangle Fan"    (5)
//   0xd36614   -> 0xc7d4c8   -> "Triangles"       (6)
//   0xd36618   -> 0xc7d4d4   -> "Quad Strip"      (7)
//   0xd3661c   -> 0xc7d4e0   -> "Quads"           (8)
//
// All three arithmetic helpers dispatch on that same 9-entry enum via a
// `cmp..#8 / addls pc,pc,rN,lsl#2` jump table; values > 8 fall through to a
// shared `return 0`.  The recovered arithmetic below reproduces the exact
// observable behaviour; the original compiles the unsigned /3 and /4 cases
// as magic-constant multiplies (0xaaaaaaab >> 33 for /3) which are
// numerically identical to integer floor division for unsigned operands.

#ifndef NOVA3_RECOVERED_PRIMITIVE_TYPES_H
#define NOVA3_RECOVERED_PRIMITIVE_TYPES_H

#include <cstdint>

namespace nova3 {
namespace recovered {
namespace primitive_types {

// E_PRIMITIVE_TYPE: numeric mapping verified from the jump tables, and the
// symbolic names verified from the string table above (0xd365fc..0xd3661c).
//
//   value | getPrimitiveCount | getIndexCount | getTriangleCount | name
//   ------+-------------------+---------------+------------------+------------
//     0   | N                 | P             | 0                | Points
//     1   | max(N-1,0)        | P+1           | 0                | Line Strip
//     2   | N                 | P             | 0                | Line Loop
//     3   | N/2               | 0 (quirk)     | 0                | Lines
//     4   | max(N-2,0)        | P+2           | max(N-2,0)       | Triangle Strip
//     5   | max(N-2,0)        | P+2           | max(N-2,0)       | Triangle Fan
//     6   | N/3               | 3P            | N/3              | Triangles
//     7   | max((N-2)/2,0)    | 2P+2          | 2*max((N-2)/2,0) | Quad Strip
//     8   | N/4               | 4P            | 2*floor(N/4)     | Quads
//   else  | 0                 | 0             | 0                | (default)
//
// NOTE: value 3 ("Lines") is genuinely asymmetric in the original binary:
// getPrimitiveCount divides the vertex count by 2 (one line per two
// vertices), yet getIndexCount and getTriangleCount both return 0 for it.
// This is reproduced verbatim and documented as an observed quirk rather
// than silently normalized.
//
// CORRECTION (pass 10): an earlier pass labeled value 3 "legacy Triangles"
// and gave it N/3 arithmetic.  Re-reading the jump table shows case 3
// branches to `lsr r1,r1,#1` (N/2) *not* the `umull` divide-by-3 used by
// case 6 ("Triangles"); the string table confirms value 3 is "Lines".

enum E_PRIMITIVE_TYPE : std::uint32_t {
    EPT_POINTS         = 0,  // "Points"
    EPT_LINE_STRIP     = 1,  // "Line Strip"
    EPT_LINE_LOOP      = 2,  // "Line Loop"
    EPT_LINES          = 3,  // "Lines"
    EPT_TRIANGLE_STRIP = 4,  // "Triangle Strip"
    EPT_TRIANGLE_FAN   = 5,  // "Triangle Fan"
    EPT_TRIANGLES      = 6,  // "Triangles"
    EPT_QUAD_STRIP     = 7,  // "Quad Strip"
    EPT_QUADS          = 8,  // "Quads"
};

// Number of primitives produced by an index buffer of the given length.
inline std::uint32_t getPrimitiveCount(E_PRIMITIVE_TYPE type,
                                       std::uint32_t indexCount) {
    switch (type) {
        case EPT_POINTS:         return indexCount;
        case EPT_LINE_STRIP:     return (indexCount >= 1) ? indexCount - 1 : 0;
        case EPT_LINE_LOOP:      return indexCount;
        case EPT_LINES:          return indexCount / 2;   // original: lsr #1
        case EPT_TRIANGLE_STRIP: return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_FAN:   return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLES:      return indexCount / 3;   // original: umull /3
        case EPT_QUAD_STRIP:     return (indexCount >= 2) ? (indexCount - 2) / 2 : 0;
        case EPT_QUADS:          return indexCount / 4;   // original: lsr #2
        default:                 return 0;
    }
}

// Number of triangles produced by an index buffer of the given length.
// Only triangle/quad primitives (values 4..8) yield a non-zero count.
inline std::uint32_t getTriangleCount(E_PRIMITIVE_TYPE type,
                                      std::uint32_t indexCount) {
    switch (type) {
        case EPT_TRIANGLE_STRIP: return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_FAN:   return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLES:      return indexCount / 3;
        case EPT_QUAD_STRIP:     return (indexCount >= 2) ? ((indexCount - 2) / 2) * 2 : 0;
        case EPT_QUADS:          return (indexCount / 4) * 2;
        default:                 return 0;
    }
}

// Number of index entries required to hold the given primitive count.
// Reproduces the original's odd EPT_LINES ("Lines") -> 0 quirk.
inline std::uint32_t getIndexCount(E_PRIMITIVE_TYPE type,
                                   std::uint32_t primitiveCount) {
    switch (type) {
        case EPT_POINTS:         return primitiveCount;
        case EPT_LINE_STRIP:     return primitiveCount + 1;
        case EPT_LINE_LOOP:      return primitiveCount;
        case EPT_LINES:          return 0;  // observed quirk, not normalized
        case EPT_TRIANGLE_STRIP: return primitiveCount + 2;
        case EPT_TRIANGLE_FAN:   return primitiveCount + 2;
        case EPT_TRIANGLES:      return primitiveCount * 3;
        case EPT_QUAD_STRIP:     return (primitiveCount + 1) * 2;
        case EPT_QUADS:          return primitiveCount * 4;
        default:                 return 0;
    }
}

// The original's display name for each enum value, transcribed from the
// .data.rel.ro string table at 0xd365fc (see header note above).  Returns
// null for out-of-range discriminants, matching the >8 fall-through of the
// arithmetic helpers (the original string accessors carry no range guard of
// their own; callers bound the discriminant to 0..8).
inline const char* getPrimitiveTypeName(E_PRIMITIVE_TYPE type) {
    switch (type) {
        case EPT_POINTS:         return "Points";
        case EPT_LINE_STRIP:     return "Line Strip";
        case EPT_LINE_LOOP:      return "Line Loop";
        case EPT_LINES:          return "Lines";
        case EPT_TRIANGLE_STRIP: return "Triangle Strip";
        case EPT_TRIANGLE_FAN:   return "Triangle Fan";
        case EPT_TRIANGLES:      return "Triangles";
        case EPT_QUAD_STRIP:     return "Quad Strip";
        case EPT_QUADS:          return "Quads";
        default:                 return nullptr;
    }
}

}  // namespace primitive_types
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_PRIMITIVE_TYPES_H