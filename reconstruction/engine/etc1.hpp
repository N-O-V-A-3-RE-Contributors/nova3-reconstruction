#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace nova3 {
enum class Etc1Error { ok, bounds, limit, invalid_block };
// Decode row-major ETC1 blocks into tightly packed RGB8. Source rows can include
// padding. Crops edge blocks for tiny mips. Output is unchanged on errors.
Etc1Error decode_etc1(const std::uint8_t* data,std::size_t size,
                     std::uint32_t width,std::uint32_t height,
                     std::vector<std::uint8_t>& output,bool flip_y=false,
                     std::size_t source_pitch=0,std::size_t pixel_limit=16*1024*1024);
}
