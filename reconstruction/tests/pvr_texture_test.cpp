#include "pvr_texture.hpp"
#include <cstdlib>
#include <iostream>
#include <vector>
static void require(bool ok){if(!ok)std::exit(1);}
static void put(std::vector<std::uint8_t>& b,std::size_t p,std::uint32_t n){for(unsigned i=0;i<4;++i)b[p+i]=static_cast<std::uint8_t>(n>>(i*8));}
int main(){
 std::vector<std::uint8_t> b(76,0);put(b,0,52);put(b,4,4);put(b,8,4);put(b,12,2);put(b,16,0x136);put(b,20,24);put(b,24,4);put(b,44,0x21525650);put(b,48,1);
 nova3::Etc1Texture out;require(nova3::read_etc1_pvr(b.data(),b.size(),out)==nova3::PvrError::ok);
 require(out.width==4 && out.mips.size()==3 && out.mips[0].offset==52 && out.mips[2].size==8);
 auto bad=b;put(bad,12,1);require(!nova3::valid_pvr_v2_header(bad.data(),bad.size()));
 bad=b;put(bad,20,23);require(nova3::read_etc1_pvr(bad.data(),bad.size(),out)==nova3::PvrError::bounds && out.mips.size()==3);
 bad=b;put(bad,16,0x1136);require(!nova3::valid_pvr_v2_header(bad.data(),bad.size()));
 for(std::size_t n=0;n<52;++n)require(!nova3::valid_pvr_v2_header(b.data(),n));
 b[44]=0;require(nova3::read_etc1_pvr(b.data(),b.size(),out)==nova3::PvrError::header);
 std::cout<<"PVRv2 native header rules, ETC1 mip bounds and atomic errors passed\n";
}
