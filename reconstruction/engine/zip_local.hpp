#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
namespace nova3 {
enum class ZipError { ok, truncated, signature, unsupported, unsafe_name, bounds };
struct ZipLocalEntry {
    std::string name;
    std::uint16_t flags = 0, method = 0;
    std::uint32_t crc = 0, compressed_size = 0, size = 0;
    std::uint64_t data_offset = 0, next_offset = 0;
};
// Header-reading portion of glitch::io::CZipReader::scanLocalHeader,
// Premium ARM0x007fd420. Only flags 0/UTF8 and stored/deflate local headers are supported.
// bytes need contain header/name/extra; archive_size bounds the referenced data.
// Unsupported descriptors/ZIP64 are explicit errors, not silently misparsed.
ZipError parse_zip_local(const std::uint8_t* bytes, std::size_t available,
                        std::uint64_t offset, std::uint64_t archive_size,
                        ZipLocalEntry& entry);
}
