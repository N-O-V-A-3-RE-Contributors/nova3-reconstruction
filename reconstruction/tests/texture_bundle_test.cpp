#include "texture_bundle.hpp"
#include "crc32.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
using Bytes=std::vector<std::uint8_t>;
static void require(bool b){if(!b){std::cerr<<"texture bundle assertion failed\n";std::exit(1);}}
static void put(Bytes& b,std::size_t at,std::uint32_t n){for(unsigned i=0;i<4;++i)b[at+i]=static_cast<std::uint8_t>(n>>(i*8));}
static void u16(Bytes& b,unsigned n){b.push_back(n&255);b.push_back((n>>8)&255);}
static void u32(Bytes& b,std::uint32_t n){u16(b,n&65535);u16(b,n>>16);}
static Bytes pvr(){Bytes b(60);for(auto p:std::vector<std::pair<unsigned,unsigned>>{{0,52},{4,4},{8,4},{16,0x36},{20,8},{24,4},{44,0x21525650},{48,1}})put(b,p.first,p.second);return b;}
static Bytes zip(const std::vector<std::pair<std::string,Bytes>>& entries){
 Bytes b,c;
 for(const auto& e:entries){
  auto start=b.size();auto size=e.second.size();auto crc=nova3::compute_crc(e.second.data(),size);
  u32(b,0x04034b50);u16(b,20);u16(b,0);u16(b,0);u32(b,0);u32(b,crc);u32(b,size);u32(b,size);u16(b,e.first.size());u16(b,0);b.insert(b.end(),e.first.begin(),e.first.end());b.insert(b.end(),e.second.begin(),e.second.end());
  u32(c,0x02014b50);u16(c,20);u16(c,20);u16(c,0);u16(c,0);u32(c,0);u32(c,crc);u32(c,size);u32(c,size);u16(c,e.first.size());u16(c,0);u16(c,0);u16(c,0);u16(c,0);u32(c,0);u32(c,start);c.insert(c.end(),e.first.begin(),e.first.end());
 }
 auto start=b.size();b.insert(b.end(),c.begin(),c.end());u32(b,0x06054b50);u16(b,0);u16(b,0);u16(b,entries.size());u16(b,entries.size());u32(b,c.size());u32(b,start);u16(b,0);return b;
}
int main(){
 auto image=pvr();nova3::TextureBundle out;
 require(nova3::read_texture_bundle(image,out)==nova3::TextureError::ok && !out.has_alpha && out.rgb.image.width==4);
 auto packed=zip({{"dir/Alpha.pvr",image},{"RGB.PVR",image},{"SPLIT",Bytes{3,0,0,0}}});
 require(nova3::read_texture_bundle(packed,out)==nova3::TextureError::ok && out.has_alpha && out.alpha_mode==3 && out.rgb.bytes==image && out.alpha.bytes==image);
 require(nova3::read_texture_bundle(zip({{"rgb.pvr",image},{"alpha.pvr",image},{"SPLIT",Bytes{4,0,0,0}}}),out)==nova3::TextureError::split);
 require(out.has_alpha && out.alpha_mode==3);
 require(nova3::read_texture_bundle(zip({{"rgb.pvr",image},{"SPLIT",Bytes{0,0,0,0}}}),out)==nova3::TextureError::split);
 require(nova3::read_texture_bundle(zip({{"rgb.pvr",image}}),out)==nova3::TextureError::unsupported);
 auto bad=image;bad[44]=0;
 require(nova3::read_texture_bundle(zip({{"rgb.pvr",bad},{"alpha.pvr",image},{"SPLIT",Bytes{0,0,0,0}}}),out)==nova3::TextureError::image);
 require(out.rgb.bytes==image && out.alpha_mode==3);
 std::cout<<"direct PVR, nested RGB/alpha, native case/path selection, modes and malformed bundles passed\n";
}
