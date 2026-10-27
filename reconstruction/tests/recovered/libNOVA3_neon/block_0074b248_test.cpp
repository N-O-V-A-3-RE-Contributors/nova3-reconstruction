// Standalone build (no original executable required):
/* c++ -std=c++17 -O2 -Wall -Wextra -Werror -pedantic \
     reconstruction/recovered/libNOVA3_neon/block_0074b248.cpp \
     reconstruction/tests/recovered/libNOVA3_neon/block_0074b248_test.cpp \
     -o /tmp/nova3-task39-test */
// Native-oracle reproduction is recorded in the task's evidence JSON.
#include <array>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace nova3::recovered::block_0074b248 {
std::uint32_t compute_pitch(std::uint32_t format, std::uint32_t width);
std::uint32_t compute_size_in_bytes(std::uint32_t format, std::uint32_t width,
                                    std::uint32_t height);
}

namespace {
using nova3::recovered::block_0074b248::compute_pitch;
using nova3::recovered::block_0074b248::compute_size_in_bytes;
unsigned checks = 0;

void equal(std::uint32_t actual, std::uint32_t expected, const char* label) {
    ++checks;
    if (actual != expected) {
        std::cerr << label << ": got " << actual << ", expected " << expected << '\n';
        throw std::runtime_error("pixel sizing regression");
    }
}

void golden_cases() {
    // Outputs observed by executing the original ARM entries with synthetic
    // dimensions 13x9. These are scalar test results, not copied game data.
    constexpr std::array<std::uint32_t, 50> pitches = {
        13,26,13,26,26,26,26,26,26,26,39,39,52,52,52,52,52,
        32,32,64,64,16,16,32,32,16,16,32,32,32,32,64,32,64,64,
        32,64,78,104,156,208,26,26,52,52,52,1,6,13,0
    };
    constexpr std::array<std::uint32_t, 50> sizes = {
        117,234,117,234,234,234,234,234,234,234,351,351,
        468,468,468,468,468,96,96,192,192,48,48,96,96,48,48,
        96,96,96,96,192,96,192,192,96,192,702,936,1404,1872,
        234,234,468,468,468,9,54,117,0
    };
    for (std::uint32_t format = 0; format < pitches.size(); ++format) {
        equal(compute_pitch(format, 13), pitches[format], "golden row pitch");
        equal(compute_size_in_bytes(format, 13, 9), sizes[format], "golden extent");
    }
}

void boundaries() {
    equal(compute_pitch(46, 7), 0, "1-bit partial byte");
    equal(compute_pitch(46, 8), 1, "1-bit complete byte");
    equal(compute_pitch(47, 1), 0, "4-bit partial byte");
    equal(compute_pitch(47, 2), 1, "4-bit complete byte");
    equal(compute_size_in_bytes(46, 7, 8), 0, "truncation per row");
    equal(compute_size_in_bytes(47, 1, 8), 0, "no packing across rows");
    equal(compute_pitch(29, 0), 0, "empty compressed row");
    equal(compute_pitch(29, 1), 8, "partial block");
    equal(compute_pitch(29, 4), 8, "whole block");
    equal(compute_pitch(29, 5), 16, "next block");
    equal(compute_pitch(21, 8), 8, "wide block");
    equal(compute_pitch(21, 9), 16, "next wide block");
    equal(compute_size_in_bytes(17, 0, 0), 0, "no minimum");
    equal(compute_size_in_bytes(17, 4, 5), 16, "block rows round up");
    equal(compute_size_in_bytes(21, 0, 0), 32, "32-byte minimum");
    equal(compute_size_in_bytes(23, 1, 16), 32, "minimum on total extent");
    equal(compute_size_in_bytes(23, 1, 17), 40, "above minimum");
    equal(compute_size_in_bytes(29, 0, 8), 8, "8-byte minimum");
    equal(compute_size_in_bytes(31, 0, 0), 16, "16-byte minimum");
    equal(compute_pitch(41, 1), 2, "format41 uses bits, not block bytes");
    equal(compute_size_in_bytes(49, 123, 456), 0, "zero descriptor");
}

void overflow() {
    constexpr auto max = std::numeric_limits<std::uint32_t>::max();
    equal(compute_pitch(0, max), 536870911, "bits wrap before shift");
    equal(compute_pitch(10, max), 536870909, "24-bit product wrap");
    equal(compute_pitch(0, 0x80000000u), 0, "8-bit product wrap");
    equal(compute_pitch(47, max), 536870911, "4-bit product wrap");
    equal(compute_pitch(17, max - 3), max - 7, "last block before round overflow");
    equal(compute_pitch(17, max - 2), 0, "block round overflow");
    equal(compute_pitch(21, max), 0, "wide block round overflow");
    equal(compute_size_in_bytes(17, max - 3, 8), max - 15, "size product wrap");
    equal(compute_size_in_bytes(17, 4, max), 0, "height round overflow");
    equal(compute_size_in_bytes(29, 4, max), 8, "minimum after height wrap");
    equal(compute_size_in_bytes(23, 0x80000000u, 3), 32, "minimum after pitch wrap");
    equal(compute_size_in_bytes(21, 0x80000000u, 8), 32, "minimum after size wrap");
}

void invalid_ids() {
    // Added safety policy; the native functions index their table unchecked.
    for (auto format : {50u, 51u, 0xffffffffu}) {
        bool rejected = false;
        try { (void)compute_pitch(format, 1); }
        catch (const std::out_of_range&) { rejected = true; }
        equal(rejected, true, "invalid pitch format");
        rejected = false;
        try { (void)compute_size_in_bytes(format, 1, 1); }
        catch (const std::out_of_range&) { rejected = true; }
        equal(rejected, true, "invalid size format");
    }
}
} // namespace

int main(int argc, char** argv) {
    try {
        // Source-only query interface for a separate local research oracle.
        // Each decimal input line is: format width height. No native code is
        // loaded or executed by this program.
        if (argc == 2 && std::string(argv[1]) == "--query") {
            std::uint64_t format, width, height;
            while (std::cin >> format >> width >> height) {
                constexpr auto max = std::numeric_limits<std::uint32_t>::max();
                if (format > 49 || width > max || height > max) return 2;
                std::cout << compute_pitch(static_cast<std::uint32_t>(format),
                                           static_cast<std::uint32_t>(width)) << ' '
                          << compute_size_in_bytes(static_cast<std::uint32_t>(format),
                                                   static_cast<std::uint32_t>(width),
                                                   static_cast<std::uint32_t>(height)) << '\n';
            }
            return std::cin.eof() ? 0 : 2;
        }
        if (argc != 1) return 2;
        golden_cases();
        boundaries();
        overflow();
        invalid_ids();
        std::cout << "PASS: " << checks << " pixel sizing checks\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
