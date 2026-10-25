#pragma once
#include "zip_local.hpp"
#include "zip_lookup.hpp"
#include <fstream>
#include <limits>
#include <vector>
namespace nova3 {
struct ZipArchiveOptions { bool ignore_case=false; bool ignore_paths=false; };
struct ZipArchiveLimits {
    std::size_t entries=65534;
    std::size_t metadata_bytes=32*1024*1024;
    std::size_t compressed_bytes=64*1024*1024;
    std::size_t output_bytes=64*1024*1024;
};
enum class ArchiveError { ok, io, invalid, unsupported, limit, not_found, payload, checksum };
struct ZipArchiveEntry { ZipLocalEntry local; std::uint64_t header_offset=0; };
// Source-owned seekable ZIP reader. Lookup/sort follows Premium CZipReader;
// central-directory validation and explicit bounds are added safety policy.
// A region may start inside another file. The caller must serialize access to one instance.
class ZipArchive {
public:
    ArchiveError open(const std::string& path, ZipArchiveOptions options={},
                      ZipArchiveLimits limits={}, std::uint64_t region_offset=0,
                      std::uint64_t region_size=std::numeric_limits<std::uint64_t>::max());
    ArchiveError open_memory(std::vector<std::uint8_t> bytes, ZipArchiveOptions options={}, ZipArchiveLimits limits={});
    const std::vector<ZipArchiveEntry>& entries() const noexcept { return entries_; }
    std::size_t find(const std::string& name) const;
    ArchiveError read(const std::string& name, std::vector<std::uint8_t>& output);
    ArchiveError read_index(std::size_t index, std::vector<std::uint8_t>& output);
private:
    bool read_at(std::uint64_t position, std::uint8_t* output, std::size_t size);
    ArchiveError index();
    ArchiveError index_directory(const std::uint8_t* eocd, std::uint64_t end_offset);
    std::ifstream file_;
    std::vector<std::uint8_t> memory_;
    std::uint64_t base_=0, size_=0;
    ZipArchiveOptions options_;
    ZipArchiveLimits limits_;
    std::vector<ZipArchiveEntry> entries_;
    std::vector<ZipLookupEntry> lookup_;
};
// Earlier successfully mounted ZIP wins, as observed CGlfFileSystem traversal.
// A failed decode tries later mounts; if none succeed the last error is reported.
class ResourcePackages {
public:
    ArchiveError mount(const std::string& path, ZipArchiveOptions options={}, ZipArchiveLimits limits={});
    ArchiveError read(const std::string& name, std::vector<std::uint8_t>& output);
private:
    std::vector<ZipArchive> archives_;
};
}
