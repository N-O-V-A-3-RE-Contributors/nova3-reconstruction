// glitch::video::CVertexStreams::addOffset(int)
// Premium ELF VA 0x0077be04 (64 B, A32).
// Mangled: _ZN6glitch5video14CVertexStreams9addOffsetEi
// See vertex_streams.h for the entry layout and equivalent source.
#include "vertex_streams.h"

extern "C" void nova3_recovered_add_offset(
    void* container, std::uint32_t entry_count, std::int32_t delta) {
    nova3::recovered::vertex_streams::addOffset(
        container, entry_count, delta);
}