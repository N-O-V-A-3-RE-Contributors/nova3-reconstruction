// Task #64. Reference libNOVA3_neon.so SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// Only the four forwarding boundaries are reconstructed. No billing operation,
// Java service, JNI environment ownership or downstream side effect is supplied.
#include "block_000d60d0/billing_forwarders.h"

namespace nova3::recovered::block_000d60d0 {

// 0x000d60d0, 12 bytes. JNI nativeInit discards the environment argument and
// forwards class/object to InAppBilling::init(_jclass*, _jobject*).
ResultBits native_init(void*, void* billing_class, void* billing_object) {
    return nova3_billing_init_dependency(billing_class, billing_object);
}

// 0x000d6bc8, 4 bytes. Tail dispatch to restoreTransactions().
ResultBits restore_transactions() {
    return nova3_billing_restore_dependency();
}

// 0x000d6e30, 4 bytes. Tail dispatch to sendNotifyConfirmation(char const*).
// The argument is borrowed and forwarded verbatim; no string access occurs here.
ResultBits send_notify_confirmation(const char* notification) {
    return nova3_billing_notify_dependency(notification);
}

// 0x000d6f88, 4 bytes. Tail dispatch to getItemList(char const*).
ResultBits get_item_list(const char* request) {
    return nova3_billing_item_list_dependency(request);
}

} // namespace nova3::recovered::block_000d60d0
