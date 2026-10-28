// Source-owned recovery of three Premium ARMv7 CPU query functions.
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Assembly comparison: NOT MATCHED. No historical compiler/flags claim.
#include <cstdint>

namespace nova3::recovered::block_000d2f10 {

// Source-owned dependency interface, NOT the original ARM32 object layout or
// exported Android ABI. Original no-argument queries use persistent globals.
// All three queries must share one context and once control. A platform adapter
// supplies pthread_once and the initializer; this module does not detect CPUs.
//
// Preconditions: context, callbacks and cache pointers are valid; once_control
// refers to a platform once object accepted by run_once. That service must
// publish initializer writes before returning, as pthread_once does. The
// initializer must not recursively call a query on the same once control.
// Cache storage outlives every query; concurrent writes after initialization
// require external synchronization. Callback exceptions are outside this API.
struct CpuQueryContext {
    void* once_control;
    void (*initialize)();
    int (*run_once)(void*, void (*)());
    const std::int32_t* cpu_count;
    const std::uint64_t* cpu_features;
    const std::int32_t* cpu_family;
};

// android_getCpuCount, ELF VA 0x000d2f10, 44 bytes, A32.
// g_once 0x00d96598; g_cpuCount 0x00d9659c.
extern "C" std::int32_t nova3_recovered_cpu_count(const CpuQueryContext* context) {
    // The reference ignores the once service's return status. Do not replace
    // this with a cached snapshot, clamp the count, or infer a fallback value.
    (void)context->run_once(context->once_control, context->initialize);
    return *context->cpu_count;
}

// android_getCpuFeatures, ELF VA 0x000d2f3c, 48 bytes, A32.
// g_cpuFeatures 0x00d965a0; AAPCS32 result occupies r0 (low) and r1 (high).
extern "C" std::uint64_t nova3_recovered_cpu_features(const CpuQueryContext* context) {
    (void)context->run_once(context->once_control, context->initialize);
    return *context->cpu_features;
}

// android_getCpuFamily, ELF VA 0x000d2f6c, 44 bytes, A32.
// g_cpuFamily 0x00d965a8. Preserve its 32-bit representation without validating
// enum values. Signed int/enum interpretation is INFERRED from the local NDK
// API; the original instructions establish only the returned bit pattern.
extern "C" std::int32_t nova3_recovered_cpu_family(const CpuQueryContext* context) {
    (void)context->run_once(context->once_control, context->initialize);
    return *context->cpu_family;
}

// android_cpuInit, ELF VA 0x000d2f98, 2632 bytes: NOT RECONSTRUCTED.
// Its filesystem reads, parsing, CPU feature policy and dependencies need a
// separate bounded recovery. No host CPU detection or success stub substitutes
// for it. The context above exposes that unresolved dependency explicitly.

} // namespace nova3::recovered::block_000d2f10
