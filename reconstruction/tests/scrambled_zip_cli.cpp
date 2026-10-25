#include "scrambled_zip.hpp"
#include "zip_payload.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
int main(int argc,char** argv){
 const bool verify=argc==4 && std::string(argv[3])=="--verify";
 if(argc!=3 && argc!=5 && !verify)return 2;
 std::ifstream in(argv[1],std::ios::binary|std::ios::ate);if(!in || in.tellg()>256*1024*1024)return 3;
 const auto length=in.tellg();if(length<0)return 3;
 std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));in.seekg(0);in.read(reinterpret_cast<char*>(bytes.data()),bytes.size());if(!in)return 3;std::size_t at=0;bool comma=false,found=false;
 if(argc!=5)std::cout<<'[';
 while(bytes.size()-at>=4 && bytes[at]=='S' && bytes[at+1]=='B'){
  nova3::ZipLocalEntry entry;if(nova3::parse_scrambled_local(bytes.data()+at,bytes.size()-at,at,bytes.size(),argv[2],entry)!=nova3::ZipError::ok)return 4;
  if(verify){std::vector<std::uint8_t> output;if(nova3::decode_zip_payload(entry,bytes.data()+entry.data_offset,entry.compressed_size,64*1024*1024,output)!=nova3::ZipPayloadError::ok)return 5;}
  if(argc!=5){
   if(comma)std::cout<<',';comma=true;std::cout<<"{\"name_hex\":\"";
   const char* digits="0123456789abcdef";for(unsigned char c:entry.name)std::cout<<digits[c>>4]<<digits[c&15];
   std::cout<<"\",\"offset\":"<<entry.data_offset<<",\"compressed\":"<<entry.compressed_size<<",\"size\":"<<entry.size<<",\"crc\":"<<entry.crc<<",\"method\":"<<entry.method<<'}';
  }else if(entry.name==argv[3]){
   std::vector<std::uint8_t> output;if(nova3::decode_zip_payload(entry,bytes.data()+entry.data_offset,entry.compressed_size,64*1024*1024,output)!=nova3::ZipPayloadError::ok)return 5;
   std::ofstream out(argv[4],std::ios::binary);out.write(reinterpret_cast<const char*>(output.data()),output.size());if(!out)return 6;found=true;
  }
  at=static_cast<std::size_t>(entry.next_offset);
 }
 // SDIR central directory is not yet parsed; SEND can represent an empty archive.
 if(bytes.size()-at<4 || (std::string(reinterpret_cast<const char*>(bytes.data()+at),4)!="SDIR" && std::string(reinterpret_cast<const char*>(bytes.data()+at),4)!="SEND"))return 7;
 if(argc!=5)std::cout<<"]\n";
 return argc==5 && !found?8:0;
}
