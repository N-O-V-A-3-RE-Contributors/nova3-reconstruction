// Standalone tests: include the implementation, do not also link its .o.
#include "../../../recovered/libNOVA3_neon/block_000d4dbc.cpp"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {
int billing_result;
int clear_calls;
int state_calls;
std::vector<int> events;
JNIEnv* expected_environment;
const char* expected_name;
const char* expected_message;
jclass class_result;
jint throw_result;

void require(bool condition) {
    if (!condition) {
        std::fputs("FAIL: block_000d4dbc regression\n", stderr);
        std::abort();
    }
}

jclass find_class(JNIEnv* env, const char* name) {
    require(env == expected_environment && name == expected_name);
    events.push_back(1);
    return class_result;
}
jint throw_new(JNIEnv* env, jclass type, const char* message) {
    require(env == expected_environment && type == class_result);
    require(message == expected_message);
    events.push_back(2);
    return throw_result;
}
void delete_local(JNIEnv* env, jobject type) {
    require(env == expected_environment && type == class_result);
    events.push_back(3);
}

void check_conversion(const char* data, std::int32_t length, std::uint64_t expected) {
    std::array<std::uint64_t, 3> guarded{0x123456789abcdef0ULL, ~0ULL,
                                       0xfedcba9876543210ULL};
    charToUI64(&guarded[1], length, reinterpret_cast<const unsigned char*>(data));
    require(guarded[1] == expected);
    require(guarded[0] == 0x123456789abcdef0ULL);
    require(guarded[2] == 0xfedcba9876543210ULL);
}
} // namespace

// Test-only definitions for unreconstructed external billing methods.
int InAppBilling::clearItemList() { ++clear_calls; return billing_result; }
int InAppBilling::getState() { ++state_calls; return billing_result; }

// Used by the optional offline differential harness, never production code.
extern "C" void nova69_test_billing_reset(int result) {
    billing_result = result;
    clear_calls = state_calls = 0;
}
extern "C" int nova69_test_billing_calls(int state) {
    return state ? state_calls : clear_calls;
}
extern "C" std::uint64_t nova69_test_unsigned_conversion(double value) {
    return arm_unsigned_conversion(value);
}

int main() {
    check_conversion(nullptr, 0, 0);
    check_conversion(nullptr, -1, 0);
    check_conversion(nullptr, std::numeric_limits<std::int32_t>::min(), 0);
    check_conversion("0", 1, 0);
    check_conversion("12345", 5, 12345);
    check_conversion("00000001", 8, 1);
    check_conversion("9007199254740993", 16, 9007199254740992ULL);
    check_conversion("18446744073709551615", 20, ~0ULL);
    check_conversion("10000000000000000001", 20, 10000000000000000000ULL);
    check_conversion("1/", 2, 9);
    check_conversion("/1", 2, 1); // Negative intermediate saturates to zero.
    check_conversion(":", 1, 10);
    check_conversion("1\0", 2, 0); // Embedded NUL is a signed-byte contribution.
    const char high[] = {static_cast<char>(0x80), static_cast<char>(0xff)};
    check_conversion(high, 1, 0);
    check_conversion(high + 1, 1, 0);
    std::uint64_t aliased = 0x3131313131313131ULL;
    charToUI64(&aliased, 8, reinterpret_cast<const unsigned char*>(&aliased));
    require(aliased == 0); // Initialization overwrites all eight input bytes.
    std::puts("PASS: 16 conversion vectors, including rounding, signed bytes and overlap");

    JNINativeInterface table{};
    table.FindClass = find_class;
    table.ThrowNew = throw_new;
    table.DeleteLocalRef = delete_local;
    JNIEnv env{&table};
    expected_environment = &env;
    _jclass token;
    for (const auto type : {static_cast<jclass>(nullptr), &token}) {
        for (const auto status : {0, -1, std::numeric_limits<int>::min(),
                                  std::numeric_limits<int>::max()}) {
            for (const auto message : {static_cast<const char*>(nullptr), "message"}) {
                events.clear();
                class_result = type;
                throw_result = status;
                expected_name = "java/lang/IllegalArgumentException";
                expected_message = message;
                JNU_ThrowByName(&env, expected_name, message);
                require(events == (type ? std::vector<int>{1, 2, 3}
                                        : std::vector<int>{1, 3}));
            }
        }
    }
    std::puts("PASS: 16 JNI lookup/throw/delete traces");

    for (const int result : {0, 1, -1, std::numeric_limits<int>::min(),
                             std::numeric_limits<int>::max()}) {
        nova69_test_billing_reset(result);
        require(InAppBilling_ClearItemList() == result);
        require(clear_calls == 1 && state_calls == 0);
        require(InAppBilling_GetState() == result);
        require(clear_calls == 1 && state_calls == 1);
    }
    std::puts("PASS: 10 forwarding wrapper calls; billing targets are test doubles");
}
