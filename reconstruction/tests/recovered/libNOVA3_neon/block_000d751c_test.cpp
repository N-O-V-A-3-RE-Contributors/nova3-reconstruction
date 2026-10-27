// Standalone dependency-boundary test. Link the recovered .cpp separately.
// Test doubles exercise transport; they do not implement original billing.
#include "../../../recovered/libNOVA3_neon/block_000d751c/billing_forwarders.hpp"

#include <array>
#include <cstdio>
#include <cstdlib>

namespace {
unsigned calls;
unsigned expected_call;
Nova3BillingResult result;
const char* expected_first;
const char* expected_last;
Nova3BillingValue expected_value;
char* expected_buffer;
char*** expected_output;
char** replacement;

void require(bool condition) {
    if (!condition) {
        std::fputs("FAIL: task66 forwarding boundary\n", stderr);
        std::abort();
    }
}
void called(unsigned which) {
    require(which == expected_call);
    ++calls;
}
} // namespace

InAppBilling::Result InAppBilling::showDialog() {
    called(0);
    return result;
}
InAppBilling::Result InAppBilling::buyItem(
    const char* first, BillingValue value, const char* last) {
    called(1);
    require(first == expected_first && value == expected_value && last == expected_last);
    return result;
}
InAppBilling::Result InAppBilling::getLastResults(char* output) {
    called(2);
    require(output == expected_buffer);
    if (output) output[0] = 'R';
    return result;
}
InAppBilling::Result InAppBilling::fillTypeArray(char*** output) {
    called(3);
    require(output == expected_output);
    if (output) *output = replacement;
    return result;
}

int main() {
    const std::array<Nova3BillingResult, 6> results{
        0u, 1u, 0x7fffffffu, 0x80000000u, 0xffffffffu, 0x12345678u};
    const std::array<Nova3BillingValue, 7> values{
        0ull, 1ull, 0xffffffffull, 0x100000000ull, 0x8000000000000000ull,
        0xffffffffffffffffull, 0x0123456789abcdefull};
    char text[] = "synthetic";
    char buffer[] = {'L', 'x', 'G'};
    char* array[] = {text, nullptr};
    char** output = nullptr;
    const char* pointers[] = {nullptr, text, text + 1};
    for (auto value : results) {
        result = value;
        expected_call = 0;
        require(InAppBilling_ShowPendingDialog() == result);
        expected_call = 1;
        for (auto wide : values) {
            expected_value = wide;
            for (auto first : pointers) {
                expected_first = first;
                for (auto last : pointers) {
                    expected_last = last;
                    require(InAppBilling_BuyItem(first, wide, last) == result);
                }
            }
        }
        expected_call = 2;
        expected_buffer = nullptr;
        require(InAppBilling_GetLastResults(nullptr) == result);
        expected_buffer = buffer + 1;
        buffer[1] = 'x';
        require(InAppBilling_GetLastResults(expected_buffer) == result);
        require(buffer[0] == 'L' && buffer[1] == 'R' && buffer[2] == 'G');
        expected_call = 3;
        expected_output = nullptr;
        require(InAppBilling_FillTypeArray(nullptr) == result);
        expected_output = &output;
        for (char** next : {static_cast<char**>(nullptr), array}) {
            replacement = next;
            output = next == nullptr ? array : nullptr;
            require(InAppBilling_FillTypeArray(&output) == result);
            require(output == next);
        }
    }
    require(calls == 6u * (1u + 7u * 3u * 3u + 2u + 3u));
    std::printf("PASS: %u forwarding calls; argument identity, 64-bit values, "
                "result bits and output mutations\n", calls);
}
