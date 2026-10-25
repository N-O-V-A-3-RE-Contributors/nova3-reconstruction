#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace nova3 {
enum class BresError { ok, truncated, magic, table_bounds, slot_bounds, unsupported_alias };
// Contiguous-buffer byte transformation from res::File::Init()0x69c6f0,
// branch0x69ccf8. base is an explicit ARM32 virtual address, never executable.
// Bytes stay byte-addressed on 64-bit hosts; no cast to original C++ objects.
// Duplicate ordinary slots and wrapping u32 arithmetic follow the original.
// Aliases modifying native loop-control fields and tables inside the header
// are explicitly unsupported; the native routine reloads those fields.
// Added bounds checks and atomic output are safety policy beyond original API.
BresError relocate_bres32(const std::uint8_t* bytes, std::size_t size,
                         std::uint32_t base, std::vector<std::uint8_t>& output);
}
