// glitch::video::CVertexStreams::getStream(E_VERTEX_ATTRIBUTE,
//   const SVertexStream*, const SVertexStream*) const — Premium ELF VA
// 0x0077bd7c (68 B, A32).
//
// Sorted-array search with fallback to container's first embedded entry.
// Structurally identical to the non-const overload at 0x0077bdc0.
#include "vertex_stream_getstream.h"

extern "C" const nova3::recovered::vertex_streams::SVertexStream*
nova3_recovered_get_stream_const(
    const void* container,
    std::uint32_t key,
    const nova3::recovered::vertex_streams::SVertexStream* begin,
    const nova3::recovered::vertex_streams::SVertexStream* end)
{
    return nova3::recovered::vertex_streams::getStream(
        container, key, begin, end);
}