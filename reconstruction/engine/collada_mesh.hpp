#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>
namespace nova3 {
enum class MeshError { ok, header, bounds, unsupported, limit, invalid };
struct MeshAttribute { std::uint32_t stride=0,offset=0,type=0,components=0; };
struct TriangleBuffer { std::vector<std::uint32_t> indices; };
struct ColladaMesh {
    std::string name;
    std::vector<MeshAttribute> attributes;
    std::vector<std::array<float,3>> positions;
    std::vector<TriangleBuffer> buffers;
    std::array<float,6> bounds{};
};
// Observed inline SGeometry tag0, interleaved float32 positions and serialized
// tail buffers. Native-backed offsets in research/assets/COLLADA.md/GL_ENUMS.md.
// Per-buffer relative indices are expanded by their native minimum-vertex base.
// Additional source safety validates ranges/indices and rejects other shapes.
// Native shared strings, animation, materials and runtime GPU buffers excluded.
MeshError read_collada_mesh(const std::uint8_t* bytes,std::size_t size,
    std::size_t geometry_index,ColladaMesh& output,std::size_t vertex_limit=1000000,
    std::size_t index_limit=3000000);
}
