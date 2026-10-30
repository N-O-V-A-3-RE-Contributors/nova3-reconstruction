// Recovered Premium ARMv7 CPU-query thunks — TRUE no-argument ABI candidate.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   android_getCpuCount    ELF VA 0x000d2f10, 44 bytes, A32, STT_FUNC
//   android_getCpuFeatures ELF VA 0x000d2f3c, 48 bytes, A32, STT_FUNC
//   android_getCpuFamily   ELF VA 0x000d2f6c, 44 bytes, A32, STT_FUNC
//
// Evidence summary:
//   * Each body is an A32 prologue/epilogue pair around one call and one or two
//     loads from a single PC-relative anchor, so the exported names take no
//     arguments and read persistent state through one base register.
//   * The call target is the shared-library stub for the once-control service;
//     the first argument is the anchor address itself and the second is a
//     no-argument initializer address. The bionic pthread_once contract is
//     INFERRED from that argument/call shape, not recovered from the bytes.
//   * Return widths are observed: getCpuCount and getCpuFamily move one 32-bit
//     word into r0, getCpuFeatures moves a 64-bit pair into r0:r1 (AAPCS32).
//     Signedness of the 32-bit results is NOT established; int is INFERRED from
//     the public NDK cpufeatures interface.
//   * The anchor is one object with 32-bit, 64-bit and 32-bit fields at fixed
//     non-negative offsets in the order count, features, family, with the once
//     control at offset zero. Grouping the fields into the struct below is an
//     INFERRED layout hypothesis that explains those offsets; it was selected
//     to reproduce them and is not independently recovered.
//
// Matching status (measured by tools/compare/cpuquery_pilot.py, not assumed):
// with a matching-context variant (file-local initializer, so the state writes
// are visible) NDK clang 21, modern arm-none-eabi-g++ and the local NDK r8e
// GCC 4.4.3 reproduce the instruction stream of these three windows; only the
// call displacement and the literal-pool words differ, i.e. link-time values an
// ET_REL object cannot contain. Instruction agreement therefore comes from the
// source shape and does NOT identify the original compiler. The measured
// discriminator is the 64-bit load: the reference has two LDRs (48-byte body),
// GCC 4.4.3 reproduces that for a word-aligned aggregate, and both clang 21 and
// GCC 16.2 emit LDRD (44-byte body) even for a word-aligned aggregate.
// None of this is a byte match, proof of the original toolchain, or a recovered
// original object or relocation set.
//
// SUPERSEDED (2026-10-31): the single-struct layout hypothesis in this file is
// retracted. The reference symbol table names four separate file-scope objects
// at the anchor - g_once (0x00d96598), g_cpuCount (+4), g_cpuFeatures (+8,
// 8 bytes), g_cpuFamily (+16) - and the TU is the AOSP cpufeatures
// "cpu-features.c" (STT_FILE symbol), not an anonymous struct. See
// reconstruction/recovered/libNOVA3_neon/cpu_features.c for the corrected
// reconstruction and tools/compare/cpufeatures_pilot.py for the measured
// window comparison. This file is kept only as the record of the earlier,
// narrower query-only model.
//
// Compiling this file alone costs two extra divergent words: android_cpuInit is
// declared extern here, so it is preemptible and addressed through an indirect
// literal, whereas the reference binds the initializer directly. The original
// translation unit therefore owned the initializer; this module cannot
// reproduce that binding until android_cpuInit is reconstructed. Compiling
// alone also lets clang 21 constant-fold the queries to zero, because nothing
// in the unit writes the state.
//
// android_cpuInit remains NOT RECONSTRUCTED (ELF VA 0x000d2f98, 2632 bytes).
// It is declared here as an unresolved external dependency; no stub stands in
// for it.

extern "C" {

// bionic pthread_once_t is a 32-bit int. The once control occupies the first
// four bytes of the anchor block. The service is a platform dependency.
typedef int nova3_once_t;
int pthread_once(nova3_once_t* once_control, void (*initializer)(void));

// NOT RECONSTRUCTED. Declared so these thunks do not pretend a placeholder
// initializer is equivalent to the original routine.
void android_cpuInit(void);

} // extern "C"

namespace {

// INFERRED layout hypothesis: the observed offsets are 0, 4, 8 and 16 from one
// anchor, so the queries share a single state object. The aggregate is modelled
// as word-aligned because the reference loads `features` as two LDRs: the local
// GCC 4.4.3 emits exactly that pair for a 4-byte-aligned aggregate and emits
// LDRD for an 8-byte-aligned one (measured, see tools/compare/cpuquery_pilot.py).
struct __attribute__((aligned(4))) CpuQueryState {
    nova3_once_t once;
    int count;
    unsigned long long features;
    int family;
};

CpuQueryState g_state = {0, 0, 0, 0};

} // namespace

extern "C" int android_getCpuCount(void) {
    // The reference discards the once service's status: the 44-byte window has
    // no error branch, no cached snapshot and no fallback count.
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.count;
}

extern "C" unsigned long long android_getCpuFeatures(void) {
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.features;
}

extern "C" int android_getCpuFamily(void) {
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.family;
}
