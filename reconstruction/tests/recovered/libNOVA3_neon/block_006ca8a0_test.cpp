// Standalone because task 41 does not permit editing the project's CMakeLists.
/* c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
     reconstruction/tests/recovered/libNOVA3_neon/block_006ca8a0_test.cpp \
     -o /tmp/nova3-task41-test */
// /tmp/nova3-task41-test
// Optional offline research oracle (Python 3 + Unicorn; no download/install):
// /tmp/nova3-task41-test --arm-oracle /local/path/to/libNOVA3_neon.so
// The oracle reads the hash-checked reference only in this test process.
// It executes branch slices, stops before external calls, and never patches
// executable bytes or substitutes engine helper results. No runtime emulator
// or proprietary code is part of the reconstructed source module.

#include "../../../recovered/libNOVA3_neon/block_006ca8a0.cpp"

#include <array>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#if defined(__unix__)
#include <unistd.h>
#endif

namespace recovered = nova3::recovered::block_006ca8a0;

namespace {

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

constexpr std::array<std::uint32_t, 3> upper_bits{
    0u, 0x80000000u, 0xfffff800u};

void regressions() {
    using namespace recovered;
    require(!select_instance_material_binding_table(0), "zero flags");
    require(!select_instance_material_binding_table(0xfffff800u), "upper bits");
    // Individual bits, including all three noncontiguous bits of 0x460.
    constexpr std::array<std::uint32_t, 11> expected{
        0x1c, 0x1c, 0x1c, 0x24, 0x24, 0x14,
        0x14, 0x34, 0x2c, 0x2c, 0x14};
    for (unsigned bit = 0; bit < expected.size(); ++bit) {
        require(select_instance_material_binding_table(1u << bit) == expected[bit],
                "individual binding-table bit");
    }
    require(select_instance_material_binding_table(0x7ff) == 0x1cu,
            "first family wins");
    require(select_instance_material_binding_table(0x7f8) == 0x24u,
            "second family wins");
    require(select_instance_material_binding_table(0x7e0) == 0x14u,
            "third family wins");
    require(select_instance_material_binding_table(0x380) == 0x2cu,
            "fourth family wins");
    for (std::uint32_t bits = 0; bits < 0x800; ++bits) {
        for (auto upper : upper_bits) {
            require(select_instance_material_binding_table(bits | upper) ==
                        select_instance_material_binding_table(bits),
                    "upper bits cannot change binding selection");
        }
    }

    require(select_material_renderer_route(0, true, 0xffff) ==
                MaterialRendererRoute::null_shader_manager, "driver priority");
    require(select_material_renderer_route(1, false, 7) ==
                MaterialRendererRoute::pink_wireframe, "missing-effect priority");
    require(select_material_renderer_route(1, true, 0) ==
                MaterialRendererRoute::cached_renderer, "zero is a valid ID");
    require(select_material_renderer_route(1, true, 0xfffe) ==
                MaterialRendererRoute::cached_renderer, "last valid ID");
    require(select_material_renderer_route(1, true, 0xffff) ==
                MaterialRendererRoute::effect_builder, "missing-ID sentinel");
    for (std::uint32_t id = 0; id < 0xffff; ++id) {
        require(select_material_renderer_route(0xffffffffu, true,
                        static_cast<std::uint16_t>(id)) ==
                    MaterialRendererRoute::cached_renderer, "valid ID range");
    }

    require(select_mesh_buffer_lookup(false, false) ==
                MeshBufferLookup::create_private, "uncached private buffer");
    require(select_mesh_buffer_lookup(false, true) ==
                MeshBufferLookup::create_private, "private ignores cache");
    require(select_mesh_buffer_lookup(true, true) ==
                MeshBufferLookup::reuse_shared, "shared cache hit");
    require(select_mesh_buffer_lookup(true, false) ==
                MeshBufferLookup::lock_and_recheck, "shared miss needs lock");
    require(recheck_mesh_buffer_cache(true) ==
                LockedMeshBufferLookup::unlock_and_reuse_shared,
            "another publisher won before lock");
    require(recheck_mesh_buffer_cache(false) ==
                LockedMeshBufferLookup::create_and_publish_shared,
            "cache still empty under lock");
}

// A small plain-text protocol lets the independent ARM oracle compare the
// actual compiled C++ decisions without duplicating their implementation.
void dump_policies() {
    using namespace recovered;
    for (auto upper : upper_bits) {
        for (std::uint32_t low = 0; low < 0x800; ++low) {
            const auto flags = upper | low;
            const auto offset = select_instance_material_binding_table(flags);
            std::cout << "A " << flags << ' ' << offset.value_or(0) << '\n';
        }
    }
    for (std::uint32_t mode : {0u, 1u, 0xffffffffu}) {
        for (bool effect : {false, true}) {
            for (std::uint16_t id : {0u, 1u, 0xfffeu, 0xffffu}) {
                std::cout << "R " << mode << ' ' << effect << ' ' << id << ' '
                          << static_cast<unsigned>(
                                 select_material_renderer_route(mode, effect, id))
                          << '\n';
            }
        }
    }
    for (bool share : {false, true}) {
        for (bool cached : {false, true}) {
            std::cout << "B " << share << ' ' << cached << ' '
                      << static_cast<unsigned>(select_mesh_buffer_lookup(share, cached))
                      << '\n';
        }
    }
    for (bool cached : {false, true}) {
        std::cout << "L " << cached << ' '
                  << static_cast<unsigned>(recheck_mesh_buffer_cache(cached)) << '\n';
    }
}

#if defined(__unix__)
// Original executable bytes are never embedded in this script. The original
// ARM instructions, not a second implementation of the masks, determine the
// expected results. Synthetic object/stack data is confined to test memory.
constexpr char arm_oracle[] = R"NOVA_ORACLE(
import hashlib
import json
import pathlib
import struct
import subprocess
import sys

reference = pathlib.Path(sys.argv[2])
raw = reference.read_bytes()
expected_hash = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
if hashlib.sha256(raw).hexdigest() != expected_hash:
    raise SystemExit('FAIL: reference SHA256 mismatch; no comparison performed')
if raw[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', raw, 18)[0] != 40:
    raise SystemExit('FAIL: expected ELF32 little-endian ARM reference')

import unicorn
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_SP, UC_ARM_REG_PC)

# Only pages containing the specified branch slices are mapped as code.
code_lo, code_hi = 0x6ca000, 0x6d9000
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
u.mem_map(code_lo, code_hi - code_lo)
phoff = struct.unpack_from('<I', raw, 28)[0]
phentsize, phnum = struct.unpack_from('<HH', raw, 42)
if phentsize != 32 or phoff + phentsize * phnum > len(raw):
    raise SystemExit('FAIL: invalid program header bounds')
loaded = []
for i in range(phnum):
    kind, offset, va, _, filesz, memsz, _, _ = struct.unpack_from('<8I', raw, phoff+i*32)
    if kind != 1:
        continue
    if filesz > memsz or offset + filesz > len(raw):
        raise SystemExit('FAIL: invalid PT_LOAD bounds')
    lo, hi = max(code_lo, va), min(code_hi, va + filesz)
    if lo < hi:
        u.mem_write(lo, raw[offset + lo - va:offset + hi - va])
        loaded.append((lo, hi))
if not any(lo <= code_lo and hi >= code_hi for lo, hi in loaded):
    raise SystemExit('FAIL: branch-slice code range not backed by reference')

data = 0x2000000
u.mem_map(data, 0x10000)
sp, mesh, table, output = data + 0xf000, data + 0x100, data + 0x1000, data + 0x8000
def put(address, value):
    u.mem_write(address, struct.pack('<I', value))

run_spec = None
def check_code(uc, address, size, _):
    # Stops happen before external calls/refcount operations. No instruction
    # hook changes a register, memory value, or control-flow destination.
    stops, intervals = run_spec
    if address in stops:
        uc.emu_stop()
    elif not any(lo <= address < hi for lo, hi in intervals):
        raise RuntimeError('unexpected slice exit at ' + hex(address))
u.hook_add(UC_HOOK_CODE, check_code)

def run(start, stops, intervals, registers):
    global run_spec
    run_spec = (set(stops), intervals)
    u.reg_write(UC_ARM_REG_SP, sp)
    for register, value in registers.items():
        u.reg_write(register, value)
    u.emu_start(start, code_hi, count=64)
    pc = u.reg_read(UC_ARM_REG_PC)
    if pc not in run_spec[0]:
        raise RuntimeError('instruction budget exhausted at ' + hex(pc))
    return pc

def equal(actual, expected, context):
    if actual != expected:
        raise RuntimeError(f'{context}: original={actual}, source={expected}')

counts = dict(attribute_selection=0, renderer_dispatch=0, renderer_id_lookup=0,
              mesh_initial_lookup=0, mesh_locked_recheck=0)
lines = subprocess.check_output([sys.argv[1], '--dump-policies'], text=True).splitlines()
for line in lines:
    fields = line.split()
    values = list(map(int, fields[1:]))
    if fields[0] == 'A':
        flags, expected = values
        put(sp+28, output)
        put(output, 0xdeadbeef)
        pc = run(0x6ca928, (0x6ca940, 0x6cace4),
                 ((0x6ca928, 0x6ca940), (0x6cacb8, 0x6cace4)),
                 {UC_ARM_REG_R0: flags, UC_ARM_REG_R4: table, UC_ARM_REG_R7: 0xffffffff})
        if pc == 0x6ca940:
            actual = u.reg_read(UC_ARM_REG_R7) - table
        else:
            equal(struct.unpack('<I', u.mem_read(output, 4))[0], 0, 'null map write')
            actual = 0
        equal(actual, expected, 'attribute flags ' + hex(flags))
        counts['attribute_selection'] += 1
    elif fields[0] == 'R':
        mode, effect, cached_id, expected = values
        pc = run(0x6cad48, (0x6cae88, 0x6cafac, 0x6cad58),
                 ((0x6cad48, 0x6cad58),),
                 {UC_ARM_REG_R0: mode, UC_ARM_REG_R10: table if effect else 0})
        if pc == 0x6cae88:
            actual = 0
        elif pc == 0x6cafac:
            actual = 1
        else:
            pc = run(0x6cad88, (0x6cad94, 0x6cadc0),
                     ((0x6cad88, 0x6cad94),), {UC_ARM_REG_R0: cached_id})
            actual = 2 if pc == 0x6cad94 else 3
            counts['renderer_id_lookup'] += 1
        equal(actual, expected, f'renderer {mode}/{effect}/{cached_id}')
        counts['renderer_dispatch'] += 1
    elif fields[0] == 'B':
        share, cached, expected = values
        for index in (0, 1, 7, 31):
            put(mesh+0x3c, table)
            put(table+index*56+0x34, output if cached else 0)
            put(sp+20, share)
            regs = {UC_ARM_REG_R8: mesh, UC_ARM_REG_R7: index*56, UC_ARM_REG_R9: share}
            # Standalone CMesh constructor's initial lookup.
            pc = run(0x6d8284, (0x6d8290, 0x6d8104, 0x6d8360),
                     ((0x6d8284, 0x6d8290), (0x6d80f0, 0x6d8104)), regs)
            actual = {0x6d8290: 0, 0x6d8104: 1, 0x6d8360: 2}[pc]
            equal(actual, expected, f'constructor buffer {index}/{share}/{cached}')
            counts['mesh_initial_lookup'] += 1
            # Factory's inlined version of the same policy.
            pc = run(0x6d8a88, (0x6d8d20, 0x6d8aa4, 0x6d8d78),
                     ((0x6d8a88, 0x6d8aa4),), regs)
            actual = {0x6d8d20: 0, 0x6d8aa4: 1, 0x6d8d78: 2}[pc]
            equal(actual, expected, f'factory buffer {index}/{share}/{cached}')
            counts['mesh_initial_lookup'] += 1
    elif fields[0] == 'L':
        cached, expected = values
        for index in (0, 1, 7, 31):
            put(mesh+0x3c, table)
            put(table+index*56+0x34, output if cached else 0)
            regs = {UC_ARM_REG_R8: mesh, UC_ARM_REG_R7: index*56}
            pc = run(0x6d836c, (0x6d8380, 0x6d838c),
                     ((0x6d836c, 0x6d8380),), regs)
            equal(0 if pc == 0x6d8380 else 1, expected, 'constructor locked recheck')
            counts['mesh_locked_recheck'] += 1
            pc = run(0x6d8d84, (0x6d8d98, 0x6d8da4),
                     ((0x6d8d84, 0x6d8d98),), regs)
            equal(0 if pc == 0x6d8d98 else 1, expected, 'factory locked recheck')
            counts['mesh_locked_recheck'] += 1
    else:
        raise RuntimeError('unknown source-output record')
equal(counts, dict(attribute_selection=6144, renderer_dispatch=24,
                  renderer_id_lookup=8, mesh_initial_lookup=32, mesh_locked_recheck=16),
      'oracle coverage counts')
print(json.dumps(dict(result='PASS', reference_sha256=expected_hash,
                     python=sys.version.split()[0], unicorn=unicorn.__version__,
                     cases=counts, arm_slice_executions=sum(counts.values()),
                     differential_policy_cases=sum(counts.values())-counts['renderer_id_lookup'],
                     matching='NOT MATCHED',
                     scope='Original ARM branch slices versus compiled portable policies only; '
                           'no whole-function, ABI, allocation, lifetime, locking, GPU or gameplay equivalence.'),
                 indent=2))
)NOVA_ORACLE";
#endif

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--dump-policies") {
            dump_policies();
            return EXIT_SUCCESS;
        }
        if (argc == 3 && std::string(argv[1]) == "--arm-oracle") {
#if defined(__unix__)
            execlp("python3", "python3", "-c", arm_oracle, argv[0], argv[2],
                   static_cast<char*>(nullptr));
            throw std::runtime_error("could not execute local python3 oracle");
#else
            throw std::runtime_error("optional ARM oracle requires a Unix host");
#endif
        }
        if (argc != 1) throw std::runtime_error("unexpected arguments");
        regressions();
        std::cout << "PASS: portable control-policy regressions; full functions NOT MATCHED\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
