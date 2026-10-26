#include "scramble.hpp"
#include <array>
#include <cstdlib>
#include <iostream>
#include <vector>

// Synthetic source-only tests, not original-binary or machine-code matching.
// Keep checks active even when compiled directly with -DNDEBUG.
#define REQUIRE(condition) do { if (!(condition)) { \
    std::cerr << "scramble_test:" << __LINE__ << ": " #condition "\n"; \
    std::exit(EXIT_FAILURE); } } while (false)

namespace {
void known_vectors() {
    const std::array<std::uint8_t, 5> input{0, 1, 2, 3, 255};
    auto data = input;
    REQUIRE(nova3::unscramble(data.data(), data.size(), "ab"));
    REQUIRE((data == std::array<std::uint8_t, 5>{97, 99, 99, 97, 158}));
    REQUIRE(nova3::unscramble(data.data(), data.size(), "ab"));
    REQUIRE(data == input);

    // High bits are bytes on either signed-char or unsigned-char hosts. The
    // embedded NUL terminates the key, even though more bytes follow it.
    const char key[]{static_cast<char>(0x80), static_cast<char>(0xff), 0, 'x', 0};
    std::array<std::uint8_t, 6> high{0, 0x7f, 0x80, 0xff, 0x55, 0xaa};
    REQUIRE(nova3::unscramble(high.data(), high.size(), key));
    REQUIRE((high == std::array<std::uint8_t, 6>{0x80, 0x80, 0, 0, 0xd5, 0x55}));

    // Each invocation restarts at key[0], including calls on a subrange.
    std::array<std::uint8_t, 5> split{};
    REQUIRE(nova3::unscramble(split.data(), 3, "ab"));
    REQUIRE(nova3::unscramble(split.data() + 3, 2, "ab"));
    REQUIRE((split == std::array<std::uint8_t, 5>{'a', 'b', 'a', 'a', 'b'}));
}

void invalid_and_empty_inputs() {
    const std::array<std::uint8_t, 5> input{0, 1, 2, 3, 255};
    auto data = input;
    for (const char* key : {static_cast<const char*>(nullptr), ""}) {
        REQUIRE(!nova3::unscramble(data.data(), data.size(), key));
        REQUIRE(data == input);
        REQUIRE(!nova3::unscramble(nullptr, 1, key));
    }
    REQUIRE(!nova3::unscramble(nullptr, 1, "a"));
    REQUIRE(!nova3::unscramble(nullptr, 0, nullptr));
    REQUIRE(!nova3::unscramble(data.data(), 0, nullptr));
    for (const char* key : {"", "a", "longer-than-the-data"}) {
        REQUIRE(nova3::unscramble(nullptr, 0, key));
        REQUIRE(nova3::unscramble(data.data(), 0, key));
        REQUIRE(data == input);
    }
}

void byte_domain() {
    // All 256 data bytes against all 255 possible non-NUL single-byte keys.
    for (unsigned k = 1; k < 256; ++k) {
        const char key[]{static_cast<char>(k), 0};
        std::array<std::uint8_t, 258> data{};
        data.front() = 0x37;
        data.back() = 0xc9;
        for (unsigned b = 0; b < 256; ++b) data[b + 1] = static_cast<std::uint8_t>(b);
        const auto original = data;
        REQUIRE(nova3::unscramble(data.data() + 1, 256, key));
        REQUIRE(data.front() == original.front() && data.back() == original.back());
        for (unsigned b = 0; b < 256; ++b) REQUIRE(data[b + 1] == (b ^ k));
        REQUIRE(nova3::unscramble(data.data() + 1, 256, key));
        REQUIRE(data == original);
        REQUIRE(static_cast<unsigned char>(key[0]) == k && key[1] == 0);
    }
}

void periodic_keys_and_lengths() {
    constexpr std::array<std::size_t, 12> lengths{0, 1, 2, 3, 7, 8, 15, 16, 255, 256, 1087, 4097};
    for (const std::size_t key_size : {1u, 2u, 3u, 7u, 31u, 256u, 4098u}) {
        std::vector<char> key(key_size + 1, 0);
        for (std::size_t i = 0; i < key_size; ++i) key[i] = static_cast<char>(1 + (i * 73) % 255);
        const auto saved_key = key;
        for (const auto length : lengths) {
            std::vector<std::uint8_t> data(length + 2, 0xa5);
            for (std::size_t i = 0; i < length; ++i) data[i + 1] = static_cast<std::uint8_t>(i * 37 + 11);
            const auto original = data;
            REQUIRE(nova3::unscramble(data.data() + 1, length, key.data()));
            // Independent indexed expression, rather than an XOR round trip
            // alone (which would also accept a no-op or a wrong period).
            for (std::size_t i = 0; i < length; ++i) {
                const auto byte = static_cast<std::uint8_t>(saved_key[i % key_size]);
                REQUIRE(data[i + 1] == (original[i + 1] ^ byte));
            }
            REQUIRE(data.front() == original.front() && data.back() == original.back());
            REQUIRE(key == saved_key);
            REQUIRE(nova3::unscramble(data.data() + 1, length, key.data()));
            REQUIRE(data == original);
        }
    }
}
}

int main() {
    known_vectors();
    invalid_and_empty_inputs();
    byte_domain();
    periodic_keys_and_lengths();
    std::cout << "synthetic XOR vectors, full byte domain, periodicity, bounds and invalid inputs passed\n";
}
