#include "etc1.hpp"
#include <array>
#include <cassert>
#include <limits>
int main(){
    std::array<std::uint8_t,8> block{};std::vector<std::uint8_t> out;
    assert(nova3::decode_etc1(block.data(),8,4,4,out)==nova3::Etc1Error::ok);
    assert(out==std::vector<std::uint8_t>(48,2));
    block[0]=0xf0;block[1]=0x0f;block[2]=0x88;
    assert(nova3::decode_etc1(block.data(),8,4,4,out)==nova3::Etc1Error::ok);
    assert(out[0]==255 && out[1]==2 && out[2]==138);
    assert(out[6]==2 && out[7]==255 && out[8]==138);
    auto saved=out;block[0]=4;block[3]=2;
    assert(nova3::decode_etc1(block.data(),8,4,4,out)==nova3::Etc1Error::invalid_block && out==saved);
    block.fill(0);
    assert(nova3::decode_etc1(block.data(),7,4,4,out)==nova3::Etc1Error::bounds && out==saved);
    assert(nova3::decode_etc1(block.data(),8,4,4,out,false,7)==nova3::Etc1Error::bounds);
    assert(nova3::decode_etc1(block.data(),8,4,4,out,false,0,15)==nova3::Etc1Error::limit);
    assert(nova3::decode_etc1(block.data(),8,0,4,out)==nova3::Etc1Error::bounds);
    assert(nova3::decode_etc1(block.data(),8,1,1,out)==nova3::Etc1Error::ok && out==std::vector<std::uint8_t>(3,2));
    assert(nova3::decode_etc1(block.data(),8,2,2,out,true)==nova3::Etc1Error::ok && out==std::vector<std::uint8_t>(12,2));
    block={0xa3,0x5c,0x7e,0x55,0xab,0xcd,0x12,0x34};
    std::vector<std::uint8_t> full,small,flipped;
    assert(nova3::decode_etc1(block.data(),8,4,4,full)==nova3::Etc1Error::ok);
    assert(nova3::decode_etc1(block.data(),8,2,2,small)==nova3::Etc1Error::ok);
    for(unsigned y=0;y<2;++y)for(unsigned x=0;x<6;++x)assert(small[y*6+x]==full[y*12+x]);
    assert(nova3::decode_etc1(block.data(),8,4,4,flipped,true)==nova3::Etc1Error::ok);
    for(unsigned y=0;y<4;++y)for(unsigned x=0;x<12;++x)assert(flipped[y*12+x]==full[(3-y)*12+x]);
    std::uint32_t rng=0x45544331;
    for(unsigned n=0;n<1000;++n){
        for(auto& byte:block){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;byte=static_cast<std::uint8_t>(rng);}
        auto before=out;const auto error=nova3::decode_etc1(block.data(),n%9,n%5,(n/5)%5,out);
        if(error!=nova3::Etc1Error::ok)assert(before==out);
    }
    assert(nova3::decode_etc1(block.data(),8,4,8,out,false,std::numeric_limits<std::size_t>::max())==nova3::Etc1Error::bounds);
}
