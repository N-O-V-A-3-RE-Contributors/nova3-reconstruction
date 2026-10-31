// Source-owned recovery of the NEON capability probe in the Premium ARMv7
// engine.  Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   _ZN3vox29DriverCallbackSourceInterface10DetectNeonEv
//   ELF VA 0x009dad14, 32 bytes, A32, STT_FUNC (not stripped)
//
// The reference body is seven instructions: a frame push, a call to
// android_getCpuFamily, a compare against 1, a conditional zero return, a call
// to android_getCpuFeatures, an extract of bit 2, and a return.  This file
// reproduces that program in readable C++; measured agreement is recorded in
// docs/progress/STATUS.md and re-measured by tools/compare/detect_neon_pilot.py.
//
// Evidence (parameters, all observed in the reference window):
//   * The family comparison is against the literal 1.  ANDROID_CPU_FAMILY_ARM
//     is 1 in the public NDK cpufeatures header; the *name* is INFERRED from
//     that header, the constant is recovered.
//   * The feature test extracts bit 2 into the result register, i.e. the
//     return value is exactly one bit and no other feature bit is consulted.
//     ANDROID_CPU_ARM_FEATURE_NEON is (1 << 2) in the same header; that name is
//     INFERRED, the bit position is recovered.
//   * The false path pops before the second call: android_getCpuFeatures is
//     NOT called when the family is not ARM.  That short-circuit is visible in
//     the control flow and is asserted by the host behaviour test, so it is a
//     behaviour claim rather than a source-shape preference.
//   * `this` (r0 on entry) is never read — the first use of r0 in the window is
//     the `cmp` on android_getCpuFamily's *return* value — so the probe reads no
//     object state.  Under the Itanium C++ ABI a static member function mangles
//     identically to a non-static one, and both spellings compile to this exact
//     body, so "static" vs "non-static" is NOT recoverable from the function:
//     it is underdetermined, not inferred.  The non-static declaration below is
//     an ABI-only placeholder for the exported name, and nothing about the
//     class layout is recovered or used.
//
// Matching caveat, measured not assumed: the two BL words differ from the
// reference because an ET_REL object cannot contain the link-time branch
// displacement.  With NDK r8e GCC 4.4.3 the window is 32/32 bytes with
// 6 EQUAL + 2 RELOCATION_FIELD + 0 ENCODING_DIFFERENCE at -O2, -O3 and -Os, and
// `code_window_equal` stays false.  No byte-match, compiler, flag or
// whole-`.so` claim follows from that.
#include "cpu_features.h"

namespace vox {

// ABI-only declaration: only the exported member name and its no-argument,
// bool-returning signature are recovered.  The class body, base classes and
// data members are NOT reconstructed; this declaration must be replaced by the
// real recovered interface if one is ever established.
class DriverCallbackSourceInterface {
public:
    bool DetectNeon();
};

// vox::DriverCallbackSourceInterface::DetectNeon, ELF VA 0x009dad14, 32 bytes.
// Returns true only for the ARM family with the NEON feature bit set.
bool DriverCallbackSourceInterface::DetectNeon() {
    if (android_getCpuFamily() != ANDROID_CPU_FAMILY_ARM)
        return false;
    return (android_getCpuFeatures() & ANDROID_CPU_ARM_FEATURE_NEON) != 0;
}

} // namespace vox
