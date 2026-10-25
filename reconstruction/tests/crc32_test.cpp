#include "crc32.hpp"
#include <array>
#include <cstdlib>
#include <iostream>
#include <string_view>
static void require(bool ok) { if (!ok) { std::cerr << "CRC assertion failed\n"; std::exit(1); } }
int main() {
    const auto* s = reinterpret_cast<const std::uint8_t*>("123456789");
    require(nova3::compute_crc(nullptr, 0) == 0);
    require(nova3::compute_crc(s, 0) == 0);
    require(nova3::compute_crc(s, 9) == 0xcbf43926u);
    require(nova3::crc32(nullptr, 0, 123) == 0);
    require(nova3::crc32(s, 0, 123) == 123);
    const auto a = nova3::crc32(s, 4);
    require(nova3::crc32(s + 4, 5, a) == 0xcbf43926u);
    std::array<std::uint8_t, 256> bytes{};
    for (std::size_t i = 0; i < bytes.size(); ++i) bytes[i] = static_cast<std::uint8_t>(i);
    require(nova3::compute_crc(bytes.data(), bytes.size()) == 0x29058c73u);
    std::cout << "CRC known vectors, null/empty semantics, incremental chaining passed\n";
}
