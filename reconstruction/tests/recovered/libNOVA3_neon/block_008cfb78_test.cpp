// Standalone build and offline original-ARM oracle commands are recorded in
// coordination/evidence/functions-3bf9bf965c33d8a5985d2e.json.
#include <array>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string_view>
#include <vector>

namespace nova3::recovered {
std::uint32_t crc32_008d890c(const void*, std::int32_t, std::uint32_t) noexcept;
}

namespace {
using nova3::recovered::crc32_008d890c;

void require(bool condition, std::string_view message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

// Bit-at-a-time reference, independent of the production table implementation.
std::uint32_t bitwise_crc(const std::uint8_t* data, std::size_t length,
                          std::uint32_t prior) {
    if (!data) return 0;
    std::uint32_t value = ~prior;
    for (std::size_t i = 0; i < length; ++i) {
        value ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1u) ? 0xedb88320u : 0u);
    }
    return ~value;
}

// Emit only synthetic input and source results, never reference machine code.
// Protocol: prior signed_length offset is_null result hex_input (or '-').
void emit_case(std::uint32_t prior, std::int32_t length, unsigned offset,
               const std::uint8_t* data, std::uint32_t result) {
    std::cout << std::dec << prior << ' ' << length << ' ' << offset << ' '
              << (data == nullptr) << ' ' << result << ' ';
    if (!data || length == 0) {
        std::cout << '-';
    } else {
        for (std::int32_t i = 0; i < length; ++i)
            std::cout << std::hex << std::setfill('0') << std::setw(2)
                      << static_cast<unsigned>(data[i]);
    }
    std::cout << std::dec << '\n';
}

} // namespace

int main(int argc, char** argv) {
    const bool emit = argc == 2 && std::string_view(argv[1]) == "--emit-cases";
    if (argc != 1 && !emit) return 2;
    std::size_t checked = 0;
    const auto* digits = reinterpret_cast<const std::uint8_t*>("123456789");
    require(crc32_008d890c(digits, 9, 0) == 0xcbf43926u, "CRC-32 check vector");
    if (emit) emit_case(0, 9, 0, digits, crc32_008d890c(digits, 9, 0));
    ++checked;
    std::array<std::uint8_t, 256> ascending{};
    for (std::size_t i = 0; i < ascending.size(); ++i)
        ascending[i] = static_cast<std::uint8_t>(i);
    require(crc32_008d890c(ascending.data(), 256, 0) == 0x29058c73u,
            "all byte values check vector");
    if (emit) emit_case(0, 256, 0, ascending.data(),
                        crc32_008d890c(ascending.data(), 256, 0));
    ++checked;

    const std::array<std::uint32_t, 5> seeds{
        0u, 1u, 0xffffffffu, 0x12345678u, 0x80000000u};
    const std::array<std::int32_t, 25> lengths{
        0, 1, 2, 3, 4, 7, 8, 15, 16, 17, 31, 32, 33,
        63, 64, 65, 127, 128, 255, 256, 257, 1023, 1024, 4096, 65535};
    const std::array<std::int32_t, 5> null_lengths{
        0, 100, -1, std::numeric_limits<std::int32_t>::min(),
        std::numeric_limits<std::int32_t>::max()};
    for (const auto seed : seeds) {
        for (const auto length : null_lengths) {
            const auto result = crc32_008d890c(nullptr, length, seed);
            require(result == 0, "null input ignores seed and length");
            if (emit) emit_case(seed, length, 0, nullptr, result);
            ++checked;
        }
    }

    // All pointer alignments and small/chunk boundaries; exact requested extent
    // is allocated, so sanitizer builds also exercise the final readable byte.
    for (unsigned offset = 0; offset < 8; ++offset) {
        for (const auto length : lengths) {
            std::vector<std::uint8_t> storage(offset + static_cast<std::size_t>(length) +
                                               (length == 0 ? 1u : 0u));
            auto* data = storage.data() + offset;
            std::uint32_t random = 0x4e4f5641u;
            for (std::int32_t i = 0; i < length; ++i) {
                random ^= random << 13;
                random ^= random >> 17;
                random ^= random << 5;
                data[i] = static_cast<std::uint8_t>(random);
            }
            const auto original_storage = storage;
            for (const auto seed : seeds) {
                const auto result = crc32_008d890c(data, length, seed);
                require(result == bitwise_crc(data, static_cast<std::size_t>(length), seed),
                        "synthetic seeded CRC against bitwise reference");
                if (emit) emit_case(seed, length, offset, data, result);
                const auto split = length / 2;
                const auto partial = crc32_008d890c(data, split, seed);
                require(crc32_008d890c(data + split, length - split, partial) == result,
                        "incremental chaining");
                require(crc32_008d890c(data, 0, seed) == seed,
                        "non-null empty input preserves seed");
                ++checked;
            }
            require(storage == original_storage, "CRC leaves the input buffer unchanged");
        }
    }
    if (!emit)
        std::cout << "PASS: " << checked
                  << " cases including 2 check vectors, chaining, empty semantics and input immutability; "
                  << "JNI initializer not tested\n";
}
