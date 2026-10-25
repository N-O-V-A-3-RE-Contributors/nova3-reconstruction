#include "scramble.hpp"
#include <array>
#include <cstdlib>
#include <iostream>
static void require(bool ok) { if (!ok) std::exit(1); }
int main() {
    std::array<std::uint8_t,5> input{0,1,2,3,255};
    auto data = input;
    require(nova3::unscramble(data.data(),data.size(),"ab"));
    require((data == std::array<std::uint8_t,5>{97,99,99,97,158}));
    require(nova3::unscramble(data.data(),data.size(),"ab"));
    require(data == input);
    require(!nova3::unscramble(data.data(),data.size(),""));
    require(data == input);
    require(!nova3::unscramble(nullptr,1,"a"));
    require(!nova3::unscramble(data.data(),1,nullptr));
    require(nova3::unscramble(nullptr,0,""));
    std::cout << "repeating XOR and checked invalid inputs passed\n";
}
