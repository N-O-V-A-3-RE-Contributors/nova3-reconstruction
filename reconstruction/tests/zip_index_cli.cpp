#include "zip_local.hpp"
#include <array>
#include <iostream>
#include <vector>
// Stream local entries. Compressed asset bytes are skipped, never executed.
int main(int argc,char** argv) {
    if (argc!=2) return 2;
    std::uint64_t total;
    try { total=std::stoull(argv[1]); } catch (...) { return 2; }
    std::uint64_t offset=0;
    std::size_t count=0;
    std::array<char,65536> scratch{};
    const char* hex="0123456789abcdef";
    std::cout << "[";
    while (offset<total) {
        std::vector<std::uint8_t> bytes(30);
        std::cin.read(reinterpret_cast<char*>(bytes.data()),30);
        if (std::cin.gcount()!=30) return 3;
        // Central-directory start ends this LOCAL-HEADER inventory. Central
        // CRC, offset and filename consistency require separate verification.
        if (bytes[0]=='P' && bytes[1]=='K' && bytes[2]==1 && bytes[3]==2) {
            std::cout << "]\n"; return 0;
        }
        const auto suffix=std::size_t(bytes[26])+(std::size_t(bytes[27])<<8)+bytes[28]+(std::size_t(bytes[29])<<8);
        bytes.resize(30+suffix);
        std::cin.read(reinterpret_cast<char*>(bytes.data()+30),static_cast<std::streamsize>(suffix));
        if (static_cast<std::size_t>(std::cin.gcount())!=suffix) return 3;
        nova3::ZipLocalEntry entry;
        if (nova3::parse_zip_local(bytes.data(),bytes.size(),offset,total,entry)!=nova3::ZipError::ok) return 4;
        if (count++) std::cout << ',';
        std::cout << "{\"name_hex\":\"";for(unsigned char c:entry.name)std::cout<<hex[c>>4]<<hex[c&15];
        std::cout << "\",\"offset\":" << offset << ",\"data_offset\":" << entry.data_offset << ",\"compressed_size\":" << entry.compressed_size << ",\"size\":" << entry.size << ",\"crc32\":" << entry.crc << ",\"method\":" << entry.method << ",\"flags\":" << entry.flags << '}';
        std::uint64_t left=entry.compressed_size;
        while (left) {
            const auto chunk=static_cast<std::streamsize>(left>scratch.size()?scratch.size():left);
            std::cin.read(scratch.data(),chunk);
            if (std::cin.gcount()!=chunk) return 3;
            left-=static_cast<std::uint64_t>(chunk);
        }
        offset=entry.next_offset;
    }
    // A ZIP lacking central directory is incomplete even when locals fit.
    return 5;
}
