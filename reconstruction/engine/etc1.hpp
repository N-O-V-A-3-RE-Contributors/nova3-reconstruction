#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace nova3 {
enum class Etc1Error { ok, bounds, limit, invalid_block };
// Decode row-major, big-endian ETC1 blocks into tightly packed row-major RGB8.
// Width and height must be nonzero. Crops partial edge blocks; flip_y reverses
// the cropped image's rows. A zero source_pitch means ceil(width/4)*8 bytes;
// otherwise it must be at least that large. Padding after the last block row is
// not required, and trailing bytes are ignored. data must address size bytes.
// Rejects differential endpoints outside [0,31], even in cropped-out pixels.
// pixel_limit bounds decoded pixels; unrepresentable output also returns limit.
// Output is unchanged on errors; allocation failures can throw. Input may alias
// output's storage (the input pointer is then invalidated on success).
Etc1Error decode_etc1(const std::uint8_t* data,std::size_t size,
                     std::uint32_t width,std::uint32_t height,
                     std::vector<std::uint8_t>& output,bool flip_y=false,
                     std::size_t source_pitch=0,std::size_t pixel_limit=16*1024*1024);
}
