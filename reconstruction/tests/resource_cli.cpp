#include "zip_archive.hpp"
#include "bres.hpp"
#include <fstream>
#include <iostream>
int main(int argc,char** argv){
    if(argc<5){std::cerr<<"usage: nova3_resource NAME OUTPUT --plain|--bres ARCHIVE...\n";return 2;}
    const std::string mode=argv[3];if(mode!="--plain" && mode!="--bres")return 2;
    nova3::ResourcePackages packages;
    for(int i=4;i<argc;++i){const auto error=packages.mount(argv[i]);if(error!=nova3::ArchiveError::ok){std::cerr<<"mount error "<<static_cast<int>(error)<<'\n';return 3;}}
    std::vector<std::uint8_t> output;const auto error=packages.read(argv[1],output);
    if(error!=nova3::ArchiveError::ok){std::cerr<<"read error "<<static_cast<int>(error)<<'\n';return 4;}
    if(mode=="--bres"){
        std::vector<std::uint8_t> relocated;const auto result=nova3::relocate_bres32(output.data(),output.size(),0x02000000,relocated);
        if(result!=nova3::BresError::ok){std::cerr<<"BRES error "<<static_cast<int>(result)<<'\n';return 5;}
        output=std::move(relocated);
    }
    std::ofstream out(argv[2],std::ios::binary);out.write(reinterpret_cast<const char*>(output.data()),static_cast<std::streamsize>(output.size()));
    if(!out)return 6;
    std::cout<<"loaded "<<output.size()<<" bytes\n";return 0;
}
