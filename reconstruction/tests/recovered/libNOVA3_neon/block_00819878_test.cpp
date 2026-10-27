// Standalone test: c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror
// reconstruction/tests/recovered/libNOVA3_neon/block_00819878_test.cpp -o /tmp/nova30-test
// /tmp/nova30-test
// Optional local oracle (requires pyelftools and Unicorn, no downloads):
// /tmp/nova30-test --emit-oracle | /path/to/python - /path/to/libNOVA3_neon.so /tmp/nova30-test
#include "../../../recovered/libNOVA3_neon/block_00819878.cpp"
#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

using namespace nova3::recovered::block_00819878;
namespace {
void require(bool condition) {
    if (!condition) { std::cerr << "test failure\n"; std::exit(1); }
}
struct References {
    unsigned events = 0;
    unsigned count = 7;
};
void retain(void* context, std::uint32_t buffer) {
    require(buffer == 0x2010000);
    auto& state = *static_cast<References*>(context);
    state.events = state.events * 10 + 1;
    ++state.count;
}
void release(void* context, std::uint32_t buffer) {
    require(buffer == 0x2010000);
    auto& state = *static_cast<References*>(context);
    state.events = state.events * 10 + 2;
    --state.count;
}
struct Lookup {
    std::int32_t index;
    unsigned calls = 0;
    const char* name;
};
std::int32_t find(void* context, const char* name) {
    auto& state = *static_cast<Lookup*>(context);
    require(state.calls == 0 && name == state.name);
    state.calls = 1;
    return state.index;
}
std::uintptr_t open_index(void* context, std::int32_t index) {
    auto& state = *static_cast<Lookup*>(context);
    require(state.calls == 1 && index == state.index);
    state.calls = 12;
    return 0x12345678;
}
void update(void* context) { ++*static_cast<unsigned*>(context); }

void unit_tests() {
    unsigned calls = 0;
    android_update(nullptr);
    require(calls == 0);
    AndroidApplication application{&calls, update};
    android_update(&application);
    android_update(&application);
    require(calls == 2);
    const char name[] = "synthetic.zip.entry";
    for (auto index : {-1, 0, 4, -2, std::numeric_limits<std::int32_t>::min(),
                       std::numeric_limits<std::int32_t>::max()}) {
        Lookup lookup{index, 0, name};
        const auto handle = open_zip_named({&lookup, find, open_index}, name);
        require(handle == (index == -1 ? 0u : 0x12345678u));
        require(lookup.calls == (index == -1 ? 1u : 12u));
    }
    std::uint32_t stride[] = {65537}, offset[] = {12}, type[] = {6}, components[] = {65538};
    MeshStreams mesh{0x2010000, stride, offset, type, components};
    References references;
    std::array<VertexStream, 256> records{};
    require(add_stream(mesh, 0xffffffffu, 0, records.data(), 255,
                       {&references, retain, release}) == 0);
    require(records[255].byte_offset == 0xffff000bu);
    require(records[255].stride == 1 && records[255].components == 2);
    require(records[255].buffer == mesh.buffer && records[255].type == 6);
    require(references.events == 112 && references.count == 8);
    references = {};
    require(add_stream(mesh, 0, 0, nullptr, 0, {&references, retain, release}) == 1);
    require(references.events == 12 && references.count == 7);
    mesh.buffer = 0;
    references = {};
    require(add_stream(mesh, 0, 0, records.data(), 0, {&references, retain, release}) == 1);
    require(records[0].buffer == 0 && references.events == 0 && references.count == 7);
}

void stream_vectors() {
    const std::uint32_t strides[] = {12, 65537, 0, 0xffffffffu};
    const std::uint32_t offsets[] = {0, 12, 0xffffffffu, 2};
    const std::uint32_t types[] = {6, 0x12345678, 0, 0xffffffffu};
    const std::uint32_t components[] = {3, 65538, 0, 0xffffffffu};
    for (auto buffer : {0u, 0x2010000u})
    for (auto minimum : {0u, 1u, 914u, 0xffffffffu})
    for (unsigned attribute = 0; attribute < 4; ++attribute)
    for (unsigned selector : {0u, 1u, 255u, 256u}) {
        const bool absent = selector == 256;
        const auto index = static_cast<std::uint8_t>(absent ? 0 : selector);
        std::array<VertexStream, 256> records{};
        References references;
        const auto result = add_stream({buffer, strides, offsets, types, components},
            minimum, attribute, absent ? nullptr : records.data(), index,
            {&references, retain, release});
        for (unsigned i = 0; i < records.size(); ++i)
            if (absent || i != index)
                require(records[i].buffer == 0 && records[i].byte_offset == 0 &&
                        records[i].type == 0 && records[i].components == 0 && records[i].stride == 0);
        const VertexStream value = absent ? VertexStream{} : records[index];
        std::cout << unsigned(result) << ' ' << value.buffer << ' ' << value.byte_offset
                  << ' ' << value.type << ' ' << value.components << ' ' << value.stride
                  << ' ' << references.events << ' ' << references.count << '\n';
    }
}

void dispatch_vectors() {
    const char name[] = "synthetic.zip.entry";
    for (auto index : {-1, 0, 4, -2, std::numeric_limits<std::int32_t>::min(),
                       std::numeric_limits<std::int32_t>::max()}) {
        Lookup lookup{index, 0, name};
        const auto handle = open_zip_named({&lookup, find, open_index}, name);
        std::cout << handle << ' ' << lookup.calls << '\n';
    }
    unsigned calls = 0;
    android_update(nullptr);
    std::cout << calls << '\n';
    AndroidApplication application{&calls, update};
    android_update(&application);
    std::cout << calls << '\n';
}

constexpr const char* oracle = R"NOVA30PY(
# Oracle scope: complete selected ARM wrappers/helper, with dependency calls
# hooked to synthetic contracts. This is not an original-engine integration test.
import hashlib, itertools, pathlib, struct, subprocess, sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *

binary, executable = map(pathlib.Path, sys.argv[1:])
expected_hash = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected_hash
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
with binary.open('rb') as f:
    for segment in ELFFile(f).iter_segments():
        if segment['p_type'] == 'PT_LOAD':
            lo = segment['p_vaddr'] & ~4095
            end = (segment['p_vaddr'] + segment['p_memsz'] + 4095) & ~4095
            u.mem_map(lo, end-lo)
            u.mem_write(segment['p_vaddr'], segment.data())
data, stack, stop, callback = 0x2000000, 0x3000000, 0x4000000, 0x4000100
u.mem_map(data, 0x20000)
u.mem_map(stack, 0x10000)
u.mem_map(stop, 0x1000)
def put(address, value): u.mem_write(address, struct.pack('<I', value & 0xffffffff))
def word(address): return struct.unpack('<I', u.mem_read(address, 4))[0]
def reg(register): return u.reg_read(register)
def ret(value=None):
    if value is not None: u.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
    u.reg_write(UC_ARM_REG_PC, reg(UC_ARM_REG_LR))
def run(address, args):
    for r in range(UC_ARM_REG_R0, UC_ARM_REG_R12+1): u.reg_write(r, 0)
    u.reg_write(UC_ARM_REG_SP, stack+0x8000)
    u.reg_write(UC_ARM_REG_LR, stop)
    for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
        u.reg_write(register, value)
    u.emu_start(address, stop, count=1000)
    assert reg(UC_ARM_REG_PC) == stop
    return reg(UC_ARM_REG_R0)

mode, events, count, lookup_index, update_calls = '', 0, 7, 0, 0
def hook(uc, address, size, unused):
    global events, count, update_calls
    if mode == 'stream':
        if address == 0x851bcc:
            assert reg(UC_ARM_REG_R0) == data+0x10000+4
            events = events*10+1; count += 1; ret(count)
        elif address == 0x109ab0:
            buffer = word(reg(UC_ARM_REG_R1))
            assert buffer in (0, data+0x10000)
            put(reg(UC_ARM_REG_R0), buffer)
            if buffer: events = events*10+1; count += 1
            ret()
        elif address == 0xec294:
            assert reg(UC_ARM_REG_R0) == data+0x10000
            events = events*10+2; count -= 1; ret()
    elif mode == 'zip':
        if address == 0x7fcec0:
            assert reg(UC_ARM_REG_R0) == data+0x100 and reg(UC_ARM_REG_R1) == data+0x200
            events = events*10+1; ret(lookup_index)
        elif address == 0x819878:
            assert reg(UC_ARM_REG_R0) == data and reg(UC_ARM_REG_R1) == data+0x100
            assert reg(UC_ARM_REG_R2) == lookup_index & 0xffffffff
            events = events*10+2; put(data, 0x12345678); ret(data)
        elif address == 0x7f5718:
            assert reg(UC_ARM_REG_R0) == data and reg(UC_ARM_REG_R1) == 0
            put(data, 0); ret(data)
    elif mode == 'android' and address == callback:
        assert reg(UC_ARM_REG_R0) == data
        update_calls += 1
        put(0xda44b0, 0) # callback may change gApp: dispatch still returns normally
        ret()
u.hook_add(UC_HOOK_CODE, hook)

mode = 'stream'
mesh, mesh_buffer, arrays, output = data, data+0x100, data+0x200, data+0x1000
values = ((12, 65537, 0, 0xffffffff), (0, 12, 0xffffffff, 2),
          (6, 0x12345678, 0, 0xffffffff), (3, 65538, 0, 0xffffffff))
for i, field in enumerate((0x0c, 0x14, 0x1c, 0x24)):
    put(mesh+field, arrays+i*16)
    for j, value in enumerate(values[i]): put(arrays+i*16+j*4, value)
actual = []
for buffer, minimum, attribute, selector in itertools.product(
        (0, data+0x10000), (0, 1, 914, 0xffffffff), range(4), (0, 1, 255, 256)):
    absent = selector == 256
    index = 0 if absent else selector
    put(mesh+0x34, buffer); put(mesh_buffer+0x20, minimum)
    u.mem_write(output, bytes(4096))
    put(stack+0x8000, 0 if absent else output)
    put(stack+0x8004, index)
    put(stack+0x8008, 0) # unused config reference
    events, count = 0, 7
    result = run(0x81bd20, (0, mesh, mesh_buffer, attribute))
    record = bytes(16) if absent else bytes(u.mem_read(output+index*16, 16))
    fields = struct.unpack('<IIIHH', record)
    rest = bytearray(u.mem_read(output, 4096))
    if not absent: rest[index*16:index*16+16] = bytes(16)
    assert not any(rest), 'writes outside selected stream'
    actual.append(' '.join(map(str, (result, *fields, events, count))))
source = subprocess.check_output([str(executable), '--stream-vectors'], text=True).splitlines()
assert len(actual) == len(source) == 128
for i, (reference, compiled) in enumerate(zip(actual, source)):
    assert reference == compiled, ('stream mismatch', i, reference, compiled)

mode = 'zip'
dispatch = []
for lookup_index in (-1, 0, 4, -2, -2147483648, 2147483647):
    events = 0; put(data, 0xdeadbeef)
    result = run(0x819ed8, (data, data+0x100, data+0x200))
    assert result == data
    assert word(data) == (0 if lookup_index == -1 else 0x12345678)
    assert events == (1 if lookup_index == -1 else 12)
    dispatch.append(f'{word(data)} {events}')

mode = 'android'
for present in (False, True):
    update_calls = 0
    put(0xda44b0, data if present else 0)
    put(data, data+0x100); put(data+0x100+0x24, callback)
    run(0x8ca0c0, ())
    assert update_calls == int(present)
    dispatch.append(str(update_calls))
compiled_dispatch = subprocess.check_output([str(executable), '--dispatch-vectors'], text=True).splitlines()
assert dispatch == compiled_dispatch, 'dispatch differential mismatch'
print('PASS: 128 stream differential cases; 6 ZIP forwarding contract cases; 2 Android dispatch cases')
print('Scope: hooked dependencies, synthetic disjoint memory, valid inputs; NOT MATCHED machine code')
print('Versions: Python', sys.version.split()[0])
import elftools, unicorn
print('pyelftools', elftools.__version__, 'Unicorn', unicorn.__version__)
)NOVA30PY";
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--emit-oracle") {
        std::cout << oracle;
        return 0;
    }
    unit_tests();
    if (argc == 2 && std::string(argv[1]) == "--stream-vectors") stream_vectors();
    else if (argc == 2 && std::string(argv[1]) == "--dispatch-vectors") dispatch_vectors();
    else if (argc == 1) std::cout << "PASS: source unit tests\n";
    else return 2;
}
