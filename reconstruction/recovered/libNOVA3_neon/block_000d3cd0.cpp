// Independently authored, task #71. Premium reference SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
#include "block_000d3cd0/interface.h"

namespace nova3::recovered::block_000d3cd0 {

// 0x000d3cd0: JNI-named installer entry tail-forwards to nativeStart (0xd3c08).
extern "C" Word nova3_installer_native_start(Word a0, Word a1, Word a2, Word a3) {
    return nova3_installer_start_dependency(a0, a1, a2, a3);
}

// 0x000d3d0c: drmDebugLog has no logging operation in this build. The original
// spills r0-r3 below SP and immediately restores SP; dead stack residue is not
// reproduced here. This is an observed inactive function, not a success stub.
extern "C" void nova3_drm_debug_log(const char*, ...) {}

// 0x000d4cd4: InAppBilling_setJavaVM forwards to the setter at 0xd4cc0.
// VM ownership/global mutation belongs to that unresolved dependency.
extern "C" Word nova3_billing_set_java_vm(Word vm) {
    return nova3_billing_set_vm_dependency(vm);
}

// 0x000d4cd8: InAppBilling::launchInAppBilling(char const*) immediately returns.
extern "C" void nova3_billing_launch(void*, const char*) {}

} // namespace nova3::recovered::block_000d3cd0
