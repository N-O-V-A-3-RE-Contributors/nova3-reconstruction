// Standalone, source-only regression: no original ELF or private source needed.
#include "driver_callback.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <limits>

namespace {

static_assert(sizeof(float) == sizeof(std::uint32_t) &&
              std::numeric_limits<float>::is_iec559 &&
              std::numeric_limits<float>::digits == 24,
              "This oracle requires IEEE binary32");

// Decode a binary32 rational, shift its significand by the fixed-point bit
// count, and discard fractional bits. No floating multiply or float-to-int
// conversion is used by the oracle. Its caller supplies only in-range inputs.
std::int32_t oracle(float value) {
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    const unsigned exponent = (bits >> 23) & 255u;
    if (exponent == 255u) std::abort();
    std::uint64_t magnitude = bits & 0x7fffffu;
    if (exponent != 0) magnitude |= 0x800000u;
    const int shift = exponent == 0 ? -135 : static_cast<int>(exponent) - 136;
    if (shift >= 0) {
        if (shift >= 40) std::abort();
        magnitude <<= shift;
    } else {
        magnitude = shift <= -64 ? 0 : magnitude >> -shift;
    }
    const std::int64_t result = (bits >> 31) != 0
        ? -static_cast<std::int64_t>(magnitude)
        : static_cast<std::int64_t>(magnitude);
    if (result < std::numeric_limits<std::int32_t>::min() ||
        result > std::numeric_limits<std::int32_t>::max()) std::abort();
    return static_cast<std::int32_t>(result);
}

unsigned checks = 0;
void check(float period) {
    const auto expected = oracle(period);
    vox::DriverCallbackSourceInterface::SetDriverCallbackPeriod(period);
    const auto actual = vox::MinibusDataGeneratorInterface::s_driverCallbackPeriod;
    if (actual != expected) {
        std::fprintf(stderr, "period %.9g: got %ld, expected %ld\n",
                     static_cast<double>(period), static_cast<long>(actual),
                     static_cast<long>(expected));
        std::abort();
    }
    ++checks;
}

} // namespace

int main() {
    if (vox::MinibusDataGeneratorInterface::s_driverCallbackPeriod != 2147483647)
        std::abort();
    check(0.0f);
    check(-0.0f);
    check(std::numeric_limits<float>::denorm_min());
    check(-std::numeric_limits<float>::denorm_min());
    check(std::numeric_limits<float>::min());
    check(-std::numeric_limits<float>::min());

    // Exact fixed-point values and the immediately adjacent binary32 values,
    // with both signs, distinguish truncation from floor and nearest rounding.
    for (int units : {1, 2, 3, 7, 63, 1024, 16383, 16384, 65537, 1048575}) {
        const float boundary = std::ldexp(static_cast<float>(units), -14);
        for (float sign : {1.0f, -1.0f}) {
            const float value = sign * boundary;
            check(std::nextafter(value, -std::numeric_limits<float>::infinity()));
            check(value);
            check(std::nextafter(value, std::numeric_limits<float>::infinity()));
            check(sign * std::ldexp(static_cast<float>(units) + 0.5f, -14));
        }
    }
    check(std::nextafter(131072.0f, 0.0f));
    check(-131072.0f);
    check(std::nextafter(-131072.0f, 0.0f));

    // Distinct writes, consecutive identical writes, and reset to zero all
    // observe the same confirmed static destination after every call.
    for (float period : {1.5f, -0.25f, 0.0f, 0.5f, 0.5f, -1.0f, 0.0f})
        check(period);
    std::printf("driver callback period: %u checks passed\n", checks);
}
