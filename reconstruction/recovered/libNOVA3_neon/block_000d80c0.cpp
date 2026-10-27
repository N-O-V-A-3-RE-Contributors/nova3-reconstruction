// Task #70. Local reference libNOVA3_neon.so SHA256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Source-owned ABI adapters only; the four billing dependencies are unresolved.
// Return types below model observed register transport, not original C++ types.
#include "block_000d80c0/billing_forwarders.hpp"

// ELF VA 0x000d80c0 -> InAppBilling::fillIdArray(char***), 0x000d7e5c.
extern "C" unsigned int InAppBilling_FillIdArray(char*** output) {
    return nova3_billing_fill_id_array(output);
}

// ELF VA 0x000d82e4 -> InAppBilling::getHeaders(), 0x000d80c4.
// Complete wrapper size is 20 bytes (0x14), not 14 decimal bytes.
extern "C" void* InAppBilling_GetHeaders(void* output) {
    nova3_billing_get_headers(output);
    return output;
}

// ELF VA 0x000d8580 -> InAppBilling::fillBillingAttArrayByName, 0x000d82f8.
extern "C" unsigned int InAppBilling_FillBillingAttArrayByName(
    char*** output, const char* name) {
    return nova3_billing_fill_billing_att_array_by_name(output, name);
}

// ELF VA 0x000d880c -> InAppBilling::fillAttributeArrayByName, 0x000d8584.
extern "C" unsigned int InAppBilling_FillAttributeArrayByName(
    char*** output, const char* name) {
    return nova3_billing_fill_attribute_array_by_name(output, name);
}
