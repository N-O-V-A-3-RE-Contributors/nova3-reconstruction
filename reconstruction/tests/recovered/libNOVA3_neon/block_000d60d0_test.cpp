#include "../../../recovered/libNOVA3_neon/block_000d60d0/billing_forwarders.h"
#include <array>
#include <cstdio>
#include <cstdlib>

namespace {
enum class Operation { none, init, restore, notify, list };
Operation operation = Operation::none;
void* received_class = nullptr;
void* received_object = nullptr;
const char* received_text = nullptr;
unsigned calls = 0;
unsigned checks = 0;
unsigned int result_bits = 0;

void require(bool ok) {
    ++checks;
    if (!ok) {
        std::fputs("FAIL: billing forwarding boundary\n", stderr);
        std::abort();
    }
}
void reset() {
    operation = Operation::none;
    received_class = received_object = nullptr;
    received_text = nullptr;
    calls = 0;
}
} // namespace

// Test doubles exercise the dependency boundary only. Never part of production.
extern "C" unsigned int nova3_billing_init_dependency(void* klass, void* object) {
    ++calls;
    operation = Operation::init;
    received_class = klass;
    received_object = object;
    return result_bits;
}
extern "C" unsigned int nova3_billing_restore_dependency() {
    ++calls;
    operation = Operation::restore;
    return result_bits;
}
extern "C" unsigned int nova3_billing_notify_dependency(const char* text) {
    ++calls;
    operation = Operation::notify;
    received_text = text;
    return result_bits;
}
extern "C" unsigned int nova3_billing_item_list_dependency(const char* text) {
    ++calls;
    operation = Operation::list;
    received_text = text;
    return result_bits;
}

int main() {
    using namespace nova3::recovered::block_000d60d0;
    int env_token = 1, class_token = 2, object_token = 3;
    std::array<char, 7> payload{{'a', '\0', 'b', 'c', 'd', 'e', 'f'}};
    const auto before = payload;
    for (const auto bits : {0u, 1u, 0x7fffffffu, 0x80000000u, 0xffffffffu}) {
        result_bits = bits;
        for (void* environment : {static_cast<void*>(nullptr), static_cast<void*>(&env_token)}) {
            for (void* klass : {static_cast<void*>(nullptr), static_cast<void*>(&class_token)}) {
                for (void* object : {static_cast<void*>(nullptr), static_cast<void*>(&object_token)}) {
                    reset();
                    require(native_init(environment, klass, object) == bits);
                    require(calls == 1 && operation == Operation::init);
                    require(received_class == klass && received_object == object);
                    require(received_text == nullptr);
                }
            }
        }
        reset();
        require(restore_transactions() == bits);
        require(calls == 1 && operation == Operation::restore);
        for (const char* text : {static_cast<const char*>(nullptr),
                                 static_cast<const char*>(payload.data()),
                                 static_cast<const char*>(payload.data() + 2),
                                 static_cast<const char*>(payload.data() + 6)}) {
            // Last pointer is not a terminated string. Forwarders never read it.
            reset();
            require(send_notify_confirmation(text) == bits);
            require(calls == 1 && operation == Operation::notify);
            require(received_text == text && payload == before);
            reset();
            require(get_item_list(text) == bits);
            require(calls == 1 && operation == Operation::list);
            require(received_text == text && payload == before);
        }
    }
    require(env_token == 1 && class_token == 2 && object_token == 3);
    std::printf("PASS: %u boundary checks; downstream billing not tested\n", checks);
}
