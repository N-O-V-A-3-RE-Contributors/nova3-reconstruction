#pragma once

// Opaque JNI handles; these wrappers never dereference either argument.
struct _JNIEnv;
struct _jobject;

namespace nova3::recovered::block_000d52fc::dependencies {
// Integration seams, not implementations of the original billing service.
// Map to InAppBilling::{getTotalItems,setIABObject,setContext}, respectively.
int get_total_items();
void set_iab_object(_jobject* object);
void set_context(_jobject* context);
} // namespace nova3::recovered::block_000d52fc::dependencies

extern "C" {
int InAppBilling_getTotalItems();
void InAppBilling_FreeArrayMem(char** items);
void Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetIABObject(
    _JNIEnv* environment, _jobject* receiver, _jobject* object);
void Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetContext(
    _JNIEnv* environment, _jobject* receiver, _jobject* context);
}
