// glitch::video::getTriangleCount — Premium ELF VA 0x0077c1e0 (112 B, A32).
// Mangled: _ZN6glitch5video16getTriangleCountENS0_16E_PRIMITIVE_TYPEEj
// See primitive_types.h for the recovered arithmetic and enum mapping.
#include "primitive_types.h"

using nova3::recovered::primitive_types::E_PRIMITIVE_TYPE;

extern "C" std::uint32_t nova3_recovered_get_triangle_count(
    E_PRIMITIVE_TYPE type, std::uint32_t indexCount) {
    return nova3::recovered::primitive_types::getTriangleCount(type, indexCount);
}