// Standalone source-only regression:
// c++ -std=c++17 -Wall -Wextra -Wpedantic -Werror -O2 -pthread
//   reconstruction/tests/recovered/libNOVA3_neon/block_000d2f10_test.cpp -o /tmp/nova86-test
// Optional private research: --emit-oracle prints a Python differential harness.
// Normal tests neither need nor execute any original game code.
#include "../../../recovered/libNOVA3_neon/block_000d2f10.cpp"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <pthread.h>
#include <string_view>
#include <thread>
#include <vector>

using namespace nova3::recovered::block_000d2f10;

namespace {
void require(bool condition) {
    if (!condition) {
        std::fputs("FAIL: CPU query regression\n", stderr);
        std::abort();
    }
}

struct Cache {
    std::int32_t count;
    std::uint64_t features;
    std::int32_t family;
};

Cache cache{}, initial{};
int control, once_calls, initialization_calls, service_status;
bool done, allow_initialization;
void initialize() {
    ++initialization_calls;
    cache = initial;
}
int scripted_once(void* once, void (*callback)()) {
    require(once == &control && callback == initialize);
    ++once_calls;
    if (!done && allow_initialization) {
        callback();
        done = true;
    }
    return service_status;
}
CpuQueryContext context{&control, initialize, scripted_once,
                        &cache.count, &cache.features, &cache.family};

std::uint32_t random_word(std::uint32_t& state) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}
std::int32_t signed_bits(std::uint32_t value) {
    std::int32_t result;
    std::memcpy(&result, &value, sizeof result);
    return result;
}

void test_scripted_service() {
    constexpr std::array<std::int32_t, 8> boundaries{
        0, 1, -1, 2, -2, 255, std::numeric_limits<std::int32_t>::min(),
        std::numeric_limits<std::int32_t>::max()};
    constexpr std::array<std::uint64_t, 8> feature_boundaries{
        0, 1, 0xffffffffULL, 0x100000000ULL, 0x8000000000000000ULL,
        0xffffffffffffffffULL, 0x0123456789abcdefULL, 0xfedcba9876543210ULL};
    std::uint32_t rng = 0x86d2f10;
    for (unsigned i = 0; i < 1024; ++i) {
        initial = i < boundaries.size()
            ? Cache{boundaries[i], feature_boundaries[i], boundaries[7 - i]}
            : Cache{signed_bits(random_word(rng)),
                    (std::uint64_t{random_word(rng)} << 32) | random_word(rng),
                    signed_bits(random_word(rng))};
        cache = {-123, 0xdeadbeefcafebabeULL, -456};
        done = false;
        allow_initialization = true;
        initialization_calls = once_calls = 0;
        service_status = boundaries[i % boundaries.size()];
        // Rotate which query first triggers initialization of shared state.
        for (unsigned j = 0; j < 3; ++j) {
            switch ((i + j) % 3) {
            case 0: require(nova3_recovered_cpu_count(&context) == initial.count); break;
            case 1: require(nova3_recovered_cpu_features(&context) == initial.features); break;
            case 2: require(nova3_recovered_cpu_family(&context) == initial.family); break;
            }
        }
        require(once_calls == 3 && initialization_calls == 1);
        // Reread cache after a completed once; the queries do not snapshot it.
        cache = {signed_bits(random_word(rng)),
                 (std::uint64_t{random_word(rng)} << 32) | random_word(rng),
                 signed_bits(random_word(rng))};
        const auto expected = cache;
        require(nova3_recovered_cpu_count(&context) == expected.count);
        require(nova3_recovered_cpu_features(&context) == expected.features);
        require(nova3_recovered_cpu_family(&context) == expected.family);
        require(once_calls == 6 && initialization_calls == 1);

        // Dependency failure/no-write still returns the current cached bits.
        done = false;
        allow_initialization = false;
        require(nova3_recovered_cpu_count(&context) == expected.count);
        require(nova3_recovered_cpu_features(&context) == expected.features);
        require(nova3_recovered_cpu_family(&context) == expected.family);
        require(once_calls == 9 && initialization_calls == 1);
        require(cache.count == expected.count && cache.features == expected.features &&
                cache.family == expected.family);
    }
}

pthread_once_t real_control = PTHREAD_ONCE_INIT;
Cache published{};
std::atomic<unsigned> published_calls{0}, real_once_calls{0};
void publish() {
    ++published_calls;
    published = {7, 0xfedcba9876543210ULL, 3};
}
int real_once(void* once, void (*callback)()) {
    require(once == &real_control && callback == publish);
    ++real_once_calls;
    return pthread_once(static_cast<pthread_once_t*>(once), callback);
}
void test_real_once() {
    const CpuQueryContext queries{&real_control, publish, real_once,
                                 &published.count, &published.features, &published.family};
    std::atomic<unsigned> ready{0};
    std::atomic<bool> start{false};
    std::vector<std::thread> threads;
    for (unsigned t = 0; t < 8; ++t) {
        threads.emplace_back([&, t] {
            ++ready;
            while (!start.load()) std::this_thread::yield();
            for (unsigned i = 0; i < 256; ++i) {
                switch ((i + t) % 3) {
                case 0: require(nova3_recovered_cpu_count(&queries) == 7); break;
                case 1: require(nova3_recovered_cpu_features(&queries) == 0xfedcba9876543210ULL); break;
                case 2: require(nova3_recovered_cpu_family(&queries) == 3); break;
                }
            }
        });
    }
    while (ready.load() != 8) std::this_thread::yield();
    start = true;
    for (auto& thread : threads) thread.join();
    require(published_calls == 1 && real_once_calls == 8 * 256);
}

// Local oracle only: instructions are read into an isolated emulator from a
// hash-checked reference, never embedded in this test or loaded by the product.
// pthread_once/CPU detection are modeled dependencies, not verified by emulation.
constexpr char oracle[] = R"PY(#!/usr/bin/env python3
import argparse, ctypes as C, hashlib, importlib.metadata, itertools, json, random, struct
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP, UC_ARM_REG_PC)

p = argparse.ArgumentParser()
p.add_argument('--reference', required=True)
p.add_argument('--candidate', required=True, help='Source-built host shared library')
a = p.parse_args()
digest = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
with open(a.reference, 'rb') as f:
    if hashlib.file_digest(f, 'sha256').hexdigest() != digest:
        raise SystemExit('FAIL: reference SHA-256 mismatch')
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
entries = (0xd2f10, 0xd2f3c, 0xd2f6c)
sizes = (44, 48, 44)
names = ('android_getCpuCount', 'android_getCpuFeatures', 'android_getCpuFamily')
with open(a.reference, 'rb') as f:
    e = ELFFile(f)
    assert e.elfclass == 32 and e.little_endian and e['e_machine'] == 'EM_ARM'
    symbols = list(e.get_section_by_name('.symtab').iter_symbols())
    mappings = sorted((s['st_value'], s.name) for s in symbols
                      if s.name in ('$a', '$t', '$d'))
    for name, va, size in zip(names, entries, sizes):
        symbol, = [s for s in symbols if s.name == name]
        assert symbol['st_value'] == va and symbol['st_size'] == size
        assert symbol['st_info']['type'] == 'STT_FUNC' and not (va & 1)
        assert max((x for x in mappings if x[0] <= va))[1] == '$a'
        overlaps = [s for s in symbols if s['st_info']['type'] == 'STT_FUNC'
                    and s['st_size'] and s['st_value'] < va + size
                    and va < s['st_value'] + s['st_size']]
        assert len(overlaps) == 1
    for s in e.iter_segments():
        if s['p_type'] == 'PT_LOAD':
            lo = s['p_vaddr'] & ~4095
            hi = (s['p_vaddr'] + s['p_memsz'] + 4095) & ~4095
            u.mem_map(lo, hi - lo)
            u.mem_write(s['p_vaddr'], s.data())

stack, stop = 0x2000000, 0x2010000
u.mem_map(stack, 0x11000)
once_va, count_va, features_va, family_va = 0xd96598, 0xd9659c, 0xd965a0, 0xd965a8
initialize_va, once_plt = 0xd2f98, 0xd2910
lib = C.CDLL(a.candidate)
Initializer = C.CFUNCTYPE(None)
Once = C.CFUNCTYPE(C.c_int, C.c_void_p, Initializer)
class Context(C.Structure):
    _fields_ = [('once_control', C.c_void_p), ('initialize', Initializer),
                ('run_once', Once), ('count', C.POINTER(C.c_int32)),
                ('features', C.POINTER(C.c_uint64)), ('family', C.POINTER(C.c_int32))]
count, features, family, control = C.c_int32(), C.c_uint64(), C.c_int32(), C.c_int32()
source_events, original_events, callback_errors = [], [], []
initial = (0, 0, 0)
source_done = original_done = False
allow_initialization, status = True, 0
@Initializer
def initialize():
    source_events.append('initialize')
    count.value, features.value, family.value = initial
@Once
def run_once(ptr, callback):
    global source_done
    # ctypes suppresses exceptions from callbacks: propagate failures explicitly.
    if ptr != C.addressof(control):
        callback_errors.append('wrong once control')
    if C.cast(callback, C.c_void_p).value != C.cast(initialize, C.c_void_p).value:
        callback_errors.append('wrong initializer')
    source_events.append('once')
    if allow_initialization and not source_done:
        callback()
        source_done = True
    return status
context = Context(C.addressof(control), initialize, run_once,
                  C.pointer(count), C.pointer(features), C.pointer(family))
funcs = [lib.nova3_recovered_cpu_count, lib.nova3_recovered_cpu_features,
         lib.nova3_recovered_cpu_family]
for fn, result in zip(funcs, (C.c_int32, C.c_uint64, C.c_int32)):
    fn.argtypes = [C.POINTER(Context)]
    fn.restype = result

def set_original(values):
    n, mask, fam = values
    u.mem_write(count_va, struct.pack('<IQI', n & 0xffffffff, mask, fam & 0xffffffff))
def original_cache():
    return struct.unpack('<IQI', bytes(u.mem_read(count_va, 16)))
def source_cache():
    return count.value & 0xffffffff, features.value, family.value & 0xffffffff
def hook(uc, address, size, _):
    global original_done
    if address == once_plt:
        assert uc.reg_read(UC_ARM_REG_R0) == once_va
        assert uc.reg_read(UC_ARM_REG_R1) == initialize_va
        original_events.append('once')
        if allow_initialization and not original_done:
            original_events.append('initialize')
            set_original(initial)
            original_done = True
        uc.reg_write(UC_ARM_REG_R0, status & 0xffffffff)
        for reg in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12):
            uc.reg_write(reg, 0xa5a5a5a5)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    else:
        assert any(va <= address < va + size for va, size in zip(entries, sizes)), 'unexpected code'
def write_hook(uc, access, address, size, value, _):
    # No stores to globals by a getter; only its bounded stack frame is writable.
    assert stack <= address and address + size <= stack + 0x8000
u.hook_add(UC_HOOK_CODE, hook)
u.hook_add(UC_HOOK_MEM_WRITE, write_hook)
preserved = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
def invoke(index):
    source_events.clear()
    original_events.clear()
    for i, reg in enumerate(preserved):
        u.reg_write(reg, 0x12345000 + i)
    u.reg_write(UC_ARM_REG_SP, stack + 0x8000)
    u.reg_write(UC_ARM_REG_LR, stop)
    u.emu_start(entries[index], stop, count=100)
    assert u.reg_read(UC_ARM_REG_PC) == stop, 'instruction budget exhausted'
    assert u.reg_read(UC_ARM_REG_SP) == stack + 0x8000
    assert all(u.reg_read(reg) == 0x12345000 + i for i, reg in enumerate(preserved))
    result = u.reg_read(UC_ARM_REG_R0)
    if index == 1:
        result |= u.reg_read(UC_ARM_REG_R1) << 32
    actual = funcs[index](C.byref(context))
    assert not callback_errors, callback_errors
    assert result == actual & (0xffffffffffffffff if index == 1 else 0xffffffff)
    assert original_events == source_events
    assert original_cache() == source_cache()
    return result

rng = random.Random(0x86d2f10)
boundaries = (0, 1, 0xffffffff, 0x80000000, 0x7fffffff, 2, 255, 0xfffffffe)
masks = (0, 1, 0xffffffff, 0x100000000, 0x8000000000000000,
         0xffffffffffffffff, 0x0123456789abcdef, 0xfedcba9876543210)
cases = [0, 0, 0]
for scenario in range(256):
    initial = ((boundaries[scenario], masks[scenario], boundaries[7-scenario])
               if scenario < 8 else (rng.getrandbits(32), rng.getrandbits(64), rng.getrandbits(32)))
    for order in itertools.permutations(range(3)):
        source_done = original_done = False
        allow_initialization = True
        status = C.c_int32(boundaries[scenario % 8]).value
        stale = (0xdeadbeef, 0xaabbccddeeff0011, 0xcafebabe)
        set_original(stale)
        count.value, features.value, family.value = stale
        for i in order:
            assert invoke(i) == initial[i]
            cases[i] += 1
        # Completed once calls must still read the current persistent globals.
        updated = (rng.getrandbits(32), rng.getrandbits(64), rng.getrandbits(32))
        set_original(updated)
        count.value, features.value, family.value = updated
        for i in order:
            assert invoke(i) == updated[i]
            cases[i] += 1
        # Also establish the observed ignored-error/no-write path.
        source_done = original_done = False
        allow_initialization = False
        for i in order:
            assert invoke(i) == updated[i]
            cases[i] += 1
print(json.dumps({'reference_sha256': digest, 'matching': 'NOT MATCHED',
    'differential_cases_per_function': dict(zip(names, cases)),
    'versions': {n: importlib.metadata.version(n) for n in ('unicorn', 'pyelftools')},
    'scope': 'A32 getters with modeled pthread_once and initializer; return bits, call order, cache, preserved registers and SP; no actual CPU detection or Android integration'}, indent=2))
)PY";
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view{argv[1]} == "--emit-oracle") {
        std::fputs(oracle, stdout);
        return 0;
    }
    require(argc == 1);
    test_scripted_service();
    test_real_once();
    std::puts("PASS: 9216 scripted queries; 2048 concurrent pthread_once queries, one initialization");
}
