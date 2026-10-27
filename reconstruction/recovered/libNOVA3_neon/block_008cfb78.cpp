// Task #32: source reconstruction of the independently understood cluster member.
// Reference SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// ELF VA 0x008d890c, 24 bytes, ARM: glf::CrcChecker::CRC32(void const*, int, unsigned).
// The original forwards (prior, buffer, uint32(length)) to crc32, preserving LR.
// This portable entry uses the already source-owned CRC implementation instead.
// The issue's sizes "18" and "934" are hexadecimal: 24 and 2356 bytes.
// Local ELF symbol metadata confirms both extents; boundaries alone do not
// establish behavior. Offline reference checks are separate from runtime code.
// Machine-code status: NOT MATCHED; see the task evidence JSON for test scope.
//
// ELF VA 0x008cfb78, 2356 bytes: JNI GL2JNILib_init is NOT RECONSTRUCTED / UNVERIFIED.
// No JNI entry or success stub is provided: its ABI, dependencies and effects
// have not been established sufficiently to implement them faithfully.

#include "../../engine/crc32.hpp"

#include <cstdint>
#include <limits>

namespace nova3::recovered {

// This is a portable test entry, not an assertion of original C++/JNI ABI parity.
// Non-null input must describe a readable buffer of uint32(length) bytes.
// A negative length is therefore not validated as an empty/error input: the
// original forwards its 32-bit representation. Only null-buffer negative lengths
// are exercised by our tests; a null buffer returns zero without reading data.
std::uint32_t crc32_008d890c(const void* buffer, std::int32_t length,
                           std::uint32_t prior) noexcept {
    static_assert(std::numeric_limits<std::uint32_t>::digits == 32);
    return nova3::crc32(static_cast<const std::uint8_t*>(buffer),
                       static_cast<std::uint32_t>(length), prior);
}

} // namespace nova3::recovered
