#include "zip_local.hpp"
#include <utility>
namespace nova3 {
namespace {
std::uint16_t u16(const std::uint8_t* p) { return p[0] | (std::uint16_t(p[1]) << 8); }
std::uint32_t u32(const std::uint8_t* p) { return u16(p) | (std::uint32_t(u16(p+2)) << 16); }
bool safe_name(const std::string& name) {
    if (name.empty() || name.front()=='/' || name.front()=='\\') return false;
    std::string part;
    for (char c : name) {
        if (c=='\0' || c==':' || c=='\\') return false;
        if (c=='/') { if (part=="..") return false; part.clear(); }
        else part+=c;
    }
    return part!="..";
}
}
ZipError parse_zip_local(const std::uint8_t* p, std::size_t available,
                        std::uint64_t offset, std::uint64_t archive_size,
                        ZipLocalEntry& entry) {
    if (!p || available<30) return ZipError::truncated;
    if (u32(p)!=0x04034b50u) return ZipError::signature;
    ZipLocalEntry next;
    next.flags=u16(p+6); next.method=u16(p+8);
    if ((next.flags & ~0x0800u) || (next.method!=0 && next.method!=8)) return ZipError::unsupported;
    next.crc=u32(p+14); next.compressed_size=u32(p+18); next.size=u32(p+22);
    if (next.compressed_size==0xffffffffu || next.size==0xffffffffu) return ZipError::unsupported;
    const auto header_size=std::size_t(30)+u16(p+26)+u16(p+28);
    if (header_size>available) return ZipError::truncated;
    next.name.assign(reinterpret_cast<const char*>(p+30),u16(p+26));
    if (!safe_name(next.name)) return ZipError::unsafe_name;
    if (offset>archive_size || header_size>archive_size-offset) return ZipError::bounds;
    next.data_offset=offset+header_size;
    if (next.compressed_size>archive_size-next.data_offset) return ZipError::bounds;
    if (next.method==0 && next.size!=next.compressed_size) return ZipError::bounds;
    next.next_offset=next.data_offset+next.compressed_size;
    entry=std::move(next);
    return ZipError::ok;
}
}
