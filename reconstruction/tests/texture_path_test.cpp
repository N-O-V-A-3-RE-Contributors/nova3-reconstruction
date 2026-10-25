#include "texture_path.hpp"
#include <cassert>
int main(){
    assert(nova3::etc_texture_path("SF_Intro_Building_02_Wall.tga")=="SF_Intro_Building_02_Wall.etc");
    assert(nova3::etc_texture_path("a.tga/x.psd/z")=="a.tga/x.etc");
    assert(nova3::etc_texture_path("a.TGA.trailing")=="a.etc");
    assert(nova3::etc_texture_path("a.Tga")=="a.Tga");
    assert(nova3::etc_texture_path("a.etc")=="a.etc");
    assert(nova3::etc_texture_path("a.pvx.dds")=="a.etc");
    assert(nova3::etc_texture_path(".psd")==".etc");
    assert(nova3::etc_texture_path("").empty());
    nova3::ResourcePackages packages;nova3::TextureBundle out;out.alpha_mode=123;
    auto error=nova3::load_etc_texture(packages,"none.tga",out);
    assert(error.archive==nova3::ArchiveError::not_found && out.alpha_mode==123);
}
