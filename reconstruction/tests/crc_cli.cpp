#include "crc32.hpp"
#include <array>
#include <fstream>
#include <iostream>
#include <string>
int main(int argc, char** argv) {
    if (argc != 2 && argc != 3) { std::cerr << "usage: nova3_crc FILE [PRIOR]\n"; return 2; }
    std::uint32_t prior = 0;
    if (argc == 3) {
        try { const auto value=std::stoull(argv[2],nullptr,0); if (value>0xffffffffu) return 2;prior=static_cast<std::uint32_t>(value); }
        catch (...) { return 2; }
    }
    if (std::string(argv[1])=="--null") { std::cout << nova3::crc32(nullptr,0,prior) << '\n'; return 0; }
    std::ifstream in(argv[1], std::ios::binary);
    if (!in) return 2;
    std::array<std::uint8_t, 65536> buffer{};
    std::uint32_t crc = prior;
    while (in) {
        in.read(reinterpret_cast<char*>(buffer.data()), buffer.size());
        crc = nova3::crc32(buffer.data(), static_cast<std::size_t>(in.gcount()), crc);
    }
    if (!in.eof()) return 3;
    std::cout << std::hex << crc << '\n';
}
