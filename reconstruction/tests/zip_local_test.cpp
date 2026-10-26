#include "zip_local.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

// Synthetic fixtures only: these test the portable contract, not binary parity.
static void require(bool ok, const char* expression, int line) {
    if (!ok) {
        std::cerr << "ZIP assertion failed at line " << line << ": " << expression << '\n';
        std::exit(1);
    }
}
#define REQUIRE(condition) require((condition), #condition, __LINE__)
using nova3::ZipError;
using nova3::ZipLocalEntry;

static void put16(std::vector<std::uint8_t>& b, std::size_t at, std::uint16_t value) {
    b[at] = static_cast<std::uint8_t>(value);
    b[at + 1] = static_cast<std::uint8_t>(value >> 8);
}
static void put32(std::vector<std::uint8_t>& b, std::size_t at, std::uint32_t value) {
    put16(b, at, static_cast<std::uint16_t>(value));
    put16(b, at + 2, static_cast<std::uint16_t>(value >> 16));
}
static std::vector<std::uint8_t> header(const std::string& name = "a.bin",
                                        std::uint16_t extra = 0) {
    REQUIRE(name.size() <= 65535);
    std::vector<std::uint8_t> b(30 + name.size() + extra, 0);
    put32(b, 0, 0x04034b50u);
    put16(b, 26, static_cast<std::uint16_t>(name.size()));
    put16(b, 28, extra);
    std::copy(name.begin(), name.end(), b.begin() + 30);
    return b;
}
static bool equal(const ZipLocalEntry& a, const ZipLocalEntry& b) {
    return a.name == b.name && a.flags == b.flags && a.method == b.method &&
           a.crc == b.crc && a.compressed_size == b.compressed_size && a.size == b.size &&
           a.data_offset == b.data_offset && a.next_offset == b.next_offset;
}
static ZipLocalEntry sentinel() {
    return {"unchanged", 0x1234, 0x5678, 0xabcdef12u, 17, 19, 23, 29};
}
static void expect_error(const std::vector<std::uint8_t>& b, ZipError expected,
                         std::uint64_t offset = 0, std::uint64_t archive_size = 1000000) {
    auto output = sentinel();
    REQUIRE(nova3::parse_zip_local(b.data(), b.size(), offset, archive_size, output) == expected);
    REQUIRE(equal(output, sentinel()));
}

static void fields_and_offsets() {
    auto b = header("Dir/a.bin", 7);
    put16(b, 6, 0x0800);
    put16(b, 8, 8);
    put32(b, 14, 0xfedcba98u);
    put32(b, 18, 0x01020304u);
    put32(b, 22, 0x89abcdefu);
    // Extra bytes are opaque, even when they contain a header signature.
    put32(b, b.size() - 7, 0x04034b50u);
    const std::uint64_t offset = 913;
    const auto end = offset + b.size() + 0x01020304u;
    // Deliberately unaligned header pointer; offset is not a pointer adjustment.
    std::vector<std::uint8_t> storage(3 + b.size(), 0xff);
    std::copy(b.begin(), b.end(), storage.begin() + 3);
    expect_error(storage, ZipError::signature, offset, end);
    auto entry = sentinel();
    REQUIRE(nova3::parse_zip_local(storage.data() + 3, b.size(), offset, end, entry) == ZipError::ok);
    REQUIRE(entry.name == "Dir/a.bin" && entry.flags == 0x0800 && entry.method == 8);
    REQUIRE(entry.crc == 0xfedcba98u && entry.compressed_size == 0x01020304u);
    REQUIRE(entry.size == 0x89abcdefu && entry.data_offset == offset + b.size());
    REQUIRE(entry.next_offset == end);
    expect_error(b, ZipError::bounds, offset, end - 1);
    REQUIRE(nova3::parse_zip_local(b.data(), b.size(), offset, end + 1, entry) == ZipError::ok);

    // The parser reads exactly the selected header, rather than scanning for PK.
    const auto first = header("first");
    const auto second = header("second");
    auto joined = first;
    joined.insert(joined.end(), second.begin(), second.end());
    REQUIRE(nova3::parse_zip_local(joined.data(), joined.size(), 0, joined.size(), entry) == ZipError::ok);
    REQUIRE(entry.name == "first" && entry.next_offset == first.size());
    REQUIRE(nova3::parse_zip_local(joined.data() + first.size(), second.size(),
                                 first.size(), joined.size(), entry) == ZipError::ok);
    REQUIRE(entry.name == "second" && entry.next_offset == joined.size());
}

static void truncation_and_lengths() {
    const auto b = header(std::string(255, 'a'), 257);
    for (std::size_t n = 0; n < b.size(); ++n) {
        // Exact-sized allocations let ASan catch reads beyond available.
        const std::vector<std::uint8_t> prefix(b.begin(), b.begin() + n);
        expect_error(prefix, ZipError::truncated);
    }
    auto output = sentinel();
    for (const auto available : {std::size_t(0), std::size_t(30),
                                  std::numeric_limits<std::size_t>::max()}) {
        REQUIRE(nova3::parse_zip_local(nullptr, available, 0, 0, output) == ZipError::truncated);
        REQUIRE(equal(output, sentinel()));
    }
    const auto maximum = header(std::string(65535, 'a'), 65535);
    REQUIRE(maximum.size() == 131100);
    REQUIRE(nova3::parse_zip_local(maximum.data(), maximum.size(), 0,
                                 maximum.size(), output) == ZipError::ok);
    REQUIRE(output.name.size() == 65535 && output.data_offset == 131100);
    REQUIRE(output.next_offset == 131100);
    expect_error(std::vector<std::uint8_t>(maximum.begin(), maximum.end() - 1), ZipError::truncated);
    expect_error(maximum, ZipError::bounds, 0, maximum.size() - 1);
    auto invalid = header();
    put16(invalid, 26, 0xffff);
    expect_error(invalid, ZipError::truncated);
    invalid = header();
    put16(invalid, 28, 0xffff);
    expect_error(invalid, ZipError::truncated);
}

static void unsupported_and_names() {
    auto b = header();
    for (std::size_t i = 0; i < 4; ++i) {
        b[i] ^= 0x80;
        expect_error(b, ZipError::signature);
        b[i] ^= 0x80;
    }
    for (unsigned bit = 0; bit < 16; ++bit) {
        if (bit == 11) continue;
        put16(b, 6, static_cast<std::uint16_t>(1u << bit));
        expect_error(b, ZipError::unsupported);
        put16(b, 6, static_cast<std::uint16_t>((1u << bit) | 0x0800));
        expect_error(b, ZipError::unsupported);
    }
    put16(b, 6, 0);
    for (const auto method : {1, 7, 9, 12, 99, 0xffff}) {
        put16(b, 8, static_cast<std::uint16_t>(method));
        expect_error(b, ZipError::unsupported);
    }
    put16(b, 8, 8);
    put32(b, 18, 0xffffffffu);
    expect_error(b, ZipError::unsupported);
    put32(b, 18, 0);
    put32(b, 22, 0xffffffffu);
    expect_error(b, ZipError::unsupported);

    const std::vector<std::string> unsafe = {
        "", "/abs", "\\abs", "a\\b", "C:a", "a:b", "..", "../a", "a/..",
        "a/../b", "a//../b", std::string("a\0b", 3), std::string("\0", 1)
    };
    for (const auto& name : unsafe) expect_error(header(name), ZipError::unsafe_name);
    for (const auto& name : {".", "./a", "a/./b", "a//b", "a/", "...", "a..", "..a"}) {
        const auto valid = header(name);
        ZipLocalEntry output;
        REQUIRE(nova3::parse_zip_local(valid.data(), valid.size(), 0, valid.size(), output) == ZipError::ok);
        REQUIRE(output.name == name);
    }
}

static void size_boundaries() {
    auto b = header();
    const auto h = static_cast<std::uint64_t>(b.size());
    const auto max = std::numeric_limits<std::uint64_t>::max();
    ZipLocalEntry entry;
    REQUIRE(nova3::parse_zip_local(b.data(), b.size(), max - h, max, entry) == ZipError::ok);
    REQUIRE(entry.data_offset == max && entry.next_offset == max);
    // A successful empty entry must also replace every stale caller field.
    entry = sentinel();
    REQUIRE(nova3::parse_zip_local(b.data(), b.size(), 0, h, entry) == ZipError::ok);
    const ZipLocalEntry empty = {"a.bin", 0, 0, 0, 0, 0, h, h};
    REQUIRE(equal(entry, empty));
    expect_error(b, ZipError::bounds, max - h + 1, max);
    expect_error(b, ZipError::bounds, max, max);
    expect_error(b, ZipError::bounds, max, 0);
    expect_error(b, ZipError::bounds, h + 1, h);
    expect_error(b, ZipError::bounds, 0, h - 1);
    for (const auto method : {0, 8}) {
        put16(b, 8, static_cast<std::uint16_t>(method));
        for (const auto length : {1u, 0x80000000u, 0xfffffffeu}) {
            put32(b, 18, length);
            put32(b, 22, length);
            const auto offset = max - h - length;
            REQUIRE(nova3::parse_zip_local(b.data(), b.size(), offset, max, entry) == ZipError::ok);
            REQUIRE(entry.data_offset == max - length && entry.next_offset == max);
            REQUIRE(entry.compressed_size == length && entry.size == length);
            expect_error(b, ZipError::bounds, offset + 1, max);
            expect_error(b, ZipError::bounds, offset, max - 1);
        }
    }
    put16(b, 8, 0);
    put32(b, 18, 1);
    put32(b, 22, 0);
    expect_error(b, ZipError::bounds);
    put32(b, 18, 0);
    put32(b, 22, 1);
    expect_error(b, ZipError::bounds);
}

static void malformed_corpus() {
    std::uint32_t random = 0x4e4f5641u;
    auto byte = [&random]() {
        random = random * 1664525u + 1013904223u;
        return static_cast<std::uint8_t>(random >> 24);
    };
    std::size_t successes = 0, failures = 0;
    for (unsigned iteration = 0; iteration < 20000; ++iteration) {
        std::vector<std::uint8_t> input(96);
        for (auto& value : input) value = byte();
        if (iteration % 2) put32(input, 0, 0x04034b50u);
        if (iteration % 4 == 3) {
            // Structured mutations reach name and bounds checks, unlike purely
            // random flags/methods/lengths which almost always fail much earlier.
            input = header(iteration % 8 == 3 ? "safe/name" : "../bad", byte() % 8);
            put16(input, 8, 8);
            put32(input, 18, byte());
            put32(input, 22, byte());
        }
        input.resize(byte() % (input.size() + 1));
        const auto offset = std::uint64_t(byte());
        const auto archive_size = std::uint64_t(byte()) + byte();
        auto output = sentinel();
        const auto error = nova3::parse_zip_local(input.data(), input.size(), offset, archive_size, output);
        if (error == ZipError::ok) {
            ++successes;
            REQUIRE(offset <= output.data_offset && output.data_offset <= output.next_offset);
            REQUIRE(output.next_offset <= archive_size);
            REQUIRE(output.next_offset - output.data_offset == output.compressed_size);
        } else {
            ++failures;
            REQUIRE(equal(output, sentinel()));
        }
    }
    REQUIRE(successes != 0 && failures != 0);
}

int main() {
    fields_and_offsets();
    truncation_and_lengths();
    unsupported_and_names();
    size_boundaries();
    malformed_corpus();
    std::cout << "PASS: ZIP local fields, truncation, names, 64-bit bounds and malformed corpus\n";
}
