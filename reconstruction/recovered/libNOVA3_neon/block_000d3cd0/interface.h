#pragma once
#include <stdint.h>

namespace nova3::recovered::block_000d3cd0 {
using Word = uintptr_t;

// Register-boundary adapters, not recovered Java/JNI declarations. Word models
// one ARM32 register (and permits host testing without truncating pointers).
// NativeStart's source signature and return type are unknown. Its unconditional
// branch preserves arguments; this interface covers r0-r3 and the r0 result.
// Stack arguments, additional result registers and exception ABI are unmodeled.
extern "C" Word nova3_installer_native_start(Word, Word, Word, Word);
extern "C" Word nova3_billing_set_java_vm(Word vm);

// The format/varargs and this/request source signatures are INFERRED from
// symbol names and calling convention, not recovered DWARF. Neither function
// reads arguments or calls dependencies in this reference build.
extern "C" void nova3_drm_debug_log(const char* format, ...);
extern "C" void nova3_billing_launch(void* instance, const char* request);
} // namespace nova3::recovered::block_000d3cd0

// Integration requires real source implementations of these dependencies.
// Test doubles are supplied only by the standalone test, never by this module.
extern "C" uintptr_t nova3_installer_start_dependency(
    uintptr_t, uintptr_t, uintptr_t, uintptr_t);
extern "C" uintptr_t nova3_billing_set_vm_dependency(uintptr_t);
