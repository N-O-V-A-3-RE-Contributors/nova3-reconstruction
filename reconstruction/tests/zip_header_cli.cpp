#include "zip_local.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc,char** argv) {
    if (argc!=3) return 2;
    std::ifstream in(argv[1],std::ios::binary);if (!in) return 2;
    // Research header inspector reads a bounded prefix, not the full archive.
    std::vector<std::uint8_t> bytes(65536);in.read(reinterpret_cast<char*>(bytes.data()),bytes.size());bytes.resize(static_cast<std::size_t>(in.gcount()));
    std::uint64_t archive_size;
    try { archive_size=std::stoull(argv[2]); } catch (...) { return 2; }
    nova3::ZipLocalEntry entry;
    auto error=nova3::parse_zip_local(bytes.data(),bytes.size(),0,archive_size,entry);
    if (error!=nova3::ZipError::ok) { std::cerr << "header error " << static_cast<int>(error) << '\n';return 3; }
    const char* hex="0123456789abcdef";
    std::cout << "{\"name_hex\":\"";for(unsigned char c:entry.name)std::cout<<hex[c>>4]<<hex[c&15];
    std::cout << "\",\"flags\":" << entry.flags << ",\"method\":" << entry.method << ",\"crc32\":" << entry.crc
              << ",\"compressed_size\":" << entry.compressed_size << ",\"size\":" << entry.size << ",\"data_offset\":" << entry.data_offset << ",\"next_offset\":" << entry.next_offset << "}\n";
}
