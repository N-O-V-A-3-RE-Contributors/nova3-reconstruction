#include "zip_payload.hpp"
#include <cstdlib>
#include <iostream>
static void require(bool ok) { if (!ok) { std::cerr << "payload assertion failed\n";std::exit(1); } }
int main() {
    nova3::ZipLocalEntry e;e.method=8;e.size=1;e.compressed_size=3;e.crc=0xd3d99e8bu;
    const std::uint8_t raw[]{0x73,0x04,0x00}; // RFC1951 fixed-Huffman stream for A.
    std::vector<std::uint8_t> out{42};
    require(nova3::decode_zip_payload(e,raw,3,1,out)==nova3::ZipPayloadError::ok);
    require(out==std::vector<std::uint8_t>{65});
    require(nova3::decode_zip_payload(e,raw,2,1,out)==nova3::ZipPayloadError::size);
    require(nova3::decode_zip_payload(e,raw,3,0,out)==nova3::ZipPayloadError::limit);
    ++e.crc;require(nova3::decode_zip_payload(e,raw,3,1,out)==nova3::ZipPayloadError::checksum);--e.crc;
    e.size=0;require(nova3::decode_zip_payload(e,raw,3,1,out)==nova3::ZipPayloadError::inflate);
    require(out==std::vector<std::uint8_t>{65});
    e.method=0;e.size=1;e.compressed_size=1;
    const std::uint8_t a=65;require(nova3::decode_zip_payload(e,&a,1,1,out)==nova3::ZipPayloadError::ok);
    e.method=8;e.size=0;e.compressed_size=2;e.crc=0;
    const std::uint8_t empty[]{3,0};require(nova3::decode_zip_payload(e,empty,2,0,out)==nova3::ZipPayloadError::ok && out.empty());
    std::cout << "stored/raw-DEFLATE,size/limit/checksum/zero-size semantics passed\n";
}
