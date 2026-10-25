#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace nova3 {
// Header acceptance predicate from readPVRHeader ARM0x781c78. File I/O and
// logging are outside this pure predicate; inputs are little-endian52 bytes.
bool valid_pvr_v2_header(const std::uint8_t* header,std::size_t available) noexcept;
enum class PvrError { ok, header, unsupported, bounds };
struct TextureMip { std::uint32_t width=0,height=0;std::size_t offset=0,size=0; };
struct Etc1Texture { std::uint32_t width=0,height=0;std::vector<TextureMip> mips; };
// Observed .etc data uses PVRv2 format0x36, one2Dsurface,4bpp. Added checked
// subset: power-of-two dimensions, known flags and exact complete mip payload.
// This exposes compressed bytes for upload; it does not decode or render pixels.
PvrError read_etc1_pvr(const std::uint8_t* bytes,std::size_t size,Etc1Texture& output);
}
