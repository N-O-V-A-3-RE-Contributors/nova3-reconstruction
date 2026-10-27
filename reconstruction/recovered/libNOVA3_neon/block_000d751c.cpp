// Task #66. Reference libNOVA3_neon.so SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Four observed ARM tail-call wrappers, each with an ELF symbol size of 4.
// Only the forwarding boundary is reconstructed. Billing/JNI implementations
// and the original source return types are unresolved; see the header contract.
// Match evidence is recorded separately, never inferred from compilation.
#include "block_000d751c/billing_forwarders.hpp"

// ELF VA 0x000d751c -> 0x000d737c.
Nova3BillingResult InAppBilling_ShowPendingDialog() {
    return InAppBilling::showDialog();
}

// ELF VA 0x000d76cc -> 0x000d7520. The 64-bit argument uses r2/r3 on
// ARM AAPCS, with the final pointer at the caller's stack pointer.
extern "C" Nova3BillingResult InAppBilling_BuyItem(
    const char* first, Nova3BillingValue value, const char* last) {
    return InAppBilling::buyItem(first, value, last);
}

// ELF VA 0x000d7bf0 -> 0x000d79fc. No buffer size or format is inferred.
extern "C" Nova3BillingResult InAppBilling_GetLastResults(char* output) {
    return InAppBilling::getLastResults(output);
}

// ELF VA 0x000d7e58 -> 0x000d7bf4. No allocation/ownership policy is inferred.
extern "C" Nova3BillingResult InAppBilling_FillTypeArray(char*** output) {
    return InAppBilling::fillTypeArray(output);
}
