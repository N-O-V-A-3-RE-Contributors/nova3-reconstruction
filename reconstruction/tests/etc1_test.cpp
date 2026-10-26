#include "etc1.hpp"
#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>

namespace {
using Bytes = std::vector<std::uint8_t>;
using Block = std::array<std::uint8_t, 8>;
using Error = nova3::Etc1Error;

void check(bool ok, const char* expression, int line) {
    if (!ok) {
        std::cerr << "ETC1 line " << line << ": " << expression << '\n';
        std::exit(1);
    }
}
#define CHECK(expression) check((expression), #expression, __LINE__)

// Synthetic ETC1 vectors only; these do not establish original-game equivalence.
void selector_vectors() {
    constexpr int modifiers[8][4] = {
        {2, 8, -2, -8}, {5, 17, -5, -17}, {9, 29, -9, -29},
        {13, 42, -13, -42}, {18, 60, -18, -60}, {24, 80, -24, -80},
        {33, 106, -33, -106}, {47, 183, -47, -183}
    };
    // Selector bit planes encode 0,1,2,3 across columns or down rows.
    // Literal bytes detect byte order, bit-plane order and x/y transposition.
    for (unsigned horizontal = 0; horizontal < 2; ++horizontal) {
        for (unsigned first_table = 0; first_table < 8; ++first_table) {
            for (unsigned second_table = 0; second_table < 8; ++second_table) {
                Block block{0x48, 0x59, 0x6a, 0, 0xff, 0x00, 0xf0, 0xf0};
                if (horizontal) {
                    block[4] = 0xcc; block[5] = 0xcc;
                    block[6] = 0xaa; block[7] = 0xaa;
                }
                block[3] = static_cast<std::uint8_t>(first_table * 32 + second_table * 4 + horizontal);
                Bytes out;
                CHECK(nova3::decode_etc1(block.data(), block.size(), 4, 4, out) == Error::ok);
                CHECK(out.size() == 48);
                for (unsigned y = 0; y < 4; ++y) {
                    for (unsigned x = 0; x < 4; ++x) {
                        const unsigned selector = horizontal ? y : x;
                        const bool second = selector >= 2;
                        const int modifier = modifiers[second ? second_table : first_table][selector];
                        for (unsigned c = 0; c < 3; ++c) {
                            const int base = (second ? 8 + c : 4 + c) * 17;
                            CHECK(out[(y * 4 + x) * 3 + c] == std::clamp(base + modifier, 0, 255));
                        }
                    }
                }
            }
        }
    }
}

void differential_endpoints() {
    constexpr int deltas[8] = {0, 1, 2, 3, -4, -3, -2, -1};
    for (unsigned channel = 0; channel < 3; ++channel) {
        for (unsigned base = 0; base < 32; ++base) {
            for (unsigned encoded_delta = 0; encoded_delta < 8; ++encoded_delta) {
                Block block{0x80, 0x80, 0x80, 2, 0, 0, 0, 0};
                block[channel] = static_cast<std::uint8_t>(base * 8 + encoded_delta);
                Bytes out{9, 8, 7};
                const Bytes saved = out;
                const int endpoint = static_cast<int>(base) + deltas[encoded_delta];
                if (endpoint < 0 || endpoint > 31) {
                    // Invalid second endpoint is rejected even for a crop using only the first.
                    for (const auto width : {1u, 4u}) {
                        CHECK(nova3::decode_etc1(block.data(), block.size(), width, 4, out) == Error::invalid_block);
                        CHECK(out == saved);
                    }
                } else {
                    CHECK(nova3::decode_etc1(block.data(), block.size(), 4, 4, out) == Error::ok);
                    for (unsigned y = 0; y < 4; ++y) {
                        for (unsigned x = 0; x < 4; ++x) {
                            for (unsigned c = 0; c < 3; ++c) {
                                const int value = c != channel ? 16 : (x < 2 ? static_cast<int>(base) : endpoint);
                                CHECK(out[(y * 4 + x) * 3 + c] == std::min(255, value * 8 + value / 4 + 2));
                            }
                        }
                    }
                }
            }
        }
    }
}

void crop_pitch_and_flip() {
    // 5x7 uses four blocks, a deliberately unaligned start, and three poison
    // padding bytes between block rows. No final-row padding is supplied.
    Bytes source(36, 0xff);
    for (unsigned by = 0; by < 2; ++by) {
        for (unsigned bx = 0; bx < 2; ++bx) {
            const auto at = 1 + by * 19 + bx * 8;
            std::fill_n(source.begin() + at, 8, 0);
            const auto base = static_cast<std::uint8_t>((1 + (by * 2 + bx) * 2) * 0x11);
            std::fill_n(source.begin() + at, 3, base);
        }
    }
    for (unsigned height = 5; height <= 8; ++height) {
        for (unsigned width = 5; width <= 8; ++width) {
            for (const bool flip : {false, true}) {
                Bytes out;
                CHECK(nova3::decode_etc1(source.data() + 1, 35, width, height, out, flip, 19) == Error::ok);
                CHECK(out.size() == width * height * 3);
                for (unsigned y = 0; y < height; ++y) {
                    for (unsigned x = 0; x < width; ++x) {
                        const auto source_y = flip ? height - 1 - y : y;
                        const auto block = (source_y / 4) * 2 + x / 4;
                        for (unsigned c = 0; c < 3; ++c)
                            CHECK(out[(y * width + x) * 3 + c] == (1 + block * 2) * 17 + 2);
                    }
                }
            }
        }
    }
    // Every truncation, including the final block's last byte, must be atomic.
    for (std::size_t length = 0; length < 35; ++length) {
        const Bytes truncated(source.begin() + 1, source.begin() + 1 + length);
        Bytes out{1, 2, 3};
        CHECK(nova3::decode_etc1(truncated.data(), truncated.size(), 5, 7, out, false, 19) == Error::bounds);
        CHECK(out == Bytes({1, 2, 3}));
    }
    // A malformed final block cannot expose partially decoded output.
    source[28] = 4; source[31] = 2;
    Bytes out{1, 2, 3};
    CHECK(nova3::decode_etc1(source.data() + 1, 35, 5, 7, out, false, 19) == Error::invalid_block);
    CHECK(out == Bytes({1, 2, 3}));
}

void bounds_and_aliasing() {
    const Block block{};
    Bytes out{42};
    const auto fail = [&](const std::uint8_t* data, std::size_t size, std::uint32_t w,
                          std::uint32_t h, Error expected, std::size_t pitch = 0,
                          std::size_t limit = 16 * 1024 * 1024) {
        CHECK(nova3::decode_etc1(data, size, w, h, out, false, pitch, limit) == expected);
        CHECK(out == Bytes({42}));
    };
    fail(nullptr, 8, 4, 4, Error::bounds);
    fail(block.data(), 8, 0, 4, Error::bounds);
    fail(block.data(), 8, 4, 0, Error::bounds);
    fail(block.data(), 8, 4, 4, Error::bounds, 7);
    fail(block.data(), 8, 5, 4, Error::bounds, 8);
    fail(block.data(), 8, 4, 4, Error::limit, 0, 15);
    fail(block.data(), 8, 1, 1, Error::limit, 0, 0);
    const auto maximum = std::numeric_limits<std::size_t>::max();
    fail(block.data(), 8, 4, 8, Error::bounds, maximum);
    fail(block.data(), 8, 4, 12, Error::bounds, maximum / 2 + 1);
    const auto largest_dimension = std::numeric_limits<std::uint32_t>::max();
    fail(block.data(), 8, largest_dimension, largest_dimension, Error::limit, 0, maximum);
    // This fits size_t*3 arithmetic on 64-bit hosts, but not vector::max_size.
    if (std::uint64_t(largest_dimension) * (1u << 30) > Bytes{}.max_size() / 3)
        fail(block.data(), 8, largest_dimension, 1u << 30, Error::limit, 0, maximum);

    CHECK(nova3::decode_etc1(block.data(), 8, 4, 4, out, false, maximum, 16) == Error::ok);
    CHECK(out == Bytes(48, 2)); // A single row never uses pitch to step forward.
    for (unsigned height = 1; height <= 4; ++height) {
        for (unsigned width = 1; width <= 4; ++width) {
            CHECK(nova3::decode_etc1(block.data(), 8, width, height, out) == Error::ok);
            CHECK(out == Bytes(width * height * 3, 2));
        }
    }
    Bytes alias(9, 0); alias.back() = 0xff; // Trailing data is ignored.
    CHECK(nova3::decode_etc1(alias.data(), alias.size(), 4, 4, alias) == Error::ok);
    CHECK(alias == Bytes(48, 2));
}
}

int main() {
    selector_vectors();
    differential_endpoints();
    crop_pitch_and_flip();
    bounds_and_aliasing();
    std::cout << "ETC1 synthetic selectors, endpoints, crops, pitches, limits and atomic errors passed\n";
}
