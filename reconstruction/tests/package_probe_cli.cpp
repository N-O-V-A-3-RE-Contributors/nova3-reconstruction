#include "zip_archive.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
int main(int argc,char** argv){
    if(argc<3)return 2;
    nova3::ZipArchive archive;auto error=archive.open(argv[1]);
    if(error!=nova3::ArchiveError::ok){std::cerr<<"mount error "<<static_cast<int>(error)<<'\n';return 3;}
    if(std::string(argv[2])=="--list"){
        const char* hex="0123456789abcdef";std::cout<<'[';
        for(std::size_t i=0;i<archive.entries().size();++i){
            if(i)std::cout<<',';const auto& e=archive.entries()[i];const auto& a=e.local;
            std::cout<<"{\"name_hex\":\"";for(unsigned char c:a.name)std::cout<<hex[c>>4]<<hex[c&15];
            std::cout<<"\",\"offset\":"<<e.header_offset<<",\"data_offset\":"<<a.data_offset<<",\"compressed_size\":"<<a.compressed_size<<",\"size\":"<<a.size<<",\"crc32\":"<<a.crc<<",\"method\":"<<a.method<<",\"flags\":"<<a.flags<<'}';
        }
        std::cout<<"]\n";return 0;
    }
    for(int i=3;i<argc;++i){
        std::vector<std::uint8_t> output;error=archive.read(argv[i],output);
        if(error!=nova3::ArchiveError::ok){std::cerr<<"read error "<<static_cast<int>(error)<<'\n';return 4;}
        std::ofstream file(std::filesystem::path(argv[2])/(std::to_string(i-3)+".asset"),std::ios::binary);
        if(!output.empty())file.write(reinterpret_cast<const char*>(output.data()),static_cast<std::streamsize>(output.size()));
        if(!file)return 5;
    }
    return 0;
}
