#include "scrambled_zip.hpp"
#include "scramble.hpp"
#include <cassert>
#include <vector>
int main(){
    std::vector<std::uint8_t> b(35);b[0]='S';b[1]='B';b[2]=27;b[4]=20;
    b[18]=4;b[22]=4;b[26]=1;b[30]='x';
    assert(nova3::unscramble(b.data()+4,27,"test.gla"));nova3::ZipLocalEntry entry;
    assert(nova3::parse_scrambled_local(b.data(),b.size(),0,b.size(),"test.gla",entry)==nova3::ZipError::ok);
    assert(entry.name=="x" && entry.size==4 && entry.data_offset==31 && entry.next_offset==35);
    const auto saved=entry.name;
    for(std::size_t n=0;n<31;++n){assert(nova3::parse_scrambled_local(b.data(),n,0,b.size(),"test.gla",entry)!=nova3::ZipError::ok);assert(entry.name==saved);}
    assert(nova3::parse_scrambled_local(b.data(),b.size(),0,b.size(),"",entry)==nova3::ZipError::unsupported);
    assert(nova3::parse_scrambled_local(b.data(),b.size(),0,b.size(),"wrong.gla",entry)!=nova3::ZipError::ok);
    b[2]=0xff;b[3]=0xff;assert(nova3::parse_scrambled_local(b.data(),b.size(),0,b.size(),"test.gla",entry)==nova3::ZipError::unsupported);
}
