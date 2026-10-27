// Standalone task test: no CMake/header edit is allocated to this issue.
// Compile this TU with reconstruction/engine/scramble.cpp; do not additionally
// link block_002af5c4.cpp because its interface is included here.
// Source tests alone do NOT establish reference equivalence or matching.
#include "../../../recovered/libNOVA3_neon/block_002af5c4.cpp"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace recovered = nova3::recovered::block_002af5c4;

#define REQUIRE(condition) do { if (!(condition)) { \
    std::cerr << "task37:" << __LINE__ << ": " #condition "\n"; \
    std::exit(EXIT_FAILURE); } } while (false)

namespace {
struct Event {
    char kind;
    std::uint32_t value;
    bool relative;
};

class TestFile final : public recovered::FileProbe {
public:
    std::array<std::uint8_t, 4> bytes{};
    std::uint32_t cursor = 0;
    std::uint32_t read_result = 4;
    bool seek_start_result = true;
    bool seek_restore_result = true;
    std::uint32_t bytes_to_write = 4;
    std::vector<Event> events;

    std::uint32_t position() override {
        events.push_back({'p', cursor, false});
        return cursor;
    }
    bool seek(std::uint32_t offset, bool relative) override {
        const bool result = events.size() == 1 ? seek_start_result : seek_restore_result;
        events.push_back({'s', offset, relative});
        if (result) cursor = relative ? cursor + offset : offset;
        return result;
    }
    std::uint32_t read(std::uint8_t* destination, std::uint32_t count) override {
        events.push_back({'r', count, false});
        REQUIRE(count == 4);
        std::copy_n(bytes.begin(), bytes_to_write, destination);
        cursor += bytes_to_write;
        return read_result;
    }
};

void require_probe_trace(const TestFile& file, std::uint32_t initial) {
    REQUIRE(file.events.size() == 4);
    REQUIRE(file.events[0].kind == 'p' && file.events[0].value == initial);
    REQUIRE(file.events[1].kind == 's' && file.events[1].value == 0);
    REQUIRE(!file.events[1].relative);
    REQUIRE(file.events[2].kind == 'r' && file.events[2].value == 4);
    REQUIRE(file.events[3].kind == 's' && file.events[3].value == initial);
    REQUIRE(!file.events[3].relative);
}

std::array<std::uint8_t, 4> header_bytes(std::uint32_t word) {
    return {static_cast<std::uint8_t>(word), static_cast<std::uint8_t>(word >> 8),
            static_cast<std::uint8_t>(word >> 16), static_cast<std::uint8_t>(word >> 24)};
}

void probe_tests() {
    // Exhaust every two-byte prefix, including the SB path's ignored tail.
    // The integer predicate is independent of the source's byte comparisons.
    for (std::uint32_t prefix = 0; prefix < 65536; ++prefix) {
        TestFile file;
        file.bytes = header_bytes(0xa5370000u | prefix);
        file.cursor = 0x80000011u;
        const bool result = recovered::is_valid_scrambled_zip(file);
        REQUIRE(result == (prefix == 0x4253u));
        REQUIRE(file.cursor == 0x80000011u);
        require_probe_trace(file, 0x80000011u);
    }
    for (const auto word : {0x444e4553u, 0x444e4253u, 0x04034b50u, 0u, 0xffffffffu}) {
        for (const auto initial : {0u, 17u, 0x7fffffffu, 0x80000000u, 0xffffffffu}) {
            for (const auto read_result : {0u, 1u, 4u, 0xffffffffu}) {
                for (unsigned failures = 0; failures < 4; ++failures) {
                    TestFile file;
                    file.bytes = header_bytes(word);
                    file.cursor = initial;
                    file.read_result = read_result;
                    file.seek_start_result = !(failures & 1);
                    file.seek_restore_result = !(failures & 2);
                    const bool result = recovered::is_valid_scrambled_zip(file);
                    REQUIRE(result == ((word & 0xffffu) == 0x4253u || word == 0x444e4553u));
                    require_probe_trace(file, initial);
                    REQUIRE(file.cursor == (file.seek_restore_result ? initial :
                            (file.seek_start_result ? 0u : initial) + 4u));
                }
            }
        }
    }
    // Explicit source-policy tests, NOT original short-read equivalence tests.
    for (std::uint32_t written = 0; written < 4; ++written) {
        TestFile file;
        file.bytes = {'S', 'B', 0xff, 0xff};
        file.bytes_to_write = written;
        file.read_result = written;
        REQUIRE(recovered::is_valid_scrambled_zip(file) == (written >= 2));
        require_probe_trace(file, 0);
    }
}

struct LookupState {
    unsigned calls = 0;
    void* destination = nullptr;
    const char* filename = nullptr;
    std::uint32_t value = 0;
};

void* record_lookup(void* context, void* destination, const char* filename) {
    auto& state = *static_cast<LookupState*>(context);
    ++state.calls;
    state.destination = destination;
    state.filename = filename;
    *static_cast<std::uint32_t*>(destination) = state.value;
    return &state; // Deliberately different from the output storage.
}

void image_tests() {
    const char name[] = "synthetic-image.tga";
    for (const auto filename : {static_cast<const char*>(nullptr), "", name}) {
        for (const auto value : {0u, 1u, 0xffffffffu}) {
            std::uint32_t destination = 0x12345678;
            LookupState state;
            state.value = value;
            recovered::TextureLookup lookup{&state, record_lookup};
            REQUIRE(recovered::create_image_into(&destination, {filename}, lookup) == &destination);
            REQUIRE(state.calls == 1 && state.destination == &destination);
            REQUIRE(state.filename == filename && destination == value);
        }
    }
}

void xor_tests() {
    for (unsigned key_byte = 1; key_byte < 256; ++key_byte) {
        const char key[]{static_cast<char>(key_byte), 0};
        std::array<std::uint8_t, 258> data{};
        for (unsigned i = 0; i < data.size(); ++i) data[i] = static_cast<std::uint8_t>(i);
        const auto saved = data;
        REQUIRE(recovered::unscramble(data.data() + 1, 256, key));
        REQUIRE(data.front() == saved.front() && data.back() == saved.back());
        for (unsigned i = 0; i < 256; ++i) REQUIRE(data[i + 1] == (saved[i + 1] ^ key_byte));
        REQUIRE(recovered::unscramble(data.data() + 1, 256, key));
        REQUIRE(data == saved);
    }
    for (const unsigned length : {0u, 1u, 2u, 31u, 255u, 256u, 4097u}) {
        for (const unsigned period : {1u, 2u, 3u, 31u, 256u}) {
            std::vector<char> key(period + 3, 0);
            for (unsigned i = 0; i < period; ++i) key[i] = static_cast<char>(1 + i % 255);
            key[period + 1] = 'x'; // Ignored after NUL.
            const auto saved_key = key;
            std::vector<std::uint8_t> data(length + 2, 0xa5);
            REQUIRE(recovered::unscramble(data.data() + 1, length, key.data()));
            for (unsigned i = 0; i < length; ++i) {
                REQUIRE(data[i + 1] == (0xa5u ^ static_cast<unsigned char>(key[i % period])));
            }
            REQUIRE(data.front() == 0xa5 && data.back() == 0xa5);
            REQUIRE(key == saved_key);
        }
    }
    std::array<std::uint8_t, 5> split{};
    REQUIRE(recovered::unscramble(split.data(), 3, "ab"));
    REQUIRE(recovered::unscramble(split.data() + 3, 2, "ab"));
    REQUIRE((split == std::array<std::uint8_t, 5>{'a', 'b', 'a', 'a', 'b'}));
    // These check only the existing source API's explicit validation policy.
    REQUIRE(!recovered::unscramble(nullptr, 1, "x"));
    REQUIRE(!recovered::unscramble(split.data(), 5, ""));
    REQUIRE(!recovered::unscramble(split.data(), 5, nullptr));
}

std::vector<std::uint8_t> decode_hex(const std::string& text) {
    if (text == "-") return {};
    REQUIRE(text.size() % 2 == 0);
    const auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return static_cast<unsigned>(c - '0');
        if (c >= 'a' && c <= 'f') return static_cast<unsigned>(c - 'a' + 10);
        REQUIRE(false);
        return 0;
    };
    std::vector<std::uint8_t> bytes;
    for (std::size_t i = 0; i < text.size(); i += 2) {
        bytes.push_back(static_cast<std::uint8_t>((digit(text[i]) << 4) | digit(text[i + 1])));
    }
    return bytes;
}

void print_hex(const std::vector<std::uint8_t>& bytes) {
    constexpr char digits[] = "0123456789abcdef";
    if (bytes.empty()) std::cout << '-';
    for (const auto byte : bytes) std::cout << digits[byte >> 4] << digits[byte & 15];
    std::cout << '\n';
}

// Line-oriented seam for an offline oracle. Only synthetic data is exchanged.
// probe WORD POSITION SEEK_START_OK SEEK_RESTORE_OK READ_RETURN
// xor DATA_HEX_OR_DASH KEY_HEX
// image FILENAME_HEX_OR_DASH VALUE
void oracle_mode() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream input(line);
        std::string operation;
        input >> operation;
        if (operation == "probe") {
            std::uint32_t word = 0;
            TestFile file;
            input >> word >> file.cursor >> file.seek_start_result >> file.seek_restore_result >> file.read_result;
            REQUIRE(!input.fail());
            file.bytes = header_bytes(word);
            const auto initial = file.cursor;
            const bool result = recovered::is_valid_scrambled_zip(file);
            require_probe_trace(file, initial);
            std::cout << result << ' ' << file.cursor << " 9 6 3 6\n";
        } else if (operation == "xor") {
            std::string data_text, key_text;
            input >> data_text >> key_text;
            REQUIRE(!input.fail());
            auto data = decode_hex(data_text);
            const auto key_bytes = decode_hex(key_text);
            REQUIRE(!key_bytes.empty());
            std::string key(key_bytes.begin(), key_bytes.end());
            REQUIRE(key.find('\0') == std::string::npos);
            REQUIRE(data.size() <= std::numeric_limits<std::uint32_t>::max());
            REQUIRE(recovered::unscramble(data.data(), static_cast<std::uint32_t>(data.size()), key.c_str()));
            print_hex(data);
        } else if (operation == "image") {
            std::string name_text;
            std::uint32_t value = 0;
            input >> name_text >> value;
            REQUIRE(!input.fail());
            const auto bytes = decode_hex(name_text);
            const std::string name(bytes.begin(), bytes.end());
            const char* filename = name_text == "-" ? nullptr : name.c_str();
            std::uint32_t destination = 0;
            LookupState state;
            state.value = value;
            recovered::TextureLookup lookup{&state, record_lookup};
            const auto result = recovered::create_image_into(&destination, {filename}, lookup);
            std::cout << (result == &destination) << ' ' << state.calls << ' '
                      << (state.destination == &destination && state.filename == filename)
                      << ' ' << destination << '\n';
        } else {
            REQUIRE(false);
        }
    }
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--oracle") {
        oracle_mode();
        return 0;
    }
    REQUIRE(argc == 1);
    probe_tests();
    image_tests();
    xor_tests();
    std::cout << "PASS: task37 source adapter tests (NOT MATCHED)\n";
}
