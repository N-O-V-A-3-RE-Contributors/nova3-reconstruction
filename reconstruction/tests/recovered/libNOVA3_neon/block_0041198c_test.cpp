// Standalone test because this task may not edit CMakeLists.txt or add a header.
// Synthetic fixtures only; optional --probe mode supports a local ARM oracle.
#include "../../../recovered/libNOVA3_neon/block_0041198c.cpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>

#define REQUIRE(condition) do { if (!(condition)) { \
    std::cerr << "block_0041198c_test:" << __LINE__ << ": " #condition "\n"; \
    std::exit(EXIT_FAILURE); } } while (false)

namespace {
namespace recovered = nova3::recovered::block_0041198c;
using recovered::Entry;
using recovered::MemoryFile;
using recovered::OpenError;
using Bytes = std::vector<std::uint8_t>;

// Independently authored RFC 1951 final uncompressed block; no compressor or
// production parser is used to construct the test stream.
Bytes raw_deflate(const Bytes& data) {
    REQUIRE(data.size() <= 0xffff);
    const auto size = static_cast<std::uint16_t>(data.size());
    const auto inverse = static_cast<std::uint16_t>(~size);
    Bytes result{1, static_cast<std::uint8_t>(size), static_cast<std::uint8_t>(size >> 8),
                 static_cast<std::uint8_t>(inverse), static_cast<std::uint8_t>(inverse >> 8)};
    result.insert(result.end(), data.begin(), data.end());
    return result;
}

bool equal(const MemoryFile& a, const MemoryFile& b) {
    return a.name == b.name && a.bytes == b.bytes;
}

void rejection(const Bytes& archive, const std::vector<Entry>& entries, int index,
               std::size_t limit, OpenError expected) {
    const auto saved = archive;
    MemoryFile output{"sentinel", {9, 8, 7}};
    const auto before = output;
    REQUIRE(recovered::open_file({archive.data(), archive.size()}, entries, index,
                                limit, output) == expected);
    REQUIRE(equal(output, before));
    REQUIRE(archive == saved);
}

void successful_opening() {
    for (std::size_t length = 0; length <= 256; ++length) {
        Bytes plain(length);
        for (std::size_t i = 0; i < length; ++i)
            plain[i] = static_cast<std::uint8_t>(i * 137 + length);
        for (bool compressed : {false, true}) {
            auto payload = compressed ? raw_deflate(plain) : plain;
            Bytes archive(7 + payload.size() + 2, 0xcc);
            std::copy(payload.begin(), payload.end(), archive.begin() + 7);
            archive[archive.size() - 2] = 0xaa;
            archive.back() = 0xbb;
            const auto saved = archive;
            const auto packed = compressed ? 0x80000000u | static_cast<std::uint32_t>(payload.size())
                                           : 0x7fffffffu;
            const std::vector<Entry> entries{{0, 0, 0, "unused"},
                {7, static_cast<std::uint32_t>(length), packed, "dir/synthetic.bin"}};
            MemoryFile output{"old", {1}};
            REQUIRE(recovered::open_file({archive.data(), archive.size()}, entries,
                                        1, length, output) == OpenError::ok);
            REQUIRE(output.name == "dir/synthetic.bin" && output.bytes == plain);
            REQUIRE(archive == saved);
            // Returned memory owns its data independently of the archive view.
            std::fill(archive.begin(), archive.end(), 0);
            REQUIRE(output.bytes == plain);
        }
    }
    MemoryFile empty;
    REQUIRE(recovered::open_file({nullptr, 0}, {{0, 0, 0, "empty"}}, 0, 0, empty) == OpenError::ok);
    REQUIRE(empty.name == "empty" && empty.bytes.empty());

    // RFC 1951 fixed-Huffman stream for independently specified ASCII text.
    const Bytes fixed{0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x57, 0x28,
                      0xcf, 0x2f, 0xca, 0x49, 0x01, 0x00};
    MemoryFile decoded;
    REQUIRE(recovered::open_file({fixed.data(), fixed.size()},
        {{0, 11, 0x8000000du, "hello"}}, 0, 11, decoded) == OpenError::ok);
    REQUIRE(std::string(decoded.bytes.begin(), decoded.bytes.end()) == "hello world");
}

void failure_cases() {
    const Bytes bytes{1, 2, 3};
    rejection(bytes, {}, 0, 10, OpenError::index);
    rejection(bytes, {{0, 3, 0, "x"}}, -1, 10, OpenError::index);
    rejection(bytes, {{0, 3, 0, "x"}}, 1, 10, OpenError::index);
    rejection(bytes, {{0, 3, 0, "x"}}, std::numeric_limits<int>::max(), 10, OpenError::index);
    rejection(bytes, {{0, 3, 0, "x"}}, 0, 2, OpenError::limit);
    rejection(bytes, {{4, 0, 0, "x"}}, 0, 10, OpenError::bounds);
    rejection(bytes, {{3, 1, 0, "x"}}, 0, 10, OpenError::bounds);
    rejection(bytes, {{0xffffffffu, 1, 0, "x"}}, 0, 10, OpenError::bounds);
    rejection(bytes, {{0, 1, 0xffffffffu, "x"}}, 0, 10, OpenError::bounds);
    rejection(bytes, {{0, 3, 0x80000003u, "x"}}, 0, 10, OpenError::inflate);
    MemoryFile sentinel{"old", {7}};
    REQUIRE(recovered::open_file({nullptr, 3}, {{0, 0, 0, "x"}}, 0, 10, sentinel) == OpenError::bounds);
    REQUIRE(sentinel.name == "old" && sentinel.bytes == Bytes{7});

    const Bytes plain{10, 20, 30, 40, 50};
    auto compressed = raw_deflate(plain);
    Entry entry{0, 5, 0x80000000u | static_cast<std::uint32_t>(compressed.size()), "x"};
    // Every compressed-stream truncation, including a cut within the payload.
    for (std::size_t size = 0; size < compressed.size(); ++size) {
        const Bytes cut(compressed.begin(), compressed.begin() + size);
        auto truncated = entry;
        truncated.packed_size = 0x80000000u | static_cast<std::uint32_t>(size);
        rejection(cut, {truncated}, 0, 100, OpenError::inflate);
    }
    for (auto length : {0u, 1u, 4u, 6u, 100u}) {
        auto wrong = entry;
        wrong.output_size = length;
        rejection(compressed, {wrong}, 0, 100, OpenError::inflate);
    }
    compressed.push_back(0xff);
    ++entry.packed_size;
    rejection(compressed, {entry}, 0, 100, OpenError::inflate);
    // A zlib envelope is not a raw DEFLATE stream.
    rejection({0x78, 0x9c, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01},
              {{0, 0, 0x80000008u, "x"}}, 0, 0, OpenError::inflate);
}

Bytes from_hex(const std::string& text) {
    if (text.size() % 2) throw std::invalid_argument("odd hex length");
    Bytes result;
    for (std::size_t i = 0; i < text.size(); i += 2) {
        std::size_t consumed = 0;
        const auto value = std::stoul(text.substr(i, 2), &consumed, 16);
        if (consumed != 2) throw std::invalid_argument("invalid hex");
        result.push_back(static_cast<std::uint8_t>(value));
    }
    return result;
}

int probe(char** argv) {
    const auto packed = std::stoull(argv[2], nullptr, 0);
    const auto length = std::stoull(argv[3], nullptr, 0);
    if (packed > 0xffffffffu || length > 0xffffffffu) return 2;
    const auto archive = from_hex(argv[4]);
    MemoryFile output;
    const auto error = recovered::open_file({archive.data(), archive.size()},
        {{0, static_cast<std::uint32_t>(length), static_cast<std::uint32_t>(packed), "fixture.bin"}},
        0, 1024 * 1024, output);
    if (error != OpenError::ok) {
        std::cout << "error\n";
        return 0;
    }
    std::cout << "ok:";
    const char* hex = "0123456789abcdef";
    for (auto value : output.bytes) std::cout << hex[value >> 4] << hex[value & 15];
    std::cout << '\n';
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 5 && std::string(argv[1]) == "--probe") return probe(argv);
    if (argc != 1) return 2;
    successful_opening();
    failure_cases();
    std::cout << "PASS: 514 generated opens, fixed-Huffman fixture, ownership and rejection cases; NOT MATCHED\n";
}
