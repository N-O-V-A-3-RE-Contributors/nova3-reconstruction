// Standalone host tests. Replacement allocation functions exist only here.
#include "../../../recovered/libNOVA3_neon/block_000d52fc/api.h"
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <new>

namespace {
void require(bool condition) {
    if (!condition) { std::fputs("FAIL: task65 assertion\n", stderr); std::abort(); }
}
struct Event { char kind; std::uintptr_t value; };
Event events[128];
unsigned event_count;
int total_items, count_calls;
bool recording;
void* blocks[68];
bool deleted[68];
unsigned block_count;
char** outer;
char* initial_slots[66];
unsigned slot_count;

void event(char kind, std::uintptr_t value) {
    require(event_count < 128);
    events[event_count++] = {kind, value};
}
void trace_delete(void* pointer) {
    if (!recording) return;
    unsigned index = 0;
    while (index < block_count && blocks[index] != pointer) ++index;
    require(index < block_count && !deleted[index]);
    if (pointer == outer) {
        // The recovery must not clear array slots, even after freeing entries.
        require(std::memcmp(outer, initial_slots, slot_count * sizeof(char*)) == 0);
    } else {
        const char* data = static_cast<const char*>(pointer);
        require(data[0] == 'a' && data[1] == 'b' && data[2] == '\0');
    }
    deleted[index] = true;
    event('D', index);
    // A fresh query after a deletion would return a different count.
    total_items = -17;
}
void reset(int count) {
    recording = false;
    event_count = 0;
    count_calls = 0;
    block_count = 0;
    slot_count = 0;
    outer = nullptr;
    std::memset(blocks, 0, sizeof blocks);
    std::memset(deleted, 0, sizeof deleted);
    total_items = count;
}
void expect_event(unsigned index, char kind, std::uintptr_t value) {
    require(index < event_count && events[index].kind == kind && events[index].value == value);
}

void free_case(int count, unsigned long long mask, bool null_outer) {
    require(count <= 64 && (!null_outer || count <= 0));
    reset(count);
    // Extra slots are deliberately present when count <= 0, to prove no entry
    // deletion happens. Two trailing slots also detect a count+1 loop.
    slot_count = count > 0 ? static_cast<unsigned>(count) + 2 : 3;
    if (!null_outer) {
        outer = new char*[slot_count];
        // Stable IDs: slot index, with the outer array assigned slot_count.
        block_count = slot_count + 1;
        blocks[slot_count] = outer;
        for (unsigned i = 0; i < slot_count; ++i) {
            const bool trailing = count <= 0 || i >= static_cast<unsigned>(count);
            outer[i] = (trailing || (i < 64 && (mask & (1ULL << i))))
                ? new char[3]{'a','b','\0'} : nullptr;
            blocks[i] = initial_slots[i] = outer[i];
        }
    }
    recording = true;
    InAppBilling_FreeArrayMem(outer);
    recording = false;
    require(count_calls == 1);
    expect_event(0, 'C', static_cast<std::uint32_t>(count));
    unsigned expected = 1;
    if (count > 0) {
        for (unsigned i = 0; i < static_cast<unsigned>(count); ++i)
            if (blocks[i] != nullptr) expect_event(expected++, 'D', i);
    }
    if (!null_outer) expect_event(expected++, 'D', slot_count);
    require(event_count == expected);
    // Cleanup allocations deliberately left outside the recovered count.
    for (unsigned i = 0; i < slot_count; ++i)
        if (!deleted[i]) delete[] static_cast<char*>(blocks[i]);
}

void setter_case(bool context, std::uintptr_t environment,
                 std::uintptr_t receiver, std::uintptr_t object) {
    reset(0);
    auto setter = context
        ? Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetContext
        : Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSetIABObject;
    setter(reinterpret_cast<_JNIEnv*>(environment), reinterpret_cast<_jobject*>(receiver),
           reinterpret_cast<_jobject*>(object));
    require(event_count == 1 && count_calls == 0);
    expect_event(0, context ? 'X' : 'I', object);
}
void print_trace() {
    for (unsigned i = 0; i < event_count; ++i)
        std::printf("%c:%llu%s", events[i].kind,
                    static_cast<unsigned long long>(events[i].value),
                    i + 1 == event_count ? "\n" : ",");
}
} // namespace

void* operator new[](std::size_t size) {
    if (void* value = std::malloc(size ? size : 1)) return value;
    throw std::bad_alloc();
}
void operator delete[](void* pointer) noexcept {
    trace_delete(pointer);
    std::free(pointer);
}
void operator delete[](void* pointer, std::size_t) noexcept { operator delete[](pointer); }

namespace nova3::recovered::block_000d52fc::dependencies {
int get_total_items() { ++count_calls; event('C', static_cast<std::uint32_t>(total_items)); return total_items; }
void set_iab_object(_jobject* object) { event('I', reinterpret_cast<std::uintptr_t>(object)); }
void set_context(_jobject* object) { event('X', reinterpret_cast<std::uintptr_t>(object)); }
}

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--probe") == 0) {
        char command[8];
        while (std::scanf("%7s", command) == 1) {
            int count, null_outer;
            unsigned long long mask, env, receiver, object;
            if (std::strcmp(command, "free") == 0) {
                require(std::scanf("%d %llu %d", &count, &mask, &null_outer) == 3);
                free_case(count, mask, null_outer != 0);
            } else if (std::strcmp(command, "count") == 0) {
                require(std::scanf("%d", &count) == 1);
                reset(count);
                const auto value = InAppBilling_getTotalItems();
                require(count_calls == 1 && value == count);
                event('R', static_cast<std::uint32_t>(value));
            } else {
                require(std::strcmp(command, "iab") == 0 || std::strcmp(command, "context") == 0);
                require(std::scanf("%llu %llu %llu", &env, &receiver, &object) == 3);
                setter_case(std::strcmp(command, "context") == 0, env, receiver, object);
            }
            print_trace();
        }
        return 0;
    }
    require(argc == 1);
    for (int count : {INT_MIN, -100, -1, 0, 1, INT_MAX}) {
        reset(count);
        require(InAppBilling_getTotalItems() == count && count_calls == 1);
        require(event_count == 1);
    }
    for (int count : {INT_MIN, -100, -1, 0}) {
        free_case(count, 7, true);
        free_case(count, 7, false);
    }
    unsigned free_tests = 8;
    for (int count = 1; count <= 10; ++count) {
        for (unsigned mask = 0; mask < (1u << count); ++mask) {
            free_case(count, mask, false);
            ++free_tests;
        }
    }
    free_case(64, ~0ULL, false);
    free_case(64, 0xaaaaaaaaaaaaaaaaULL, false);
    for (auto object : {std::uintptr_t(0), std::uintptr_t(1), std::uintptr_t(0xffffffffu)}) {
        setter_case(false, 0x123, 0x456, object);
        setter_case(true, 0x987, 0x654, object);
    }
    std::printf("PASS: 6 count, %u cleanup, 6 JNI forwarding cases\n", free_tests + 2);
}
