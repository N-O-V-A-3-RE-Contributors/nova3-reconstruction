// Source-owned reconstruction. Premium reference SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Match/verification scope is recorded separately in task #69 evidence.
#include "block_000d4dbc/recovered.hpp"

#include <cmath>
#include <limits>

// 0x000d4dbc and 0x000d5224 are forwarding veneers, not billing logic.
extern "C" int InAppBilling_ClearItemList() {
    return InAppBilling::clearItemList();
}

extern "C" int InAppBilling_GetState() {
    return InAppBilling::getState();
}

namespace {
// The observed ARM __fixunsdfdi helper (0x00be323c) converts via two
// saturating uint32 conversions. For binary64 values its combined result is
// truncation clamped to [0, UINT64_MAX], with NaN converted to zero. A raw
// C++ floating-to-integer cast would be undefined outside the uint64 range.
std::uint64_t arm_unsigned_conversion(double value) noexcept {
    if (!(value > 0.0)) return 0; // Includes NaN, negative values, and zero.
    if (value >= 0x1p64) return std::numeric_limits<std::uint64_t>::max();
    return static_cast<std::uint64_t>(value);
}
} // namespace

// 0x000d4de4. Preserve the original floating arithmetic, signed-byte load,
// output initialization, and per-byte conversion/store. This deliberately
// is not strtoull: no digit checking, NUL handling, or integer multiply by 10.
// Build with -ffp-contract=off and without fast-math. pow is a platform libm
// dependency: its bitwise agreement with the original Android libm is unknown.
extern "C" void charToUI64(std::uint64_t* output, std::int32_t length,
                           const unsigned char* input) {
    std::uint64_t accumulated = 0;
    *output = 0;
    for (std::int32_t i = 0; i < length; ++i) {
        const double place = std::pow(10.0, static_cast<double>(length - 1 - i));
        const unsigned byte = input[i];
        const int signed_byte = byte < 128 ? static_cast<int>(byte)
                                          : static_cast<int>(byte) - 256;
        const double contribution = place * static_cast<double>(signed_byte - '0');
        accumulated = arm_unsigned_conversion(
            contribution + static_cast<double>(accumulated));
        *output = accumulated;
    }
}

// 0x000d4f74. JNI table offsets in the A32 reference are respectively
// 0x18 (FindClass), 0x38 (ThrowNew), and 0x5c (DeleteLocalRef).
extern "C" void JNU_ThrowByName(JNIEnv* environment, const char* class_name,
                                const char* message) {
    const jclass type = environment->FindClass(class_name);
    if (type != nullptr) environment->ThrowNew(type, message);
    environment->DeleteLocalRef(type);
}
