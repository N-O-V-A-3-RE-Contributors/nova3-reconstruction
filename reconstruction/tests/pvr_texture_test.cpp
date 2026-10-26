#include "pvr_texture.hpp"
#include "etc1.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <vector>

namespace {
using Bytes = std::vector<std::uint8_t>;
using Error = nova3::PvrError;

void check(bool ok, const char* expression, int line) {
    if (!ok) {
        std::cerr << "PVR line " << line << ": " << expression << '\n';
        std::exit(1);
    }
}
#define CHECK(expression) check((expression), #expression, __LINE__)

void put(Bytes& b, std::size_t at, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) b[at + i] = static_cast<std::uint8_t>(value >> (i * 8));
}

// Hand-authored synthetic sizes; no game content or original-binary oracle.
Bytes image(std::uint32_t w = 4, std::uint32_t h = 4, std::uint32_t levels = 0,
            std::uint32_t payload = 8, bool mipmapped = false) {
    Bytes b(52 + payload, 0);
    put(b, 0, 52); put(b, 4, h); put(b, 8, w); put(b, 12, levels);
    put(b, 16, mipmapped ? 0x136 : 0x36); put(b, 20, payload);
    put(b, 24, 4); put(b, 44, 0x21525650); put(b, 48, 1);
    return b;
}

bool equal(const nova3::Etc1Texture& a, const nova3::Etc1Texture& b) {
    if (a.width != b.width || a.height != b.height || a.mips.size() != b.mips.size()) return false;
    for (std::size_t i = 0; i < a.mips.size(); ++i) {
        const auto& x = a.mips[i]; const auto& y = b.mips[i];
        if (x.width != y.width || x.height != y.height || x.offset != y.offset || x.size != y.size) return false;
    }
    return true;
}

void expect_error(const Bytes& b, Error expected) {
    nova3::Etc1Texture out{91, 73, {{7, 5, 123, 456}, {3, 2, 579, 16}}};
    const auto saved = out;
    CHECK(nova3::read_etc1_pvr(b.data(), b.size(), out) == expected);
    CHECK(equal(out, saved));
}

void mip_layout_and_decode() {
    struct Case { unsigned w, h, levels, payload; bool mipmapped; };
    for (const auto c : {
        Case{1, 1, 0, 8, false}, Case{2, 2, 0, 8, false}, Case{4, 4, 0, 8, false},
        Case{4, 4, 2, 24, true}, Case{8, 2, 3, 40, true}, Case{2, 8, 3, 40, true},
        Case{16, 4, 4, 72, true}, Case{16384, 1, 0, 32768, false}
    }) {
        auto b = image(c.w, c.h, c.levels, c.payload, c.mipmapped);
        nova3::Etc1Texture out;
        CHECK(nova3::read_etc1_pvr(b.data(), b.size(), out) == Error::ok);
        CHECK(out.width == c.w && out.height == c.h && out.mips.size() == c.levels + 1);
        std::size_t end = 52;
        for (unsigned i = 0; i <= c.levels; ++i) {
            const auto& mip = out.mips[i];
            CHECK(mip.width == std::max(1u, c.w >> i));
            CHECK(mip.height == std::max(1u, c.h >> i));
            CHECK(mip.offset == end && mip.size >= 8 && mip.size % 8 == 0);
            CHECK(mip.offset <= b.size() && mip.size <= b.size() - mip.offset);
            // Fill all blocks in this mip with a distinct, constant RGB value.
            for (std::size_t at = mip.offset; at < mip.offset + mip.size; at += 8)
                std::fill_n(b.begin() + at, 3, static_cast<std::uint8_t>(i * 0x11));
            Bytes rgb;
            CHECK(nova3::decode_etc1(b.data() + mip.offset, mip.size, mip.width, mip.height, rgb) == nova3::Etc1Error::ok);
            CHECK(rgb == Bytes(std::size_t(mip.width) * mip.height * 3, static_cast<std::uint8_t>(i * 17 + 2)));
            end += mip.size;
        }
        CHECK(end == b.size());
    }

    // Non-square chain: independently fixed offsets and lengths, including tiny mips.
    const auto b = image(8, 2, 3, 40, true);
    nova3::Etc1Texture out;
    CHECK(nova3::read_etc1_pvr(b.data(), b.size(), out) == Error::ok);
    const std::size_t offsets[] = {52, 68, 76, 84};
    const std::size_t sizes[] = {16, 8, 8, 8};
    for (unsigned i = 0; i < 4; ++i) {
        CHECK(out.mips[i].offset == offsets[i]);
        CHECK(out.mips[i].size == sizes[i]);
    }
    Bytes unaligned(b.size() + 1, 0xff);
    std::copy(b.begin(), b.end(), unaligned.begin() + 1);
    nova3::Etc1Texture other;
    CHECK(nova3::read_etc1_pvr(unaligned.data() + 1, b.size(), other) == Error::ok);
    CHECK(equal(out, other));
    const auto single = image();
    CHECK(nova3::read_etc1_pvr(single.data(), single.size(), out) == Error::ok);
    CHECK(out.mips.size() == 1 && out.width == 4 && out.height == 4);
}

void header_and_subset() {
    const auto good = image();
    CHECK(!nova3::valid_pvr_v2_header(nullptr, 52));
    nova3::Etc1Texture out{91, 73, {{7, 5, 123, 456}}};
    const auto saved = out;
    CHECK(nova3::read_etc1_pvr(nullptr, 52, out) == Error::header);
    CHECK(equal(out, saved));
    for (unsigned offset : {0u, 44u, 45u, 46u, 47u}) {
        auto bad = good; bad[offset] ^= 1;
        expect_error(bad, Error::header);
    }
    auto bad = good; put(bad, 0, 0x34000000); // Swapped-endian length is invalid.
    expect_error(bad, Error::header);
    for (unsigned offset : {4u, 8u}) {
        for (const auto value : {0u, 3u, 32768u, 0xffffffffu}) {
            bad = good; put(bad, offset, value);
            CHECK(nova3::valid_pvr_v2_header(bad.data(), bad.size()));
            expect_error(bad, Error::unsupported);
        }
    }
    for (const auto bits : {0u, 1u, 8u, 32u, 0xffffffffu}) {
        bad = good; put(bad, 24, bits); expect_error(bad, Error::unsupported);
    }
    for (const auto surfaces : {0u, 2u, 6u, 0xffffffffu}) {
        bad = good; put(bad, 48, surfaces); expect_error(bad, Error::unsupported);
    }
    for (const auto format : {0u, 0x35u, 0x37u, 0xffu}) {
        bad = good; put(bad, 16, format); expect_error(bad, Error::unsupported);
    }
    for (unsigned bit = 8; bit < 32; ++bit) {
        bad = good; put(bad, 16, 0x36u | (1u << bit));
        const auto expected = bit == 8 || bit == 12 ? Error::header : Error::unsupported;
        expect_error(bad, expected);
    }
    bad = good; put(bad, 12, 1); // A count without the mip flag is unsupported.
    expect_error(bad, Error::unsupported);
    bad = image(4, 4, 1, 16, true); expect_error(bad, Error::header);
    bad = image(1, 1, 0, 8, true); expect_error(bad, Error::header);
    bad = good; put(bad, 12, std::numeric_limits<std::uint32_t>::max());
    expect_error(bad, Error::unsupported);
    put(bad, 16, 0x136); expect_error(bad, Error::header);

    // Preserve the general header predicate's cube/volume rules separately from
    // the stricter ETC1 subset. Header acceptance never implies payload safety.
    bad = good; put(bad, 16, 0x1036); put(bad, 48, 6);
    CHECK(nova3::valid_pvr_v2_header(bad.data(), 52));
    expect_error(bad, Error::unsupported);
    bad = image(4, 4, 3, 8, true); put(bad, 16, 0x4136); put(bad, 48, 8);
    CHECK(nova3::valid_pvr_v2_header(bad.data(), 52));
    expect_error(bad, Error::unsupported);
    put(bad, 12, 2); CHECK(!nova3::valid_pvr_v2_header(bad.data(), 52));
}

void payload_bounds() {
    const auto good = image(8, 2, 3, 40, true);
    for (std::size_t length = 0; length < good.size(); ++length) {
        const Bytes truncated(good.begin(), good.begin() + length);
        expect_error(truncated, length < 52 ? Error::header : Error::bounds);
    }
    for (const auto declared : {0u, 39u, 41u, 0xffffffffu}) {
        auto bad = good; put(bad, 20, declared); expect_error(bad, Error::bounds);
    }
    auto bad = good; bad.push_back(0); expect_error(bad, Error::bounds);
    put(bad, 20, 41); expect_error(bad, Error::bounds); // Self-consistent trailing byte.
    bad = good; bad.pop_back(); put(bad, 20, 39); expect_error(bad, Error::bounds);
    bad = image(4, 4, 2, 8, true); // Header advertises full chain but only base exists.
    expect_error(bad, Error::bounds);
    bad = image(4, 4, 0, 24, false); // Extra mips without the mip flag.
    expect_error(bad, Error::bounds);
    bad = image(16384, 16384, 14, 8, true); // Huge dimensions, tiny real allocation.
    expect_error(bad, Error::bounds);
}

void deterministic_mutations() {
    const auto good = image(8, 2, 3, 40, true);
    std::uint32_t random = 0x50565232;
    const nova3::Etc1Texture sentinel{91, 73, {{7, 5, 123, 456}, {3, 2, 579, 16}}};
    for (unsigned i = 0; i < 2048; ++i) {
        auto b = good;
        random ^= random << 13; random ^= random >> 17; random ^= random << 5;
        b[random % b.size()] ^= static_cast<std::uint8_t>(1u << ((random >> 16) % 8));
        auto out = sentinel;
        const auto error = nova3::read_etc1_pvr(b.data(), b.size(), out);
        if (error != Error::ok) {
            CHECK(equal(out, sentinel));
        } else {
            CHECK(nova3::valid_pvr_v2_header(b.data(), b.size()));
            CHECK(out.width && out.height && !out.mips.empty() && out.mips.size() <= 15);
            std::size_t end = 52;
            for (const auto& mip : out.mips) {
                CHECK(mip.offset == end && mip.size <= b.size() - end);
                end += mip.size;
            }
            CHECK(end == b.size());
        }
    }
}
}

int main() {
    mip_layout_and_decode();
    header_and_subset();
    payload_bounds();
    deterministic_mutations();
    std::cout << "PVRv2 synthetic headers, mip layout, payload bounds and atomic errors passed\n";
}
