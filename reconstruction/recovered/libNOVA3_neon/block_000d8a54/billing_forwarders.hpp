#pragma once

// Explicit register-level interface for the four recovered A32 wrappers.
// output is caller-owned result storage, passed in r0; string arguments use
// r1/r2. Its size, construction and ownership belong to the dependencies.
// This deliberately does not expose a host std::string as an Android string.
// The original C++ result type/layout is not part of this recovered interface.
extern "C" {

// Source implementations of these dependencies are still required for billing.
// The wrappers ignore their return values and return output instead.
void* nova3_billing_get_item_type_by_id(void* output, const char* id);
void* nova3_billing_get_shop_attribute_by_name(void* output, const char* name);
void* nova3_billing_get_billing_att_by_id(void* output, const char* id,
                                       const char* attribute);
void* nova3_billing_get_att_by_id(void* output, const char* id,
                               const char* attribute);

void* InAppBilling_GetItemTypeByID(void* output, const char* id);
void* InAppBilling_GetShopAttributeByName(void* output, const char* name);
void* InAppBilling_GetBillingAttByID(void* output, const char* id,
                                  const char* attribute);
void* InAppBilling_GetAttByID(void* output, const char* id,
                           const char* attribute);
}
