#include "zip_payload.hpp"
#include "crc32.hpp"
#include <zlib.h>
namespace nova3 {
ZipPayloadError decode_zip_payload(const ZipLocalEntry& entry,
    const std::uint8_t* compressed,std::size_t compressed_size,
    std::size_t output_limit,std::vector<std::uint8_t>& output) {
    if (compressed_size!=entry.compressed_size || (!compressed && compressed_size)) return ZipPayloadError::size;
    if (entry.size>output_limit || entry.size==0xffffffffu || compressed_size>=0xffffffffu) return ZipPayloadError::limit;
    if (entry.flags & ~0x0800u) return ZipPayloadError::unsupported;
    std::vector<std::uint8_t> decoded;
    if (entry.method==0) {
        if (entry.size!=compressed_size) return ZipPayloadError::size;
        if (compressed_size) decoded.assign(compressed,compressed+compressed_size);
    } else if (entry.method==8) {
        // One extra output byte detects streams larger than the stated size,
        // including the zero-size case, without allocating an unbounded buffer.
        decoded.resize(static_cast<std::size_t>(entry.size)+1);
        z_stream stream{};
        stream.next_in=const_cast<Bytef*>(compressed);
        stream.avail_in=static_cast<uInt>(compressed_size);
        stream.next_out=decoded.data();
        stream.avail_out=static_cast<uInt>(decoded.size());
        if (inflateInit2(&stream,-15)!=Z_OK) return ZipPayloadError::inflate;
        const auto result=inflate(&stream,Z_FINISH);
        const bool valid=result==Z_STREAM_END && stream.total_in==compressed_size && stream.total_out==entry.size;
        inflateEnd(&stream);
        if (!valid) return ZipPayloadError::inflate;
        decoded.resize(entry.size);
    } else return ZipPayloadError::unsupported;
    if (compute_crc(decoded.data(),decoded.size())!=entry.crc) return ZipPayloadError::checksum;
    output=std::move(decoded);
    return ZipPayloadError::ok;
}
}
