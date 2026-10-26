#pragma once
#include <cstddef>
#include <cstdint>
namespace nova3 {
// Premium CScrambledZipReader::unscramble, 0x00411610 (ARM).
// XOR with repeating NUL-terminated key. Same operation scrambles/unscrambles.
// key must point to a NUL-terminated string that does not overlap the data.
// Every call starts at key[0]; bytes after the first NUL are ignored.
// Checked API rejects null keys, empty keys with nonempty data, and null
// nonempty data without writes. Zero size accepts an empty (but not null) key.
bool unscramble(std::uint8_t* data, std::size_t size, const char* key) noexcept;
}
