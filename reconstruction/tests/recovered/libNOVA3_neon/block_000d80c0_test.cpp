#include "../../../recovered/libNOVA3_neon/block_000d80c0/billing_forwarders.hpp"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Test doubles only. The optional offline oracle uses these same seams to
// compare source forwarding with the original wrappers, without billing/JNI.
using ArrayCallback = unsigned int (*)(unsigned, char***, const char*);
using HeadersCallback = void* (*)(void*);
namespace {
ArrayCallback array_callback = nullptr;
HeadersCallback headers_callback = nullptr;
}
extern "C" void nova3_test_set_callbacks(ArrayCallback array, HeadersCallback headers) {
    array_callback = array;
    headers_callback = headers;
}
extern "C" unsigned int nova3_billing_fill_id_array(char*** output) {
    return array_callback(0, output, nullptr);
}
extern "C" void* nova3_billing_get_headers(void* output) {
    return headers_callback(output);
}
extern "C" unsigned int nova3_billing_fill_billing_att_array_by_name(
    char*** output, const char* name) {
    return array_callback(2, output, name);
}
extern "C" unsigned int nova3_billing_fill_attribute_array_by_name(
    char*** output, const char* name) {
    return array_callback(3, output, name);
}

#ifndef NOVA3_ORACLE_LIBRARY
namespace {
void require(bool condition) {
    if (!condition) {
        std::fputs("FAIL: task70 billing forwarding boundary\n", stderr);
        std::abort();
    }
}
unsigned expected_kind, calls, result_bits;
char*** expected_array;
const char* expected_name;
char** replacement;
bool mutate;
void* expected_storage;
void* ignored_result;

unsigned observe_array(unsigned kind, char*** output, const char* name) {
    require(kind == expected_kind && output == expected_array && name == expected_name);
    ++calls;
    if (mutate) *output = replacement;
    return result_bits;
}
void* observe_headers(void* output) {
    require(expected_kind == 1 && output == expected_storage);
    ++calls;
    if (mutate) std::memset(output, 0x3c, 16);
    return ignored_result;
}
unsigned invoke_array(unsigned kind, char*** output, const char* name) {
    switch (kind) {
    case 0: return InAppBilling_FillIdArray(output);
    case 2: return InAppBilling_FillBillingAttArrayByName(output, name);
    default: return InAppBilling_FillAttributeArrayByName(output, name);
    }
}
}

int main() {
    nova3_test_set_callbacks(observe_array, observe_headers);
    char text[] = "synthetic attribute";
    char* array[] = {text, nullptr};
    char** slot = nullptr;
    const std::array<unsigned, 6> results{
        0, 1, 0x7fffffff, 0x80000000, 0xffffffff, 0x12345678};
    const std::array<const char*, 5> names{
        nullptr, "", text, text + 1, reinterpret_cast<const char*>(&slot)};
    unsigned cases = 0;
    for (unsigned kind : {0u, 2u, 3u}) {
        expected_kind = kind;
        for (unsigned bits : results) for (const char* name : names) {
            result_bits = bits;
            expected_name = kind == 0 ? nullptr : name;
            // Null is forwarded; the wrapper adds no validation or dereference.
            calls = 0; mutate = false; expected_array = nullptr;
            require(invoke_array(kind, nullptr, name) == bits && calls == 1);
            ++cases;
            for (bool writes : {false, true}) for (char** next : {static_cast<char**>(nullptr), array}) {
                slot = next == nullptr ? array : nullptr;
                char** previous = slot;
                replacement = next;
                mutate = writes; expected_array = &slot; calls = 0;
                require(invoke_array(kind, &slot, name) == bits && calls == 1);
                require(slot == (writes ? next : previous));
                ++cases;
            }
        }
    }
    expected_kind = 1;
    for (unsigned alignment = 0; alignment < 8; ++alignment) {
        for (bool writes : {false, true}) for (unsigned bits : results) {
            std::array<unsigned char, 64> storage;
            storage.fill(0xa5);
            expected_storage = storage.data() + 16 + alignment;
            // Distinct/null/opaque dependency return values must be ignored.
            ignored_result = reinterpret_cast<void*>(static_cast<std::uintptr_t>(bits));
            calls = 0; mutate = writes;
            require(InAppBilling_GetHeaders(expected_storage) == expected_storage && calls == 1);
            for (unsigned i = 0; i < storage.size(); ++i) {
                require(storage[i] == (writes && i >= 16 + alignment && i < 32 + alignment
                                      ? 0x3c : 0xa5));
            }
            ++cases;
        }
    }
    calls = 0; mutate = false; expected_storage = nullptr; ignored_result = &slot;
    require(InAppBilling_GetHeaders(nullptr) == nullptr && calls == 1);
    ++cases;
    std::printf("PASS: %u forwarding cases; argument identity, result transport, "
                "output mutations and canaries\n", cases);
}
#endif
