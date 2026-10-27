// Standalone bounded test; no reference executable is linked or packaged.
// The optional emitted Python program uses a locally supplied ELF as an oracle.
#include "../../../recovered/libNOVA3_neon/block_000d39f0.cpp"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <string_view>
#include <vector>

using namespace nova3::recovered::block_000d39f0;

namespace {
void require(bool ok) {
    if (!ok) {
        std::fputs("FAIL: recovered function regression\n", stderr);
        std::abort();
    }
}

std::uint32_t bitwise_crc(const std::uint8_t* data, std::size_t length) {
    std::uint32_t crc = 0xffffffffu;
    for (std::size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int b = 0; b < 8; ++b)
            crc = (crc & 1u) ? (crc >> 1) ^ 0xedb88320u : crc >> 1;
    }
    return ~crc;
}

std::vector<int> events;
void* expected_vm;
void** expected_slot;
std::int32_t environment_status;
bool write_environment;
int environment_token;
void voice(void* vm) { require(vm == expected_vm); events.push_back(1); }
void billing(void* vm) { require(vm == expected_vm); events.push_back(2); }
std::int32_t get_environment(void* vm, void** slot, std::int32_t version) {
    require(vm == expected_vm && slot == expected_slot && version == 0x10004);
    events.push_back(3);
    if (write_environment) *slot = &environment_token;
    return environment_status;
}

// Reproducible, optional research harness. It emits only aggregate evidence;
// original instruction bytes and decompiler output never leave local memory.
constexpr char oracle[] = R"PY(#!/usr/bin/env python3
import argparse, ctypes as C, hashlib, importlib.metadata, json, random, struct, zlib
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

p = argparse.ArgumentParser()
p.add_argument('--reference', required=True)
p.add_argument('--candidate', required=True)
p.add_argument('--candidate-arm-object')
a = p.parse_args()
expected_hash = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
with open(a.reference, 'rb') as f:
    actual_hash = hashlib.file_digest(f, 'sha256').hexdigest()
if actual_hash != expected_hash:
    raise SystemExit('FAIL: reference hash mismatch')
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
with open(a.reference, 'rb') as f:
    elf = ELFFile(f)
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
    for s in elf.iter_segments():
        if s['p_type'] != 'PT_LOAD':
            continue
        lo = s['p_vaddr'] & ~4095
        hi = (s['p_vaddr'] + s['p_memsz'] + 4095) & ~4095
        u.mem_map(lo, hi - lo)
        u.mem_write(s['p_vaddr'], s.data())

buf, stack, hook_page = 0x2000000, 0x3000000, 0x4000000
u.mem_map(buf, 0x20000)
u.mem_map(stack, 0x10000)
u.mem_map(hook_page, 0x1000)
stop = hook_page + 4
lib = C.CDLL(a.candidate)
crc = lib.nova3_recovered_compute_crc
crc.argtypes = [C.c_void_p, C.c_int32]
crc.restype = C.c_uint32
etc = lib.nova3_recovered_use_texture_etc
etc.argtypes = [C.c_void_p]
etc.restype = None
setter_type = C.CFUNCTYPE(None, C.c_void_p)
get_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.POINTER(C.c_void_p), C.c_int32)
class Services(C.Structure):
    _fields_ = [('voice', setter_type), ('billing', setter_type), ('get', get_type)]
load = lib.nova3_recovered_on_load
load.argtypes = [C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(Services)]
load.restype = C.c_int32

def run(entry, r0, r1=0, r2=0, budget=2000000):
    for reg, value in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1),
                       (UC_ARM_REG_R2, r2), (UC_ARM_REG_SP, stack + 0x8000),
                       (UC_ARM_REG_LR, stop)):
        u.reg_write(reg, value)
    u.emu_start(entry, stop, count=budget)
    assert u.reg_read(UC_ARM_REG_PC) == stop, 'oracle instruction budget exhausted'
    return u.reg_read(UC_ARM_REG_R0)

rng = random.Random(0x36d39f0)
crc_count = 0
vectors = [b'', b'123456789', bytes(range(256))]
vectors += [rng.randbytes(n) for n in (1,2,3,4,7,8,15,16,31,32,255,256,257,4096,65535)]
vectors += [rng.randbytes(rng.randrange(2048)) for _ in range(100)]
for data in vectors:
    for alignment in range(4):
        address = buf + alignment
        u.mem_write(address, data or b'\0')
        before = bytes(u.mem_read(address, len(data)))
        original = run(0xde2cc, address, len(data))
        owned = C.create_string_buffer(data or b'\0')
        assert original == crc(owned, len(data)) == zlib.crc32(data)
        assert bytes(u.mem_read(address, len(data))) == before
        assert owned.raw[:len(data)] == data
        crc_count += 1
assert run(0xde2cc, 0, 0) == crc(None, 0) == 0
crc_count += 1

etc_count = 0
for alignment in range(4):
    for _ in range(64):
        initial = rng.randbytes(128)
        address = buf + alignment
        u.mem_write(address, initial)
        run(0x29e7b0, address, budget=20)
        owned = C.create_string_buffer(initial, len(initial))
        etc(owned)
        expected = initial[:76] + struct.pack('<I', 4) + initial[80:]
        assert owned.raw == bytes(u.mem_read(address, 128)) == expected
        etc(owned)  # setter is idempotent
        assert owned.raw == expected
        etc_count += 1

status, writes = 0, False
native_events, source_events, env_addresses = [], [], []
env_address = None
old_env, new_env = 0x12345678, 0x456789ab
def reference_hook(uc, address, size, _):
    global env_address
    if address not in (0x9d1384, 0xd4cd4, hook_page):
        return
    vm = uc.reg_read(UC_ARM_REG_R0)
    assert vm == buf
    if address == hook_page:
        out = uc.reg_read(UC_ARM_REG_R1)
        assert uc.reg_read(UC_ARM_REG_R2) == 0x10004
        env_addresses.append(out)
        env_address = out
        if writes:
            uc.mem_write(out, struct.pack('<I', new_env))
        native_events.append(('get', vm, 0x10004))
        uc.reg_write(UC_ARM_REG_R0, status & 0xffffffff)
    else:
        native_events.append(('voice' if address == 0x9d1384 else 'billing', vm))
        # Poison ignored return values to test that original preserves the VM.
        uc.reg_write(UC_ARM_REG_R0, 0xdeadbeef)
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE, reference_hook)
@setter_type
def voice(vm): source_events.append(('voice', vm))
@setter_type
def billing(vm): source_events.append(('billing', vm))
@get_type
def get(vm, out, version):
    source_events.append(('get', vm, version))
    if writes:
        out[0] = new_env
    return status
services = Services(voice, billing, get)
u.mem_write(buf, struct.pack('<I', buf + 0x100))
u.mem_write(buf + 0x100 + 24, struct.pack('<I', hook_page))
# Discover the original output slot from actual GetEnv arguments, not a guess.
run(0xd39f0, buf, 0x77777777, budget=100)
assert env_address is not None
jni_count = 0
for status in (0,1,2,-1,-2,-3,-2147483648,2147483647):
    for writes in (False, True):
        u.mem_write(env_address, struct.pack('<I', old_env))
        native_events.clear()
        source_events.clear()
        slot = C.c_void_p(old_env)
        original = run(0xd39f0, buf, 0x77777777, budget=100)
        owned = load(buf, C.byref(slot), C.byref(services))
        assert original == (owned & 0xffffffff) == (0x10004 if status == 0 else 0xffffffff)
        assert native_events == source_events == [('voice',buf),('billing',buf),('get',buf,0x10004)]
        expected = new_env if writes else old_env
        assert struct.unpack('<I', bytes(u.mem_read(env_address, 4)))[0] == slot.value == expected
        jni_count += 1
assert len(set(env_addresses)) == 1, 'environment slot must persist across loads'
assembly = []
if a.candidate_arm_object:
    with open(a.candidate_arm_object, 'rb') as f:
        candidate = ELFFile(f)
        assert candidate['e_machine'] == 'EM_ARM' and candidate.elfclass == 32 and candidate.little_endian
        symbols = candidate.get_section_by_name('.symtab')
        for name, address, size in (
            ('nova3_recovered_compute_crc', 0xde2cc, 84),
            ('nova3_recovered_use_texture_etc', 0x29e7b0, 12)):
            symbol, = symbols.get_symbol_by_name(name)
            assert symbol['st_info']['type'] == 'STT_FUNC' and not (symbol['st_value'] & 1)
            section = candidate.get_section(symbol['st_shndx'])
            offset = symbol['st_value'] - section['sh_addr']
            rebuilt = section.data()[offset:offset + symbol['st_size']]
            reference = bytes(u.mem_read(address, size))
            # Raw symbol regions only: no register renaming or relocation masking.
            assembly.append({
                'candidate_symbol': name, 'reference_address': hex(address),
                'candidate_size': len(rebuilt), 'reference_size': len(reference),
                'candidate_region_sha256': hashlib.sha256(rebuilt).hexdigest(),
                'reference_region_sha256': hashlib.sha256(reference).hexdigest(),
                'exact_region_bytes': rebuilt == reference,
                'mode': 'A32', 'method': 'raw unlinked symbol-region equality',
            })
print(json.dumps({
    'reference_sha256': actual_hash, 'matching_claim': 'NOT MATCHED',
    'oracle': 'offline original A32 emulation with JNI dependency hooks',
    'versions': {n: importlib.metadata.version(n) for n in ('unicorn','pyelftools')},
    'crc_differential_cases': crc_count, 'etc_differential_cases': etc_count,
    'jni_differential_cases': jni_count, 'jni_environment_slot': hex(env_address),
    'assembly_comparisons': assembly,
    'scope': 'nonnegative readable CRC input; opaque 128-byte ETC objects; JNI entry with mocked VM consumers/GetEnv; no material renderer verification',
}, sort_keys=True))
)PY";
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--emit-oracle") {
        std::fputs(oracle, stdout);
        return 0;
    }
    if (argc != 1) return 2;

    const std::uint8_t check[] = {'1','2','3','4','5','6','7','8','9'};
    require(nova3_recovered_compute_crc(check, 9) == 0xcbf43926u);
    require(nova3_recovered_compute_crc(nullptr, 0) == 0);
    std::array<std::uint8_t, 1027> data{};
    std::uint32_t random = 0x363939u;
    for (auto& byte : data) {
        random = random * 1664525u + 1013904223u;
        byte = static_cast<std::uint8_t>(random >> 24);
    }
    for (int offset = 0; offset < 4; ++offset) {
        for (int length = 0; length <= 1024; ++length) {
            const auto snapshot = data;
            require(nova3_recovered_compute_crc(data.data() + offset, length) ==
                    bitwise_crc(data.data() + offset, static_cast<std::size_t>(length)));
            require(data == snapshot);
        }
        std::array<std::uint8_t, 132> object{};
        std::fill(object.begin(), object.end(), 0xa5);
        auto expected = object;
        expected[static_cast<std::size_t>(offset) + 76] = 4;
        std::fill(expected.begin() + offset + 77, expected.begin() + offset + 80, 0);
        nova3_recovered_use_texture_etc(object.data() + offset);
        require(object == expected);
        nova3_recovered_use_texture_etc(object.data() + offset);
        require(object == expected);
    }

    int vm_token, previous_environment;
    expected_vm = &vm_token;
    const VmServices services{voice, billing, get_environment};
    for (auto status : {0, 1, 2, -1, -2, -3,
                        std::numeric_limits<std::int32_t>::min(),
                        std::numeric_limits<std::int32_t>::max()}) {
        for (bool write : {false, true}) {
            void* slot = &previous_environment;
            expected_slot = &slot;
            environment_status = status;
            write_environment = write;
            events.clear();
            require(nova3_recovered_on_load(expected_vm, &slot, &services) ==
                    (status == 0 ? 0x10004 : -1));
            require(events == std::vector<int>({1, 2, 3}));
            require(slot == (write ? &environment_token : &previous_environment));
        }
    }
    std::puts("PASS: 4100 CRC comparisons + 2 known/empty vectors, 4 ETC objects, 16 JNI cases");
}
