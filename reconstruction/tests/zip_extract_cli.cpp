#include "zip_payload.hpp"
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc,char** argv) {
    if (argc!=3) return 2;
    std::ifstream in(argv[1],std::ios::binary);if (!in) return 2;
    // Explicit bounded fixture/asset inspector; never archive-controlled paths.
    std::vector<std::uint8_t> bytes(64*1024*1024);
    in.read(reinterpret_cast<char*>(bytes.data()),bytes.size());bytes.resize(static_cast<std::size_t>(in.gcount()));
    nova3::ZipLocalEntry entry;
    if (nova3::parse_zip_local(bytes.data(),bytes.size(),0,bytes.size(),entry)!=nova3::ZipError::ok) return 3;
    std::vector<std::uint8_t> output;
    const auto error=nova3::decode_zip_payload(entry,bytes.data()+entry.data_offset,entry.compressed_size,64*1024*1024,output);
    if (error!=nova3::ZipPayloadError::ok) { std::cerr << "payload error " << static_cast<int>(error) << '\n';return 4; }
    std::ofstream out(argv[2],std::ios::binary);out.write(reinterpret_cast<const char*>(output.data()),static_cast<std::streamsize>(output.size()));
    return out?0:5;
}
