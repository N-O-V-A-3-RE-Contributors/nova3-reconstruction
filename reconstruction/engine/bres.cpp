#include "bres.hpp"
#include <utility>
namespace nova3 {
namespace {
std::uint32_t u32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1])<<8) |
           (std::uint32_t(p[2])<<16) | (std::uint32_t(p[3])<<24);
}
void put32(std::uint8_t* p,std::uint32_t v) {
    for (unsigned i=0;i<4;++i) p[i]=static_cast<std::uint8_t>(v>>(i*8));
}
}
BresError relocate_bres32(const std::uint8_t* bytes,std::size_t size,
                         std::uint32_t base,std::vector<std::uint8_t>& output) {
    // The native routine reads fields through +0x38 before its magic test.
    if (!bytes || size<60) return BresError::truncated;
    if (bytes[0]!='B' || bytes[1]!='R' || bytes[2]!='E' || bytes[3]!='S') return BresError::magic;
    std::vector<std::uint8_t> next(bytes,bytes+size);
    if (!(next[7]&0x80u)) {
        const auto count=u32(next.data()+16),table=u32(next.data()+24);
        if (table>size || count>(size-table)/4) return BresError::table_bounds;
        if (count && table<60) return BresError::unsupported_alias;
        next[7]|=0x80u;
        put32(next.data()+24,base+table);
        for (std::uint32_t i=0;i<count;++i) {
            auto* entry=next.data()+table+static_cast<std::size_t>(i)*4;
            const auto slot=u32(entry);
            if (slot>size || size-slot<4) return BresError::slot_bounds;
            if (i && ((slot<20 && slot+4>16) || (slot<28 && slot+4>24))) return BresError::unsupported_alias;
            put32(entry,base+slot);
            // First entry has already been fixed up above. Read/mutate each
            // subsequent target in loop order; repeated slots add base again.
            if (i) put32(next.data()+slot,base+u32(next.data()+slot));
        }
    }
    output=std::move(next);return BresError::ok;
}
}
