#include "texture_path.hpp"
#include <utility>
namespace nova3 {
std::string etc_texture_path(const std::string& name){
    for(const auto* extension:{".psd",".tga",".TGA",".pvx",".dds"}){
        const auto at=name.find(extension);
        if(at!=std::string::npos)return name.substr(0,at)+".etc";
    }
    return name;
}
TextureResourceResult load_etc_texture(ResourcePackages& packages,const std::string& name,TextureBundle& output){
    std::vector<std::uint8_t> bytes;const auto candidate=etc_texture_path(name);
    auto error=packages.read(candidate,bytes);
    if(error!=ArchiveError::ok && candidate!=name)error=packages.read(name,bytes);
    if(error!=ArchiveError::ok)return {error,TextureError::ok};
    return {ArchiveError::ok,read_texture_bundle(std::move(bytes),output)};
}
}
