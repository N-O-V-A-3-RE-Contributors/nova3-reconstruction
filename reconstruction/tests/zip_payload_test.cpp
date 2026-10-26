#include "zip_payload.hpp"
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

using Bytes = std::vector<std::uint8_t>;
using Error = nova3::ZipPayloadError;
static void require(bool ok, const char* message) {
    if (!ok) { std::cerr << "payload: " << message << '\n'; std::exit(1); }
}
static void rejects(const nova3::ZipLocalEntry& entry, const Bytes& compressed,
                    std::size_t limit, Error expected, const char* message) {
    Bytes output{0xa5, 0x5a};
    require(nova3::decode_zip_payload(entry, compressed.data(), compressed.size(), limit, output)==expected, message);
    require(output==Bytes({0xa5, 0x5a}), "failed decode changed output");
}
static void test_dynamic_huffman() {
    // Reproduce with Python's zlib: c=zlib.compressobj(9,zlib.DEFLATED,-15);
    // c.compress(b'NOVA3 synthetic ZIP payload.\n'*1024)+c.flush().
    // This synthetic BTYPE=2 stream exercises dynamic trees and LZ77 copies.
    const Bytes compressed{
        0xed, 0xc9, 0x31, 0x0e, 0x40, 0x30, 0x00, 0x00, 0xc0, 0xdd, 0x2b, 0xfa, 0x02, 0x8b, 0x17, 0x18,
        0x2d, 0x65, 0x32, 0xd8, 0x1a, 0x24, 0x95, 0x08, 0x12, 0x5d, 0xfa, 0x7b, 0x0f, 0x71, 0xb7, 0x5e,
        0x1c, 0xe7, 0xbe, 0x0b, 0x6f, 0xbd, 0x4a, 0xde, 0xcb, 0xb1, 0x86, 0x65, 0x98, 0xc2, 0x93, 0xea,
        0x79, 0xa7, 0xad, 0x6d, 0xa2, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94,
        0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52,
        0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a,
        0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29,
        0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0x52, 0x4a, 0x29, 0xa5, 0x94, 0xff, 0xce, 0x0f
    };
    std::string expected;
    for(unsigned i=0;i<1024;++i) expected+="NOVA3 synthetic ZIP payload.\n";
    nova3::ZipLocalEntry entry;
    entry.method=8; entry.size=expected.size(); entry.compressed_size=compressed.size(); entry.crc=0x850a5587u;
    Bytes output;
    require(nova3::decode_zip_payload(entry, compressed.data(), compressed.size(), expected.size(), output)==Error::ok &&
            std::string(output.begin(), output.end())==expected, "dynamic Huffman and LZ77 payload");
    for(std::size_t length=0;length<compressed.size();++length) {
        entry.compressed_size=static_cast<std::uint32_t>(length);
        rejects(entry, Bytes(compressed.begin(), compressed.begin()+length), expected.size(), Error::inflate,
                "truncated dynamic Huffman stream");
    }
}
int main() {
    // Synthetic RFC1951 fixed-Huffman stream for A; CRC-32/ISO-HDLC is known.
    const Bytes raw{0x73, 0x04, 0x00};
    nova3::ZipLocalEntry entry;
    entry.method=8; entry.size=1; entry.compressed_size=3; entry.crc=0xd3d99e8bu;
    Bytes output{42};
    require(nova3::decode_zip_payload(entry, raw.data(), raw.size(), 1, output)==Error::ok && output==Bytes{'A'},
            "raw DEFLATE at exact output limit");
    rejects(entry, Bytes{0x73, 0x04}, 1, Error::size, "compressed argument size mismatch");
    rejects(entry, raw, 0, Error::limit, "output limit");
    require(nova3::decode_zip_payload(entry, nullptr, raw.size(), 1, output)==Error::size && output==Bytes{'A'},
            "nonempty null input");
    auto bad=entry; ++bad.crc;
    rejects(bad, raw, 1, Error::checksum, "DEFLATE checksum mismatch");
    bad=entry; bad.size=0;
    rejects(bad, raw, 1, Error::inflate, "stream exceeds declared zero output");
    bad=entry; bad.size=2;
    rejects(bad, raw, 2, Error::inflate, "stream shorter than declared output");
    for (std::size_t length=0; length<raw.size(); ++length) {
        bad=entry; bad.compressed_size=static_cast<std::uint32_t>(length);
        rejects(bad, Bytes(raw.begin(), raw.begin()+length), 1, Error::inflate, "truncated DEFLATE stream");
    }
    const Bytes reserved_block{0x07}; // BFINAL=1, reserved BTYPE=3.
    bad=entry; bad.compressed_size=reserved_block.size();
    rejects(bad, reserved_block, 1, Error::inflate, "reserved DEFLATE block type");
    auto trailing=raw; trailing.push_back(0);
    bad=entry; bad.compressed_size=trailing.size();
    rejects(bad, trailing, 1, Error::inflate, "trailing compressed byte");
    auto concatenated=raw; concatenated.insert(concatenated.end(), raw.begin(), raw.end());
    bad=entry; bad.compressed_size=concatenated.size();
    rejects(bad, concatenated, 1, Error::inflate, "concatenated raw streams");
    // A complete zlib-wrapped stream for A must not be accepted as raw DEFLATE.
    const Bytes wrapped{0x78, 0x9c, 0x73, 0x04, 0x00, 0x00, 0x42, 0x00, 0x42};
    bad=entry; bad.compressed_size=wrapped.size();
    rejects(bad, wrapped, 1, Error::inflate, "zlib wrapper");
    // RFC1951 non-compressed block: LEN=3, NLEN=~3, followed by ABC.
    const Bytes block{0x01, 0x03, 0x00, 0xfc, 0xff, 'A', 'B', 'C'};
    bad=entry; bad.compressed_size=block.size(); bad.size=3; bad.crc=0xa3830348u;
    require(nova3::decode_zip_payload(bad, block.data(), block.size(), 3, output)==Error::ok && output==Bytes({'A','B','C'}),
            "non-compressed DEFLATE block");
    auto corrupt_block=block; corrupt_block[3]^=1;
    rejects(bad, corrupt_block, 3, Error::inflate, "invalid DEFLATE LEN/NLEN");
    bad.size=1;
    rejects(bad, block, 1, Error::inflate, "stream exceeds allocated output guard byte");

    entry.method=0; entry.size=1; entry.compressed_size=1;
    const Bytes stored{'A'};
    require(nova3::decode_zip_payload(entry, stored.data(), stored.size(), 1, output)==Error::ok && output==stored,
            "stored payload");
    bad=entry; bad.size=2;
    rejects(bad, stored, 2, Error::size, "stored size mismatch");
    bad=entry; ++bad.crc;
    rejects(bad, stored, 1, Error::checksum, "stored checksum mismatch");
    for (unsigned bit=0; bit<16; ++bit) {
        bad=entry; bad.flags=static_cast<std::uint16_t>(1u<<bit);
        if (bit==11) {
            require(nova3::decode_zip_payload(bad, stored.data(), stored.size(), 1, output)==Error::ok && output==stored,
                    "UTF-8 flag allowed");
        } else rejects(bad, stored, 1, Error::unsupported, "unsupported flag");
    }
    bad=entry; bad.method=99;
    rejects(bad, stored, 1, Error::unsupported, "unsupported compression method");
    bad=entry; bad.size=0xffffffffu;
    rejects(bad, stored, std::numeric_limits<std::size_t>::max(), Error::limit, "ZIP64 output sentinel");
    bad=entry; bad.compressed_size=0xffffffffu;
    // The count is rejected before dereferencing the one-byte input.
    require(nova3::decode_zip_payload(bad, stored.data(), bad.compressed_size,
            std::numeric_limits<std::size_t>::max(), output)==Error::limit && output==stored,
            "ZIP64 compressed sentinel");
    // Input may refer to output's own allocation; commit only after decoding.
    output=raw; entry.method=8; entry.compressed_size=raw.size();
    require(nova3::decode_zip_payload(entry, output.data(), output.size(), 1, output)==Error::ok && output==stored,
            "aliased DEFLATE input/output");
    entry.method=0; entry.compressed_size=1;
    require(nova3::decode_zip_payload(entry, output.data(), output.size(), 1, output)==Error::ok && output==stored,
            "aliased stored input/output");

    entry.size=0; entry.compressed_size=0; entry.crc=0;
    require(nova3::decode_zip_payload(entry, nullptr, 0, 0, output)==Error::ok && output.empty(), "empty stored payload");
    const Bytes empty{0x03, 0x00};
    entry.method=8; entry.compressed_size=empty.size();
    require(nova3::decode_zip_payload(entry, empty.data(), empty.size(), 0, output)==Error::ok && output.empty(),
            "empty DEFLATE payload");
    entry.crc=1;
    rejects(entry, empty, 0, Error::checksum, "empty checksum mismatch");
    test_dynamic_huffman();
    std::cout << "synthetic stored/raw-DEFLATE validation and transactional output passed\n";
}
