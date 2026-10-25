#include "zip_archive.hpp"
#include "zip_payload.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <utility>
namespace nova3 {
namespace {
std::uint16_t u16(const std::uint8_t* p) { return p[0] | (std::uint16_t(p[1])<<8); }
std::uint32_t u32(const std::uint8_t* p) { return u16(p) | (std::uint32_t(u16(p+2))<<16); }
constexpr auto missing=std::numeric_limits<std::size_t>::max();
}
bool ZipArchive::read_at(std::uint64_t pos,std::uint8_t* output,std::size_t count) {
    if (pos>size_ || count>size_-pos) return false;
    if (!memory_.empty()) { if(count)std::memcpy(output,memory_.data()+pos,count);return true; }
    if (base_+pos>static_cast<std::uint64_t>(std::numeric_limits<std::streamoff>::max())) return false;
    file_.clear(); file_.seekg(static_cast<std::streamoff>(base_+pos));
    if (!file_) return false;
    if (!count) return true;
    file_.read(reinterpret_cast<char*>(output),static_cast<std::streamsize>(count));
    return static_cast<std::size_t>(file_.gcount())==count;
}
ArchiveError ZipArchive::open(const std::string& path,ZipArchiveOptions options,ZipArchiveLimits limits,
                              std::uint64_t offset,std::uint64_t length) {
    ZipArchive next;next.file_.open(path,std::ios::binary);
    if (!next.file_) return ArchiveError::io;
    next.file_.seekg(0,std::ios::end);const auto end=next.file_.tellg();
    if (end<0) return ArchiveError::io;
    const auto physical=static_cast<std::uint64_t>(end);
    if (offset>physical) return ArchiveError::invalid;
    if (length==std::numeric_limits<std::uint64_t>::max()) length=physical-offset;
    if (length>physical-offset || length<22) return ArchiveError::invalid;
    // Original reader stores classic ZIP offsets/sizes in 32 bits.
    if (length>0xffffffffu) return ArchiveError::unsupported;
    next.base_=offset;next.size_=length;next.options_=options;next.limits_=limits;
    auto error=next.index();if (error!=ArchiveError::ok) return error;
    *this=std::move(next);return ArchiveError::ok;
}
ArchiveError ZipArchive::open_memory(std::vector<std::uint8_t> bytes,ZipArchiveOptions options,ZipArchiveLimits limits) {
    if(bytes.size()<22)return ArchiveError::invalid;
    if(bytes.size()>0xffffffffu)return ArchiveError::unsupported;
    ZipArchive next;next.size_=bytes.size();next.memory_=std::move(bytes);next.options_=options;next.limits_=limits;
    const auto error=next.index();if(error!=ArchiveError::ok)return error;
    *this=std::move(next);return ArchiveError::ok;
}
ArchiveError ZipArchive::index() {
    const auto tail_size=static_cast<std::size_t>(std::min<std::uint64_t>(size_,65557));
    std::vector<std::uint8_t> tail(tail_size);
    if (!read_at(size_-tail_size,tail.data(),tail.size())) return ArchiveError::io;
    ArchiveError last=ArchiveError::invalid;
    unsigned candidates=0;
    for (std::size_t p=tail_size-22;;--p) {
        if (u32(tail.data()+p)==0x06054b50u && p+22+u16(tail.data()+p+20)==tail_size) {
            // Arbitrary ZIP comments can themselves contain EOCD signatures.
            // Try earlier candidates when a later candidate is inconsistent.
            if (++candidates>32) return ArchiveError::limit;
            entries_.clear();lookup_.clear();
            last=index_directory(tail.data()+p,size_-tail_size+p);
            if (last==ArchiveError::ok || last==ArchiveError::io) return last;
        }
        if (!p) break;
    }
    return last;
}
ArchiveError ZipArchive::index_directory(const std::uint8_t* e,std::uint64_t end_offset) {
    if (u16(e+4) || u16(e+6) || u16(e+8)!=u16(e+10)) return ArchiveError::unsupported;
    const auto count=u16(e+10);const std::uint64_t central_size=u32(e+12),central=u32(e+16);
    if (count==0xffff || central_size==0xffffffffu || central==0xffffffffu) return ArchiveError::unsupported;
    if (count>limits_.entries) return ArchiveError::limit;
    if (central>end_offset || central_size!=end_offset-central) return ArchiveError::invalid;
    std::uint64_t cursor=central;std::size_t metadata=0;
    entries_.reserve(count);
    for (std::size_t i=0;i<count;++i) {
        std::array<std::uint8_t,46> c{};
        if (cursor>end_offset || c.size()>end_offset-cursor || !read_at(cursor,c.data(),c.size())) return ArchiveError::invalid;
        if (u32(c.data())!=0x02014b50u) return ArchiveError::invalid;
        const auto name_size=u16(c.data()+28),extra_size=u16(c.data()+30),comment_size=u16(c.data()+32);
        const auto record_size=std::size_t(46)+name_size+extra_size+comment_size;
        if (record_size>end_offset-cursor) return ArchiveError::invalid;
        if (record_size>limits_.metadata_bytes || metadata>limits_.metadata_bytes-record_size) return ArchiveError::limit;
        metadata+=record_size;
        if (u16(c.data()+34)) return ArchiveError::unsupported;
        const std::uint64_t local_offset=u32(c.data()+42);
        if (local_offset>=central) return ArchiveError::invalid;
        std::vector<std::uint8_t> name(name_size);
        if (!read_at(cursor+46,name.data(),name.size())) return ArchiveError::io;
        std::array<std::uint8_t,30> h{};
        if (!read_at(local_offset,h.data(),h.size())) return ArchiveError::invalid;
        const auto header_size=std::size_t(30)+u16(h.data()+26)+u16(h.data()+28);
        std::vector<std::uint8_t> header(header_size);
        if (!read_at(local_offset,header.data(),header.size())) return ArchiveError::invalid;
        ZipLocalEntry entry;
        const auto parsed=parse_zip_local(header.data(),header.size(),local_offset,central,entry);
        if (parsed==ZipError::unsupported) return ArchiveError::unsupported;
        if (parsed!=ZipError::ok) return ArchiveError::invalid;
        if (entry.name.size()!=name.size() || !std::equal(name.begin(),name.end(),reinterpret_cast<const std::uint8_t*>(entry.name.data())) ||
            entry.flags!=u16(c.data()+8) || entry.method!=u16(c.data()+10) || entry.crc!=u32(c.data()+16) ||
            entry.compressed_size!=u32(c.data()+20) || entry.size!=u32(c.data()+24)) return ArchiveError::invalid;
        entries_.push_back({std::move(entry),local_offset});cursor+=record_size;
    }
    if (cursor!=end_offset) return ArchiveError::invalid;
    // Native scan order is physical local-header order, not central order.
    std::sort(entries_.begin(),entries_.end(),[](const auto& a,const auto& b){return a.header_offset<b.header_offset;});
    std::uint64_t next=0;
    for (std::size_t i=0;i<entries_.size();++i) {
        if (entries_[i].header_offset!=next) return ArchiveError::invalid;
        next=entries_[i].local.next_offset;
        lookup_.push_back({zip_entry_key(entries_[i].local.name,options_.ignore_case,options_.ignore_paths),i});
    }
    if (next!=central) return ArchiveError::invalid;
    sort_zip_lookup(lookup_);return ArchiveError::ok;
}
std::size_t ZipArchive::find(const std::string& name) const {
    if (name.find('\0')!=std::string::npos) return missing; // checked C-string domain
    return find_zip_lookup(lookup_,zip_query_key(name,options_.ignore_case,options_.ignore_paths));
}
ArchiveError ZipArchive::read(const std::string& name,std::vector<std::uint8_t>& output) {
    auto index=find(name);if (index==missing) return ArchiveError::not_found;
    return read_index(index,output);
}
ArchiveError ZipArchive::read_index(std::size_t index,std::vector<std::uint8_t>& output) {
    if (index>=entries_.size()) return ArchiveError::not_found;
    const auto& entry=entries_[index].local;
    if (entry.compressed_size>limits_.compressed_bytes || entry.size>limits_.output_bytes) return ArchiveError::limit;
    std::vector<std::uint8_t> compressed(entry.compressed_size);
    if (!read_at(entry.data_offset,compressed.data(),compressed.size())) return ArchiveError::io;
    switch (decode_zip_payload(entry,compressed.data(),compressed.size(),limits_.output_bytes,output)) {
        case ZipPayloadError::ok:return ArchiveError::ok;
        case ZipPayloadError::checksum:return ArchiveError::checksum;
        case ZipPayloadError::limit:return ArchiveError::limit;
        case ZipPayloadError::unsupported:return ArchiveError::unsupported;
        default:return ArchiveError::payload;
    }
}
ArchiveError ResourcePackages::mount(const std::string& path,ZipArchiveOptions options,ZipArchiveLimits limits) {
    ZipArchive archive;auto error=archive.open(path,options,limits);
    if (error==ArchiveError::ok) archives_.push_back(std::move(archive));
    return error;
}
ArchiveError ResourcePackages::read(const std::string& name,std::vector<std::uint8_t>& output) {
    auto last=ArchiveError::not_found;
    for (auto& archive:archives_) {
        if (archive.find(name)!=missing) {
            last=archive.read(name,output);
            if (last==ArchiveError::ok) return last;
        }
    }
    return last;
}
}
