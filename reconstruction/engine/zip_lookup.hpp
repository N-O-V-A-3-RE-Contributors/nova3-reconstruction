#pragma once
#include <cstddef>
#include <string>
#include <vector>
namespace nova3 {
struct ZipLookupEntry { std::string key; std::size_t archive_index; };
// ASCII-only case folding. Entries strip '/' paths; queries also strip '\\'.
// With ignore_paths, a final separator at position zero is preserved.
std::string zip_entry_key(std::string name, bool ignore_case, bool ignore_paths);
std::string zip_query_key(std::string name, bool ignore_case, bool ignore_paths);
// Sorts lexicographically by key; duplicate ordering is intentionally unstable.
void sort_zip_lookup(std::vector<ZipLookupEntry>& entries);
// Requires entries sorted by key and an already-normalized query. Returns the
// first equal key's original archive index, or size_t(-1) on absence.
std::size_t find_zip_lookup(const std::vector<ZipLookupEntry>& entries,
                            const std::string& normalized_query);
}
