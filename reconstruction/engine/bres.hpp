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
//
// Input words are little-endian, may be unaligned, and remain byte-addressed.
// Unrelocated +16 is the table count and +24 is a buffer-relative byte offset;
// each table word is a buffer-relative slot offset, not an index or address.
// Every slot must have four readable bytes, including the first slot. The first
// entry is converted to base+slot but its target is not incremented; later
// targets are incremented even when zero or repeated. Entries/targets may alias
// the table, so later entries are read from the already modified working copy.
// Stored address sums wrap modulo 2^32; they are never host pointers. Target
// values need not denote bytes inside this buffer; stored values are never
// dereferenced as pointers.
// With byte +7 bit 7 already set, only length/magic are checked; bytes are copied
// unchanged regardless of base. This is not a full resource structure validator.
//
// bytes must designate size readable bytes for the duration of the call (or may
// be null, which returns truncated). It may refer into output; errors leave
// output unchanged. Allocation exceptions propagate without modifying output.
// The synthetic tests cover this contract; target binary matching is NOT MATCHED
// without a separate original-reference comparison.
BresError relocate_bres32(const std::uint8_t* bytes, std::size_t size,
                         std::uint32_t base, std::vector<std::uint8_t>& output);
}
