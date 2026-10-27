#include "../../../recovered/libNOVA3_neon/block_000d8a54/billing_forwarders.hpp"
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Test-only dependency seam, also used by the optional local ARM oracle.
using Callback = void* (*)(unsigned, void*, const char*, const char*);
namespace { Callback callback = nullptr; }
extern "C" void nova3_test_set_callback(Callback value) { callback = value; }
extern "C" void* nova3_billing_get_item_type_by_id(void* out, const char* id) {
    return callback(0, out, id, nullptr);
}
extern "C" void* nova3_billing_get_shop_attribute_by_name(void* out, const char* name) {
    return callback(1, out, name, nullptr);
}
extern "C" void* nova3_billing_get_billing_att_by_id(
    void* out, const char* id, const char* attribute) {
    return callback(2, out, id, attribute);
}
extern "C" void* nova3_billing_get_att_by_id(
    void* out, const char* id, const char* attribute) {
    return callback(3, out, id, attribute);
}

#ifndef NOVA3_ORACLE_LIBRARY
namespace {
void require(bool value) {
    if (!value) { std::fputs("FAIL: billing forwarding\n", stderr); std::abort(); }
}
unsigned expected_kind, calls;
void* expected_output;
const char* expected_first;
const char* expected_second;
bool writes;
int ignored_result;
void* observe(unsigned kind, void* output, const char* first, const char* second) {
    ++calls;
    require(kind == expected_kind && output == expected_output);
    require(first == expected_first && second == expected_second);
    if (writes) std::memset(output, 0x30 + kind, 16);
    return &ignored_result; // Must never replace the original output pointer.
}
}

int main() {
    nova3_test_set_callback(observe);
    const char id[] = "item";
    const char attribute[] = "attribute";
    const std::array<const char*, 4> values = {nullptr, "", id, attribute};
    unsigned cases = 0;
    for (unsigned kind = 0; kind != 4; ++kind) {
        for (const char* first : values) for (const char* second : values) {
            for (bool mutate : {false, true}) {
                std::array<unsigned char, 48> bytes;
                bytes.fill(0xa5);
                expected_kind = kind;
                expected_output = bytes.data() + 16;
                expected_first = first;
                expected_second = kind < 2 ? nullptr : second;
                writes = mutate;
                calls = 0;
                void* result = nullptr;
                switch (kind) {
                case 0: result = InAppBilling_GetItemTypeByID(expected_output, first); break;
                case 1: result = InAppBilling_GetShopAttributeByName(expected_output, first); break;
                case 2: result = InAppBilling_GetBillingAttByID(expected_output, first, second); break;
                case 3: result = InAppBilling_GetAttByID(expected_output, first, second); break;
                }
                require(calls == 1 && result == expected_output);
                for (unsigned i = 0; i < bytes.size(); ++i) {
                    const unsigned char expected = mutate && i >= 16 && i < 32
                        ? static_cast<unsigned char>(0x30 + kind) : 0xa5;
                    require(bytes[i] == expected);
                }
                ++cases;
            }
        }
    }
    // No null check or dereference is introduced by a wrapper. Dependencies
    // decide whether null/aliased arguments are valid in a real billing call.
    for (unsigned kind = 0; kind != 4; ++kind) {
        expected_kind = kind; expected_output = nullptr;
        expected_first = nullptr; expected_second = nullptr;
        writes = false; calls = 0;
        void* result = nullptr;
        switch (kind) {
        case 0: result = InAppBilling_GetItemTypeByID(nullptr, nullptr); break;
        case 1: result = InAppBilling_GetShopAttributeByName(nullptr, nullptr); break;
        case 2: result = InAppBilling_GetBillingAttByID(nullptr, nullptr, nullptr); break;
        case 3: result = InAppBilling_GetAttByID(nullptr, nullptr, nullptr); break;
        }
        require(result == nullptr && calls == 1);
        ++cases;
    }
    std::printf("PASS: %u forwarding cases\n", cases);
}
#endif
