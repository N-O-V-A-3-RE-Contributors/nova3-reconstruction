#pragma once
#include "zip_local.hpp"
#include <vector>
namespace nova3 {
enum class ZipPayloadError { ok, size, limit, unsupported, inflate, checksum };
// Observed CZipReader::openFile(int)0x819878 selects method8 and raw inflate
// windowBits=-15. This checked source reader additionally validates exact byte
// counts and our recovered CRC; it does not reproduce engine allocation classes.
ZipPayloadError decode_zip_payload(const ZipLocalEntry& entry,
    const std::uint8_t* compressed, std::size_t compressed_size,
    std::size_t output_limit, std::vector<std::uint8_t>& output);
}
