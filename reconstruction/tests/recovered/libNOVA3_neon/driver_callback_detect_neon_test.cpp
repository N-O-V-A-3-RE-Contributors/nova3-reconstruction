// Standalone source-only regression for the recovered NEON probe:
// c++ -std=c++17 -Wall -Wextra -Wpedantic -Werror -O2
//   -Ireconstruction/recovered/libNOVA3_neon
//   reconstruction/tests/recovered/libNOVA3_neon/driver_callback_detect_neon_test.cpp
//   -o /tmp/nova-detectneon-test
//
// The recovered translation unit is included unchanged; only the two
// cpufeatures entry points it calls are supplied here, as counting stubs.  That
// makes the probe's *own* logic observable: which getter is called, in what
// order, how many times, and what the 64-bit feature mask is actually tested
// against.  No original game code is linked, loaded or executed.
//
// Scope: behaviour of the source on the host.  Machine-code agreement with the
// reference window is measured separately by tools/compare/detect_neon_pilot.py
// and is NOT claimed here.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <limits>

#include "../../../recovered/libNOVA3_neon/driver_callback_detect_neon.cpp"

namespace {

void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::abort();
    }
}

// Counting stubs standing in for the bionic cpufeatures API.
int family_result = ANDROID_CPU_FAMILY_ARM;
std::uint64_t features_result = 0;
unsigned family_calls = 0;
unsigned features_calls = 0;

void reset(int family, std::uint64_t features) {
    family_result = family;
    features_result = features;
    family_calls = features_calls = 0;
}

// `this` is never read by the recovered body, so two distinct objects must
// behave identically; that is asserted rather than assumed.
bool probe(vox::DriverCallbackSourceInterface& object) {
    return object.DetectNeon();
}

void test_short_circuit() {
    // Every non-ARM family returns false and must NOT consult the feature mask.
    for (int family : {ANDROID_CPU_FAMILY_UNKNOWN, ANDROID_CPU_FAMILY_X86,
                       ANDROID_CPU_FAMILY_MIPS}) {
        reset(family, std::numeric_limits<std::uint64_t>::max());
        vox::DriverCallbackSourceInterface object;
        require(!probe(object), "non-ARM family must return false");
        require(family_calls == 1, "family queried exactly once");
        require(features_calls == 0, "feature mask must not be read for non-ARM");
    }
    // A family value outside the enumerated set is also not ARM.
    reset(7, std::numeric_limits<std::uint64_t>::max());
    vox::DriverCallbackSourceInterface object;
    require(!probe(object), "unknown family value must return false");
    require(features_calls == 0, "feature mask must not be read for family 7");
}

void test_only_bit_2_is_consulted() {
    vox::DriverCallbackSourceInterface first, second;
    // Single-bit sweep over the whole 64-bit mask: exactly bit 2 (the recovered
    // NEON position) may produce true; every other bit must produce false.
    for (unsigned bit = 0; bit < 64; ++bit) {
        reset(ANDROID_CPU_FAMILY_ARM, std::uint64_t{1} << bit);
        const bool expected = (bit == 2);
        require(probe(first) == expected, "single-bit sweep: only bit 2 is NEON");
        require(probe(second) == expected, "`this` must not influence the result");
        require(features_calls == 2, "one feature read per probe");
        require(family_calls == 2, "one family read per probe");
    }
    // The recovered feature constants, in combination, must behave as the NDK
    // ones: only the NEON bit drives the result.
    reset(ANDROID_CPU_FAMILY_ARM, ANDROID_CPU_ARM_FEATURE_ARMv7
                                | ANDROID_CPU_ARM_FEATURE_VFPv3
                                | ANDROID_CPU_ARM_FEATURE_LDREX_STREX);
    require(!probe(first), "ARMv7|VFPv3|LDREX_STREX without NEON is false");
    reset(ANDROID_CPU_FAMILY_ARM, ANDROID_CPU_ARM_FEATURE_ARMv7
                                | ANDROID_CPU_ARM_FEATURE_VFPv3
                                | ANDROID_CPU_ARM_FEATURE_NEON
                                | ANDROID_CPU_ARM_FEATURE_LDREX_STREX);
    require(probe(first), "full ARMv7 feature set is true");
    // Zero and the all-ones mask bracket the range.
    reset(ANDROID_CPU_FAMILY_ARM, 0);
    require(!probe(first), "no features is false");
    reset(ANDROID_CPU_FAMILY_ARM, std::numeric_limits<std::uint64_t>::max());
    require(probe(first), "all features set is true");
    // A NEON bit synthesised in the upper half must not be mistaken for bit 2.
    reset(ANDROID_CPU_FAMILY_ARM, std::uint64_t{1} << 34);
    require(!probe(first), "upper-half bit 34 is not the NEON bit");
}

void test_call_order() {
    // The family call must precede the feature call; the probe reads the family
    // first and short-circuits on it.
    reset(ANDROID_CPU_FAMILY_ARM, ANDROID_CPU_ARM_FEATURE_NEON);
    vox::DriverCallbackSourceInterface object;
    require(probe(object), "ARM + NEON is true");
    require(family_calls == 1 && features_calls == 1,
            "both getters consulted exactly once on the ARM path");
}

} // namespace

// Stub definitions for the two cpufeatures entry points the recovered TU calls.
extern "C" int android_getCpuFamily(void) {
    ++family_calls;
    return family_result;
}

extern "C" std::uint64_t android_getCpuFeatures(void) {
    ++features_calls;
    return features_result;
}

int main() {
    test_short_circuit();
    test_only_bit_2_is_consulted();
    test_call_order();
    std::puts("PASS: short-circuit, single-bit mask sweep (64 bits), feature "
              "combinations and call order for the recovered DetectNeon probe");
    return 0;
}
