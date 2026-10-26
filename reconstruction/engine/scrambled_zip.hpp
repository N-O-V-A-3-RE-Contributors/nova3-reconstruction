#pragma once
#include "zip_local.hpp"
namespace nova3 {
// CScrambledZipReader::scanLocalHeader0x411fc0: SB,u16 length, XORed ZIP
// local-header body without PK signature. Key is original archive basename.
// Checked subset: flags0/UTF8, methods0/8, no embedded descriptor records.
// The encoded body length must be 26..1087 and exactly cover name/extra fields.
// bytes need contain only the header; archive_size bounds the referenced payload.
// Input is not modified; output is replaced only on success. key follows the
// NUL-terminated string contract of unscramble. Payload/CRC and SDIR validation
// are outside this header parser; XOR does not authenticate the key or contents.
ZipError parse_scrambled_local(const std::uint8_t* bytes,std::size_t available,
                              std::uint64_t offset,std::uint64_t archive_size,
                              const char* key,ZipLocalEntry& output);
}
