#include "texture_bundle.hpp"
#include "crc32.hpp"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

using Bytes = std::vector<std::uint8_t>;
using Member = std::pair<std::string, Bytes>;

static void require(bool ok, const std::string& label) {
    if (!ok) throw std::runtime_error(label);
}
static void put(Bytes& b, std::size_t at, std::uint32_t n) {
    for (unsigned i = 0; i < 4; ++i) b.at(at + i) = static_cast<std::uint8_t>(n >> (i * 8));
}
static void u16(Bytes& b, unsigned n) {
    b.push_back(n & 255); b.push_back((n >> 8) & 255);
}
static void u32(Bytes& b, std::uint32_t n) {
    u16(b, n & 65535); u16(b, n >> 16);
}
// Synthetic 8x4, 4x2, 2x1, 1x1 chain: ETC1 blocks occupy 16,8,8,8 bytes.
// Distinct payload bytes make plane swaps and ZIP-relative mip offsets visible.
static Bytes pvr(std::uint8_t value) {
    Bytes b(92, value);
    for (std::size_t i = 0; i < 52; ++i) b[i] = 0;
    for (auto p : std::vector<std::pair<unsigned, unsigned>>{
             {0,52}, {4,4}, {8,8}, {12,3}, {16,0x136}, {20,40},
             {24,4}, {44,0x21525650}, {48,1}}) put(b, p.first, p.second);
    return b;
}
struct Packed {
    Bytes bytes;
    std::vector<std::size_t> local, data, central;
    std::size_t directory = 0, end = 0;
};
// Stored ZIP fixture; all bytes, including the extra fields, are synthetic.
static Packed zip(const std::vector<Member>& entries) {
    Packed result;
    auto& b = result.bytes;
    for (const auto& e : entries) {
        result.local.push_back(b.size());
        const auto size = static_cast<std::uint32_t>(e.second.size());
        const auto crc = nova3::compute_crc(e.second.data(), size);
        u32(b,0x04034b50); u16(b,20); u16(b,0); u16(b,0); u32(b,0);
        u32(b,crc); u32(b,size); u32(b,size); u16(b,e.first.size()); u16(b,5);
        b.insert(b.end(),e.first.begin(),e.first.end());
        b.insert(b.end(),{0xfe,0xca,1,0,0x5a}); // private extra field
        result.data.push_back(b.size());
        b.insert(b.end(),e.second.begin(),e.second.end());
    }
    result.directory = b.size();
    for (std::size_t i = 0; i < entries.size(); ++i) {
        const auto& e = entries[i];
        const auto size = static_cast<std::uint32_t>(e.second.size());
        result.central.push_back(b.size());
        u32(b,0x02014b50); u16(b,20); u16(b,20); u16(b,0); u16(b,0); u32(b,0);
        u32(b,nova3::compute_crc(e.second.data(),size)); u32(b,size); u32(b,size);
        u16(b,e.first.size()); u16(b,0); u16(b,0); u16(b,0); u16(b,0);
        u32(b,0); u32(b,static_cast<std::uint32_t>(result.local[i]));
        b.insert(b.end(),e.first.begin(),e.first.end());
    }
    result.end = b.size();
    u32(b,0x06054b50); u16(b,0); u16(b,0); u16(b,entries.size()); u16(b,entries.size());
    u32(b,static_cast<std::uint32_t>(result.end-result.directory));
    u32(b,static_cast<std::uint32_t>(result.directory)); u16(b,0);
    return result;
}
static bool same_plane(const nova3::TexturePlane& a, const nova3::TexturePlane& b) {
    if (a.bytes != b.bytes || a.image.width != b.image.width ||
        a.image.height != b.image.height || a.image.mips.size() != b.image.mips.size()) return false;
    for (std::size_t i = 0; i < a.image.mips.size(); ++i) {
        const auto& x = a.image.mips[i]; const auto& y = b.image.mips[i];
        if (x.width != y.width || x.height != y.height || x.offset != y.offset || x.size != y.size) return false;
    }
    return true;
}
static bool same_bundle(const nova3::TextureBundle& a, const nova3::TextureBundle& b) {
    return a.has_alpha == b.has_alpha && a.alpha_mode == b.alpha_mode &&
           same_plane(a.rgb,b.rgb) && same_plane(a.alpha,b.alpha);
}
static void check_plane(const nova3::TexturePlane& plane, const Bytes& bytes) {
    require(plane.bytes == bytes, "plane owns selected member bytes");
    require(plane.image.width == 8 && plane.image.height == 4 && plane.image.mips.size() == 4,
            "non-square image dimensions and mip count");
    const nova3::TextureMip expected[] = {{8,4,52,16},{4,2,68,8},{2,1,76,8},{1,1,84,8}};
    for (std::size_t i = 0; i < 4; ++i) {
        const auto& mip = plane.image.mips[i];
        const auto& want = expected[i];
        require(mip.width == want.width && mip.height == want.height &&
                mip.offset == want.offset && mip.size == want.size, "plane-relative mip layout");
        require(mip.offset <= plane.bytes.size() && mip.size <= plane.bytes.size()-mip.offset,
                "mip lies inside owned bytes");
        require(plane.bytes[mip.offset] == bytes[52] &&
                plane.bytes[mip.offset+mip.size-1] == bytes[52], "mip references selected payload");
    }
}
static void expect_error(const Bytes& bytes, nova3::TextureError error,
                         const nova3::TextureBundle& before, const std::string& label) {
    auto out = before;
    require(nova3::read_texture_bundle(bytes,out) == error, label + ": error category");
    require(same_bundle(out,before), label + ": output unchanged");
}

int main() try {
    const auto rgb = pvr(0x31), alpha = pvr(0xa7), other = pvr(0x69);
    nova3::TextureBundle out;
    const auto packed = zip({{"dir/Alpha.pvr",alpha},{"RGB.PVR",rgb},{"SPLIT",{3,0,0,0}}});
    require(nova3::read_texture_bundle(packed.bytes,out) == nova3::TextureError::ok, "split image");
    require(out.has_alpha && out.alpha_mode == 3, "split metadata");
    check_plane(out.rgb,rgb); check_plane(out.alpha,alpha);
    const auto snapshot = out;

    for (std::uint8_t mode = 0; mode < 4; ++mode) {
        const auto fixture = zip({{"rgb",rgb},{"alpha",alpha},{"meta/sPlIt",{mode,0,0,0}}});
        require(nova3::read_texture_bundle(fixture.bytes,out) == nova3::TextureError::ok &&
                out.has_alpha && out.alpha_mode == mode, "all little-endian split modes");
    }
    // Prefix matching uses sorted lowercase basenames, not physical ZIP order
    // or a particular image suffix. Unselected image payloads are not decoded.
    const auto sorted = zip({{"rgb.z",other},{"alpha.z",other},{"SPLIT",{0,0,0,0}},
                             {"long/nested/RGB.a",rgb},{"nested/ALPHA.a",alpha},
                             {"notrgb",{}},{"xalpha",{}},{"rgb.zz",{1}}});
    require(nova3::read_texture_bundle(sorted.bytes,out) == nova3::TextureError::ok, "sorted prefix selection");
    check_plane(out.rgb,rgb); check_plane(out.alpha,alpha);
    expect_error(zip({{"notrgb",rgb},{"alpha",alpha},{"SPLIT",{0,0,0,0}}}).bytes,
                 nova3::TextureError::split,snapshot,"rgb must be a prefix");
    expect_error(zip({{"rgb",rgb},{"xalpha",alpha},{"SPLIT",{0,0,0,0}}}).bytes,
                 nova3::TextureError::split,snapshot,"alpha must be a prefix");

    // Checked-reader policy: corrupt first members must not silently select a
    // later prefix match. No original-engine equivalence is claimed here.
    for (std::size_t selected : {std::size_t(0), std::size_t(2)}) {
        auto broken = zip({{"rgb.a",rgb},{"rgb.z",other},{"alpha.a",alpha},
                           {"alpha.z",other},{"SPLIT",{0,0,0,0}}});
        broken.bytes[broken.data[selected]+52] ^= 1;
        expect_error(broken.bytes,nova3::TextureError::split,snapshot,"first selected member checksum");
    }
    for (const auto& marker : std::vector<Bytes>{{},{0},{0,0,0},{0,0,0,0,0},
                                               {4,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}}) {
        expect_error(zip({{"rgb",rgb},{"alpha",alpha},{"SPLIT",marker}}).bytes,
                     nova3::TextureError::split,snapshot,"invalid split marker");
    }
    expect_error(zip({{"rgb",rgb},{"SPLIT",{0,0,0,0}}}).bytes,
                 nova3::TextureError::split,snapshot,"missing alpha");
    expect_error(zip({{"alpha",alpha},{"SPLIT",{0,0,0,0}}}).bytes,
                 nova3::TextureError::split,snapshot,"missing rgb");
    expect_error(zip({{"rgb",rgb},{"alpha",alpha},{"MULTI",{0,0,0,0}}}).bytes,
                 nova3::TextureError::unsupported,snapshot,"unsupported bundle kind");
    expect_error(zip({{"rgb",rgb},{"alpha",alpha},{"SPLIT.extra",{0,0,0,0}}}).bytes,
                 nova3::TextureError::unsupported,snapshot,"marker is an exact basename");
    for (std::size_t plane = 0; plane < 2; ++plane) {
        for (unsigned corruption = 0; corruption < 4; ++corruption) {
            auto bad = plane == 0 ? rgb : alpha;
            if (corruption == 0) bad[44] = 0;
            if (corruption == 1) bad.pop_back();
            if (corruption == 2) put(bad,20,0xffffffffu);
            if (corruption == 3) bad.clear();
            expect_error(zip({{"rgb",plane == 0 ? bad : rgb},{"alpha",plane == 1 ? bad : alpha},
                              {"SPLIT",{0,0,0,0}}}).bytes,
                         nova3::TextureError::image,snapshot,"invalid selected PVR");
        }
    }
    // Offset, name-length and size fields in local/central/end records.
    for (const auto at : {packed.central[0]+42, packed.end+16, packed.local[0]+18,
                          packed.local[0]+22, packed.central[0]+20, packed.central[0]+24,
                          packed.local[0]+26, packed.central[0]+28}) {
        auto broken = packed.bytes; put(broken,at,0xfffffffeu);
        expect_error(broken,nova3::TextureError::archive,snapshot,"malformed ZIP field");
    }
    for (const auto& name : std::vector<std::string>{"../rgb","/rgb","dir/../rgb",
                                                    "dir\\rgb",std::string("rgb\0tail",8)}) {
        expect_error(zip({{name,rgb},{"alpha",alpha},{"SPLIT",{0,0,0,0}}}).bytes,
                     nova3::TextureError::archive,snapshot,"unsafe member name");
    }
    for (std::size_t length = 0; length < packed.bytes.size(); ++length) {
        expect_error(Bytes(packed.bytes.begin(),packed.bytes.begin()+length),
                     length < 4 ? nova3::TextureError::image : nova3::TextureError::archive,
                     snapshot,"every truncated ZIP prefix");
    }
    for (std::size_t length = 0; length < rgb.size(); ++length) {
        expect_error(Bytes(rgb.begin(),rgb.begin()+length),nova3::TextureError::image,
                     snapshot,"every truncated PVR prefix");
    }
    auto trailing = rgb; trailing.push_back(0);
    expect_error(trailing,nova3::TextureError::image,snapshot,"trailing PVR bytes");

    // A successful direct read replaces all previous split state, not only RGB.
    out = snapshot;
    require(nova3::read_texture_bundle(rgb,out) == nova3::TextureError::ok, "direct image");
    check_plane(out.rgb,rgb);
    require(!out.has_alpha && out.alpha_mode == 0 && out.alpha.bytes.empty() &&
            out.alpha.image.width == 0 && out.alpha.image.height == 0 && out.alpha.image.mips.empty(),
            "direct image clears stale alpha state");
    auto copied = out;
    auto moved = std::move(copied);
    check_plane(moved.rgb,rgb);
    std::cout << "synthetic bundle selection, mip offsets, malformed records and atomic errors passed\n";
    return EXIT_SUCCESS;
} catch (const std::exception& e) {
    std::cerr << "texture bundle: " << e.what() << '\n';
    return EXIT_FAILURE;
}
