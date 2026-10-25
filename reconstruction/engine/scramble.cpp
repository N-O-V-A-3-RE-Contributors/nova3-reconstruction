#include "scramble.hpp"
#include <cstring>
namespace nova3 {
bool unscramble(std::uint8_t* data, std::size_t size, const char* key) noexcept {
    if (!key || (!data && size)) return false;
    const auto key_size = std::strlen(key);
    if (size && !key_size) return false;
    std::size_t cursor = 0;
    for (std::size_t i = 0; i < size; ++i) {
        data[i] ^= static_cast<std::uint8_t>(key[cursor]);
        if (++cursor == key_size) cursor = 0;
    }
    return true;
}
}
