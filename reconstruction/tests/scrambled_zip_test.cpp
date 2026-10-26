#include "scrambled_zip.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

// Synthetic source-only fixtures: no original data, filesystem dependency, or
// production unscramble call. These establish checked behavior, NOT MATCHED
// native behavior. Checks also run when compiled directly with -DNDEBUG.
#define REQUIRE(condition) do { if (!(condition)) { \
    std::cerr << "scrambled_zip_test:" << __LINE__ << ": " #condition "\n"; \
    std::exit(EXIT_FAILURE); } } while (false)

namespace {
using Bytes = std::vector<std::uint8_t>;
using nova3::ZipError;
using nova3::ZipLocalEntry;
constexpr char key[] = "test.gla";
constexpr auto max_offset = std::numeric_limits<std::uint64_t>::max();

struct Fields {
    std::string name = "x";
    Bytes extra;
    std::uint16_t flags = 0, method = 0;
    std::uint32_t crc = 0x89abcdefu, compressed = 4, size = 4;
};

void put16(Bytes& bytes, std::size_t at, std::uint16_t value) {
    bytes[at] = static_cast<std::uint8_t>(value);
    bytes[at + 1] = static_cast<std::uint8_t>(value >> 8);
}
void put32(Bytes& bytes, std::size_t at, std::uint32_t value) {
    put16(bytes, at, static_cast<std::uint16_t>(value));
    put16(bytes, at + 2, static_cast<std::uint16_t>(value >> 16));
}

Bytes plain_header(const Fields& fields) {
    Bytes bytes(30 + fields.name.size() + fields.extra.size(), 0);
    bytes[0] = 'P'; bytes[1] = 'K'; bytes[2] = 3; bytes[3] = 4;
    put16(bytes, 4, 20);
    put16(bytes, 6, fields.flags);
    put16(bytes, 8, fields.method);
    put32(bytes, 14, fields.crc);
    put32(bytes, 18, fields.compressed);
    put32(bytes, 22, fields.size);
    put16(bytes, 26, static_cast<std::uint16_t>(fields.name.size()));
    put16(bytes, 28, static_cast<std::uint16_t>(fields.extra.size()));
    std::copy(fields.name.begin(), fields.name.end(), bytes.begin() + 30);
    std::copy(fields.extra.begin(), fields.extra.end(), bytes.begin() + 30 + fields.name.size());
    return bytes;
}

// Independent indexed encoder, checked against a literal vector below.
Bytes encode(const Bytes& plain, const std::string& password = key) {
    REQUIRE(plain.size() >= 4 && plain.size() - 4 <= 0xffff && !password.empty());
    Bytes bytes(plain.size());
    bytes[0] = 'S'; bytes[1] = 'B';
    put16(bytes, 2, static_cast<std::uint16_t>(plain.size() - 4));
    for (std::size_t i = 4; i < bytes.size(); ++i) {
        bytes[i] = plain[i] ^ static_cast<std::uint8_t>(password[(i - 4) % password.size()]);
    }
    return bytes;
}

bool equal(const ZipLocalEntry& a, const ZipLocalEntry& b) {
    return a.name == b.name && a.flags == b.flags && a.method == b.method &&
           a.crc == b.crc && a.compressed_size == b.compressed_size && a.size == b.size &&
           a.data_offset == b.data_offset && a.next_offset == b.next_offset;
}
ZipLocalEntry sentinel() {
    return {"unchanged", 0x1234, 0x5678, 0x98765432u, 0x76543210u, 0xabcdef01u,
            0x123456789abcdef0ull, 0xfedcba9876543210ull};
}

// Check ALL output fields on errors and input immutability on every call.
void reject(const Bytes& bytes, ZipError expected, std::uint64_t offset = 0,
            std::uint64_t archive_size = 100000, const char* password = key) {
    auto output = sentinel();
    const auto saved = bytes;
    REQUIRE(nova3::parse_scrambled_local(bytes.data(), bytes.size(), offset,
                                        archive_size, password, output) == expected);
    REQUIRE(equal(output, sentinel()));
    REQUIRE(bytes == saved);
}

void accept(const Fields& fields, std::uint64_t offset = 0,
            const std::string& password = key) {
    const auto bytes = encode(plain_header(fields), password);
    const auto saved = bytes;
    auto output = sentinel();
    const auto end = offset + bytes.size() + fields.compressed;
    REQUIRE(nova3::parse_scrambled_local(bytes.data(), bytes.size(), offset, end,
                                        password.c_str(), output) == ZipError::ok);
    const ZipLocalEntry expected{fields.name, fields.flags, fields.method, fields.crc,
                                fields.compressed, fields.size, offset + bytes.size(), end};
    REQUIRE(equal(output, expected));
    REQUIRE(bytes == saved);
}

void literal_vector() {
    // Authored fixture: deflate, UTF8, CRC=89abcdef, compressed=3, size=9,
    // name="dir/a.bin", extra=34 12 02 00 aa 55. Only its header is supplied.
    const Bytes bytes{
        0x53, 0x42, 0x29, 0x00, 0x60, 0x65, 0x73, 0x7c, 0x26, 0x67, 0x6c, 0x61,
        0x74, 0x65, 0x9c, 0xb9, 0x85, 0xee, 0x6f, 0x61, 0x74, 0x65, 0x7a, 0x74,
        0x2e, 0x67, 0x65, 0x61, 0x72, 0x65, 0x17, 0x1d, 0x5c, 0x48, 0x0d, 0x4f,
        0x16, 0x0c, 0x1d, 0x40, 0x3c, 0x65, 0x6c, 0xcb, 0x21};
    Fields fields;
    fields.name = "dir/a.bin"; fields.flags = 0x800; fields.method = 8;
    fields.compressed = 3; fields.size = 9; fields.extra = {0x34, 0x12, 2, 0, 0xaa, 0x55};
    REQUIRE(encode(plain_header(fields)) == bytes);
    accept(fields, 0x100000003ull);

    // Exact allocations expose truncation overreads to ASan, instead of passing
    // a shortened logical length while keeping the entire header allocated.
    for (std::size_t n = 0; n < bytes.size(); ++n) {
        reject(Bytes(bytes.begin(), bytes.begin() + n), ZipError::truncated);
    }
    auto output = sentinel();
    REQUIRE(nova3::parse_scrambled_local(nullptr, bytes.size(), 0, 1000, key, output) == ZipError::truncated);
    REQUIRE(equal(output, sentinel()));
    reject(bytes, ZipError::unsupported, 0, 1000, nullptr);
    reject(bytes, ZipError::unsupported, 0, 1000, "");
    reject(bytes, ZipError::unsupported, 0, 1000, "wrong.gla");
    accept(fields, 7, std::string{static_cast<char>(0x80), static_cast<char>(0xff), 'k'});

    const char terminated[]{'t','e','s','t','.','g','l','a',0,'z',0};
    REQUIRE(nova3::parse_scrambled_local(bytes.data(), bytes.size(), 0, 48,
                                        terminated, output) == ZipError::ok);
    REQUIRE(output.name == fields.name && output.data_offset == 45 && output.next_offset == 48);
}

void lengths_and_prefixes() {
    const auto valid = encode(plain_header(Fields{}));
    for (unsigned i = 0; i < 3; ++i) {
        auto bad = valid;
        bad[0] = i & 1 ? 'S' : 'P'; bad[1] = i & 2 ? 'B' : 'K';
        reject(bad, ZipError::signature);
    }
    // Exhaust the entire u16 body-length domain, including the 1087 cap.
    for (unsigned length = 0; length <= 0xffff; ++length) {
        auto bad = valid;
        put16(bad, 2, static_cast<std::uint16_t>(length));
        if (length < 26 || length > 1087) reject(bad, ZipError::unsupported);
        else if (length > 27) reject(bad, ZipError::truncated);
        else if (length == 26) reject(bad, ZipError::unsupported); // fields require 27
        else accept(Fields{});
    }
    Fields fields;
    fields.name.clear();
    reject(encode(plain_header(fields)), ZipError::unsafe_name); // body length 26
    fields.name = std::string(1061, 'a'); // body length 1087; high byte nonzero
    accept(fields);
    fields.name.push_back('a');
    reject(encode(plain_header(fields)), ZipError::unsupported);
    fields.name = "x";
    fields.extra = Bytes(1060, 0xa5);
    accept(fields);
    fields.extra.push_back(0);
    reject(encode(plain_header(fields)), ZipError::unsupported);

    // Name/extra lengths may neither exceed nor underfill the body.
    auto plain = plain_header(Fields{});
    for (const auto length : {0u, 2u, 255u, 256u, 0xffffu}) {
        put16(plain, 26, static_cast<std::uint16_t>(length));
        reject(encode(plain), ZipError::unsupported);
    }
    plain = plain_header(Fields{});
    for (const auto length : {1u, 256u, 0xffffu}) {
        put16(plain, 28, static_cast<std::uint16_t>(length));
        reject(encode(plain), ZipError::unsupported);
    }
}

void validation_and_bounds() {
    Fields fields;
    accept(fields);
    fields.compressed = fields.size = 0;
    accept(fields);
    fields = Fields{}; fields.flags = 0x800;
    accept(fields);
    for (unsigned bit = 0; bit < 16; ++bit) {
        if (bit == 11) continue;
        fields.flags = static_cast<std::uint16_t>(1u << bit);
        reject(encode(plain_header(fields)), ZipError::unsupported);
    }
    fields = Fields{};
    for (const auto method : {1u, 7u, 9u, 99u, 0xffffu}) {
        fields.method = static_cast<std::uint16_t>(method);
        reject(encode(plain_header(fields)), ZipError::unsupported);
    }
    fields = Fields{}; fields.method = 8; fields.size = 12345;
    accept(fields); // deflate need not have equal compressed/uncompressed sizes
    fields.method = 0;
    reject(encode(plain_header(fields)), ZipError::bounds);
    fields = Fields{}; fields.size = 0xffffffffu;
    reject(encode(plain_header(fields)), ZipError::unsupported);
    fields.size = 4; fields.compressed = 0xffffffffu;
    reject(encode(plain_header(fields)), ZipError::unsupported);

    for (const auto& name : {std::string{}, std::string("/abs"), std::string("\\abs"),
                            std::string("a\\b"), std::string("C:foo"), std::string("../x"),
                            std::string("a/../x"), std::string("a/.."), std::string("a\0b", 3)}) {
        fields = Fields{}; fields.name = name;
        reject(encode(plain_header(fields)), ZipError::unsafe_name);
    }
    fields = Fields{}; fields.name = "a/.../b"; accept(fields);
    fields.name = "dir/\xc3\xa9.bin"; fields.flags = 0x800; accept(fields);

    const auto bytes = encode(plain_header(Fields{})); // header 31, payload 4
    for (std::uint64_t archive_size = 0; archive_size < 35; ++archive_size) {
        reject(bytes, ZipError::bounds, 0, archive_size);
    }
    reject(bytes, ZipError::bounds, 36, 35);
    reject(bytes, ZipError::bounds, max_offset, max_offset);
    reject(bytes, ZipError::bounds, max_offset - 30, max_offset); // header wraps
    reject(bytes, ZipError::bounds, max_offset - 34, max_offset); // payload wraps
    accept(Fields{}, max_offset - 35); // exact fit at UINT64_MAX
    accept(Fields{}, 0x100000003ull);

    // Unsigned sizes without allocation or payload bytes in the header buffer.
    fields = Fields{}; fields.compressed = fields.size = 0xfffffffeu;
    accept(fields, 0x100000003ull);

    // Trailing payload bytes are not XORed or consumed as part of the header.
    auto trailing = bytes;
    trailing.insert(trailing.end(), {0x53, 0x42, 0xff, 0xff, 0, 0xff});
    auto output = sentinel();
    const auto saved = trailing;
    REQUIRE(nova3::parse_scrambled_local(trailing.data(), trailing.size(), 0, 35,
                                        key, output) == ZipError::ok);
    REQUIRE(output.data_offset == 31 && output.next_offset == 35 && trailing == saved);
}

std::uint32_t random_word(std::uint32_t& state) {
    state = state * 1664525u + 1013904223u;
    return state;
}
void deterministic_corpus() {
    std::uint32_t state = 0x4e4f5641u;
    unsigned accepted = 0, rejected = 0;
    for (unsigned iteration = 0; iteration < 4000; ++iteration) {
        Fields fields;
        fields.name = "dir/" + std::string(1 + random_word(state) % 32, 'a' + iteration % 26);
        fields.extra.resize(random_word(state) % 32);
        for (auto& byte : fields.extra) byte = static_cast<std::uint8_t>(random_word(state) >> 24);
        fields.crc = random_word(state);
        fields.method = iteration % 3 ? 8 : 0;
        fields.compressed = random_word(state) % 10000;
        fields.size = fields.method ? random_word(state) : fields.compressed;
        fields.flags = iteration % 2 ? 0x800 : 0;
        if (iteration % 7 == 0) fields.flags |= 8;
        if (iteration % 11 == 0) fields.name = "../bad";
        const auto plain = plain_header(fields);
        const auto bytes = encode(plain);
        const auto saved = bytes;
        const auto offset = std::uint64_t(random_word(state)) << 8;
        const auto end = offset + bytes.size() + fields.compressed;
        const auto archive_size = iteration % 5 == 0 ? end - 1 : end;
        auto expected = sentinel();
        // Wrapper regression check against the ordinary source ZIP reader;
        // this shared validator is not an independent original-game oracle.
        const auto result = nova3::parse_zip_local(plain.data(), plain.size(), offset, archive_size, expected);
        auto actual = sentinel();
        REQUIRE(nova3::parse_scrambled_local(bytes.data(), bytes.size(), offset, archive_size,
                                            key, actual) == result);
        REQUIRE(equal(actual, expected) && bytes == saved);
        if (result == ZipError::ok) {
            ++accepted;
            REQUIRE(actual.name == fields.name && actual.crc == fields.crc);
            REQUIRE(actual.data_offset == offset + bytes.size() && actual.next_offset == end);
        } else {
            ++rejected;
            REQUIRE(equal(actual, sentinel()));
        }

        // Fixed-seed mutation corpus checks invariants/sanitizers. A wrong key
        // or corrupted ciphertext can still produce a structurally valid header.
        auto mutated = bytes;
        const auto position = random_word(state) % mutated.size();
        mutated[position] ^= static_cast<std::uint8_t>(1u << (iteration % 8));
        actual = sentinel();
        const auto mutated_saved = mutated;
        const auto error = nova3::parse_scrambled_local(mutated.data(), mutated.size(), offset,
                                                       archive_size, key, actual);
        REQUIRE(mutated == mutated_saved);
        if (error == ZipError::ok) {
            REQUIRE(actual.data_offset == offset + mutated.size());
            REQUIRE(actual.data_offset <= actual.next_offset && actual.next_offset <= archive_size);
            REQUIRE(actual.next_offset - actual.data_offset == actual.compressed_size);
            REQUIRE(actual.method == 0 || actual.method == 8);
        } else REQUIRE(equal(actual, sentinel()));
    }
    REQUIRE(accepted > 1000 && rejected > 1000);
}
}

int main() {
    literal_vector();
    lengths_and_prefixes();
    validation_and_bounds();
    deterministic_corpus();
    std::cout << "synthetic SB vectors, length domain, ZIP validation, bounds and 4000 seeded cases passed\n";
}
