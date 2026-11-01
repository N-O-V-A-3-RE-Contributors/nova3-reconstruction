// glitch::video::getPrimitiveCount — Premium ELF VA 0x0077bf84 (152 B, A32).
// Mangled: _ZN6glitch5video17getPrimitiveCountENS0_16E_PRIMITIVE_TYPEEj
// See primitive_types.h for the recovered arithmetic and enum mapping.
#include "primitive_types.h"

using nova3::recovered::primitive_types::E_PRIMITIVE_TYPE;

extern "C" std::uint32_t nova3_recovered_get_primitive_count(
    E_PRIMITIVE_TYPE type, std::uint32_t indexCount) {
    return nova3::recovered::primitive_types::getPrimitiveCount(type, indexCount);
}