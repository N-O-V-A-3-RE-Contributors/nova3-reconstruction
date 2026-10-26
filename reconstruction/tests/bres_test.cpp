#include "bres.hpp"
#include <algorithm>
#include <array>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <limits>
#include <vector>

namespace {
using Bytes=std::vector<std::uint8_t>;
using nova3::BresError;
constexpr auto max32=std::numeric_limits<std::uint32_t>::max();

void require(bool condition,const char* message) {
    if (!condition) {
        std::cerr<<"BRES test failed: "<<message<<'\n';
        std::exit(1);
    }
}
void put(Bytes& b,std::size_t p,std::uint32_t v) {
    for (unsigned i=0;i<4;++i) b.at(p+i)=static_cast<std::uint8_t>(v>>(i*8));
}
Bytes fixture(std::size_t size=128,std::uint32_t count=3,std::uint32_t table=60) {
    Bytes b(size,0);
    b.at(0)='B';b.at(1)='R';b.at(2)='E';b.at(3)='S';
    put(b,16,count);put(b,24,table);
    return b;
}
void literal(Bytes& b,std::size_t p,std::initializer_list<std::uint8_t> bytes) {
    for (const auto byte:bytes) b.at(p++)=byte;
}
void expect_error(const Bytes& input,BresError error) {
    Bytes out{0x19,0x87,0x65};
    const auto prior=out;
    require(nova3::relocate_bres32(input.data(),input.size(),0x01020304,out)==error,
            "expected error classification");
    require(out==prior,"error must preserve separate output");
    auto in_place=input;
    require(nova3::relocate_bres32(in_place.data(),in_place.size(),0x01020304,in_place)==error,
            "expected in-place error classification");
    require(in_place==input,"error must preserve aliased input/output");
}

void unaligned_little_endian() {
    // Entirely synthetic bytes, not extracted assets or an original oracle.
    // Table/targets and the input pointer deliberately have odd alignment.
    auto input=fixture(104,3,61);
    input[7]=0x35;
    put(input,61,24);put(input,65,89);put(input,69,100);
    put(input,89,0xfefdfcfb);put(input,100,max32);
    auto expected=input;
    expected[7]=0xb5;
    literal(expected,24,{0x41,0x03,0x02,0x01});
    literal(expected,61,{0x1c,0x03,0x02,0x01});
    literal(expected,65,{0x5d,0x03,0x02,0x01});
    literal(expected,69,{0x68,0x03,0x02,0x01});
    literal(expected,89,{0xff,0xff,0xff,0xff});
    literal(expected,100,{0x03,0x03,0x02,0x01});
    Bytes storage(input.size()+2,0xa5);
    std::copy(input.begin(),input.end(),storage.begin()+1);
    const auto prior=storage;
    Bytes out;
    require(nova3::relocate_bres32(storage.data()+1,input.size(),0x01020304,out)==BresError::ok,
            "unaligned relocation");
    require(out==expected,"literal little-endian bytes, slot offsets, and unchanged bytes");
    require(nova3::relocate_bres32(out.data(),out.size(),max32,out)==BresError::ok &&
            out==expected,"successful output is unchanged when relocated again at another base");
    require(storage==prior,"separate input is read-only");
    auto in_place=input;
    require(nova3::relocate_bres32(in_place.data(),in_place.size(),0x01020304,in_place)==BresError::ok &&
            in_place==expected,"unrelocated in-place input is copied before writes");
    // A subrange of output is also a valid input; its prefix/suffix are excluded.
    require(nova3::relocate_bres32(storage.data()+1,input.size(),0x01020304,storage)==BresError::ok &&
            storage==expected,"input subrange may alias output");
}

void wrapping_and_repeated_slots() {
    auto input=fixture(128,4);
    put(input,60,24);put(input,64,92);put(input,68,92);put(input,72,96);
    put(input,96,0xfffffff0);
    auto expected=input;
    expected[7]=0x80;
    put(expected,24,0x5c);put(expected,60,0x38);
    put(expected,64,0x7c);put(expected,68,0x7c);put(expected,72,0x80);
    put(expected,92,0x40);put(expected,96,0x10);
    Bytes out;
    require(nova3::relocate_bres32(input.data(),input.size(),0x20,out)==BresError::ok &&
            out==expected,"duplicate slots add base twice; zero target still relocated");

    input=fixture(128,3);
    put(input,60,24);put(input,64,92);put(input,68,92);put(input,92,max32);
    expected=input;expected[7]=0x80;
    put(expected,24,0x3a);put(expected,60,0x16);
    put(expected,64,0x5a);put(expected,68,0x5a);put(expected,92,0xfffffffb);
    require(nova3::relocate_bres32(input.data(),input.size(),0xfffffffe,out)==BresError::ok &&
            out==expected,"header, entries and repeated targets use wrapping ARM32 sums");

    // There is no special null-slot encoding. Offset zero names the magic word.
    input=fixture(128,2);put(input,60,24);put(input,64,0);
    expected=input;expected[7]=0x80;
    literal(expected,0,{'C','R','E','S'});
    put(expected,24,61);put(expected,60,25);put(expected,64,1);
    require(nova3::relocate_bres32(input.data(),input.size(),1,out)==BresError::ok &&
            out==expected,"zero slot is a buffer offset, not a null pointer");

    // Slots immediately adjacent to count/table fields must not be rejected
    // by the partial-overlap guard. The guard covers bytes, not alignment.
    input=fixture(128,4);
    put(input,60,24);put(input,64,12);put(input,68,20);put(input,72,28);
    put(input,12,max32);put(input,20,0x7fffffff);put(input,28,0xff00ff00);
    expected=input;expected[7]=0x80;
    put(expected,24,61);put(expected,60,25);put(expected,64,13);
    put(expected,68,21);put(expected,72,29);
    put(expected,12,0);put(expected,20,0x80000000);put(expected,28,0xff00ff01);
    require(nova3::relocate_bres32(input.data(),input.size(),1,out)==BresError::ok &&
            out==expected,"nonoverlapping header targets remain supported");
}

void sequential_table_aliases() {
    auto input=fixture(128,3);
    put(input,60,24);put(input,64,68);put(input,68,92);put(input,93,7);
    auto expected=input;expected[7]=0x80;
    put(expected,24,61);put(expected,60,25);put(expected,64,69);
    put(expected,68,94);put(expected,93,8);
    Bytes out;
    require(nova3::relocate_bres32(input.data(),input.size(),1,out)==BresError::ok &&
            out==expected,"target may modify a future slot offset before it is read");

    input=fixture(128,3);put(input,60,24);put(input,64,64);put(input,68,60);
    expected=input;expected[7]=0x80;
    put(expected,24,61);put(expected,60,26);put(expected,64,66);put(expected,68,61);
    require(nova3::relocate_bres32(input.data(),input.size(),1,out)==BresError::ok &&
            out==expected,"self slot reads after entry write; earlier entry may be a target");

    // The first slot is bounds-checked, but never dereferenced/incremented.
    input=fixture(128,1);put(input,60,16);
    expected=input;expected[7]=0x80;put(expected,24,61);put(expected,60,17);
    require(nova3::relocate_bres32(input.data(),input.size(),1,out)==BresError::ok &&
            out==expected,"first slot may name a loop-control field without modifying it");

    // A later target corrupts an unread entry. Validate its new value, with
    // no partial output committed even though earlier relocations succeeded.
    input=fixture(128,3);put(input,60,24);put(input,64,68);put(input,68,92);
    expect_error(input,BresError::slot_bounds);
}

void bounds_and_error_atomicity() {
    auto input=fixture();put(input,60,24);put(input,64,92);put(input,68,124);
    Bytes out{42};
    const auto prior=out;
    for (std::size_t size=0;size<60;++size) {
        // Exact allocations let ASan detect reads beyond each short input.
        const Bytes short_input(input.begin(),input.begin()+static_cast<Bytes::difference_type>(size));
        expect_error(short_input,BresError::truncated);
    }
    for (const auto size:std::array<std::size_t,3>{0,60,std::numeric_limits<std::size_t>::max()}) {
        require(nova3::relocate_bres32(nullptr,size,0,out)==BresError::truncated && out==prior,
                "null input rejected before any pointer arithmetic");
    }
    for (std::size_t i=0;i<4;++i) {
        auto bad=input;bad[i]^=1;expect_error(bad,BresError::magic);
    }
    for (const auto count:std::array<std::uint32_t,3>{18,0x40000000,max32}) {
        auto bad=input;put(bad,16,count);expect_error(bad,BresError::table_bounds);
    }
    for (const auto offset:std::array<std::uint32_t,4>{117,129,0xfffffffc,max32}) {
        auto bad=input;put(bad,24,offset);expect_error(bad,BresError::table_bounds);
    }
    for (std::uint32_t table=0;table<60;++table) {
        auto bad=fixture(128,1,table);expect_error(bad,BresError::unsupported_alias);
    }
    for (const auto slot:std::array<std::uint32_t,7>{125,126,127,128,129,0xfffffffc,max32}) {
        auto bad=input;put(bad,68,slot);expect_error(bad,BresError::slot_bounds);
        put(bad,60,slot);expect_error(bad,BresError::slot_bounds);
    }
    for (std::uint32_t slot=0;slot<=28;++slot) {
        if ((slot>=13 && slot<=19) || (slot>=21 && slot<=27)) {
            auto bad=input;put(bad,64,slot);expect_error(bad,BresError::unsupported_alias);
        }
    }

    // Exact table and target ends are valid; one byte less is invalid.
    input=fixture(68,2);put(input,60,24);put(input,64,64);
    require(nova3::relocate_bres32(input.data(),input.size(),0,out)==BresError::ok,
            "table and target may end exactly at buffer end");
    input.pop_back();expect_error(input,BresError::table_bounds);
    input=fixture(128,2);put(input,60,24);put(input,64,124);
    require(nova3::relocate_bres32(input.data(),input.size(),0,out)==BresError::ok,
            "last full slot is valid");
    input.pop_back();expect_error(input,BresError::slot_bounds);
}

void empty_and_already_relocated() {
    for (const auto table:std::array<std::uint32_t,4>{0,24,59,60}) {
        const auto input=fixture(60,0,table);
        auto expected=input;expected[7]=0x80;put(expected,24,0x01020304+table);
        Bytes out;
        require(nova3::relocate_bres32(input.data(),input.size(),0x01020304,out)==BresError::ok &&
                out==expected,"empty table allows header or one-past-end offset");
    }
    expect_error(fixture(60,0,61),BresError::table_bounds);
    expect_error(fixture(60,1,60),BresError::table_bounds);

    // Relocated inputs bypass table/slot checks, even with nonsense metadata.
    // This deliberately documents the existing API; it is not full validation.
    auto input=fixture(60,max32,max32);input[7]=0xff;
    for (const auto base:std::array<std::uint32_t,3>{0,0x01020304,max32}) {
        Bytes out;
        require(nova3::relocate_bres32(input.data(),input.size(),base,out)==BresError::ok &&
                out==input,"already-relocated bytes are unchanged for any base");
        require(nova3::relocate_bres32(out.data(),out.size(),base,out)==BresError::ok &&
                out==input,"already-relocated in-place call is idempotent");
    }
    input[0]=0;expect_error(input,BresError::magic);
    input.resize(59);expect_error(input,BresError::truncated);
}
}

int main() {
    unaligned_little_endian();
    wrapping_and_repeated_slots();
    sequential_table_aliases();
    bounds_and_error_atomicity();
    empty_and_already_relocated();
    std::cout<<"BRES synthetic contract tests passed (NOT MATCHED to original binary)\n";
}
