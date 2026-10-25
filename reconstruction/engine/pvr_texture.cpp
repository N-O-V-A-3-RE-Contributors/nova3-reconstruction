#include "pvr_texture.hpp"
#include <algorithm>
#include <limits>
#include <utility>
namespace nova3 {
namespace {
std::uint32_t word(const std::uint8_t* p){return std::uint32_t(p[0])|(std::uint32_t(p[1])<<8)|(std::uint32_t(p[2])<<16)|(std::uint32_t(p[3])<<24);}
std::uint32_t log2floor(std::uint32_t value){std::uint32_t result=0xffffffffu;while(value){++result;value>>=1;}return result;}
bool power2(std::uint32_t value){return value && !(value&(value-1));}
}
bool valid_pvr_v2_header(const std::uint8_t* p,std::size_t available) noexcept {
    if(!p || available<52 || word(p)!=52 || p[44]!='P' || p[45]!='V' || p[46]!='R' || p[47]!='!')return false;
    const auto flags=word(p+16),levels=word(p+12),surfaces=word(p+48);
    if((flags&0x100u) && !levels)return false;
    if((flags&0x1000u) && surfaces!=6)return false;
    if(flags&0x100u){
        const auto depth=flags&0x4000u?surfaces:1u;
        if(levels!=std::max({log2floor(word(p+4)),log2floor(word(p+8)),log2floor(depth)}))return false;
    }
    return true;
}
PvrError read_etc1_pvr(const std::uint8_t* p,std::size_t size,Etc1Texture& output){
    if(!valid_pvr_v2_header(p,size))return PvrError::header;
    const auto flags=word(p+16),data_size=word(p+20),levels=word(p+12),bits=word(p+24),surfaces=word(p+48);
    if((flags&~0x100u)!=0x36u || bits!=4 || surfaces!=1 || (!(flags&0x100u) && levels))return PvrError::unsupported;
    const auto width=word(p+8),height=word(p+4);
    if(!power2(width) || !power2(height) || width>16384 || height>16384)return PvrError::unsupported;
    if(data_size!=size-52)return PvrError::bounds;
    Etc1Texture next;next.width=width;next.height=height;std::size_t offset=52;
    auto w=width,h=height;
    for(std::uint32_t i=0;i<=levels;++i){
        const std::uint64_t bytes=std::uint64_t((w+3)/4)*((h+3)/4)*8;
        if(bytes>size-offset)return PvrError::bounds;
        next.mips.push_back({w,h,offset,static_cast<std::size_t>(bytes)});offset+=static_cast<std::size_t>(bytes);
        w=std::max(1u,w/2);h=std::max(1u,h/2);
    }
    if(offset!=size)return PvrError::bounds;
    output=std::move(next);return PvrError::ok;
}
}
