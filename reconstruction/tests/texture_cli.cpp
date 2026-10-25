#include "pvr_texture.hpp"
#include "etc1.hpp"
#include "crc32.hpp"
#include "texture_bundle.hpp"
#include "texture_path.hpp"
#include "zip_archive.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
int main(int argc,char** argv){
 if(argc==3 && std::string(argv[1])=="--header"){
  std::ifstream in(argv[2],std::ios::binary);std::vector<std::uint8_t> b((std::istreambuf_iterator<char>(in)),{});
  if(!in.eof() && in.fail())return 2;std::cout<<(nova3::valid_pvr_v2_header(b.data(),b.size())?1:0)<<'\n';return 0;
 }
 if(argc==4){
  nova3::ResourcePackages packages;if(packages.mount(argv[1])!=nova3::ArchiveError::ok)return 3;
  nova3::TextureBundle texture;const auto loaded=nova3::load_etc_texture(packages,argv[2],texture);
  if(loaded.archive!=nova3::ArchiveError::ok)return 4;if(loaded.texture!=nova3::TextureError::ok)return 5;
  const auto save=[&](const nova3::TexturePlane& plane,const std::string& suffix){
   const auto& m=plane.image.mips.front();std::vector<std::uint8_t> rgb;
   if(nova3::decode_etc1(plane.bytes.data()+m.offset,m.size,m.width,m.height,rgb)!=nova3::Etc1Error::ok)return false;
   std::ofstream out(std::string(argv[3])+suffix,std::ios::binary);out<<"P6\n"<<m.width<<' '<<m.height<<"\n255\n";
   out.write(reinterpret_cast<const char*>(rgb.data()),rgb.size());return bool(out);
  };
  if(!save(texture.rgb,".rgb.ppm") || (texture.has_alpha && !save(texture.alpha,".alpha.ppm")))return 6;
  return 0;
 }
 const bool pixels=argc==3 && std::string(argv[2])=="--pixels";
 if(argc!=2 && !pixels)return 2;nova3::ZipArchive archive;if(archive.open(argv[1])!=nova3::ArchiveError::ok)return 3;
 std::cout<<'[';bool comma=false;
 for(std::size_t i=0;i<archive.entries().size();++i){const auto& name=archive.entries()[i].local.name;
  if(name.size()<4 || name.substr(name.size()-4)!=".etc")continue;
  std::vector<std::uint8_t> bytes;if(archive.read_index(i,bytes)!=nova3::ArchiveError::ok)return 4;
  nova3::TextureBundle texture;auto error=nova3::read_texture_bundle(std::move(bytes),texture);
  if(comma)std::cout<<',';comma=true;
  std::cout<<"{\"archive_index\":"<<i;
  if(error!=nova3::TextureError::ok){std::cout<<",\"error\":"<<static_cast<int>(error)<<'}';continue;}
  std::cout<<",\"alpha_mode\":"<<texture.alpha_mode<<",\"planes\":[";
  const auto print=[pixels](const nova3::TexturePlane& plane){
   std::cout<<"{\"width\":"<<plane.image.width<<",\"height\":"<<plane.image.height<<",\"bytes\":"<<plane.bytes.size()<<",\"mips\":[";
   for(std::size_t j=0;j<plane.image.mips.size();++j){if(j)std::cout<<',';const auto& m=plane.image.mips[j];std::cout<<"["<<m.width<<','<<m.height<<','<<m.offset<<','<<m.size<<']';}
   std::cout<<"]";
   if(pixels){std::cout<<",\"rgb_crc32\":[";
    for(std::size_t j=0;j<plane.image.mips.size();++j){
     if(j)std::cout<<',';const auto& m=plane.image.mips[j];std::vector<std::uint8_t> rgb;
     const auto error=nova3::decode_etc1(plane.bytes.data()+m.offset,m.size,m.width,m.height,rgb);
     if(error!=nova3::Etc1Error::ok){std::cout<<"null";continue;}
     std::cout<<nova3::compute_crc(rgb.data(),rgb.size());
    }std::cout<<']';
   }
   std::cout<<'}';
  };
  print(texture.rgb);if(texture.has_alpha){std::cout<<',';print(texture.alpha);}std::cout<<"]}";
 }
 std::cout<<"]\n";return 0;
}
