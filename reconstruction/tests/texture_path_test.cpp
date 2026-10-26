#include "texture_path.hpp"
#include "crc32.hpp"
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

using Bytes = std::vector<std::uint8_t>;
using Member = std::pair<std::string, Bytes>;
static void require(bool ok, const std::string& label) {
    if (!ok) throw std::runtime_error(label);
}
static void u16(Bytes& b, unsigned n) {
    b.push_back(n & 255); b.push_back((n >> 8) & 255);
}
static void u32(Bytes& b, std::uint32_t n) {
    u16(b,n & 65535); u16(b,n >> 16);
}
// All integration fixtures are generated data: a PVR header and one ETC1 block.
static Bytes pvr(std::uint8_t value) {
    Bytes b;
    for (const auto n : {52u,4u,4u,0u,0x36u,8u,4u,0u,0u,0u,0u,0x21525650u,1u}) u32(b,n);
    b.insert(b.end(),8,value);
    return b;
}
struct Packed { Bytes bytes; std::vector<std::size_t> data; };
static Packed zip(const std::vector<Member>& members) {
    Packed result;
    auto& b = result.bytes;
    Bytes central;
    for (const auto& member : members) {
        const auto start = static_cast<std::uint32_t>(b.size());
        const auto size = static_cast<std::uint32_t>(member.second.size());
        const auto crc = nova3::compute_crc(member.second.data(),size);
        u32(b,0x04034b50); u16(b,20); u16(b,0); u16(b,0); u32(b,0);
        u32(b,crc); u32(b,size); u32(b,size); u16(b,member.first.size()); u16(b,0);
        b.insert(b.end(),member.first.begin(),member.first.end());
        result.data.push_back(b.size());
        b.insert(b.end(),member.second.begin(),member.second.end());
        u32(central,0x02014b50); u16(central,20); u16(central,20); u16(central,0); u16(central,0);
        u32(central,0); u32(central,crc); u32(central,size); u32(central,size);
        u16(central,member.first.size()); u16(central,0); u16(central,0); u16(central,0); u16(central,0);
        u32(central,0); u32(central,start);
        central.insert(central.end(),member.first.begin(),member.first.end());
    }
    const auto start = static_cast<std::uint32_t>(b.size());
    b.insert(b.end(),central.begin(),central.end());
    u32(b,0x06054b50); u16(b,0); u16(b,0); u16(b,members.size()); u16(b,members.size());
    u32(b,static_cast<std::uint32_t>(central.size())); u32(b,start); u16(b,0);
    return result;
}
struct Temp {
    std::filesystem::path directory;
    unsigned sequence = 0;
    Temp() {
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        for (unsigned attempt = 0; attempt < 100; ++attempt) {
            const auto candidate = std::filesystem::temp_directory_path() /
                ("nova3-texture-path-" + std::to_string(stamp) + "-" + std::to_string(attempt));
            if (std::filesystem::create_directory(candidate)) { directory = candidate; return; }
        }
        throw std::runtime_error("cannot create fixture directory");
    }
    ~Temp() { std::error_code error; std::filesystem::remove_all(directory,error); }
    std::string save(const Packed& fixture) {
        const auto path = directory / (std::to_string(sequence++) + ".zip");
        std::ofstream file(path,std::ios::binary);
        file.write(reinterpret_cast<const char*>(fixture.bytes.data()),
                   static_cast<std::streamsize>(fixture.bytes.size()));
        file.close();
        require(bool(file),"write synthetic package");
        return path.string();
    }
};
static bool same_plane(const nova3::TexturePlane& a, const nova3::TexturePlane& b) {
    if (a.bytes != b.bytes || a.image.width != b.image.width ||
        a.image.height != b.image.height || a.image.mips.size() != b.image.mips.size()) return false;
    for (std::size_t i = 0; i < a.image.mips.size(); ++i) {
        const auto& x = a.image.mips[i]; const auto& y = b.image.mips[i];
        if (x.width != y.width || x.height != y.height || x.offset != y.offset || x.size != y.size) return false;
    }
    return true;
}
static void check_load(nova3::ResourcePackages& packages, const std::string& name,
                       nova3::ArchiveError archive, nova3::TextureError texture,
                       const Bytes& expected = {}) {
    nova3::TextureBundle before;
    before.has_alpha = true; before.alpha_mode = 3;
    before.rgb.bytes = {1,2,3}; before.rgb.image = {8,4,{{8,4,52,16}}};
    before.alpha.bytes = {4,5}; before.alpha.image = {2,1,{{2,1,52,8}}};
    auto out = before;
    const auto result = nova3::load_etc_texture(packages,name,out);
    require(result.archive == archive && result.texture == texture,"load error category: " + name);
    if (archive == nova3::ArchiveError::ok && texture == nova3::TextureError::ok) {
        require(out.rgb.bytes == expected && out.rgb.image.width == 4 && out.rgb.image.height == 4,
                "selected resource: " + name);
        require(out.rgb.image.mips.size() == 1 && out.rgb.image.mips[0].offset == 52 &&
                out.rgb.image.mips[0].size == 8,"resource-relative mip offsets");
        require(!out.has_alpha && out.alpha_mode == 0 && out.alpha.bytes.empty() &&
                out.alpha.image.width == 0 && out.alpha.image.height == 0 && out.alpha.image.mips.empty(),
                "successful direct load clears old split data");
    } else {
        require(out.has_alpha == before.has_alpha && out.alpha_mode == before.alpha_mode &&
                same_plane(out.rgb,before.rgb) && same_plane(out.alpha,before.alpha),
                "failed load leaves all output fields unchanged: " + name);
    }
}

int main() try {
    const std::pair<std::string,std::string> paths[] = {
        {"SF_Intro_Building_02_Wall.tga","SF_Intro_Building_02_Wall.etc"},
        {"a.tga/x.psd/z","a.tga/x.etc"}, {"a.TGA.trailing","a.etc"},
        {"a.Tga","a.Tga"}, {"a.PSD","a.PSD"}, {"a.DDS","a.DDS"}, {"a.PVX","a.PVX"},
        {"a.etc","a.etc"}, {"a.pvx.dds","a.etc"}, {".psd",".etc"}, {"",""},
        {"folder.tga/unchanged.png","folder.etc"}, {"folder/texture.tga/","folder/texture.etc"},
        {"dir\\texture.tga","dir\\texture.etc"}, {"../texture.tga","../texture.etc"},
        {"name.tga.tga","name.etc"}, {"name.dds.backup","name.etc"},
        {"dir/name.TGA.psd","dir/name.TGA.etc"}, {"dir/name.tga.TGA","dir/name.etc"},
        {"texture.png","texture.png"}, {"tga","tga"}, {"texture.","texture."}
    };
    for (const auto& path : paths) {
        const auto before = path.first;
        require(nova3::etc_texture_path(path.first) == path.second,"path rewrite: " + path.first);
        require(path.first == before,"path input is unchanged");
    }
    const std::string extensions[] = {".psd",".tga",".TGA",".pvx",".dds"};
    for (std::size_t i = 0; i < 5; ++i) {
        require(nova3::etc_texture_path("dir/a" + extensions[i] + ".tail") == "dir/a.etc",
                "every supported extension");
        for (std::size_t j = i+1; j < 5; ++j) {
            require(nova3::etc_texture_path("a" + extensions[j] + "/b" + extensions[i] + ".tail") ==
                    "a" + extensions[j] + "/b.etc","extension priority precedes textual position");
        }
    }
    const std::string non_ascii = std::string("\xc3\xa9") + ".tga";
    require(nova3::etc_texture_path(non_ascii) == std::string("\xc3\xa9") + ".etc","non-ASCII prefix retained");

    Temp temp;
    const auto mapped = pvr(0x31), original = pvr(0x75);
    nova3::ResourcePackages packages;
    auto fixture = zip({{"prefer.etc",mapped},{"prefer.tga",original},{"fallback.tga",original},
                        {"invalid.etc",{1,2,3}},{"invalid.tga",original},{"direct.etc",mapped},
                        {"empty.etc",{}},{"empty.tga",original},
                        {"crc.etc",mapped},{"crc.tga",original},{"only-crc.etc",mapped},
                        {"bad-original.tga",mapped},{"both-bad.etc",mapped},{"both-bad.tga",original},
                        {"dir/texture.etc",mapped}});
    // Damage only payloads, retaining coherent local/central CRC fields so the
    // mount succeeds and the resource read exercises checksum/fallback errors.
    for (const auto index : {8u,10u,11u,12u,13u}) fixture.bytes[fixture.data[index]+52] ^= 1;
    require(packages.mount(temp.save(fixture)) == nova3::ArchiveError::ok,"mount integration package");
    check_load(packages,"prefer.tga",nova3::ArchiveError::ok,nova3::TextureError::ok,mapped);
    check_load(packages,"fallback.tga",nova3::ArchiveError::ok,nova3::TextureError::ok,original);
    check_load(packages,"direct.etc",nova3::ArchiveError::ok,nova3::TextureError::ok,mapped);
    check_load(packages,"dir/texture.psd",nova3::ArchiveError::ok,nova3::TextureError::ok,mapped);
    check_load(packages,"invalid.tga",nova3::ArchiveError::ok,nova3::TextureError::image);
    check_load(packages,"empty.tga",nova3::ArchiveError::ok,nova3::TextureError::image);
    check_load(packages,"crc.tga",nova3::ArchiveError::ok,nova3::TextureError::ok,original);
    // If both reads fail, preserve the original-name read's error category.
    check_load(packages,"only-crc.tga",nova3::ArchiveError::not_found,nova3::TextureError::ok);
    check_load(packages,"only-crc.etc",nova3::ArchiveError::checksum,nova3::TextureError::ok);
    check_load(packages,"bad-original.tga",nova3::ArchiveError::checksum,nova3::TextureError::ok);
    check_load(packages,"both-bad.tga",nova3::ArchiveError::checksum,nova3::TextureError::ok);
    for (const auto& missing : std::vector<std::string>{"none.tga","prefer.Tga","PREFER.tga","texture.tga",
                                                        "",std::string("prefer\0.tga",11)}) {
        check_load(packages,missing,nova3::ArchiveError::not_found,nova3::TextureError::ok);
    }
    nova3::ResourcePackages absent;
    check_load(absent,"none.tga",nova3::ArchiveError::not_found,nova3::TextureError::ok);

    // Rewritten-name lookup runs across all mounts before original fallback.
    nova3::ResourcePackages mounts;
    require(mounts.mount(temp.save(zip({{"cross.tga",original}}))) == nova3::ArchiveError::ok,"mount originals");
    require(mounts.mount(temp.save(zip({{"cross.etc",mapped}}))) == nova3::ArchiveError::ok,"mount rewritten");
    check_load(mounts,"cross.tga",nova3::ArchiveError::ok,nova3::TextureError::ok,mapped);
    nova3::ResourcePackages case_paths;
    require(case_paths.mount(temp.save(zip({{"folder/PREFER.ETC",mapped}})),{true,true}) ==
            nova3::ArchiveError::ok,"mount case/path profile");
    check_load(case_paths,"other/PREFER.tga",nova3::ArchiveError::ok,nova3::TextureError::ok,mapped);
    std::cout << "synthetic texture names, mount integration, fallback and atomic errors passed\n";
    return EXIT_SUCCESS;
} catch (const std::exception& e) {
    std::cerr << "texture path: " << e.what() << '\n';
    return EXIT_FAILURE;
}
