// Task 35: checked, portable ZIP-reader cluster. NOT MATCHED to ARM machine code.
// Reference identity and exact verification scope live in
// coordination/evidence/functions-8708f94a70699ac28c6d23.json.
//
// This uses the existing source-owned ZIP primitives, not the original ELF.
// It is a logical interface, NOT the original CZipReader/SZipFileEntry ABI.
// In particular, COW strings, refcounts, stream vtables, descriptors, exception
// paths, and native init options outside case/path folding are not recovered.
// Tests include this file because this task does not allocate a public header.
#include "../../engine/zip_local.hpp"
#include "../../engine/zip_lookup.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace nova3::recovered::zip_cluster {

struct Options {
    bool ignore_case = false;
    bool ignore_paths = false;
};

struct Entry {
    ZipLocalEntry local;
    std::string key;
    std::uint64_t header_offset = 0;
};

enum class Result {
    ok, end, truncated, signature, unsupported, unsafe_name, bounds, limit
};

constexpr std::size_t not_found = std::numeric_limits<std::size_t>::max();

// 0x007fd2d4 extractFilename: logical key only. Entry names strip '/' only;
// ASCII folding and preservation of a separator at offset zero are intentional.
// Native filename-length and ownership handling are excluded.
std::string extract_filename(std::string name, Options options) {
    return zip_entry_key(std::move(name), options.ignore_case, options.ignore_paths);
}

// 0x007fcec0 findFile: returns the position IN THE SORTED ENTRY VECTOR, not a
// physical/archive index. The precondition is ascending bytewise key order.
// Native queries are NUL-terminated. Null is rejected by this checked API.
std::size_t find_file(const std::vector<Entry>& entries, const char* query,
                      Options options) {
    if (!query) return not_found;
    const auto key = zip_query_key(query, options.ignore_case, options.ignore_paths);
    const auto found = std::lower_bound(entries.begin(), entries.end(), key,
        [](const Entry& entry, const std::string& value) { return entry.key < value; });
    if (found == entries.end() || found->key != key) return not_found;
    return static_cast<std::size_t>(found - entries.begin());
}

namespace {
Result checked_error(ZipError error) {
    switch (error) {
        case ZipError::ok: return Result::ok;
        case ZipError::truncated: return Result::truncated;
        case ZipError::signature: return Result::signature;
        case ZipError::unsupported: return Result::unsupported;
        case ZipError::unsafe_name: return Result::unsafe_name;
        case ZipError::bounds: return Result::bounds;
    }
    return Result::unsupported;
}

void sort_entries(std::vector<Entry>& entries) {
    std::vector<ZipLookupEntry> order;
    order.reserve(entries.size());
    for (std::size_t i = 0; i < entries.size(); ++i)
        order.push_back({entries[i].key, i});
    // Native heap tie ordering matters for duplicate names. std::sort or
    // stable_sort would not preserve the observed duplicate permutations.
    sort_zip_lookup(order);
    std::vector<Entry> sorted;
    sorted.reserve(entries.size());
    for (const auto& item : order) sorted.push_back(std::move(entries[item.archive_index]));
    entries.swap(sorted);
}
}

// 0x007fd420 scanLocalHeader, PARTIAL: contiguous classic local headers only.
// Cursor is an absolute position in bytes; compressed payloads are skipped.
// CRC/payload contents and the central directory are NOT validated here.
// Extra fields are opaque. Descriptor/encrypted/ZIP64 forms are unsupported.
// EOF and central/EOCD signatures stop scanning without consuming the marker.
// Strict bounds, safe names, and unchanged outputs on error are owned safety
// policy, not claims about the original scanner's malformed-input behaviour.
Result scan_local_header(const std::uint8_t* bytes, std::size_t size,
                         std::uint64_t& cursor, Options options, Entry& output) {
    if (cursor > size) return Result::bounds;
    if (!bytes && size != 0) return Result::truncated;
    if (cursor == size) return Result::end;
    const auto position = static_cast<std::size_t>(cursor);
    const auto available = size - position;
    if (available < 4) return Result::truncated;
    const auto* header = bytes + position;
    const auto signature = std::uint32_t(header[0]) | (std::uint32_t(header[1]) << 8) |
        (std::uint32_t(header[2]) << 16) | (std::uint32_t(header[3]) << 24);
    if (signature == 0x02014b50u || signature == 0x06054b50u) return Result::end;
    Entry next;
    const auto error = parse_zip_local(header, available, cursor, size, next.local);
    if (error != ZipError::ok) return checked_error(error);
    next.key = extract_filename(next.local.name, options);
    next.header_offset = cursor;
    const auto next_cursor = next.local.next_offset;
    output = std::move(next);
    cursor = next_cursor;
    return Result::ok;
}

// 0x00810d84 init, PARTIAL: local scanning + full-entry heap permutation only.
// A source-owned in-memory adapter, not native stream setup/option packing.
// Transactional errors, resource cap and permissive local-stream EOF are our
// contract. A successful index is NOT proof of a complete/valid ZIP archive.
Result init(const std::uint8_t* bytes, std::size_t size, Options options,
            std::vector<Entry>& output, std::size_t max_entries = 65534) {
    std::vector<Entry> next;
    std::uint64_t cursor = 0;
    for (;;) {
        Entry entry;
        const auto result = scan_local_header(bytes, size, cursor, options, entry);
        if (result == Result::end) break;
        if (result != Result::ok) return result;
        if (next.size() == max_entries) return Result::limit;
        next.push_back(std::move(entry));
    }
    sort_entries(next);
    output.swap(next);
    return Result::ok;
}

} // namespace nova3::recovered::zip_cluster
