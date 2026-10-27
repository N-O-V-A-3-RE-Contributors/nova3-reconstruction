// Synthetic fixtures and an optional hex CLI for an external offline oracle.
// No proprietary fixtures or binary execution form part of this executable.
#include "../../../recovered/libNOVA3_neon/block_007fcec0.cpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>

namespace cluster = nova3::recovered::zip_cluster;

static void require(bool value, const char* expression, int line) {
    if (!value) {
        std::cerr << "FAIL line " << line << ": " << expression << '\n';
        std::exit(1);
    }
}
#define REQUIRE(condition) require((condition), #condition, __LINE__)

static void put16(std::vector<std::uint8_t>& bytes, std::size_t at, std::uint16_t n) {
    bytes.at(at) = static_cast<std::uint8_t>(n);
    bytes.at(at + 1) = static_cast<std::uint8_t>(n >> 8);
}
static void put32(std::vector<std::uint8_t>& bytes, std::size_t at, std::uint32_t n) {
    put16(bytes, at, static_cast<std::uint16_t>(n));
    put16(bytes, at + 2, static_cast<std::uint16_t>(n >> 16));
}
static void append_local(std::vector<std::uint8_t>& bytes, const std::string& name,
                          std::uint32_t tag, unsigned payload = 0) {
    REQUIRE(name.size() <= 65535);
    const auto start = bytes.size();
    bytes.resize(start + 30 + name.size() + 3 + payload, 0);
    put32(bytes, start, 0x04034b50u);
    put16(bytes, start + 6, 0x0800);
    put32(bytes, start + 14, tag); // Opaque CRC for checking metadata association.
    put32(bytes, start + 18, payload);
    put32(bytes, start + 22, payload);
    put16(bytes, start + 26, static_cast<std::uint16_t>(name.size()));
    put16(bytes, start + 28, 3);
    std::copy(name.begin(), name.end(), bytes.begin() + static_cast<std::ptrdiff_t>(start + 30));
    // Opaque extra bytes, followed by payload containing a misleading PK marker.
    if (payload >= 4) put32(bytes, bytes.size() - payload, 0x04034b50u);
}

static bool same(const cluster::Entry& a, const cluster::Entry& b) {
    return a.key == b.key && a.header_offset == b.header_offset &&
        a.local.name == b.local.name && a.local.flags == b.local.flags &&
        a.local.method == b.local.method && a.local.crc == b.local.crc &&
        a.local.compressed_size == b.local.compressed_size && a.local.size == b.local.size &&
        a.local.data_offset == b.local.data_offset && a.local.next_offset == b.local.next_offset;
}

static void integrated_index() {
    std::vector<std::uint8_t> bytes;
    append_local(bytes, "Dir/B", 10, 4);
    const auto second = bytes.size();
    append_local(bytes, "Dir/A", 20, 1);
    const auto third = bytes.size();
    append_local(bytes, "Other/B", 30);
    const auto fourth = bytes.size();
    append_local(bytes, "Other/A", 40, 9);
    const auto end = bytes.size();
    // Only a stop marker: central-directory validation is explicitly excluded.
    bytes.insert(bytes.end(), {0x50, 0x4b, 0x01, 0x02});
    std::vector<cluster::Entry> entries;
    REQUIRE(cluster::init(bytes.data(), bytes.size(), {true, true}, entries) == cluster::Result::ok);
    REQUIRE(entries.size() == 4);
    const std::uint32_t crc[] = {20, 40, 30, 10};
    const std::uint64_t offsets[] = {second, fourth, third, 0};
    for (std::size_t i = 0; i < entries.size(); ++i) {
        REQUIRE(entries[i].local.crc == crc[i]);
        REQUIRE(entries[i].header_offset == offsets[i]);
        REQUIRE(entries[i].local.data_offset == offsets[i] + 33 + entries[i].local.name.size());
        REQUIRE(entries[i].local.next_offset == entries[i].local.data_offset + entries[i].local.size);
    }
    REQUIRE(entries[0].key == "a" && entries[2].key == "b");
    // Sorted slot 0 corresponds to physical header 1; sorted slot 2 to header 2.
    REQUIRE(cluster::find_file(entries, "Elsewhere\\A", {true, true}) == 0);
    REQUIRE(cluster::find_file(entries, "Elsewhere/B", {true, true}) == 2);
    REQUIRE(cluster::find_file(entries, "missing", {true, true}) == cluster::not_found);
    REQUIRE(cluster::find_file(entries, nullptr, {}) == cluster::not_found);
    REQUIRE(cluster::find_file(entries, "A", {}) == cluster::not_found);
    std::uint64_t cursor = end;
    auto sentinel = entries[0];
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, sentinel) == cluster::Result::end);
    REQUIRE(cursor == end && same(sentinel, entries[0]));
    // A failed rebuild preserves the entire previous index.
    const auto previous = entries;
    REQUIRE(cluster::init(bytes.data(), bytes.size(), {}, entries, 3) == cluster::Result::limit);
    REQUIRE(entries.size() == previous.size());
    for (std::size_t i = 0; i < entries.size(); ++i) REQUIRE(same(entries[i], previous[i]));
    REQUIRE(cluster::init(bytes.data(), bytes.size(), {}, entries, 4) == cluster::Result::ok);
    REQUIRE(cluster::init(nullptr, 0, {}, entries, 0) == cluster::Result::ok && entries.empty());
}

static void key_and_query_boundaries() {
    REQUIRE(cluster::extract_filename("Dir\\FILE", {true, true}) == "dir\\file");
    REQUIRE(cluster::extract_filename("Dir/FILE", {true, true}) == "file");
    REQUIRE(cluster::extract_filename("/FILE", {true, true}) == "/file");
    REQUIRE(cluster::extract_filename("Dir/", {false, true}).empty());
    const std::string high = std::string(1, static_cast<char>(0x80)) + "AZ";
    REQUIRE(cluster::extract_filename(high, {true, false}) == std::string(1, static_cast<char>(0x80)) + "az");
    std::vector<cluster::Entry> entries(1);
    entries[0].key = "file";
    REQUIRE(cluster::find_file(entries, "Dir\\FILE", {true, true}) == 0);
    REQUIRE(cluster::find_file(entries, "FILE\0ignored", {true, false}) == 0);
    entries[0].key = "/file";
    REQUIRE(cluster::find_file(entries, "/FILE", {true, true}) == 0);
    entries[0].key.clear();
    REQUIRE(cluster::find_file(entries, "", {}) == 0);
    REQUIRE(cluster::find_file({}, "", {}) == cluster::not_found);
}

static void scanner_errors() {
    std::vector<std::uint8_t> bytes;
    append_local(bytes, "safe/file", 123, 8);
    std::uint64_t cursor = 0;
    cluster::Entry original;
    original.key = "sentinel";
    original.local.name = "unchanged";
    original.header_offset = 987;
    auto entry = original;
    for (std::size_t size = 1; size < bytes.size(); ++size) {
        cursor = 0;
        REQUIRE(cluster::scan_local_header(bytes.data(), size, cursor, {}, entry) != cluster::Result::ok);
        REQUIRE(cursor == 0 && same(entry, original));
    }
    cursor = bytes.size() + 1;
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::bounds);
    REQUIRE(cursor == bytes.size() + 1 && same(entry, original));
    cursor = 0;
    REQUIRE(cluster::scan_local_header(nullptr, bytes.size(), cursor, {}, entry) == cluster::Result::truncated);
    auto invalid = bytes;
    invalid[0] = 0;
    REQUIRE(cluster::scan_local_header(invalid.data(), invalid.size(), cursor, {}, entry) == cluster::Result::signature);
    invalid = bytes;
    put16(invalid, 6, 8); // Data descriptor, not reconstructed.
    REQUIRE(cluster::scan_local_header(invalid.data(), invalid.size(), cursor, {}, entry) == cluster::Result::unsupported);
    invalid.clear();
    append_local(invalid, "../escape", 0);
    REQUIRE(cluster::scan_local_header(invalid.data(), invalid.size(), cursor, {}, entry) == cluster::Result::unsafe_name);
    REQUIRE(cursor == 0 && same(entry, original));
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::ok);
    REQUIRE(cursor == bytes.size() && entry.local.name == "safe/file");
    REQUIRE(entry.local.flags == 0x0800 && entry.local.method == 0 && entry.local.crc == 123);
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::end);
    // A late malformed record must not replace a previous usable index.
    std::vector<cluster::Entry> entries{original};
    bytes.push_back(0);
    REQUIRE(cluster::init(bytes.data(), bytes.size(), {}, entries) == cluster::Result::truncated);
    REQUIRE(entries.size() == 1 && same(entries[0], original));
}

static void scanner_fields_and_stops() {
    // Unaligned header at a nonzero absolute position. Deflate bytes are opaque:
    // successful indexing must not be mistaken for successful decompression.
    std::vector<std::uint8_t> bytes(3, 0);
    append_local(bytes, "Dir/FILE", 0xfedcba98u, 4);
    put16(bytes, 3 + 8, 8);
    put32(bytes, 3 + 22, 1000);
    std::uint64_t cursor = 3;
    cluster::Entry entry;
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {true, true}, entry) == cluster::Result::ok);
    REQUIRE(entry.header_offset == 3 && entry.local.data_offset == 44);
    REQUIRE(cursor == 48 && entry.local.next_offset == 48);
    REQUIRE(entry.local.method == 8 && entry.local.compressed_size == 4 && entry.local.size == 1000);
    REQUIRE(entry.local.crc == 0xfedcba98u && entry.key == "file");
    const auto original = entry;
    for (const std::uint32_t signature : {0x02014b50u, 0x06054b50u}) {
        const auto end = bytes.size();
        bytes.resize(end + 4);
        put32(bytes, end, signature);
        cursor = end;
        REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::end);
        REQUIRE(cursor == end && same(entry, original));
        bytes.resize(end);
    }
    put32(bytes, 3 + 18, 0xffffffffu); // ZIP64 sentinel: never use as a skip length.
    cursor = 3;
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::unsupported);
    REQUIRE(cursor == 3 && same(entry, original));
    cursor = std::numeric_limits<std::uint64_t>::max();
    REQUIRE(cluster::scan_local_header(bytes.data(), bytes.size(), cursor, {}, entry) == cluster::Result::bounds);
    REQUIRE(cursor == std::numeric_limits<std::uint64_t>::max() && same(entry, original));
}

static void exhaustive_index() {
    // Every sequence of three names through length seven (3,280 indexes).
    // Check order, full metadata preservation and first-equal sorted position
    // against linear search, without assuming stable duplicate ordering.
    const std::string names[] = {"dir/B", "dir/a", "dir/A"};
    std::size_t combinations = 1;
    for (std::size_t length = 0; length <= 7; ++length) {
        for (std::size_t encoded = 0; encoded < combinations; ++encoded) {
            std::vector<std::uint8_t> bytes;
            auto digits = encoded;
            std::vector<std::uint64_t> physical;
            for (std::size_t i = 0; i < length; ++i) {
                physical.push_back(bytes.size());
                append_local(bytes, names[digits % 3], static_cast<std::uint32_t>(i), static_cast<unsigned>(i));
                digits /= 3;
            }
            std::vector<cluster::Entry> entries;
            REQUIRE(cluster::init(bytes.data(), bytes.size(), {true, true}, entries) == cluster::Result::ok);
            REQUIRE(entries.size() == length);
            REQUIRE(std::is_sorted(entries.begin(), entries.end(),
                [](const auto& a, const auto& b) { return a.key < b.key; }));
            std::vector<bool> seen(length, false);
            for (const auto& entry : entries) {
                REQUIRE(entry.local.crc < length && !seen[entry.local.crc]);
                seen[entry.local.crc] = true;
                REQUIRE(entry.header_offset == physical[entry.local.crc]);
                REQUIRE(entry.local.size == entry.local.crc);
            }
            for (const auto* query : {"a", "b", "c", ""}) {
                const auto first = std::find_if(entries.begin(), entries.end(),
                    [query](const auto& e) { return e.key == query; });
                const auto expected = first == entries.end() ? cluster::not_found :
                    static_cast<std::size_t>(first - entries.begin());
                REQUIRE(cluster::find_file(entries, query, {}) == expected);
            }
        }
        combinations *= 3;
    }
}

static std::string unhex(const std::string& text) {
    if (text.size() % 2) throw std::invalid_argument("odd hex");
    std::string result;
    const auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return static_cast<unsigned>(c - '0');
        if (c >= 'a' && c <= 'f') return static_cast<unsigned>(c - 'a' + 10);
        if (c >= 'A' && c <= 'F') return static_cast<unsigned>(c - 'A' + 10);
        throw std::invalid_argument("invalid hex");
    };
    for (std::size_t i = 0; i < text.size(); i += 2)
        result += static_cast<char>(digit(text[i]) * 16 + digit(text[i + 1]));
    return result;
}
static void hexout(const std::string& text) {
    constexpr char hex[] = "0123456789abcdef";
    for (const unsigned char c : text) std::cout << hex[c >> 4] << hex[c & 15];
    std::cout << '\n';
}

static int oracle_cli(int argc, char** argv) {
    const std::string mode = argv[1];
    if (mode == "entry" && argc == 4) {
        const auto flags = std::stoul(argv[2]);
        hexout(cluster::extract_filename(unhex(argv[3]), {bool(flags & 2), bool(flags & 4)}));
        return 0;
    }
    if (mode == "find" && argc >= 4) {
        const auto flags = std::stoul(argv[2]);
        const auto query = unhex(argv[3]);
        std::vector<cluster::Entry> entries;
        for (int i = 4; i < argc; ++i) {
            cluster::Entry entry;
            entry.key = unhex(argv[i]);
            entries.push_back(std::move(entry));
        }
        const auto found = cluster::find_file(entries, query.c_str(), {bool(flags & 2), bool(flags & 4)});
        // Normalize the portable sentinel to the original 32-bit return value.
        std::cout << (found == cluster::not_found ? std::uint64_t(0xffffffffu) : found) << '\n';
        return 0;
    }
    if (mode == "sort") {
        std::vector<std::uint8_t> bytes;
        for (int i = 2; i < argc; ++i) append_local(bytes, unhex(argv[i]), static_cast<std::uint32_t>(i - 2));
        std::vector<cluster::Entry> entries;
        if (cluster::init(bytes.data(), bytes.size(), {}, entries) != cluster::Result::ok) return 2;
        for (std::size_t i = 0; i < entries.size(); ++i) {
            if (i) std::cout << ' ';
            std::cout << entries[i].local.crc;
        }
        std::cout << '\n';
        return 0;
    }
    return 2;
}

int main(int argc, char** argv) {
    if (argc > 1) {
        try { return oracle_cli(argc, argv); }
        catch (const std::exception&) { return 2; }
    }
    integrated_index();
    key_and_query_boundaries();
    scanner_errors();
    scanner_fields_and_stops();
    exhaustive_index();
    std::cout << "PASS: ZIP cluster integration, errors, sorted slots, 3280 exhaustive indexes\n";
}
