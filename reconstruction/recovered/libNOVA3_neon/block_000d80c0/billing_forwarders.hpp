#pragma once

// Register-transport contracts for four A32 billing wrappers. The ELF method
// names establish char*** and const char* parameters, but not return types.
// ResultBits models the r0 bits forwarded by the three tail branches. Do not
// interpret them as a recovered status, count, pointer or ownership contract.
namespace nova3::recovered::block_000d80c0 {
using ResultBits = unsigned int;
static_assert(sizeof(ResultBits) == 4);
} // namespace nova3::recovered::block_000d80c0

extern "C" {
// Declaration-only seams. Integration requires source-owned implementations;
// this block neither implements billing nor loads original executable code.
unsigned int nova3_billing_fill_id_array(char*** output);
unsigned int nova3_billing_fill_billing_att_array_by_name(char*** output,
                                                       const char* name);
unsigned int nova3_billing_fill_attribute_array_by_name(char*** output,
                                                     const char* name);

// output is opaque caller-owned result storage in r0. Its size, construction,
// layout and lifetime are unresolved; no host std::string ABI is assumed.
// The wrapper ignores the dependency's r0 result and returns output instead.
void* nova3_billing_get_headers(void* output);

unsigned int InAppBilling_FillIdArray(char*** output);
void* InAppBilling_GetHeaders(void* output);
unsigned int InAppBilling_FillBillingAttArrayByName(char*** output,
                                                 const char* name);
unsigned int InAppBilling_FillAttributeArrayByName(char*** output,
                                               const char* name);
}
