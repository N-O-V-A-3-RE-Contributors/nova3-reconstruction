// Independently authored recovery of task #65, Premium ARM A32 code.
// Reference SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// See the task evidence for tested scope and per-function machine-code status.
#include "block_000d52fc/api.h"

namespace billing = nova3::recovered::block_000d52fc::dependencies;
static_assert(sizeof(int) == 4, "The observed item count is signed ARM32");

// 0x000d52fc (4 bytes): forwards the result without caching or conversion.
extern "C" int InAppBilling_getTotalItems() {
    return billing::get_total_items();
}

// 0x000d5300 (76 bytes): count is queried once, even for a null outer array.
// Preconditions: a positive count requires that many readable pointer slots;
// each non-null entry and the outer array must belong to compatible new[].
// Nonpositive counts skip entry reads but still release a non-null outer array.
// No slots are cleared, and ownership of every released allocation ends here.
// char** is an inferred source type, supported by adjacent fill*Array(char***),
// not recovered DWARF. Native code establishes pointer stride and delete[].
extern "C" void InAppBilling_FreeArrayMem(char** items) {
    const int count = billing::get_total_items();
    for (int index = 0; index < count; ++index) {
        if (items[index] != nullptr) {
            delete[] items[index];
        }
    }
    if (items != nullptr) {
        delete[] items;
    }
}

// 0x000d57d8 (8 bytes): forwards only the third argument. In particular, these
// wrappers do not perform JNI global-reference management; that is a dependency.
extern "C" void
Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetIABObject(
    _JNIEnv*, _jobject*, _jobject* object) {
    billing::set_iab_object(object);
}

// 0x000d58b8 (8 bytes). A void JNI return is an inferred source signature;
// the observed instructions tail-forward the callee's raw return register.
extern "C" void
Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetContext(
    _JNIEnv*, _jobject*, _jobject* context) {
    billing::set_context(context);
}
