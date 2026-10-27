#pragma once

// This is a register-transport model of four ARM forwarding entry points, not
// a recovered Android billing API. ELF names establish the parameter types of
// the callees. Their return types are NOT encoded in those names; Result models
// the r0 bits that the tail branches pass back unchanged. In particular, a
// Result must not be interpreted as a success code or dereferenced as a pointer.
// The original source return types remain UNVERIFIED.
namespace nova3::recovered::block_000d751c {
using Result = unsigned int;
using BillingValue = unsigned long long;
static_assert(sizeof(Result) == 4);
static_assert(sizeof(BillingValue) == 8);
} // namespace nova3::recovered::block_000d751c

// Declaration-only dependency boundary. No instance layout or implementation
// is claimed. These methods must be implemented from source before integration.
// The declarations deliberately preserve the observed callee symbol names.
class InAppBilling {
public:
    using Result = nova3::recovered::block_000d751c::Result;
    using BillingValue = nova3::recovered::block_000d751c::BillingValue;
    static Result showDialog();
    static Result buyItem(const char*, BillingValue, const char*);
    static Result getLastResults(char*);
    static Result fillTypeArray(char***);
};

using Nova3BillingResult = nova3::recovered::block_000d751c::Result;
using Nova3BillingValue = nova3::recovered::block_000d751c::BillingValue;

// The pending-dialog wrapper has C++ linkage in the reference; the rest have C
// linkage. Names of the value and string arguments remain unknown.
Nova3BillingResult InAppBilling_ShowPendingDialog();
extern "C" {
Nova3BillingResult InAppBilling_BuyItem(const char*, Nova3BillingValue, const char*);
Nova3BillingResult InAppBilling_GetLastResults(char*);
Nova3BillingResult InAppBilling_FillTypeArray(char***);
}
