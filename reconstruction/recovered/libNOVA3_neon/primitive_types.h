// Source-owned recovery of the glitch::video primitive-counting helpers.
//
// Reference: Premium libNOVA3_neon.so  (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Three public free functions (verified against linked ELF disassembly):
//   getPrimitiveCount(E_PRIMITIVE_TYPE, unsigned indexCount)    0x0077bf84 (152 B)
//   getTriangleCount(E_PRIMITIVE_TYPE, unsigned indexCount)     0x0077c1e0 (112 B)
//   getIndexCount   (E_PRIMITIVE_TYPE, unsigned primitiveCount) 0x0077c250 (124 B)
//
// All three dispatch on a 9-entry switch (enum values 0..8) built with a
// `cmp..#8 / addls pc,pc,rN,lsl#2` jump table; values >8 fall through to a
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

// Enum values and their *inferred* names.  Values 4..8 are well-established
// by the strip/fan/list arithmetic and triangle counts; 0 is confirmed
// "Points" by the surviving string at rodata VA 0xcaf4c0.  Values 2 and 3
// carry the least signal and are marked accordingly below.
//
//   value | getPrimitiveCount | getIndexCount  | getTriangleCount | name
//   ------+-------------------+----------------+------------------+----------------
//     0   | N                 | P              | 0                | Points
//     1   | max(N-1,0)        | P+1            | 0                | Line Strip
//     2   | N                 | P              | 0                | Line Loop (INFERRED)
//     3   | N/3               | 0 (quirk)      | 0 (quirk)        | legacy Triangles (INFERRED)
//     4   | max(N-2,0)        | P+2            | max(N-2,0)       | Triangle Strip
//     5   | max(N-2,0)        | P+2            | max(N-2,0)       | Triangle Fan
//     6   | N/3               | 3P             | N/3             | Triangle List
//     7   | max((N-2)/2,0)    | 2P+2           | 2*max((N-2)/2,0) | Quad Strip
//     8   | N/4               | 4P             | 2*floor(N/4)    | Quad List
//   else  | 0                 | 0              | 0                | (default)
//
// NOTE: E_PRIMITIVE_TYPE value 3 is genuinely inconsistent in the original
// binary: getPrimitiveCount divides by 3, yet getIndexCount and
// getTriangleCount both return 0 for it.  This is reproduced verbatim and
// documented as an observed quirk rather than silently normalized.

// The E_PRIMITIVE_TYPE discriminant.  Exact numeric mapping verified from
// the jump tables; the symbolic names are INFERRED.
enum E_PRIMITIVE_TYPE : std::uint32_t {
    EPT_POINTS            = 0,
    EPT_LINE_STRIP        = 1,
    EPT_LINE_LOOP         = 2,  // INFERRED (identity count, no string found)
    EPT_LEGACY_TRIANGLES  = 3,  // INFERRED (div-by-3, but idx/tri return 0)
    EPT_TRIANGLE_STRIP    = 4,
    EPT_TRIANGLE_FAN      = 5,
    EPT_TRIANGLE_LIST     = 6,
    EPT_QUAD_STRIP        = 7,
    EPT_QUAD_LIST         = 8,
};

// Number of primitives produced by an index buffer of the given length.
inline std::uint32_t getPrimitiveCount(E_PRIMITIVE_TYPE type,
                                       std::uint32_t indexCount) {
    switch (type) {
        case EPT_POINTS:           return indexCount;
        case EPT_LINE_STRIP:       return (indexCount >= 1) ? indexCount - 1 : 0;
        case EPT_LINE_LOOP:        return indexCount;
        case EPT_LEGACY_TRIANGLES: return indexCount / 3;
        case EPT_TRIANGLE_STRIP:   return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_FAN:     return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_LIST:    return indexCount / 3;
        case EPT_QUAD_STRIP:       return (indexCount >= 2) ? (indexCount - 2) / 2 : 0;
        case EPT_QUAD_LIST:        return indexCount / 4;
        default:                   return 0;
    }
}

// Number of triangles produced by an index buffer of the given length.
// Only triangle/quad primitives (values 4..8) yield a non-zero count.
inline std::uint32_t getTriangleCount(E_PRIMITIVE_TYPE type,
                                      std::uint32_t indexCount) {
    switch (type) {
        case EPT_TRIANGLE_STRIP: return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_FAN:   return (indexCount >= 2) ? indexCount - 2 : 0;
        case EPT_TRIANGLE_LIST:  return indexCount / 3;
        case EPT_QUAD_STRIP:     return (indexCount >= 2) ? ((indexCount - 2) / 2) * 2 : 0;
        case EPT_QUAD_LIST:      return (indexCount / 4) * 2;
        default:                 return 0;
    }
}

// Number of index entries required to hold the given primitive count.
// Reproduces the original's odd EPT_LEGACY_TRIANGLES -> 0 quirk.
inline std::uint32_t getIndexCount(E_PRIMITIVE_TYPE type,
                                   std::uint32_t primitiveCount) {
    switch (type) {
        case EPT_POINTS:           return primitiveCount;
        case EPT_LINE_STRIP:       return primitiveCount + 1;
        case EPT_LINE_LOOP:        return primitiveCount;
        case EPT_LEGACY_TRIANGLES: return 0;  // observed quirk, not normalized
        case EPT_TRIANGLE_STRIP:   return primitiveCount + 2;
        case EPT_TRIANGLE_FAN:     return primitiveCount + 2;
        case EPT_TRIANGLE_LIST:    return primitiveCount * 3;
        case EPT_QUAD_STRIP:       return (primitiveCount + 1) * 2;
        case EPT_QUAD_LIST:        return primitiveCount * 4;
        default:                   return 0;
    }
}

}  // namespace primitive_types
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_PRIMITIVE_TYPES_H