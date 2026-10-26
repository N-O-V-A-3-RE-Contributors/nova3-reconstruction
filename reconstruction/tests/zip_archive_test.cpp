#include "zip_archive.hpp"
#include "crc32.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <utility>
using Bytes=std::vector<std::uint8_t>;
static void require(bool b,const char* message="archive assertion failed"){if(!b){std::cerr<<message<<'\n';std::exit(1);}}
static void u16(Bytes& b,unsigned v){b.push_back(v&255);b.push_back((v>>8)&255);}
static void u32(Bytes& b,std::uint32_t v){u16(b,v&65535);u16(b,v>>16);}
static void set16(Bytes& b,std::size_t p,unsigned v){b[p]=v&255;b[p+1]=(v>>8)&255;}
static void set32(Bytes& b,std::size_t p,std::uint32_t v){for(unsigned i=0;i<4;++i)b[p+i]=static_cast<std::uint8_t>(v>>(i*8));}
static std::uint32_t get32(const Bytes& b,std::size_t p){return std::uint32_t(b[p])|(std::uint32_t(b[p+1])<<8)|(std::uint32_t(b[p+2])<<16)|(std::uint32_t(b[p+3])<<24);}
struct Member { std::string name,data; std::uint16_t method=0; Bytes compressed{}; };
// Entirely synthetic fixtures: no reference APKs or original assets required.
static Bytes zip(const std::vector<Member>& entries){
    Bytes b,central;
    for(const auto& e:entries){
        const auto start=static_cast<std::uint32_t>(b.size());const auto size=static_cast<std::uint32_t>(e.data.size());
        const auto compressed_size=e.method==0?size:static_cast<std::uint32_t>(e.compressed.size());
        const auto crc=nova3::compute_crc(reinterpret_cast<const std::uint8_t*>(e.data.data()),e.data.size());
        u32(b,0x04034b50);u16(b,20);u16(b,0);u16(b,e.method);u32(b,0);u32(b,crc);u32(b,compressed_size);u32(b,size);u16(b,e.name.size());u16(b,0);
        b.insert(b.end(),e.name.begin(),e.name.end());
        if(e.method==0)b.insert(b.end(),e.data.begin(),e.data.end());
        else b.insert(b.end(),e.compressed.begin(),e.compressed.end());
        u32(central,0x02014b50);u16(central,20);u16(central,20);u16(central,0);u16(central,e.method);u32(central,0);u32(central,crc);u32(central,compressed_size);u32(central,size);
        u16(central,e.name.size());u16(central,0);u16(central,0);u16(central,0);u16(central,0);u32(central,0);u32(central,start);central.insert(central.end(),e.name.begin(),e.name.end());
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
static void test_directory_validation(){
    using Error=nova3::ArchiveError;
    const auto good=zip({{"item","payload"}});
    const auto end=good.size()-22,central=std::size_t(get32(good,end+16));
    nova3::ZipArchive archive;
    require(archive.open_memory(good)==Error::ok,"mount validation baseline");
    auto rejects=[&](const Bytes& bytes,Error error,const char* message){
        require(archive.open_memory(bytes)==error,message);
        Bytes output{42};
        require(archive.entries().size()==1 && archive.entries()[0].local.name=="item",
                "failed open changed entries");
        require(archive.read("item",output)==Error::ok && std::string(output.begin(),output.end())=="payload",
                "failed open changed lookup or payload");
    };
    for(std::size_t length=0;length<good.size();++length)
        rejects(Bytes(good.begin(),good.begin()+length),Error::invalid,"truncated archive prefix");
    struct Change {std::size_t offset;unsigned width;std::uint32_t value;Error error;const char* message;};
    const Change changes[]{
        {end+4,2,1,Error::unsupported,"EOCD disk number"},
        {end+6,2,1,Error::unsupported,"EOCD directory disk"},
        {end+8,2,2,Error::unsupported,"EOCD split counts"},
        {end+12,4,0xffffffffu,Error::unsupported,"ZIP64 directory size"},
        {end+16,4,0xffffffffu,Error::unsupported,"ZIP64 directory offset"},
        {end+12,4,static_cast<std::uint32_t>(end-central-1),Error::invalid,"short directory extent"},
        {end+16,4,static_cast<std::uint32_t>(end+1),Error::invalid,"directory beyond EOCD"},
        {end+20,2,1,Error::invalid,"missing EOCD comment"},
        {central,4,0,Error::invalid,"directory signature"},
        {central+8,2,0x800,Error::invalid,"central/local flags mismatch"},
        {central+10,2,8,Error::invalid,"central/local method mismatch"},
        {central+16,4,0,Error::invalid,"central/local CRC mismatch"},
        {central+20,4,8,Error::invalid,"central/local compressed size mismatch"},
        {central+24,4,8,Error::invalid,"central/local output size mismatch"},
        {central+28,2,5,Error::invalid,"directory name exceeds record extent"},
        {central+30,2,1,Error::invalid,"directory extra exceeds record extent"},
        {central+32,2,1,Error::invalid,"directory comment exceeds record extent"},
        {central+34,2,1,Error::unsupported,"entry on another disk"},
        {central+42,4,1,Error::invalid,"local header offset inside header"},
        {central+42,4,static_cast<std::uint32_t>(central),Error::invalid,"local header inside directory"},
        {0,4,0,Error::invalid,"local signature"},
        {6,2,8,Error::unsupported,"data descriptor flag"},
        {8,2,99,Error::unsupported,"local compression method"},
        {18,4,0xffffffffu,Error::unsupported,"ZIP64 local compressed size"},
        {22,4,0xffffffffu,Error::unsupported,"ZIP64 local output size"},
        {22,4,8,Error::invalid,"stored local size mismatch"},
        {26,2,0,Error::invalid,"empty local name"},
        {28,2,0xffff,Error::invalid,"local extra extends past archive"},
        {30,2,0x7878,Error::invalid,"local name differs from directory"}
    };
    for(const auto& change:changes){
        auto bytes=good;
        if(change.width==2)set16(bytes,change.offset,change.value);
        else set32(bytes,change.offset,change.value);
        rejects(bytes,change.error,change.message);
    }
    auto bytes=good;set16(bytes,end+8,2);set16(bytes,end+10,2);
    rejects(bytes,Error::invalid,"too many directory entries");
    bytes=good;set16(bytes,end+8,0);set16(bytes,end+10,0);
    rejects(bytes,Error::invalid,"unconsumed directory records");
    bytes=good;set16(bytes,end+8,0xffff);set16(bytes,end+10,0xffff);
    rejects(bytes,Error::unsupported,"ZIP64 entry count");

    bytes=zip({{"x","A"},{"x","A"}});
    const auto duplicate_central=get32(bytes,bytes.size()-6);
    set32(bytes,duplicate_central+47+42,0);
    rejects(bytes,Error::invalid,"two directory records referencing one local entry");
    bytes=zip({{"x","A"},{"x","A"}});
    bytes.erase(bytes.end()-22-47,bytes.end()-22);
    const auto shorter_end=bytes.size()-22;
    set16(bytes,shorter_end+8,1);set16(bytes,shorter_end+10,1);set32(bytes,shorter_end+12,47);
    rejects(bytes,Error::invalid,"unindexed local entry");

    // Directory order must not change the physical entry indices.
    bytes=zip({{"x","A"},{"y","B"},{"z","C"}});
    const auto sorted_central=get32(bytes,bytes.size()-6);
    std::rotate(bytes.begin()+sorted_central,bytes.begin()+sorted_central+47,bytes.end()-22);
    require(archive.open_memory(bytes)==Error::ok,"reordered directory");
    for(std::size_t i=0;i<3;++i){
        Bytes output;
        require(archive.find(std::string(1,static_cast<char>('x'+i)))==i,"physical entry index");
        require(archive.read_index(i,output)==Error::ok && output==Bytes{static_cast<std::uint8_t>('A'+i)},
                "reordered directory payload");
    }
}
static void test_payload_errors(Temp& temp){
    using Error=nova3::ArchiveError;
    const auto good=zip({{"raw","A",8,{0x73,0x04,0x00}},{"empty","",8,{0x03,0x00}}});
    nova3::ZipArchive archive;
    Bytes output{42};
    require(archive.open_memory(good)==Error::ok,"deflated archive");
    require(archive.read("raw",output)==Error::ok && output==Bytes{'A'},"archive DEFLATE dispatch");
    require(archive.read("empty",output)==Error::ok && output.empty(),"archive empty DEFLATE");
    auto limits=nova3::ZipArchiveLimits{};limits.compressed_bytes=3;limits.output_bytes=1;
    require(archive.open_memory(good,{},limits)==Error::ok,"payload exact limits mount");
    require(archive.read("raw",output)==Error::ok && output==Bytes{'A'},"payload exact limits read");
    auto rejects=[&](const Bytes& bytes,Error error,const char* message){
        for(bool memory:{true,false}){
            require((memory?archive.open_memory(bytes):archive.open(temp.save("payload.zip",bytes)))==Error::ok,
                    "payload errors must not fail header indexing");
            output={42};
            require(archive.read_index(0,output)==error,message);
            require(output==Bytes{42},"failed archive read changed output");
        }
    };
    auto bytes=good;bytes[33]=0x07;
    rejects(bytes,Error::payload,"invalid DEFLATE mapped to payload error");
    bytes=zip({{"raw","A",8,{0x73,0x04,0x00,0x00}}});
    rejects(bytes,Error::payload,"DEFLATE trailing byte rejected through archive");
    bytes=zip({{"raw","B",8,{0x73,0x04,0x00}}});
    rejects(bytes,Error::checksum,"DEFLATE checksum error mapping");
    bytes=zip({{"raw","AA",8,{0x73,0x04,0x00}}});
    rejects(bytes,Error::payload,"DEFLATE output size mismatch through archive");
    bytes=zip({{"raw","A"}});bytes[33]='B';
    rejects(bytes,Error::checksum,"stored checksum error mapping");
    for(bool compressed_limit:{true,false}){
        limits={};
        if(compressed_limit)limits.compressed_bytes=2;else limits.output_bytes=0;
        require(archive.open_memory(good,{},limits)==Error::ok,"payload limit mount");
        output={42};require(archive.read("raw",output)==Error::limit && output==Bytes{42},"payload limit read");
    }
    output={42};
    require(archive.read_index(std::numeric_limits<std::size_t>::max(),output)==Error::not_found && output==Bytes{42},
            "invalid index leaves output unchanged");
    require(archive.read("missing",output)==Error::not_found && output==Bytes{42},"missing name leaves output unchanged");

    nova3::ResourcePackages packages;
    require(packages.mount(temp.save("inflate-error.zip",zip({{"raw","A",8,{0x07}}})))==Error::ok,"mount malformed stream");
    require(packages.mount(temp.save("checksum-error.zip",zip({{"raw","B",8,{0x73,0x04,0x00}}})))==Error::ok,
            "mount wrong checksum");
    require(packages.mount((temp.path/"missing.zip").string())==Error::io,"failed package mount");
    require(packages.read("raw",output)==Error::checksum && output==Bytes{42},"last matching package error");
    require(packages.read("absent",output)==Error::not_found && output==Bytes{42},"missing from all packages");
    require(packages.mount(temp.save("valid-deflate.zip",good))==Error::ok,"mount valid fallback");
    require(packages.read("raw",output)==Error::ok && output==Bytes{'A'},"fallback after multiple decode failures");
}
static void test_extensions_and_io(Temp& temp){
    using Error=nova3::ArchiveError;
    auto bytes=zip({{"item","A"}});
    const auto old_end=bytes.size()-22,old_central=std::size_t(get32(bytes,old_end+16));
    const Bytes extra{0xfe,0xca,0x02,0x00,0xab,0xcd}; // Unknown, well-formed extra field.
    bytes.insert(bytes.begin()+34,extra.begin(),extra.end());set16(bytes,28,extra.size());
    const auto central=old_central+extra.size();
    Bytes extension=extra;extension.insert(extension.end(),{'n','o','t','e'});
    bytes.insert(bytes.end()-22,extension.begin(),extension.end());
    set16(bytes,central+30,extra.size());set16(bytes,central+32,4);
    const auto end=bytes.size()-22;
    set32(bytes,end+12,static_cast<std::uint32_t>(old_end-old_central+extension.size()));
    set32(bytes,end+16,central);
    set16(bytes,end+20,0xffff);bytes.insert(bytes.end(),65535,'?');
    nova3::ZipArchiveLimits limits;limits.entries=1;limits.metadata_bytes=end-central;
    nova3::ZipArchive archive;Bytes output;
    const auto path=temp.save("extensions.zip",bytes);
    for(bool memory:{true,false}){
        require((memory?archive.open_memory(bytes,{},limits):archive.open(path,{},limits))==Error::ok,
                "extra fields, entry comment, maximal EOCD comment, exact metadata limit");
        require(archive.read("item",output)==Error::ok && output==Bytes{'A'},"extended archive data offset");
    }
    --limits.metadata_bytes;
    require(archive.open_memory(bytes,{},limits)==Error::limit,"metadata boundary");
    limits={};limits.entries=0;
    require(archive.open_memory(bytes,{},limits)==Error::limit,"entry boundary");
    require(archive.open((temp.path/"nonexistent.zip").string())==Error::io,"missing file");
    require(archive.open(path,{}, {},bytes.size()+1,22)==Error::invalid,"region offset beyond file");
    require(archive.open(path,{}, {},0,bytes.size()+1)==Error::invalid,"region length beyond file");
    require(archive.open(path,{}, {},0,21)==Error::invalid,"region too short");
    require(archive.open(path,{}, {},std::numeric_limits<std::uint64_t>::max(),22)==Error::invalid,"region offset overflow");
    require(archive.read("item",output)==Error::ok && output==Bytes{'A'},"failed file opens preserve mounted archive");
    // Header index remains valid but an externally truncated backing file must fail atomically.
    const auto truncated_path=temp.save("truncate-after-open.zip",zip({{"item","A"}}));
    require(archive.open(truncated_path)==Error::ok,"file mutation baseline");
    std::filesystem::resize_file(truncated_path,0);
    output={42};require(archive.read("item",output)==Error::io && output==Bytes{42},"short file read");
}
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
    test_directory_validation();
    test_payload_errors(temp);
    test_extensions_and_io(temp);
    std::cout<<"seekable archive, region reads, native lookup, duplicate/mount order, bounds and corruption passed\n";
}
