#include "zip_lookup.hpp"
#include <algorithm>
#include <utility>
namespace nova3 {
namespace {
std::string make_key(std::string name, bool ignore_case, bool ignore_paths,
                     const char* separators) {
    // Original makeLower 0x359ff4 changes ASCII A..Z only.
    if (ignore_case) for (char& c : name)
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
    if (ignore_paths) {
        const auto pos = name.find_last_of(separators);
        // Native scan preserves a separator at offset zero.
        if (pos != std::string::npos && pos != 0) name.erase(0, pos + 1);
    }
    return name;
}
void sink(std::vector<ZipLookupEntry>& entries, std::size_t k, std::size_t limit) {
    // Native heapsink 0x862788 uses one-based indices and exclusive limit.
    while (k <= (limit - 1) / 2) {
        std::size_t child = k * 2;
        if (child >= limit) break;
        if (child + 1 < limit && entries[child-1].key < entries[child].key) ++child;
        if (!(entries[k-1].key < entries[child-1].key)) break;
        std::swap(entries[k-1], entries[child-1]);
        k = child;
    }
}
}
std::string zip_entry_key(std::string name, bool ignore_case, bool ignore_paths) {
    return make_key(std::move(name), ignore_case, ignore_paths, "/");
}
std::string zip_query_key(std::string name, bool ignore_case, bool ignore_paths) {
    return make_key(std::move(name), ignore_case, ignore_paths, "/\\");
}
void sort_zip_lookup(std::vector<ZipLookupEntry>& entries) {
    const auto n = entries.size();
    if (n < 2) return;
    for (auto k = (n - 1) / 2 + 1; k != 0; --k) sink(entries, k, n + 1);
    // This unstable sort intentionally reproduces native duplicate ordering.
    for (auto limit = n; limit != 0; --limit) {
        std::swap(entries[0], entries[limit-1]);
        sink(entries, 1, limit);
    }
}
std::size_t find_zip_lookup(const std::vector<ZipLookupEntry>& entries,
                            const std::string& query) {
    const auto it = std::lower_bound(entries.begin(), entries.end(), query,
        [](const ZipLookupEntry& entry, const std::string& value) { return entry.key < value; });
    return it != entries.end() && it->key == query ? it->archive_index : std::size_t(-1);
}
}
