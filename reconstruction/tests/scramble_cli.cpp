#include "scramble.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    std::ifstream in(argv[1],std::ios::binary);
    if (!in) return 2;
    std::vector<std::uint8_t> data((std::istreambuf_iterator<char>(in)), {});
    if (!nova3::unscramble(data.data(),data.size(),argv[3])) return 3;
    std::ofstream out(argv[2],std::ios::binary);
    out.write(reinterpret_cast<const char*>(data.data()),static_cast<std::streamsize>(data.size()));
    return out ? 0 : 4;
}
