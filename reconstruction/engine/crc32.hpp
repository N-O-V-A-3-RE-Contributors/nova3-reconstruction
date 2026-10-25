#pragma once
#include <cstddef>
#include <cstdint>
namespace nova3 {
// Recovered from Premium ComputeCRC(unsigned char*, int), ELF VA 0x000de2cc.
// Empty input returns zero. Valid domain: nonnegative length, readable input.
std::uint32_t compute_crc(const std::uint8_t* data, std::size_t size) noexcept;
// Recovered glf::CrcChecker::CRC32(buffer, length, prior), VA 0x008d890c,
// which forwards to crc32(prior, buffer, length). Null buffer returns zero.
std::uint32_t crc32(const std::uint8_t* data, std::size_t size,
                    std::uint32_t prior = 0) noexcept;
}
