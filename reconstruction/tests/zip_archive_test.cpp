#include "zip_archive.hpp"
#include "crc32.hpp"
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <utility>
using Bytes=std::vector<std::uint8_t>;
static void require(bool b){if(!b){std::cerr<<"archive assertion failed\n";std::exit(1);}}
static void u16(Bytes& b,unsigned v){b.push_back(v&255);b.push_back((v>>8)&255);}
static void u32(Bytes& b,std::uint32_t v){u16(b,v&65535);u16(b,v>>16);}
static void set32(Bytes& b,std::size_t p,std::uint32_t v){for(unsigned i=0;i<4;++i)b[p+i]=static_cast<std::uint8_t>(v>>(i*8));}
static Bytes zip(const std::vector<std::pair<std::string,std::string>>& entries){
    Bytes b,central;
    for(const auto& e:entries){
        const auto start=static_cast<std::uint32_t>(b.size());const auto size=static_cast<std::uint32_t>(e.second.size());
        const auto crc=nova3::compute_crc(reinterpret_cast<const std::uint8_t*>(e.second.data()),e.second.size());
        u32(b,0x04034b50);u16(b,20);u16(b,0);u16(b,0);u32(b,0);u32(b,crc);u32(b,size);u32(b,size);u16(b,e.first.size());u16(b,0);
        b.insert(b.end(),e.first.begin(),e.first.end());b.insert(b.end(),e.second.begin(),e.second.end());
        u32(central,0x02014b50);u16(central,20);u16(central,20);u16(central,0);u16(central,0);u32(central,0);u32(central,crc);u32(central,size);u32(central,size);
        u16(central,e.first.size());u16(central,0);u16(central,0);u16(central,0);u16(central,0);u32(central,0);u32(central,start);central.insert(central.end(),e.first.begin(),e.first.end());
    }
    const auto start=static_cast<std::uint32_t>(b.size());b.insert(b.end(),central.begin(),central.end());
    u32(b,0x06054b50);u16(b,0);u16(b,0);u16(b,entries.size());u16(b,entries.size());u32(b,central.size());u32(b,start);u16(b,0);return b;
}
struct Temp {
    std::filesystem::path path=std::filesystem::temp_directory_path()/("nova3-package-"+std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count()));
    Temp(){std::filesystem::create_directory(path);}
    ~Temp(){std::filesystem::remove_all(path);}
    std::string save(const std::string& name,const Bytes& b){auto p=path/name;std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(b.data()),b.size());require(bool(f));return p.string();}
};
int main(){
    Temp temp;auto original=zip({{"x","A"},{"x","B"},{"x","C"},{"tail",""}});auto path=temp.save("real.zip",original);
    nova3::ZipArchive archive;Bytes out{42};
    require(archive.open(path)==nova3::ArchiveError::ok && archive.entries().size()==4);
    // In a three-item duplicate-only heap the first sorted ID is1. With the
    // fourth nonduplicate key the native heap can reorder ties differently.
    auto lookup=std::vector<nova3::ZipLookupEntry>{{"x",0},{"x",1},{"x",2},{"tail",3}};nova3::sort_zip_lookup(lookup);
    require(archive.find("x")==nova3::find_zip_lookup(lookup,"x"));
    require(archive.read_index(0,out)==nova3::ArchiveError::ok && out==Bytes{'A'});
    require(archive.read("tail",out)==nova3::ArchiveError::ok && out.empty());
    require(archive.read("missing",out)==nova3::ArchiveError::not_found && out.empty());
    require(archive.open_memory(original)==nova3::ArchiveError::ok);
    require(archive.read_index(0,out)==nova3::ArchiveError::ok && out==Bytes{'A'});
    require(archive.open_memory(Bytes{1,2,3})==nova3::ArchiveError::invalid);
    require(archive.read_index(0,out)==nova3::ArchiveError::ok && out==Bytes{'A'});
    auto names=temp.save("names.zip",zip({{"A/Foo","first"},{"B/fOo","second"}}));
    require(archive.open(names,{true,true})==nova3::ArchiveError::ok);
    require(archive.read("C:\\FOO",out)==nova3::ArchiveError::ok && std::string(out.begin(),out.end())=="second");
    Bytes region(19,99);region.insert(region.end(),original.begin(),original.end());region.insert(region.end(),5,88);
    auto region_path=temp.save("region",region);
    require(archive.open(region_path,{}, {},19,original.size())==nova3::ArchiveError::ok);
    require(archive.read_index(2,out)==nova3::ArchiveError::ok && out==Bytes{'C'});
    auto broken=original;broken.resize(broken.size()-1);auto invalid=temp.save("short.zip",broken);
    require(archive.open(invalid)==nova3::ArchiveError::invalid);
    require(archive.read_index(2,out)==nova3::ArchiveError::ok && out==Bytes{'C'});
    broken=original;const auto central=original.size()-22-(47*3+50);set32(broken,central+16,0); // central/local CRC mismatch
    require(archive.open(temp.save("crc-header.zip",broken))==nova3::ArchiveError::invalid);
    auto comment=original;comment[comment.size()-2]=22;Bytes fake(22,0);set32(fake,0,0x06054b50);comment.insert(comment.end(),fake.begin(),fake.end());
    require(archive.open(temp.save("comment.zip",comment))==nova3::ArchiveError::ok);
    require(archive.read_index(0,out)==nova3::ArchiveError::ok && out==Bytes{'A'});
    require(archive.find(std::string("x\0suffix",8))==std::size_t(-1));
    auto empty=temp.save("empty.zip",zip({}));require(archive.open(empty)==nova3::ArchiveError::ok && archive.entries().empty());
    nova3::ZipArchiveLimits limits;limits.entries=1;require(archive.open(path,{},limits)==nova3::ArchiveError::limit);
    limits={};limits.metadata_bytes=1;require(archive.open(path,{},limits)==nova3::ArchiveError::limit);
    limits={};limits.compressed_bytes=0;require(archive.open(path,{},limits)==nova3::ArchiveError::ok);out={42};
    require(archive.read("x",out)==nova3::ArchiveError::limit && out==Bytes{42});
    require(archive.open(temp.save("traversal.zip",zip({{"../x","A"}})))==nova3::ArchiveError::invalid);
    auto first=temp.save("first.zip",zip({{"same","one"}}));auto second=temp.save("second.zip",zip({{"same","two"},{"other","next"}}));
    nova3::ResourcePackages mounts;require(mounts.mount(first)==nova3::ArchiveError::ok && mounts.mount(second)==nova3::ArchiveError::ok);
    require(mounts.read("same",out)==nova3::ArchiveError::ok && std::string(out.begin(),out.end())=="one");
    require(mounts.read("other",out)==nova3::ArchiveError::ok && std::string(out.begin(),out.end())=="next");
    auto damaged=zip({{"same","bad"}});damaged[34]^=1;
    nova3::ResourcePackages fallback;require(fallback.mount(temp.save("bad-payload.zip",damaged))==nova3::ArchiveError::ok);
    require(fallback.mount(second)==nova3::ArchiveError::ok);
    require(fallback.read("same",out)==nova3::ArchiveError::ok && std::string(out.begin(),out.end())=="two");
    std::cout<<"seekable archive, region reads, native lookup, duplicate/mount order, bounds and corruption passed\n";
}
