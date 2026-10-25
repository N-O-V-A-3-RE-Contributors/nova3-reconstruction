#pragma once
#include <cstddef>
#include <cstdint>
namespace nova3 {
// Premium CScrambledZipReader::unscramble, 0x00411610 (ARM).
// XOR with repeating NUL-terminated key. Same operation scrambles/unscrambles.
// Added checked API rejects null/empty keys or null nonempty data without writes.
bool unscramble(std::uint8_t* data, std::size_t size, const char* key) noexcept;
}
