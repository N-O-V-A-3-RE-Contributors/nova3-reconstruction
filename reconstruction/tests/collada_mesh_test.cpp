#include "collada_mesh.hpp"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace {
using Bytes = std::vector<std::uint8_t>;
using nova3::ColladaMesh;
using nova3::MeshError;
constexpr std::size_t root = 128, geometry = 320, mesh = 336;
constexpr std::size_t attributes = 432, buffer = 480, vertex_block = 600;

void check(bool ok, const std::string& label) {
    if (!ok) { std::cerr << "mesh assertion failed: " << label << '\n'; std::exit(1); }
}
void put(Bytes& b, std::size_t at, std::uint32_t value) {
    check(at <= b.size() && b.size() - at >= 4, "fixture word bounds");
    for (unsigned i = 0; i < 4; ++i) b[at+i] = static_cast<std::uint8_t>(value >> (i*8));
}
std::uint32_t word(const Bytes& b, std::size_t at) {
    check(at <= b.size() && b.size() - at >= 4, "fixture read bounds");
    return std::uint32_t(b[at]) | (std::uint32_t(b[at+1]) << 8) |
        (std::uint32_t(b[at+2]) << 16) | (std::uint32_t(b[at+3]) << 24);
}
void floating(Bytes& b, std::size_t at, float value) {
    std::uint32_t bits;
    static_assert(sizeof(value) == sizeof(bits), "float32 fixture required");
    std::memcpy(&bits, &value, sizeof(bits)); put(b, at, bits);
}
void indices(Bytes& b, unsigned width, std::array<std::uint32_t, 3> values) {
    const std::size_t at = word(b, 76) + 4;
    check(at + width*values.size() <= b.size(), "fixture index bounds");
    for (std::size_t i = 0; i < values.size(); ++i)
        for (unsigned byte = 0; byte < width; ++byte)
            b[at+i*width+byte] = static_cast<std::uint8_t>(values[i] >> (byte*8));
}

// Entirely synthetic bytes, with explicitly selected index width. These tests
// exercise the source reader's subset and safety policy, not an original mesh
// or a native oracle. Empty-mesh acceptance and index-width selection remain
// NOT MATCHED; original handling of these cases needs independent evidence.
Bytes fixture(std::uint32_t vertices = 3, std::uint32_t stride = 12,
              std::uint32_t offset = 0, unsigned index_width = 2) {
    const auto raw_index = static_cast<std::uint32_t>(vertex_block + 4 + vertices*stride);
    Bytes b(raw_index + 4 + 3*index_width, 0);
    b[0] = 'B'; b[1] = 'R'; b[2] = 'E'; b[3] = 'S';
    for (auto field : std::vector<std::pair<std::size_t, std::uint32_t>>{
        {12, static_cast<std::uint32_t>(b.size())}, {16, 1}, {24, 60}, {32, root},
        {40, 64}, {48, 2}, {60, 24}, {64, 4+vertices*stride}, {68, vertex_block},
        {72, 4+3*index_width}, {76, raw_index}, {88, 4}, {root+0x84, 1},
        {root+0x88, geometry}, {geometry, 92}, {geometry+12, mesh}, {mesh, 1},
        {mesh+4, vertices}, {mesh+8, 1}, {mesh+12, attributes},
        {mesh+16, 1}, {mesh+20, attributes+4}, {mesh+24, 1}, {mesh+28, attributes+8},
        {mesh+32, 1}, {mesh+36, attributes+12}, {mesh+0x30, vertex_block},
        {mesh+0x38, 1}, {mesh+0x3c, buffer}, {attributes, stride},
        {attributes+4, offset}, {attributes+8, 6}, {attributes+12, 3},
        {buffer+0x24, vertices ? vertices-1 : 0}, {buffer+0x28, 3},
        {buffer+0x2c, raw_index}, {vertex_block, 1}, {raw_index, 1}})
        put(b, field.first, field.second);
    std::memcpy(b.data()+92, "test", 4);
    for (std::uint32_t i = 0; i < vertices; ++i) {
        const auto at = vertex_block + 4 + i*stride + offset;
        floating(b, at, static_cast<float>(i));
        floating(b, at+4, -static_cast<float>(i));
        floating(b, at+8, static_cast<float>(i)*0.5f);
    }
    const float last = vertices ? static_cast<float>(vertices-1) : 0;
    floating(b, mesh+0x44, -last);
    floating(b, mesh+0x4c, last);
    floating(b, mesh+0x54, last*0.5f);
    indices(b, index_width, {0, 1, 2});
    return b;
}

ColladaMesh sentinel() {
    ColladaMesh result;
    result.name = std::string("sentinel\0name", 13);
    result.attributes = {{17, 5, 9, 4}, {12, 0, 6, 3}};
    result.positions = {{{1, 2, 3}}, {{-4, -5, -6}}};
    result.buffers = {{{2, 1, 0}}, {{9, 8, 7}}};
    result.bounds = {{-6, -5, -4, 3, 2, 1}};
    return result;
}
bool same_mesh(const ColladaMesh& a, const ColladaMesh& b) {
    if (a.name != b.name || a.positions != b.positions || a.bounds != b.bounds ||
        a.attributes.size() != b.attributes.size() || a.buffers.size() != b.buffers.size()) return false;
    for (std::size_t i = 0; i < a.attributes.size(); ++i) {
        const auto& x = a.attributes[i]; const auto& y = b.attributes[i];
        if (x.stride != y.stride || x.offset != y.offset || x.type != y.type || x.components != y.components) return false;
    }
    for (std::size_t i = 0; i < a.buffers.size(); ++i)
        if (a.buffers[i].indices != b.buffers[i].indices) return false;
    return true;
}
void expect_error(const std::string& label, const Bytes& b, MeshError expected,
                  std::size_t which = 0, std::size_t vertex_limit = 1000000,
                  std::size_t index_limit = 3000000) {
    auto output = sentinel(); const auto before = output; const auto input = b;
    const auto error = nova3::read_collada_mesh(b.data(), b.size(), which, output, vertex_limit, index_limit);
    check(error == expected, label + " error=" + std::to_string(static_cast<int>(error)));
    check(same_mesh(output, before), label + " preserves complete output");
    check(b == input, label + " preserves input");
}
ColladaMesh decode(const Bytes& b, std::size_t which = 0,
                   std::size_t vertex_limit = 1000000, std::size_t index_limit = 3000000) {
    auto output = sentinel(); const auto input = b;
    check(nova3::read_collada_mesh(b.data(), b.size(), which, output, vertex_limit, index_limit) == MeshError::ok,
          "synthetic mesh decodes");
    check(b == input, "successful decode preserves input");
    return output;
}
void changed_word(const Bytes& base, std::size_t at, std::uint32_t value,
                  MeshError expected, const std::string& label) {
    auto b = base; put(b, at, value); expect_error(label, b, expected);
}

void header_and_table_errors() {
    const auto b = fixture();
    auto output = sentinel(); const auto before = output;
    check(nova3::read_collada_mesh(nullptr, b.size(), 0, output) == MeshError::header, "null input");
    check(same_mesh(output, before), "null input preserves output");
    auto bad = b; bad[0] = 'X'; expect_error("magic", bad, MeshError::header);
    changed_word(b, 4, 0x80000000u, MeshError::unsupported, "already relocated");
    changed_word(b, 20, 1, MeshError::unsupported, "nonzero base");
    changed_word(b, 12, static_cast<std::uint32_t>(b.size()-1), MeshError::bounds, "declared size short");
    changed_word(b, 12, static_cast<std::uint32_t>(b.size()+1), MeshError::bounds, "declared size long");
    changed_word(b, 24, 59, MeshError::header, "relocation table aliases header");
    changed_word(b, 16, 0xffffffffu, MeshError::header, "relocation count overflow guard");
    changed_word(b, 60, static_cast<std::uint32_t>(b.size()-3), MeshError::header, "truncated relocation slot");
    changed_word(b, 32, 0xffffffffu, MeshError::bounds, "root outside file");
    changed_word(b, 32, static_cast<std::uint32_t>(b.size()-0x8b), MeshError::bounds, "root one byte short");
    changed_word(b, root+0x80, 1, MeshError::unsupported, "shared root");
    changed_word(b, root+0x84, 0, MeshError::bounds, "no geometry");
    changed_word(b, root+0x84, 0xffffffffu, MeshError::bounds, "geometry count overflow guard");
    changed_word(b, root+0x88, static_cast<std::uint32_t>(b.size()-15), MeshError::bounds, "geometry descriptor one byte short");
    expect_error("geometry count boundary", b, MeshError::bounds, 1);
    expect_error("geometry index maximum", b, MeshError::bounds, std::numeric_limits<std::size_t>::max());
    changed_word(b, geometry+8, 1, MeshError::unsupported, "geometry tag");
    changed_word(b, geometry+12, static_cast<std::uint32_t>(b.size()-0x57), MeshError::bounds, "mesh descriptor one byte short");
    changed_word(b, geometry, 3, MeshError::bounds, "name prefix underflow");
    changed_word(b, geometry, 0xffffffffu, MeshError::bounds, "name pointer outside file");
    changed_word(b, 88, 4097, MeshError::bounds, "name length over cap");
    changed_word(b, 88, static_cast<std::uint32_t>(b.size()), MeshError::bounds, "name payload truncated");
    changed_word(b, mesh, 0, MeshError::unsupported, "non-interleaved vertices");
    changed_word(b, mesh+0x3c, static_cast<std::uint32_t>(b.size()-55), MeshError::bounds, "buffer descriptor one byte short");
    for (unsigned field = 0; field < 4; ++field)
        changed_word(b, mesh+12+field*8, static_cast<std::uint32_t>(b.size()-3), MeshError::bounds,
                     "attribute table " + std::to_string(field) + " one byte short");
    for (unsigned field = 1; field < 4; ++field)
        changed_word(b, mesh+8+field*8, 0, MeshError::invalid, "unequal attribute table lengths");
    bad = b;
    for (unsigned field = 0; field < 4; ++field) put(bad, mesh+8+field*8, 0);
    expect_error("no position attribute", bad, MeshError::unsupported);
}

void payload_errors() {
    const auto b = fixture();
    changed_word(b, attributes, 11, MeshError::unsupported, "stride below float3");
    changed_word(b, attributes+4, 13, MeshError::unsupported, "offset greater than stride");
    changed_word(b, attributes+4, 1, MeshError::unsupported, "position exceeds stride by one");
    changed_word(b, attributes+8, 5, MeshError::unsupported, "position type");
    changed_word(b, attributes+12, 2, MeshError::unsupported, "position arity");
    changed_word(b, attributes, 0xffffffffu, MeshError::bounds, "vertex byte product does not wrap");
    changed_word(b, 40, 0xffffffffu, MeshError::bounds, "tail table outside file");
    changed_word(b, 40, static_cast<std::uint32_t>(b.size()-15), MeshError::bounds, "tail table one byte short");
    changed_word(b, 48, 0, MeshError::bounds, "no serialized entries");
    changed_word(b, mesh+0x30, 601, MeshError::bounds, "missing vertex block");
    changed_word(b, 64, 3, MeshError::bounds, "vertex block shorter than prefix");
    changed_word(b, 64, 39, MeshError::bounds, "vertex block one byte short");
    changed_word(b, 64, 0xffffffffu, MeshError::bounds, "vertex block outside file");
    changed_word(b, buffer, 1, MeshError::unsupported, "primitive type");
    changed_word(b, buffer+12, 1, MeshError::unsupported, "position stream selector");
    changed_word(b, buffer+0x20, 3, MeshError::invalid, "minimum greater than maximum");
    changed_word(b, buffer+0x24, 3, MeshError::invalid, "maximum equals vertex count");
    changed_word(b, buffer+0x28, 2, MeshError::invalid, "incomplete triangle");
    changed_word(b, buffer+0x2c, 601, MeshError::bounds, "missing index block");
    changed_word(b, 72, 3, MeshError::bounds, "index block shorter than prefix");
    changed_word(b, 72, 9, MeshError::bounds, "index block one byte short");
    auto bad = b; indices(bad, 2, {0, 1, 3});
    expect_error("relative index over maximum", bad, MeshError::invalid);
    indices(bad, 2, {0, 1, 0xffffu}); expect_error("16-bit sentinel is not a vertex", bad, MeshError::invalid);
    // All axes and all six bounds must be finite, including late failures after
    // positions have already been decoded. Atomicity checks compare every field.
    for (const auto bits : {0x7f800000u, 0xff800000u, 0x7fc00001u}) {
        for (unsigned axis = 0; axis < 3; ++axis)
            changed_word(b, vertex_block+4+2*12+axis*4, bits, MeshError::invalid, "non-finite position");
        for (unsigned axis = 0; axis < 6; ++axis)
            changed_word(b, mesh+0x40+axis*4, bits, MeshError::invalid, "non-finite bounds");
    }
}

void limits_and_success_boundaries() {
    const auto b = fixture(); const auto decoded = decode(b, 0, 3, 3);
    check(decoded.name == "test" && decoded.positions == std::vector<std::array<float, 3>>{{{0, 0, 0}}, {{1, -1, 0.5f}}, {{2, -2, 1}}}, "exact positions");
    check(decoded.bounds == std::array<float, 6>{{0, -2, 0, 2, 0, 1}}, "exact bounds");
    check(decoded.attributes.size() == 1 && decoded.attributes[0].stride == 12 &&
          decoded.attributes[0].offset == 0 && decoded.attributes[0].type == 6 &&
          decoded.attributes[0].components == 3, "attribute metadata");
    check(decoded.buffers.size() == 1 && decoded.buffers[0].indices == std::vector<std::uint32_t>{0, 1, 2}, "triangle indices");
    expect_error("vertex limit one below", b, MeshError::limit, 0, 2);
    expect_error("zero vertex limit", b, MeshError::limit, 0, 0);
    expect_error("index limit one below", b, MeshError::limit, 0, 3, 2);
    expect_error("zero index limit", b, MeshError::limit, 0, 3, 0);
    changed_word(b, mesh+4, 0xffffffffu, MeshError::limit, "vertex count cap");
    changed_word(b, mesh+0x38, 10001, MeshError::limit, "buffer count cap");
    changed_word(b, 48, 10001, MeshError::limit, "tail count cap");
    for (unsigned field = 0; field < 4; ++field)
        changed_word(b, mesh+8+field*8, 65, MeshError::limit, "attribute count cap");

    auto two = b; std::copy_n(two.begin()+buffer, 56, two.begin()+buffer+56);
    put(two, mesh+0x38, 2);
    auto out = decode(two, 0, 3, 6);
    check(out.buffers.size() == 2 && out.buffers[0].indices == out.buffers[1].indices, "aggregate index limit exact");
    expect_error("aggregate index limit one below", two, MeshError::limit, 0, 3, 5);
    put(two, buffer+56, 1); expect_error("late unsupported second buffer", two, MeshError::unsupported);

    auto based = b; put(based, buffer+0x20, 1); indices(based, 2, {0, 1, 1});
    check(decode(based).buffers[0].indices == std::vector<std::uint32_t>{1, 2, 2}, "nonzero vertex base");
    put(based, buffer+0x20, 2); indices(based, 2, {0, 0, 0});
    check(decode(based).buffers[0].indices == std::vector<std::uint32_t>{2, 2, 2}, "minimum equals maximum");
    indices(based, 2, {0, 0, 1}); expect_error("relative range checked before rebasing", based, MeshError::invalid);

    auto padded = fixture(3, 21, 5); out = decode(padded);
    check(out.positions == decoded.positions && out.attributes[0].stride == 21 && out.attributes[0].offset == 5,
          "interleaved padding and unaligned float reads");
    Bytes unaligned(b.size()+1); std::copy(b.begin(), b.end(), unaligned.begin()+1);
    check(nova3::read_collada_mesh(unaligned.data()+1, b.size(), 0, out) == MeshError::ok && same_mesh(out, decoded), "unaligned input pointer");

    auto second = b; std::copy_n(second.begin()+geometry, 16, second.begin()+geometry-16);
    put(second, root+0x84, 2); put(second, root+0x88, geometry-16); put(second, geometry-8, 1);
    check(same_mesh(decode(second, 1), decoded), "select second geometry without decoding unsupported first");
    expect_error("selected first geometry unsupported", second, MeshError::unsupported);

    auto many = b; many.resize(b.size()+4*64*4); put(many, 12, static_cast<std::uint32_t>(many.size()));
    for (unsigned field = 0; field < 4; ++field) {
        const auto at = static_cast<std::uint32_t>(b.size()+field*64*4);
        put(many, mesh+8+field*8, 64); put(many, mesh+12+field*8, at);
        for (unsigned i = 0; i < 64; ++i) put(many, at+i*4, word(b, attributes+field*4));
    }
    check(decode(many).attributes.size() == 64, "attribute count exactly at cap");

    auto named = b; const auto name = static_cast<std::uint32_t>(named.size()+4);
    named.resize(name+4096, 'x'); put(named, name-4, 4096); put(named, geometry, name);
    put(named, 12, static_cast<std::uint32_t>(named.size()));
    check(decode(named).name == std::string(4096, 'x'), "name exactly at cap");
    named.push_back('x'); put(named, name-4, 4097);
    put(named, 12, static_cast<std::uint32_t>(named.size()));
    expect_error("complete name payload over cap", named, MeshError::bounds);
    put(named, name-4, 0); check(decode(named).name.empty(), "empty inline name");

    auto empty = fixture(0); put(empty, mesh+0x38, 0);
    out = decode(empty, 0, 0, 0);
    check(out.positions.empty() && out.buffers.empty(), "empty mesh current subset policy");
    auto no_indices = b; put(no_indices, buffer+0x28, 0); put(no_indices, 72, 4);
    check(decode(no_indices, 0, 3, 0).buffers[0].indices.empty(), "empty index block at zero limit");
}

void index_width_boundaries() {
    // The source uses the inclusive relative range (max-min), not max alone.
    auto narrow = fixture(65538); put(narrow, buffer+0x20, 3);
    indices(narrow, 2, {0, 0x8000u, 0xfffeu});
    check(decode(narrow).buffers[0].indices == std::vector<std::uint32_t>{3, 32771, 65537}, "largest 16-bit relative range");
    indices(narrow, 2, {0, 1, 0xffffu}); expect_error("16-bit range plus one", narrow, MeshError::invalid);

    auto wide = fixture(65539, 12, 0, 4); put(wide, buffer+0x20, 3);
    indices(wide, 4, {0, 0x8000u, 0xffffu});
    check(decode(wide).buffers[0].indices == std::vector<std::uint32_t>{3, 32771, 65538}, "first 32-bit relative range");
    changed_word(wide, 72, 15, MeshError::bounds, "32-bit index block one byte short");
    indices(wide, 4, {0, 1, 0x10000u}); expect_error("32-bit range plus one", wide, MeshError::invalid);
    indices(wide, 4, {0, 1, 0xffffffffu}); expect_error("32-bit index cannot wrap when rebased", wide, MeshError::invalid);
}

void truncation_and_mutations() {
    const auto b = fixture();
    // Allocate only the visible prefix so sanitizers also see its real boundary.
    // Repair the size field to exercise nested checks beyond declared-size checks.
    for (std::size_t n = 0; n < b.size(); ++n) {
        Bytes prefix(b.begin(), b.begin()+n);
        if (n >= 60) put(prefix, 12, static_cast<std::uint32_t>(n));
        expect_error("prefix length " + std::to_string(n), prefix, n < 60 ? MeshError::header :
                     n < 64 ? MeshError::header : MeshError::bounds);
    }
    // Fixed-seed mutations supplement explicit branch tests; not an oracle.
    std::uint32_t random = 0x4d455348;
    for (int i = 0; i < 10000; ++i) {
        auto malformed = b;
        for (int j = 0; j < 4; ++j) {
            random = random*1664525u+1013904223u; const auto at = random % malformed.size();
            random = random*1664525u+1013904223u;
            malformed[at] ^= static_cast<std::uint8_t>((random >> 24) | 1);
        }
        auto output = sentinel(); const auto before = output; const auto input = malformed;
        const auto error = nova3::read_collada_mesh(malformed.data(), malformed.size(), 0, output);
        check(malformed == input, "mutation input unchanged");
        if (error == MeshError::ok) {
            for (const auto& group : output.buffers)
                for (auto index : group.indices) check(index < output.positions.size(), "mutation index bounds");
        } else check(same_mesh(output, before), "mutation preserves complete output on error");
    }
}
} // namespace

int main() {
    header_and_table_errors();
    payload_errors();
    limits_and_success_boundaries();
    index_width_boundaries();
    truncation_and_mutations();
    std::cout << "synthetic COLLADA mesh boundaries, negative paths, atomicity and 10000 fixed-seed mutations passed; NOT MATCHED\n";
}
