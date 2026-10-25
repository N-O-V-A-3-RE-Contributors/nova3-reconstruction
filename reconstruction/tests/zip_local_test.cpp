#include "zip_local.hpp"
#include <cstdlib>
#include <iostream>
#include <vector>
static void require(bool ok) { if (!ok) { std::cerr << "ZIP assertion failed\n"; std::exit(1); } }
int main() {
    std::vector<std::uint8_t> b(35);b[0]='P';b[1]='K';b[2]=3;b[3]=4;b[26]=5;
    const char name[]="a.bin";for(int i=0;i<5;++i)b[30+i]=name[i];
    nova3::ZipLocalEntry entry;entry.name="unchanged";
    for (std::size_t n=0;n<b.size();++n) require(nova3::parse_zip_local(b.data(),n,0,35,entry)==nova3::ZipError::truncated);
    require(entry.name=="unchanged");
    require(nova3::parse_zip_local(b.data(),35,0,35,entry)==nova3::ZipError::ok);
    require(entry.name=="a.bin" && entry.data_offset==35 && entry.next_offset==35);
    require(nova3::parse_zip_local(b.data(),35,1,35,entry)==nova3::ZipError::bounds);
    b[6]=8;require(nova3::parse_zip_local(b.data(),35,0,35,entry)==nova3::ZipError::unsupported);b[6]=0;
    b[30]='.';b[31]='.';b[32]='/';require(nova3::parse_zip_local(b.data(),35,0,35,entry)==nova3::ZipError::unsafe_name);
    b[0]=0;require(nova3::parse_zip_local(b.data(),35,0,35,entry)==nova3::ZipError::signature);
    require(nova3::parse_zip_local(nullptr,35,0,35,entry)==nova3::ZipError::truncated);
    // Deterministic malformed-header corpus exercises lengths, flags and bounds
    // under sanitizers. Success must stay within declared archive boundaries;
    // every error must preserve the caller's prior output object.
    std::uint32_t random = 0x4e4f5641;
    for (int iteration=0;iteration<20000;++iteration) {
        std::vector<std::uint8_t> input(96,0);
        for (auto& value:input) { random=random*1664525u+1013904223u;value=static_cast<std::uint8_t>(random>>24); }
        if (iteration%2) { input[0]='P';input[1]='K';input[2]=3;input[3]=4; }
        nova3::ZipLocalEntry output;output.name="sentinel";
        const auto available=static_cast<std::size_t>(iteration%97);
        const auto error=nova3::parse_zip_local(input.data(),available,iteration%8,100000,output);
        if (error==nova3::ZipError::ok) require(output.data_offset<=output.next_offset && output.next_offset<=100000);
        else require(output.name=="sentinel");
    }
    std::cout << "ZIP supported-header/truncation/traversal/descriptor/bounds tests passed\n";
}
