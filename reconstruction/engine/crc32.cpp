#include "crc32.hpp"
#include <array>
namespace nova3 {
namespace {
constexpr auto make_table() {
    std::array<std::uint32_t, 256> table{};
    for (std::uint32_t i = 0; i != 256; ++i) {
        auto v = i;
        for (int bit = 0; bit != 8; ++bit)
            v = (v >> 1) ^ ((v & 1) ? 0xedb88320u : 0u);
        table[i] = v;
    }
    return table;
}
constexpr auto table = make_table();
}
std::uint32_t crc32(const std::uint8_t* data, std::size_t size,
                    std::uint32_t prior) noexcept {
    if (!data) return 0;
    auto crc = ~prior;
    for (std::size_t i = 0; i != size; ++i)
        crc = table[(crc ^ data[i]) & 0xff] ^ (crc >> 8);
    return ~crc;
}
std::uint32_t compute_crc(const std::uint8_t* data, std::size_t size) noexcept {
    return crc32(data, size, 0);
}
}
