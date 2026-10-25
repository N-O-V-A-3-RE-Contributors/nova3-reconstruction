#include "scrambled_zip.hpp"
#include "scramble.hpp"
#include <vector>
namespace nova3 {
ZipError parse_scrambled_local(const std::uint8_t* p,std::size_t available,
                              std::uint64_t offset,std::uint64_t archive_size,
                              const char* key,ZipLocalEntry& output){
    if(!p || available<4)return ZipError::truncated;
    if(p[0]!='S' || p[1]!='B')return ZipError::signature;
    const auto length=std::size_t(p[2])|(std::size_t(p[3])<<8);
    if(length<26 || length>1087)return ZipError::unsupported;
    if(length>available-4)return ZipError::truncated;
    std::vector<std::uint8_t> header(p,p+length+4);
    if(!unscramble(header.data()+4,length,key))return ZipError::unsupported;
    const auto names=std::size_t(header[26])|(std::size_t(header[27])<<8);
    const auto extra=std::size_t(header[28])|(std::size_t(header[29])<<8);
    if(length!=26+names+extra)return ZipError::unsupported;
    header[0]='P';header[1]='K';header[2]=3;header[3]=4;
    return parse_zip_local(header.data(),header.size(),offset,archive_size,output);
}
}
