// glitch::video::CVertexStreams::getActiveAttributeMask() const
// Premium ELF VA 0x0077bd28 (84 B, A32).
// Mangled: _ZNK6glitch5video14CVertexStreams22getActiveAttributeMaskEv
// See vertex_streams.h for the entry layout and equivalent source.
#include "vertex_streams.h"

extern "C" std::uint32_t nova3_recovered_get_active_attribute_mask(
    const void* container, std::uint32_t entry_count) {
    return nova3::recovered::vertex_streams::getActiveAttributeMask(
        container, entry_count);
}