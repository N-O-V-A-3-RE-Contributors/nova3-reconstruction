#include "../../../recovered/libNOVA3_neon/block_000d3cd0/interface.h"
#include <array>
#include <cstdio>
#include <cstdlib>

using namespace nova3::recovered::block_000d3cd0;
namespace {
unsigned calls = 0;
unsigned operation = 0;
std::array<Word, 4> received{};
Word result = 0;
void require(bool ok) {
    if (!ok) { std::fputs("FAIL: task71 boundary\n", stderr); std::abort(); }
}
}

extern "C" Word nova3_installer_start_dependency(Word a, Word b, Word c, Word d) {
    ++calls;
    operation = 1;
    received = {a, b, c, d};
    return result;
}
extern "C" Word nova3_billing_set_vm_dependency(Word vm) {
    ++calls;
    operation = 2;
    received = {vm, 0, 0, 0};
    return result;
}

// Test-only host probe. Five synthetic words: four argument bits and a modeled
// dependency result. Output: call count, dependency ID, arguments, result bits.
extern "C" void nova3_task71_probe(unsigned which, const uint32_t* input,
                                   uint64_t* output) {
    calls = operation = 0;
    received = {};
    result = input[4];
    Word returned = 0;
    if (which == 0) {
        returned = nova3_installer_native_start(input[0], input[1], input[2], input[3]);
    } else if (which == 1) {
        nova3_drm_debug_log(reinterpret_cast<const char*>(Word(input[0])),
                           input[1], input[2], input[3]);
        nova3_drm_debug_log(nullptr);
        nova3_drm_debug_log("%s %d %f", "synthetic", -7, 3.25);
        nova3_drm_debug_log(nullptr, uint64_t{0xfedcba9876543210}, nullptr);
    } else if (which == 2) {
        returned = nova3_billing_set_java_vm(input[0]);
    } else {
        require(which == 3);
        nova3_billing_launch(reinterpret_cast<void*>(Word(input[0])),
                            reinterpret_cast<const char*>(Word(input[1])));
        std::array<char, 8> object{{'o', 'b', 'j', '\0', 1, 2, 3, 4}};
        std::array<char, 8> request{{'r', '\0', 'e', 'q', 4, 3, 2, 1}};
        const auto object_before = object, request_before = request;
        nova3_billing_launch(object.data(), request.data());
        nova3_billing_launch(nullptr, nullptr);
        require(object == object_before && request == request_before);
    }
    output[0] = calls;
    output[1] = operation;
    for (unsigned i = 0; i < 4; ++i) output[i + 2] = received[i];
    output[6] = returned;
}

#ifndef NOVA3_SHARED_TEST
int main() {
    constexpr uint32_t edges[] = {0, 1, 0x7fffffff, 0x80000000, 0xffffffff};
    unsigned cases = 0;
    for (unsigned which = 0; which < 4; ++which) {
        for (auto argument : edges) for (auto value : edges) {
            const uint32_t input[] = {argument, argument ^ 0xabcdef01,
                argument ^ 0x76543210, ~argument, value};
            uint64_t output[7]{};
            nova3_task71_probe(which, input, output);
            const bool forwarding = which == 0 || which == 2;
            require(output[0] == unsigned(forwarding));
            require(output[1] == (which == 0 ? 1u : which == 2 ? 2u : 0u));
            for (unsigned i = 0; i < 4; ++i)
                require(output[2 + i] == ((which == 0 || (which == 2 && i == 0)) ? input[i] : 0));
            require(output[6] == (forwarding ? value : 0));
            ++cases;
        }
    }
    // A host boundary must also preserve pointers wider than ARM32 registers.
    Word token = 0;
    const auto pointer = reinterpret_cast<Word>(&token);
    result = pointer;
    require(nova3_installer_native_start(pointer, pointer, pointer, pointer) == pointer);
    require(received == std::array<Word, 4>{pointer, pointer, pointer, pointer});
    require(nova3_billing_set_java_vm(pointer) == pointer && received[0] == pointer);
    std::printf("PASS: %u boundary cases plus host pointer preservation\n", cases);
}
#endif
