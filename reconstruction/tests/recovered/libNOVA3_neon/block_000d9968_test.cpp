// Standalone tests: compile this translation unit alone (includes recovery).
#include "../../../recovered/libNOVA3_neon/block_000d9968.cpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>

using namespace nova3::recovered::block_000d9968;
namespace nova3::recovered::block_000d9968 {
struct StoreItem { unsigned char storage[32]; }; // Test fixture only.
}

namespace {
void require(bool condition) {
    if (!condition) { std::fputs("FAIL: block_000d9968\n", stderr); std::abort(); }
}
Word seen_first, seen_second, next_result;
unsigned calls;
Environment* expected_environment;
unsigned signature;
uint64_t* capture;

Object object_word(Word value) { return reinterpret_cast<Object>(value); }
uint64_t double_bits(double value) {
    uint64_t bits;
    static_assert(sizeof bits == sizeof value);
    std::memcpy(&bits, &value, sizeof bits);
    return bits;
}
Object record_call(Environment* env, Object object, Method method, va_list args) {
    require(env == expected_environment);
    capture[0] = reinterpret_cast<Word>(object);
    capture[1] = reinterpret_cast<Word>(method);
    capture[2] = next_result;
    unsigned count = 0;
    auto integer = [&] { capture[4 + count++] = static_cast<uint32_t>(va_arg(args, int)); };
    auto pointer = [&] { capture[4 + count++] = reinterpret_cast<Word>(va_arg(args, void*)); };
    auto wide = [&] { capture[4 + count++] = static_cast<uint64_t>(va_arg(args, int64_t)); };
    auto real = [&] { capture[4 + count++] = double_bits(va_arg(args, double)); };
    switch (signature) {
    case 0: break;
    case 1: integer(); break;
    case 2: pointer(); break;
    case 3: real(); break;
    case 4: wide(); break;
    case 5: integer(); real(); pointer(); wide(); integer(); integer(); real(); pointer(); break;
    case 6: for (int i = 0; i != 12; ++i) integer(); break;
    default: require(false);
    }
    capture[3] = count;
    ++calls;
    return object_word(next_result);
}
}

namespace nova3::recovered::block_000d9968 {
extern "C" Word nova3_iab_send_data_dependency(Object data) {
    seen_first = reinterpret_cast<Word>(data); ++calls; return next_result;
}
extern "C" Word nova3_iab_item_array_dependency() {
    ++calls; return next_result;
}
extern "C" Word nova3_iab_item_by_id_dependency(const char* id, StoreItem& item) {
    seen_first = reinterpret_cast<Word>(id);
    seen_second = reinterpret_cast<Word>(&item);
    item.storage[7] ^= 0x5a; // Verify reference identity and side-effect visibility.
    ++calls;
    return next_result;
}
}

// Test-only entry points for the optional local differential harness.
extern "C" void nova3_test_forward(unsigned which, Word first, Word second,
                                   Word third, Word result, uint64_t* out) {
    seen_first = seen_second = calls = 0;
    next_result = result;
    Word actual = 0;
    switch (which) {
    case 0: actual = nova3_000d9968(reinterpret_cast<Environment*>(first),
                                  object_word(second), object_word(third)); break;
    case 1: actual = nova3_000da9e4(); break;
    case 2: {
        StoreItem item{};
        actual = nova3_000da9e8(reinterpret_cast<const char*>(first), item);
        require(seen_second == reinterpret_cast<Word>(&item));
        require(item.storage[7] == 0x5a);
        // Report identity separately from the unrelated host address.
        seen_second = second;
        break;
    }
    default: require(false);
    }
    out[0] = seen_first; out[1] = seen_second; out[2] = calls; out[3] = actual;
}

extern "C" void nova3_test_variadic(unsigned scenario, uint32_t seed, uint64_t* out) {
    std::memset(out, 0, 16 * sizeof(*out));
    NativeInterface table{};
    table.call_object_method_v = record_call;
    Environment env{&table};
    expected_environment = &env;
    signature = scenario;
    capture = out;
    calls = 0;
    next_result = (seed & 1u) ? 0 : (0x50000000u | (seed & 0xffffffu));
    const Object obj = object_word(seed);
    const Method method = object_word(seed ^ 0x55aa55aau);
    const int integer = -1 - static_cast<int>(seed & 0x7fffffffu);
    const Object pointer = object_word(seed ^ 0x12345678u);
    const int64_t wide = -INT64_C(0x123456780000000) - seed;
    const double real = static_cast<double>(seed) * 0.25 - 1024.5;
    Object result = nullptr;
    switch (scenario) {
    case 0: result = env.call_object_method(obj, method); break;
    case 1: result = env.call_object_method(obj, method, integer); break;
    case 2: result = env.call_object_method(obj, method, pointer); break;
    case 3: result = env.call_object_method(obj, method, real); break;
    case 4: result = env.call_object_method(obj, method, wide); break;
    case 5: result = env.call_object_method(obj, method, integer, real, pointer, wide,
                                            0, -7, -real, static_cast<void*>(nullptr)); break;
    case 6: result = env.call_object_method(obj, method, 0, 1, -1, integer, 4, 5,
                                            6, 7, 8, 9, 10, 11); break;
    default: require(false);
    }
    require(calls == 1 && reinterpret_cast<Word>(result) == next_result);
}

#ifndef NOVA3_SHARED_TEST
int main() {
    uint64_t out[16];
    const Word values[] = {0, 1, 0x7fffffff, 0x80000000, 0xffffffff};
    unsigned forwarding_cases = 0, variadic_cases = 0;
    for (unsigned which = 0; which != 3; ++which)
        for (Word first : values) for (Word result : values) {
            nova3_test_forward(which, first, 0x2468, 0x1357, result, out);
            require(out[0] == (which == 0 ? 0x1357 : which == 2 ? first : 0));
            require(out[1] == (which == 2 ? 0x2468 : 0));
            require(out[2] == 1 && out[3] == result);
            ++forwarding_cases;
        }
    for (unsigned scenario = 0; scenario != 7; ++scenario) for (Word value : values) {
        nova3_test_variadic(scenario, static_cast<uint32_t>(value), out);
        require(out[0] == value && out[1] == (value ^ 0x55aa55aa));
        const unsigned counts[] = {0, 1, 1, 1, 1, 8, 12};
        require(out[3] == counts[scenario]);
        const auto integer = static_cast<uint32_t>(-1 - static_cast<int>(value & 0x7fffffff));
        const auto real = double_bits(static_cast<double>(value) * 0.25 - 1024.5);
        const auto wide = static_cast<uint64_t>(-INT64_C(0x123456780000000) - value);
        if (scenario == 1) require(out[4] == integer);
        if (scenario == 2) require(out[4] == (value ^ 0x12345678));
        if (scenario == 3) require(out[4] == real);
        if (scenario == 4) require(out[4] == wide);
        if (scenario == 5) {
            require(out[4] == integer && out[5] == real && out[6] == (value ^ 0x12345678));
            require(out[7] == wide && out[8] == 0 && out[9] == 0xfffffff9);
            require(out[10] == double_bits(-(static_cast<double>(value) * 0.25 - 1024.5)));
            require(out[11] == 0);
        }
        if (scenario == 6) {
            const uint32_t expected[] = {0, 1, 0xffffffff, integer, 4, 5, 6, 7, 8, 9, 10, 11};
            for (unsigned i = 0; i != 12; ++i) require(out[4 + i] == expected[i]);
        }
        ++variadic_cases;
    }
    std::printf("PASS: %u forwarding and %u variadic cases\n", forwarding_cases, variadic_cases);
}
#endif
