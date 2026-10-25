#include "bres.hpp"
#include <cstdlib>
#include <iostream>
#include <vector>
static void require(bool b) { if (!b) { std::cerr << "BRES test failed\n";std::exit(1); } }
static void put(std::vector<std::uint8_t>& b,std::size_t p,std::uint32_t v) { for(unsigned i=0;i<4;++i)b[p+i]=static_cast<std::uint8_t>(v>>(i*8)); }
static std::uint32_t get(const std::vector<std::uint8_t>& b,std::size_t p) { return b[p]|(std::uint32_t(b[p+1])<<8)|(std::uint32_t(b[p+2])<<16)|(std::uint32_t(b[p+3])<<24); }
int main() {
    std::vector<std::uint8_t> b(128,0);b[0]='B';b[1]='R';b[2]='E';b[3]='S';
    put(b,16,3);put(b,24,60);put(b,60,24);put(b,64,28);put(b,68,28);
    std::vector<std::uint8_t> out{42};
    require(nova3::relocate_bres32(b.data(),b.size(),0x02000000,out)==nova3::BresError::ok);
    require(get(out,24)==0x0200003c && get(out,28)==0x04000000 && out[7]==0x80);
    auto prior=out;require(nova3::relocate_bres32(out.data(),out.size(),0x02000000,out)==nova3::BresError::ok && out==prior);
    for(std::size_t n=0;n<60;++n) require(nova3::relocate_bres32(b.data(),n,0,out)==nova3::BresError::truncated);
    require(out==prior);
    put(b,16,0xffffffff);require(nova3::relocate_bres32(b.data(),b.size(),0,out)==nova3::BresError::table_bounds);
    put(b,16,3);put(b,68,127);require(nova3::relocate_bres32(b.data(),b.size(),0,out)==nova3::BresError::slot_bounds && out==prior);
    put(b,68,16);require(nova3::relocate_bres32(b.data(),b.size(),0,out)==nova3::BresError::unsupported_alias && out==prior);
    b[0]=0;require(nova3::relocate_bres32(b.data(),b.size(),0,out)==nova3::BresError::magic);
    std::cout << "BRES relocation, zero/duplicate targets, idempotence, bounds/atomic errors passed\n";
}
