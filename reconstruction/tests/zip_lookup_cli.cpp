#include "zip_lookup.hpp"
#include <iostream>
#include <string>
#include <vector>
static bool unhex(const std::string& hex,std::string& value){
 if(hex.size()%2)return false;
 for(std::size_t i=0;i<hex.size();i+=2){try{std::size_t used=0;const auto n=std::stoul(hex.substr(i,2),&used,16);if(used!=2)return false;value+=static_cast<char>(n);}catch(...){return false;}}return true;
}
static void hexout(const std::string& s){const char* hex="0123456789abcdef";for(unsigned char c:s)std::cout<<hex[c>>4]<<hex[c&15];std::cout<<'\n';}
int main(int argc,char** argv){
 if(argc<2)return 2;const std::string mode=argv[1];
 if(mode=="entry" || mode=="query"){
  if(argc!=4)return 2;unsigned flags;try{flags=std::stoul(argv[2]);}catch(...){return 2;}
  std::string input;if(!unhex(argv[3],input))return 2;
  hexout(mode=="entry"?nova3::zip_entry_key(input,flags&2,flags&4):nova3::zip_query_key(input,flags&2,flags&4));return 0;
 }
 if(mode=="sort" || mode=="find"){
  const int start=mode=="find"?3:2;if(argc<start)return 2;
  std::vector<nova3::ZipLookupEntry> entries;
  for(int i=start;i<argc;++i){std::string key;if(!unhex(argv[i],key))return 2;entries.push_back({key,entries.size()});}
  if(mode=="sort"){
   nova3::sort_zip_lookup(entries);for(std::size_t i=0;i<entries.size();++i){if(i)std::cout<<' ';std::cout<<entries[i].archive_index;}std::cout<<'\n';
  }else{std::string key;if(!unhex(argv[2],key))return 2;std::cout<<nova3::find_zip_lookup(entries,key)<<'\n';}
  return 0;
 }
 return 2;
}
