#pragma once
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

namespace nova3::recovered::block_000d9968 {

using Word = uintptr_t;
using Object = void*;
using Method = void*;
struct StoreItem; // Layout belongs to the unreconstructed billing dependency.
struct Environment;

// Minimal source-owned JNI table view. Slot 35 is CallObjectMethodV, observed
// at ARM32 byte offset 0x8c. Earlier slots are never called by this block.
struct NativeInterface {
    void (*preceding_slots[35])();
    Object (*call_object_method_v)(Environment*, Object, Method, va_list);
};
static_assert(offsetof(NativeInterface, call_object_method_v) ==
              35 * sizeof(void (*)()));

struct Environment {
    const NativeInterface* functions;
    Object call_object_method(Object object, Method method, ...);
};

// Required source-owned implementations of callees at 0xd91b8, 0xda6f8 and
// 0xd9970, respectively. These are declarations, not empty success stubs.
// Return types of the original billing APIs are NOT established by their
// wrappers. Word exposes unchanged r0 bits on ARM32; it does not assert an
// original pointer/bool/enum type or recover additional result registers.
extern "C" Word nova3_iab_send_data_dependency(Object data);
extern "C" Word nova3_iab_item_array_dependency();
extern "C" Word nova3_iab_item_by_id_dependency(const char* id, StoreItem& item);

extern "C" Word nova3_000d9968(Environment*, Object receiver, Object data);
extern "C" Word nova3_000da9e4();
extern "C" Word nova3_000da9e8(const char* id, StoreItem& item);

// Valid inputs require a live environment/table/callback and a vararg sequence
// matching the Java method signature, as with JNI. No null/error policy is added.
// This view and the new exported names are not a replacement jni.h or a claim
// of full Android JNI/link compatibility. No game executable is needed to build.
} // namespace nova3::recovered::block_000d9968
