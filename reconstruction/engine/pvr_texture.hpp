#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace nova3 {
// Header acceptance predicate from readPVRHeader ARM0x781c78. File I/O and
// logging are outside this pure predicate; inputs are 52 little-endian bytes.
// Header acceptance alone does not validate dimensions, format or payload.
bool valid_pvr_v2_header(const std::uint8_t* header,std::size_t available) noexcept;
enum class PvrError { ok, header, unsupported, bounds };
struct TextureMip { std::uint32_t width=0,height=0;std::size_t offset=0,size=0; };
struct Etc1Texture { std::uint32_t width=0,height=0;std::vector<TextureMip> mips; };
// Observed .etc data uses PVRv2 format 0x36, one 2D surface, 4bpp. Added checked
// subset: power-of-two dimensions in [1,16384], only the format and mip flag,
// and an exact payload (no trailing bytes). Without the mip flag, levels must
// be zero; with it, levels must be nonzero and counts additional mips through
// 1x1, not the base.
// Each mip occupies ceil(width/4)*ceil(height/4)*8 bytes, including tiny mips.
// Offsets refer to bytes, whose lifetime remains the caller's responsibility.
// Output is unchanged on errors; allocation failures can throw.
// This exposes compressed bytes for upload; it does not decode or render pixels.
PvrError read_etc1_pvr(const std::uint8_t* bytes,std::size_t size,Etc1Texture& output);
}
