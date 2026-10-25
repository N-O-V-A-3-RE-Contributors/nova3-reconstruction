#include "etc1.hpp"
#include <fstream>
#include <iterator>
#include <string>
int main(int argc,char** argv){
    if(argc!=7)return 2;
    std::ifstream input(argv[1],std::ios::binary);if(!input)return 3;
    std::vector<std::uint8_t> bytes((std::istreambuf_iterator<char>(input)),{}),rgb;
    try{
        auto error=nova3::decode_etc1(bytes.data(),bytes.size(),std::stoul(argv[3]),std::stoul(argv[4]),rgb,std::stoul(argv[5])!=0,std::stoul(argv[6]));
        if(error!=nova3::Etc1Error::ok)return 10+int(error);
    }catch(...){return 4;}
    std::ofstream output(argv[2],std::ios::binary);output.write(reinterpret_cast<const char*>(rgb.data()),rgb.size());return output?0:5;
}
