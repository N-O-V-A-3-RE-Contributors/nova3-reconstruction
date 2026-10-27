// Source-owned reconstruction of four Premium InAppBilling ABI forwarders.
// Reference SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Each original symbol occupies 20 bytes (0x14), not 14 decimal bytes.
// Only forwarding is recovered here; no purchase/JNI/string implementation.
#include "block_000d8a54/billing_forwarders.hpp"

// 0x000d8a54 -> InAppBilling::getItemTypeByID, 0x000d8810.
extern "C" void* InAppBilling_GetItemTypeByID(void* output, const char* id) {
    nova3_billing_get_item_type_by_id(output, id);
    return output;
}

// 0x000d8cac -> InAppBilling::getShopAttributeByName, 0x000d8a68.
extern "C" void* InAppBilling_GetShopAttributeByName(void* output, const char* name) {
    nova3_billing_get_shop_attribute_by_name(output, name);
    return output;
}

// 0x000d8f28 -> InAppBilling::getBillingAttByID, 0x000d8cc0.
extern "C" void* InAppBilling_GetBillingAttByID(
    void* output, const char* id, const char* attribute) {
    nova3_billing_get_billing_att_by_id(output, id, attribute);
    return output;
}

// 0x000d91a4 -> InAppBilling::getAttByID, 0x000d8f3c.
extern "C" void* InAppBilling_GetAttByID(
    void* output, const char* id, const char* attribute) {
    nova3_billing_get_att_by_id(output, id, attribute);
    return output;
}
