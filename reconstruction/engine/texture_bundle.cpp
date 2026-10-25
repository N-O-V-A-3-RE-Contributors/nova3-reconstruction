#include "texture_bundle.hpp"
#include "zip_archive.hpp"
#include <utility>
namespace nova3 {
TextureError read_texture_bundle(std::vector<std::uint8_t> bytes,TextureBundle& output){
    TextureBundle next;
    if(bytes.size()<4 || bytes[0]!='P' || bytes[1]!='K' || bytes[2]!=3 || bytes[3]!=4){
        if(read_etc1_pvr(bytes.data(),bytes.size(),next.rgb.image)!=PvrError::ok)return TextureError::image;
        next.rgb.bytes=std::move(bytes);
    }else{
        ZipArchive archive;if(archive.open_memory(std::move(bytes),{true,true})!=ArchiveError::ok)return TextureError::archive;
        std::vector<std::uint8_t> marker;
        if(archive.read("SPLIT",marker)!=ArchiveError::ok)return TextureError::unsupported;
        if(marker.size()!=4)return TextureError::split;
        next.alpha_mode=std::uint32_t(marker[0])|(std::uint32_t(marker[1])<<8)|(std::uint32_t(marker[2])<<16)|(std::uint32_t(marker[3])<<24);
        if(next.alpha_mode>=4)return TextureError::split;
        std::vector<ZipLookupEntry> names;
        for(std::size_t i=0;i<archive.entries().size();++i)names.push_back({zip_entry_key(archive.entries()[i].local.name,true,true),i});
        sort_zip_lookup(names);bool rgb=false,alpha=false;
        for(const auto& entry:names){
            if(!rgb && entry.key.compare(0,3,"rgb")==0)rgb=archive.read_index(entry.archive_index,next.rgb.bytes)==ArchiveError::ok;
            else if(!alpha && entry.key.compare(0,5,"alpha")==0)alpha=archive.read_index(entry.archive_index,next.alpha.bytes)==ArchiveError::ok;
        }
        if(!rgb || !alpha)return TextureError::split;
        if(read_etc1_pvr(next.rgb.bytes.data(),next.rgb.bytes.size(),next.rgb.image)!=PvrError::ok ||
           read_etc1_pvr(next.alpha.bytes.data(),next.alpha.bytes.size(),next.alpha.image)!=PvrError::ok)return TextureError::image;
        next.has_alpha=true;
    }
    output=std::move(next);return TextureError::ok;
}
}
