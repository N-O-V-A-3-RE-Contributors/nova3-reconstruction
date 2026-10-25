#pragma once
#include "zip_local.hpp"
namespace nova3 {
// CScrambledZipReader::scanLocalHeader0x411fc0: SB,u16 length, XORed ZIP
// local-header body without PK signature. Key is original archive basename.
// Checked subset: flags0/UTF8, methods0/8, no embedded descriptor records.
ZipError parse_scrambled_local(const std::uint8_t* bytes,std::size_t available,
                              std::uint64_t offset,std::uint64_t archive_size,
                              const char* key,ZipLocalEntry& output);
}
