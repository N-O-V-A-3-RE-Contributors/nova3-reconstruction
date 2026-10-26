#include "zip_lookup.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <utility>
using namespace nova3;
// Synthetic contract regressions; no original binary oracle is used here.
static void check(bool value, const char* expression, int line) {
    if (!value) {
        std::cerr << "ZIP lookup assertion failed at line " << line << ": " << expression << '\n';
        std::exit(1);
    }
}
#define require(condition) check((condition), #condition, __LINE__)
static void existing_regressions() {
    require(zip_entry_key("Dir/FILE.BDAE", true, true) == "file.bdae");
    require(zip_entry_key("Dir\\FILE.BDAE", true, true) == "dir\\file.bdae");
    require(zip_query_key("Dir\\FILE.BDAE", true, true) == "file.bdae");
    for (const auto name : {"/foo", "\\foo", "/", "\\", ""})
        require(zip_query_key(name, false, true) == name);
    require(zip_entry_key("a/", false, true).empty());
    require(zip_query_key("a\\", false, true).empty());
    require(zip_entry_key("//foo", false, true) == "foo");
    require(zip_entry_key("dir/a", false, false) == "dir/a");
    const std::string high = std::string(1, static_cast<char>(0xc0)) + "AZ";
    require(zip_entry_key(high, true, false) == std::string(1, static_cast<char>(0xc0)) + "az");
    std::vector<ZipLookupEntry> entries;
    sort_zip_lookup(entries);require(find_zip_lookup(entries, "") == std::size_t(-1));
    entries={{"x",0},{"x",1}};sort_zip_lookup(entries);
    require(entries[0].archive_index==1 && entries[1].archive_index==0);
    require(find_zip_lookup(entries,"x")==1);
    entries={{"x",0},{"x",1},{"x",2}};sort_zip_lookup(entries);
    require(entries[0].archive_index==1 && entries[1].archive_index==2 && entries[2].archive_index==0);
    entries={{"b",0},{"a",1},{"b",2},{"a",3}};sort_zip_lookup(entries);
    const std::size_t expected[]={1,3,2,0};
    for(std::size_t i=0;i<4;++i)require(entries[i].archive_index==expected[i]);
    require(find_zip_lookup(entries,"a")==1 && find_zip_lookup(entries,"b")==2);
    require(find_zip_lookup(entries,"")==std::size_t(-1));
    require(find_zip_lookup(entries,"c")==std::size_t(-1));
}

static void key_boundaries() {
    struct PathCase { const char* input; const char* entry; const char* query; };
    const PathCase cases[] = {
        {"", "", ""}, {"/", "/", "/"}, {"\\", "\\", "\\"},
        {"//", "", ""}, {"\\\\", "\\\\", ""},
        {"/A", "/A", "/A"}, {"\\A", "\\A", "\\A"},
        {"A/", "", ""}, {"A\\", "A\\", ""},
        {"A//B", "B", "B"}, {"A\\\\B", "A\\\\B", "B"},
        {"A/B\\C", "B\\C", "C"}, {"A\\B/C", "C", "C"},
        {"A/./B", "B", "B"}, {"A/../B", "B", "B"}
    };
    for (const auto& row : cases) {
        require(zip_entry_key(row.input, false, true) == row.entry);
        require(zip_query_key(row.input, false, true) == row.query);
        require(zip_entry_key(row.input, false, false) == row.input);
        require(zip_query_key(row.input, false, false) == row.input);
    }
    // All byte values, including NUL and bytes >= 128, must survive except A..Z.
    std::string bytes, folded;
    for (unsigned value = 0; value < 256; ++value) {
        bytes += static_cast<char>(value);
        folded += static_cast<char>(value >= 65 && value <= 90 ? value + 32 : value);
    }
    require(zip_entry_key(bytes, true, false) == folded);
    require(zip_query_key(bytes, true, false) == folded);
    require(zip_entry_key(bytes, false, false) == bytes);
    require(zip_query_key(bytes, false, false) == bytes);
    const std::string nul_path("A\0/B\0C", 6);
    require(zip_entry_key(nul_path, true, true) == std::string("b\0c", 3));
    require(zip_query_key(nul_path, true, true) == std::string("b\0c", 3));
    require(zip_entry_key("A/B", true, false) == "a/b");
    require(zip_query_key("A\\B", true, false) == "a\\b");
}

static void check_query(const std::vector<ZipLookupEntry>& entries, const std::string& query) {
    // Linear search is an independent oracle for lower_bound's first-key rule.
    const auto it = std::find_if(entries.begin(), entries.end(),
                                [&query](const ZipLookupEntry& entry) { return entry.key == query; });
    const auto expected = it == entries.end() ? std::size_t(-1) : it->archive_index;
    require(find_zip_lookup(entries, query) == expected);
}

static void check_sort(const std::vector<ZipLookupEntry>& original) {
    auto sorted = original;
    sort_zip_lookup(sorted);
    require(std::is_sorted(sorted.begin(), sorted.end(),
        [](const ZipLookupEntry& a, const ZipLookupEntry& b) { return a.key < b.key; }));
    // Verify the entire multiset of key/index pairs, without imposing a stable
    // tie order or accidentally validating by archive_index instead of key.
    std::vector<std::pair<std::string, std::size_t>> before, after;
    for (const auto& entry : original) before.emplace_back(entry.key, entry.archive_index);
    for (const auto& entry : sorted) after.emplace_back(entry.key, entry.archive_index);
    std::sort(before.begin(), before.end());
    std::sort(after.begin(), after.end());
    require(before == after);
    for (const auto& entry : sorted) check_query(sorted, entry.key);
    for (const auto& query : {std::string(), std::string("a"), std::string("aa"),
                              std::string("z"), std::string("\0", 1), std::string(1, '\xff')})
        check_query(sorted, query);
}

static void exhaustive_small_sorts() {
    // Every sequence over three keys through length seven: 3280 cases.
    // Includes empty/singleton, duplicates, all-equal and arbitrary key order.
    const std::string keys[] = {"", "a", "b"};
    std::size_t combinations = 1;
    for (std::size_t length = 0; length <= 7; ++length) {
        for (std::size_t encoded = 0; encoded < combinations; ++encoded) {
            std::vector<ZipLookupEntry> entries;
            auto digits = encoded;
            for (std::size_t i = 0; i < length; ++i) {
                entries.push_back({keys[digits % 3], length - i});
                digits /= 3;
            }
            check_sort(entries);
        }
        combinations *= 3;
    }
}

static void larger_sorts_and_indices() {
    const std::string keys[] = {"", "a", "aa", "b", std::string("a\0", 2),
        std::string("a\0b", 3), std::string(1, '\x7f'), std::string(1, '\x80'),
        std::string(1, '\xff')};
    for (const auto length : {15u, 16u, 17u, 31u, 32u, 33u, 63u, 64u, 65u,
                              127u, 128u, 129u, 1023u, 1024u, 1025u}) {
        std::vector<ZipLookupEntry> entries;
        for (std::size_t i = 0; i < length; ++i)
            entries.push_back({keys[(i * 7) % 9], i});
        check_sort(entries);
        std::sort(entries.begin(), entries.end(),
                  [](const ZipLookupEntry& a, const ZipLookupEntry& b) { return a.key < b.key; });
        check_sort(entries);
        std::reverse(entries.begin(), entries.end());
        check_sort(entries);
        for (auto& entry : entries) entry.key = "same";
        check_sort(entries);
    }
    // archive_index is an opaque value: lookup must not add, truncate or use it
    // as an array position. The maximum value itself is the API's miss sentinel.
    const auto large = std::numeric_limits<std::size_t>::max() - 1;
    const std::vector<ZipLookupEntry> entries = {{"a", large}, {"c", 0}};
    require(find_zip_lookup(entries, "a") == large);
    require(find_zip_lookup(entries, "b") == std::size_t(-1));
    check_sort(entries);
    const std::vector<ZipLookupEntry> duplicate_indices = {{"b", large}, {"a", large}};
    check_sort(duplicate_indices);
    // Queries are not normalized inside find_zip_lookup.
    require(find_zip_lookup(entries, "A") == std::size_t(-1));
}

static void normalized_lookup() {
    std::vector<ZipLookupEntry> entries = {
        {zip_entry_key("first/FILE", true, true), 17},
        {zip_entry_key("second/file", true, true), 29}
    };
    sort_zip_lookup(entries);
    require(entries[0].archive_index == 29);
    require(find_zip_lookup(entries, zip_query_key("elsewhere\\FiLe", true, true)) == 29);
    // Preserve the deliberate entry/query separator asymmetry. This API is a
    // key transform, not a filesystem path validator or canonicalizer.
    entries = {{zip_entry_key("Dir\\FILE", true, true), 7}};
    sort_zip_lookup(entries);
    require(find_zip_lookup(entries, zip_query_key("Dir\\FILE", true, true)) == std::size_t(-1));
    require(find_zip_lookup(entries, "dir\\file") == 7);
}

int main() {
    existing_regressions();
    key_boundaries();
    exhaustive_small_sorts();
    larger_sorts_and_indices();
    normalized_lookup();
    std::cout << "PASS: ZIP key boundaries, duplicate ordering, exhaustive sorts and lookup\n";
}
