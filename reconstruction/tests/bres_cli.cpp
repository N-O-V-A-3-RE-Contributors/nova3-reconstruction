#include "bres.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc,char** argv) {
    if (argc!=4) return 2;
    std::uint64_t base;
    try { base=std::stoull(argv[2],nullptr,0); } catch(...) { return 2; }
    if (base>0xffffffffu) return 2;
    std::ifstream in(argv[1],std::ios::binary);if(!in)return 2;
    in.seekg(0,std::ios::end);const auto length=in.tellg();
    if(length<0 || length>64*1024*1024)return 2;
    in.seekg(0);std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));
    if(length && !in.read(reinterpret_cast<char*>(bytes.data()),length))return 2;
    std::vector<std::uint8_t> out;const auto error=nova3::relocate_bres32(bytes.data(),bytes.size(),static_cast<std::uint32_t>(base),out);
    if(error!=nova3::BresError::ok){std::cerr<<"BRES error "<<static_cast<int>(error)<<'\n';return 3;}
    std::ofstream file(argv[3],std::ios::binary);file.write(reinterpret_cast<const char*>(out.data()),static_cast<std::streamsize>(out.size()));
    return file?0:4;
}
