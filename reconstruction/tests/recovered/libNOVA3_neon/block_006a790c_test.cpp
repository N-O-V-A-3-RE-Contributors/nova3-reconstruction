// Standalone test; the task's allowed paths do not include build-system files.
// Build with C++17 or later. --print-oracle emits the optional local-only Python
// oracle driver, kept here so that verification needs no unallocated repo file.
// No original bytes, disassembly, assets or decompiler output are embedded.
#include "../../../recovered/libNOVA3_neon/block_006a790c.cpp"

#include <array>
#include <charconv>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
using namespace nova3::recovered::libnova3_neon;

constexpr Address32 base = 0x02000000;
constexpr Address32 database_address = base + 0x300;
constexpr Address32 driver_address = base + 0x400;
constexpr Address32 material_address = base + 0x500;
constexpr Address32 root_address = base + 0x600;
constexpr Address32 factory_address = base + 0x700;
constexpr Address32 id_address = 0x11111111;
constexpr Address32 effect_address = 0x22222222;
constexpr Address32 renderer_name_address = 0x33333333;

struct Event {
    std::string operation;
    std::vector<Address32> arguments;
    bool operator==(const Event& other) const {
        return operation == other.operation && arguments == other.arguments;
    }
};

struct Case {
    bool has_root;
    Address32 cached;
    Address32 renderer;
    Address32 material;
    Address32 uri;
};

class RecordingDependencies final : public MaterialDependencies {
public:
    RecordingDependencies(Case values, MaterialHandle& result)
        : values_(values), result_(result) {}

    std::vector<Event> events;

    void find_material(MaterialHandle& out, Address32 root, Address32 id) noexcept override {
        events.push_back({"lookup", {root, id}});
        out.value = values_.cached;
    }

    void create_renderer(RendererHandle& out, Address32 factory,
                         const DatabaseView& database, Address32 driver,
                         Address32 effect, Address32 name, Address32 renderer_name,
                         Address32 root) noexcept override {
        events.push_back({"renderer", {factory, database.address, driver, effect,
                                      name, renderer_name, root, result_.value}});
        out.value = values_.renderer;
    }

    void build_material(MaterialHandle& out, const DatabaseView& database,
                        Address32 driver, RendererHandle renderer,
                        const MaterialView& material, Address32 root) noexcept override {
        events.push_back({"build", {database.address, driver, renderer.value,
                                   material.address, root}});
        out.value = values_.material;
    }

    void assign_material(MaterialHandle& out, MaterialHandle temporary) noexcept override {
        events.push_back({"assign", {out.value, temporary.value}});
        out = temporary;
    }

    void destroy_material_temporary(MaterialHandle temporary) noexcept override {
        events.push_back({"destroy_material", {temporary.value}});
    }

    void destroy_renderer_temporary(RendererHandle temporary) noexcept override {
        events.push_back({"destroy_renderer", {temporary.value}});
    }

private:
    Case values_;
    MaterialHandle& result_;
};

struct Result {
    Address32 value;
    bool returned_output_reference;
    std::vector<Event> events;
};

Result run_case(Case values) {
    MaterialHandle output{0xdecafbad};  // Detect failure to clear the output.
    RecordingDependencies dependencies(values, output);
    const DatabaseView database{database_address, factory_address};
    const auto material = material_fields_from_words(material_address,
        {id_address, 0x44444444, renderer_name_address, values.uri,
         0x55555555, 0x66666666, effect_address});
    auto& returned = factory_create_material_006a8444(
        output, dependencies, database, driver_address, material,
        values.has_root ? root_address : 0);
    return {output.value, &returned == &output, dependencies.events};
}

void require(bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

Event renderer_event(Address32 root, Address32 name) {
    return {"renderer", {factory_address, database_address, driver_address,
                         effect_address, name, renderer_name_address, root, 0}};
}

void unit_tests() {
    const auto hit = run_case({true, 17, 23, 29, 0x44444444});
    require(hit.value == 17 && hit.returned_output_reference, "cache hit result");
    require(hit.events == std::vector<Event>{{"lookup", {root_address, id_address}}},
            "cache hit must bypass construction and temporary cleanup");

    const auto unavailable = run_case({false, 17, 0, 29, 0x44444444});
    require(unavailable.value == 0 && unavailable.returned_output_reference,
            "renderer failure must return an empty output");
    require(unavailable.events == std::vector<Event>{
                renderer_event(0, 0x44444445), {"destroy_renderer", {0}}},
            "no root must skip lookup; empty renderer must skip material creation");

    const auto constructed = run_case({true, 0, 23, 29, 0x44444444});
    require(constructed.value == 29 && constructed.returned_output_reference,
            "constructed material result");
    require(constructed.events == std::vector<Event>{
                {"lookup", {root_address, id_address}},
                renderer_event(root_address, 0x44444445),
                {"build", {database_address, driver_address, 23, material_address, root_address}},
                {"assign", {0, 29}}, {"destroy_material", {29}}, {"destroy_renderer", {23}}},
            "cache miss argument forwarding and cleanup order");

    const auto empty = run_case({false, 0, 23, 0, 0xffffffff});
    require(empty.value == 0 && empty.returned_output_reference, "empty material result");
    require(empty.events == std::vector<Event>{
                renderer_event(0, 0),
                {"build", {database_address, driver_address, 23, material_address, 0}},
                {"assign", {0, 0}}, {"destroy_material", {0}}, {"destroy_renderer", {23}}},
            "empty material must still be assigned and destroyed; name addition wraps");

    const auto zero_uri = run_case({false, 0, 0, 0, 0});
    require(zero_uri.events.front() == renderer_event(0, 1),
            "native wrapper advances the URI without a null guard");
    const auto fields = material_fields_from_words(100, {1, 2, 3, 4, 5, 6, 7});
    require(fields.address == 100 && fields.id == 1 && fields.effect == 7 &&
            fields.effect_uri == 4 && fields.renderer_name == 3,
            "material prefix offsets must not exchange effect and renderer name");
    std::cout << "PASS: 6 wrapper cases (dispatch, arguments, empty results, cleanup, wraparound, field mapping)\n";
}

Address32 parse_number(std::string_view text) {
    int radix = 10;
    if (text.size() > 2 && text.substr(0, 2) == "0x") {
        text.remove_prefix(2);
        radix = 16;
    }
    Address32 value{};
    auto parsed = std::from_chars(text.data(), text.data() + text.size(), value, radix);
    require(parsed.ec == std::errc{} && parsed.ptr == text.data() + text.size(),
            "invalid unsigned 32-bit argument");
    return value;
}

void print_json(const Result& result) {
    std::cout << "{\"value\":" << result.value << ",\"returned_output_reference\":"
              << (result.returned_output_reference ? "true" : "false") << ",\"events\":[";
    bool first_event = true;
    for (const auto& event : result.events) {
        if (!first_event) std::cout << ',';
        first_event = false;
        std::cout << "[\"" << event.operation << '"';
        for (auto argument : event.arguments) std::cout << ',' << argument;
        std::cout << ']';
    }
    std::cout << "]}\n";
}

constexpr std::string_view oracle_script = R"NOVA_ORACLE(#!/usr/bin/env python3
"""Offline, bounded original-ARM wrapper oracle. No production dependency.
Emits metadata and test results only; never emits original code or assets.
Dependencies are semantic hooks, so their internals are NOT verified here.
"""
import argparse
import hashlib
import itertools
import json
import pathlib
import random
import struct
import subprocess

from elftools.elf.elffile import ELFFile
import elftools
import unicorn
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
)

EXPECTED_HASH = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
ENTRY, SIZE = 0x006a8444, 0xd4
BASE = 0x02000000
OUTPUT, DATABASE, DRIVER, MATERIAL, ROOT, FACTORY = (
    BASE + offset for offset in (0x100, 0x300, 0x400, 0x500, 0x600, 0x700)
)
ID, EFFECT, RENDERER_NAME = 0x11111111, 0x22222222, 0x33333333
STACK, STOP, VIRTUAL, VTABLE = BASE+0x18000, BASE+0x1fff0, BASE+0x1ffe0, BASE+0x1000
LOOKUP, BUILD, ASSIGN, DESTROY_MATERIAL, DESTROY_RENDERER = (
    0x71421c, 0x6a790c, 0x109c4c, 0x109bf4, 0x109f94
)

def original_case(segments, case, incoming_factory):
    root_present, cached, renderer, material, uri = case
    u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    # Merge page ranges: one mem_map per page exhausts Unicorn's section limit.
    ranges = []
    for address, memory_size, data in segments:
        ranges.append((address & ~4095, (address+memory_size+4095) & ~4095))
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    for low, high in merged:
        u.mem_map(low, high-low)
    for address, memory_size, data in segments:
        u.mem_write(address, data)
    u.mem_map(BASE, 0x20000)

    def put(address, value):
        u.mem_write(address, struct.pack("<I", value))

    def get(address):
        return struct.unpack("<I", u.mem_read(address, 4))[0]

    put(OUTPUT, 0xdecafbad)
    put(DATABASE+4, FACTORY)
    put(MATERIAL, ID)
    put(MATERIAL+8, RENDERER_NAME)
    put(MATERIAL+12, uri)
    put(MATERIAL+24, EFFECT)
    put(FACTORY, VTABLE)
    put(VTABLE+0x34, VIRTUAL)
    root = ROOT if root_present else 0
    put(STACK, MATERIAL)
    put(STACK+4, root)
    for register, value in (
        (UC_ARM_REG_R0, OUTPUT), (UC_ARM_REG_R1, incoming_factory),
        (UC_ARM_REG_R2, DATABASE), (UC_ARM_REG_R3, DRIVER),
        (UC_ARM_REG_SP, STACK), (UC_ARM_REG_LR, STOP),
    ):
        u.reg_write(register, value)

    events = []
    visited = set()
    hook_targets = {LOOKUP, BUILD, ASSIGN, DESTROY_MATERIAL, DESTROY_RENDERER, VIRTUAL}

    def hook(uc, address, size, ignored):
        if ENTRY <= address < ENTRY+SIZE:
            visited.add(address)
            return
        if address not in hook_targets:
            raise RuntimeError("unexpected execution outside wrapper: " + hex(address))
        r0, r1, r2, r3 = (uc.reg_read(r) for r in (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        sp = uc.reg_read(UC_ARM_REG_SP)
        if address == LOOKUP:
            assert r0 == OUTPUT
            events.append(["lookup", r1, r2])
            put(r0, cached)
        elif address == VIRTUAL:
            events.append(["renderer", r1, r2, r3, get(sp), get(sp+4),
                           get(sp+8), get(sp+12), get(OUTPUT)])
            put(r0, renderer)
        elif address == BUILD:
            events.append(["build", r1, r2, get(r3), get(sp), get(sp+4)])
            put(r0, material)
        elif address == ASSIGN:
            assert r0 == OUTPUT
            events.append(["assign", get(r0), get(r1)])
            put(r0, get(r1))
        elif address == DESTROY_MATERIAL:
            events.append(["destroy_material", get(r0)])
        elif address == DESTROY_RENDERER:
            events.append(["destroy_renderer", get(r0)])
        # Model the dependency return; no instruction bytes are patched.
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    u.hook_add(UC_HOOK_CODE, hook)
    u.emu_start(ENTRY, STOP, count=1000)
    assert u.reg_read(UC_ARM_REG_PC) == STOP, "instruction bound exceeded"
    return {"value": get(OUTPUT),
            "returned_output_reference": u.reg_read(UC_ARM_REG_R0) == OUTPUT,
            "events": events}, visited

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=pathlib.Path)
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    arguments = parser.parse_args()
    reference_hash = hashlib.sha256(arguments.reference.read_bytes()).hexdigest()
    if reference_hash != EXPECTED_HASH:
        raise SystemExit("reference SHA-256 mismatch; no oracle executed")
    with arguments.reference.open("rb") as stream:
        elf = ELFFile(stream)
        assert elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_ARM'
        segments = [(s['p_vaddr'], s['p_memsz'], s.data())
                    for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        symbols = [s for s in elf.get_section_by_name('.symtab').iter_symbols()
                   if s['st_value'] == ENTRY and s['st_size'] == SIZE
                   and s['st_info']['type'] == 'STT_FUNC']
        assert symbols, "wrapper boundary not corroborated by ELF symbol"
    region = next(data[ENTRY-address:ENTRY-address+SIZE]
                  for address, memory_size, data in segments
                  if address <= ENTRY and ENTRY+SIZE <= address+len(data))
    assert len(region) == SIZE

    # Exhaust all control combinations, edge pointer values and ignored this.
    cases = list(itertools.product((False, True), (0, 0x87654321),
                                  (0, 0x12345678), (0, 0xfedcba98),
                                  (0, 0x44444444, 0xffffffff), (0, BASE+0x200)))
    rng = random.Random(38)
    for _ in range(64):
        cases.append((bool(rng.getrandbits(1)),
                      rng.getrandbits(32) if rng.getrandbits(1) else 0,
                      rng.getrandbits(32) if rng.getrandbits(1) else 0,
                      rng.getrandbits(32) if rng.getrandbits(1) else 0,
                      rng.getrandbits(32), rng.getrandbits(32)))
    transcript = hashlib.sha256()
    visited = set()
    for index, case in enumerate(cases):
        *inputs, incoming_factory = case
        expected, addresses = original_case(segments, inputs, incoming_factory)
        visited.update(addresses)
        completed = subprocess.run(
            [str(arguments.candidate.resolve()), '--case'] +
            [str(int(value)) for value in inputs],
            check=True, text=True, capture_output=True, timeout=10)
        actual = json.loads(completed.stdout)
        if actual != expected:
            raise AssertionError({'case_index': index, 'inputs': case,
                                  'expected': expected, 'actual': actual})
        transcript.update(json.dumps([case, expected], sort_keys=True,
                                     separators=(',', ':')).encode('ascii') + b'\n')
    print(json.dumps({
        'result': 'PASS', 'scope': '0x006a8444 callback contract only',
        'cases': len(cases), 'reference_sha256': reference_hash,
        'entry_elf_va': hex(ENTRY), 'reference_function_size': SIZE,
        'reference_function_sha256': hashlib.sha256(region).hexdigest(),
        'oracle': 'Unicorn '+unicorn.__version__,
        'elf_reader': 'pyelftools '+elftools.__version__,
        'mode': 'ARM little-endian', 'elf_relocations_applied': False,
        'unique_wrapper_instruction_addresses_executed': len(visited),
        'hooked_dependencies': [hex(x) for x in sorted(hook_targets_for_report())],
        'executable_bytes_modified': False,
        'transcript_sha256': transcript.hexdigest(),
        'machine_code_match': 'NOT MATCHED',
        'excluded': ['dependency implementations', 'intrusive reference counts',
                     'original C++ ABI', 'exceptions', 'invalid memory inputs',
                     'other three assigned functions', 'engine integration'],
    }, indent=2))

def hook_targets_for_report():
    return {LOOKUP, BUILD, ASSIGN, DESTROY_MATERIAL, DESTROY_RENDERER, VIRTUAL}

if __name__ == '__main__':
    main()
)NOVA_ORACLE";

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--print-oracle") {
            std::cout << oracle_script;
        } else if (argc == 7 && std::string_view(argv[1]) == "--case") {
            const auto root = parse_number(argv[2]);
            require(root <= 1, "root flag must be 0 or 1");
            print_json(run_case({root != 0, parse_number(argv[3]), parse_number(argv[4]),
                                 parse_number(argv[5]), parse_number(argv[6])}));
        } else if (argc == 1) {
            unit_tests();
        } else {
            throw std::runtime_error("usage: test [--print-oracle | --case ROOT CACHE RENDERER MATERIAL URI]");
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
