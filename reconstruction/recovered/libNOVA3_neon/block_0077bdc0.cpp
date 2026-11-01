// glitch::video::CVertexStreams::getStream(E_VERTEX_ATTRIBUTE,
//   SVertexStream*, SVertexStream*) — Premium ELF VA 0x0077bdc0
// (68 B, A32).
//
// Non-const overload, byte-identical body to the const version at
// 0x0077bd7c.
#include "vertex_stream_getstream.h"

extern "C" nova3::recovered::vertex_streams::SVertexStream*
nova3_recovered_get_stream(
    void* container,
    std::uint32_t key,
    nova3::recovered::vertex_streams::SVertexStream* begin,
    nova3::recovered::vertex_streams::SVertexStream* end)
{
    return nova3::recovered::vertex_streams::getStream(
        container, key, begin, end);
}