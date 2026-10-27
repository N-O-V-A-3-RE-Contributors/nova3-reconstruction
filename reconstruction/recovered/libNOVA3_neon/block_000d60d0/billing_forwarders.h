#pragma once

// Source-owned boundary adapters, not recovered JNI/C++ declarations.
// The original return types are unknown. ResultBits exposes only the forwarded
// ARM r0 result, without assigning a success/error interpretation to it.
namespace nova3::recovered::block_000d60d0 {
using ResultBits = unsigned int;
static_assert(sizeof(ResultBits) == 4);

ResultBits native_init(void* environment, void* billing_class, void* billing_object);
ResultBits restore_transactions();
ResultBits send_notify_confirmation(const char* notification);
ResultBits get_item_list(const char* request);
} // namespace nova3::recovered::block_000d60d0

// Integration must supply actual source implementations. These are unresolved
// dependencies, not success stubs or hooks into an original executable.
extern "C" {
unsigned int nova3_billing_init_dependency(void* billing_class, void* billing_object);
unsigned int nova3_billing_restore_dependency();
unsigned int nova3_billing_notify_dependency(const char* notification);
unsigned int nova3_billing_item_list_dependency(const char* request);
}
