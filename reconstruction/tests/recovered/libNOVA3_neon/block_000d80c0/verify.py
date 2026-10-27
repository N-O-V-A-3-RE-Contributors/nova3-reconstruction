#!/usr/bin/env python3
"""Offline wrapper verification. Requires existing pyelftools/Unicorn and tools.

Reference bytes remain in memory only; reports contain hashes and counts.
The original and rebuilt ARM wrappers are exercised with test-only dependency
hooks. This does not run the original billing implementations or the game.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import tempfile

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
# wrapper, start, size (decimal), source dependency, original dependency, target
FUNCTIONS = [
    ("InAppBilling_FillIdArray", 0xD80C0, 4, "nova3_billing_fill_id_array",
     "_ZN12InAppBilling11fillIdArrayEPPPc", 0xD7E5C),
    ("InAppBilling_GetHeaders", 0xD82E4, 20, "nova3_billing_get_headers",
     "_ZN12InAppBilling10getHeadersEv", 0xD80C4),
    ("InAppBilling_FillBillingAttArrayByName", 0xD8580, 4,
     "nova3_billing_fill_billing_att_array_by_name",
     "_ZN12InAppBilling25fillBillingAttArrayByNameEPPPcPKc", 0xD82F8),
    ("InAppBilling_FillAttributeArrayByName", 0xD880C, 4,
     "nova3_billing_fill_attribute_array_by_name",
     "_ZN12InAppBilling24fillAttributeArrayByNameEPPPcPKc", 0xD8584),
]


def sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    if sha(args.reference) != REFERENCE_SHA256:
        raise SystemExit("Reference SHA256 mismatch; refusing oracle")

    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
        UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
        UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
        UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR,
        UC_ARM_REG_PC)

    root = Path(__file__).resolve().parents[5]
    source = root / "reconstruction/recovered/libNOVA3_neon/block_000d80c0.cpp"
    header = source.parent / "block_000d80c0/billing_forwarders.hpp"
    test = root / "reconstruction/tests/recovered/libNOVA3_neon/block_000d80c0_test.cpp"
    checks = []

    def run(command, **kwargs):
        command = [str(value) for value in command]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True,
                                timeout=60, **kwargs)
        checks.append({"command": command, "exit_code": result.returncode,
                       "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        require(result.returncode == 0, json.dumps(checks[-1]))
        return result.stdout.strip()

    def region(elf, table, name):
        symbol, = table.get_symbol_by_name(name)
        section = elf.get_section(symbol["st_shndx"])
        start = symbol["st_value"] - section["sh_addr"]
        size = symbol["st_size"]
        require(0 <= start and start + size <= section["sh_size"], "symbol bounds")
        return symbol, section.data()[start:start + size]

    common = ["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
    arm_flags = ["--target=armv7-none-eabi", "-march=armv7-a", "-marm",
                 "-mfloat-abi=soft", "-O2", "-fomit-frame-pointer",
                 "-fno-exceptions", "-fno-unwind-tables",
                 "-fno-asynchronous-unwind-tables", "-ffunction-sections"]
    with tempfile.TemporaryDirectory(prefix="nova3-task70-") as directory:
        tmp = Path(directory)
        run(["g++", *common, "-O2", "-c", source, "-o", tmp / "host.o"])
        run(["g++", *common, "-O2", source, test, "-o", tmp / "unit"])
        run([tmp / "unit"])
        run(["clang++", *common, "-O1", "-g", "-fsanitize=address,undefined",
             "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", source, test,
             "-o", tmp / "sanitized"])
        # LeakSanitizer is incompatible with the session's tracing restrictions.
        run([tmp / "sanitized"], env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        run(["clang++", *common, *arm_flags, "-c", source, "-o", tmp / "arm.o"])

        # Analysis-only address placement resolves relocations without linking
        # reference code. Absolute dependency symbols have no implementation.
        layout = "SECTIONS {\n"
        for name, address, *_ in FUNCTIONS:
            layout += f" .text.{name} {address:#x} : {{ *(.text.{name}) }}\n"
        layout += " /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }\n}\n"
        (tmp / "placement.ld").write_text(layout)
        run(["ld.lld", "-T", tmp / "placement.ld", "--entry=" + FUNCTIONS[0][0],
             *[f"--defsym={dep}={target:#x}" for _, _, _, dep, _, target in FUNCTIONS],
             tmp / "arm.o", "-o", tmp / "placed.elf"])
        print("PASS: host tests, sanitizers and source-only ARM compilation", file=sys.stderr)

        reference_regions, rebuilt_regions = {}, {}
        with args.reference.open("rb") as stream:
            elf = ELFFile(stream)
            require(elf.elfclass == 32 and elf.little_endian and
                    elf["e_machine"] == "EM_ARM", "reference architecture")
            # Keep table objects: get_symbol_by_name caches its name index per
            # object. Recreating the 107k-symbol table for each query needlessly
            # repeats a costly full scan of the same read-only reference.
            tables = {name: elf.get_section_by_name(name)
                      for name in (".symtab", ".dynsym")}
            for name, address, size, _, original_dep, target in FUNCTIONS:
                symbol, data = region(elf, tables[".symtab"], name)
                require(symbol["st_value"] == address and symbol["st_size"] == size
                        and symbol["st_info"]["type"] == "STT_FUNC", "reference symbol")
                for table in tables.values():
                    item, = table.get_symbol_by_name(name)
                    dependency, = table.get_symbol_by_name(original_dep)
                    require(item["st_value"] == address and item["st_size"] == size
                            and dependency["st_value"] == target, "symbol provenance")
                reference_regions[name] = data

        comparisons = []
        with (tmp / "placed.elf").open("rb") as stream:
            elf = ELFFile(stream)
            table = elf.get_section_by_name(".symtab")
            for name, address, size, *_ in FUNCTIONS:
                symbol, data = region(elf, table, name)
                rebuilt_regions[name] = data
                original = reference_regions[name]
                exact = data == original and symbol["st_value"] == address
                comparisons.append({"symbol": name, "address": hex(address),
                    "reference_size": size, "candidate_size": len(data),
                    "reference_region_sha256": hashlib.sha256(original).hexdigest(),
                    "candidate_region_sha256": hashlib.sha256(data).hexdigest(),
                    "exact_bytes_after_placement": exact})
                require(exact, "NOT MATCHED: " + name)
        print("PASS: four complete ARM symbol regions match after placement", file=sys.stderr)

        # Two isolated machines contain only the four wrapper bodies. Every
        # dependency is intercepted before any instruction there can execute.
        # No original ELF loading, relocation or JNI/runtime execution occurs.
        buf, stack, stop = 0x2000000, 0x3000000, 0x4000000
        sp = stack + 2048
        registers = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                     UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
                     UC_ARM_REG_R11, UC_ARM_REG_R12]
        machines = []
        events = [[], []]
        pattern = b""
        return_bits = 0
        mutate = False

        def hook(index):
            def dependency(machine, address, _size, _data):
                found = [kind for kind, item in enumerate(FUNCTIONS) if item[-1] == address]
                if not found:
                    return
                kind, = found
                output = machine.reg_read(UC_ARM_REG_R0)
                name = machine.reg_read(UC_ARM_REG_R1) if kind >= 2 else None
                events[index].append((kind, output, name,
                    machine.reg_read(UC_ARM_REG_SP), machine.reg_read(UC_ARM_REG_LR)))
                if mutate:
                    machine.mem_write(output, pattern)
                for reg in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12):
                    machine.reg_write(reg, 0xDEADBEEF)
                machine.reg_write(UC_ARM_REG_R0, return_bits)
                machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
            return dependency

        for index, regions in enumerate((reference_regions, rebuilt_regions)):
            machine = Uc(UC_ARCH_ARM, UC_MODE_ARM)
            machine.mem_map(0xD7000, 0x2000)
            for name, address, *_ in FUNCTIONS:
                machine.mem_write(address, regions[name])
            for base in (buf, stack, stop):
                machine.mem_map(base, 4096)
            machine.hook_add(UC_HOOK_CODE, hook(index))
            machines.append(machine)

        rng = random.Random(70)
        counts = {}
        for kind, (name, address, *_rest) in enumerate(FUNCTIONS):
            for case in range(1024):
                initial = rng.randbytes(128)
                displacement = 16 + (case % 8 if kind == 1 else 4 * (case % 4))
                output = 0 if case % 17 == 0 else buf + displacement
                argument = [0, output, 0xFFFFFFFF, buf + 512,
                            rng.getrandbits(32)][case % 5]
                values = [rng.getrandbits(32) for _ in registers]
                values[0:2] = [output, argument]
                return_bits = [0, 1, 0xFFFFFFFF, 0x80000000,
                               rng.getrandbits(32)][case % 5]
                mutate = output != 0 and case % 2 == 1
                pattern = rng.randbytes(16) if kind == 1 else (
                    [0, buf + 1024, 0xFFFFFFFF, output, rng.getrandbits(32)][case % 5]
                ).to_bytes(4, "little")
                expected = bytearray(initial)
                if mutate:
                    expected[displacement:displacement + len(pattern)] = pattern
                expected_result = output if kind == 1 else return_bits
                outcomes = []
                for index, machine in enumerate(machines):
                    events[index].clear()
                    machine.mem_write(buf, initial)
                    machine.mem_write(stack, b"\xA5" * 4096)
                    for reg, value in zip(registers, values):
                        machine.reg_write(reg, value)
                    machine.reg_write(UC_ARM_REG_SP, sp)
                    machine.reg_write(UC_ARM_REG_LR, stop)
                    machine.emu_start(address, stop, count=32)
                    require(machine.reg_read(UC_ARM_REG_PC) == stop, "did not return")
                    require(machine.reg_read(UC_ARM_REG_R0) == expected_result, "return bits")
                    require(machine.reg_read(UC_ARM_REG_SP) == sp, "stack restoration")
                    require([machine.reg_read(reg) for reg in registers[4:12]] == values[4:12],
                            "callee-saved registers")
                    require(events[index] == [(kind, output, argument if kind >= 2 else None,
                        sp - 8 if kind == 1 else sp, address + 12 if kind == 1 else stop)],
                        "dependency identity/arguments/call count/SP/LR")
                    memory = bytes(machine.mem_read(buf, 128))
                    require(memory == bytes(expected), "output mutation/canaries")
                    low_size = 2040 if kind == 1 else 2048
                    require(bytes(machine.mem_read(stack, low_size)) == b"\xA5" * low_size
                        and bytes(machine.mem_read(sp, 2048)) == b"\xA5" * 2048,
                        "stack write bounds")
                    outcomes.append(([machine.reg_read(reg) for reg in registers],
                                     memory, bytes(machine.mem_read(stack, 4096))))
                require(outcomes[0] == outcomes[1], "original/source ARM differential")
            counts[name] = 1024
            print(f"PASS: {name}: 1024 original/source ARM cases", file=sys.stderr)

        versions = {tool: subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
                    for tool in ("g++", "clang++", "ld.lld")}
        versions.update({name: importlib.metadata.version(name) for name in ("unicorn", "pyelftools")})
        versions["python"] = platform.python_version()
        require(sha(args.reference) == REFERENCE_SHA256, "reference changed")
        print(json.dumps({
            "reference_sha256": REFERENCE_SHA256,
            "toolchain": versions,
            "source_hashes": {str(path.relative_to(root)): sha(path)
                              for path in (source, header, test, Path(__file__).resolve())},
            "arm_flags": common + arm_flags,
            "candidate_arm_object_sha256": sha(tmp / "arm.o"),
            "candidate_placed_elf_sha256": sha(tmp / "placed.elf"),
            "checks": checks,
            "assembly_comparisons": comparisons,
            "original_vs_rebuilt_arm_cases": counts,
            "matching_scope": "Full four A32 wrapper symbol regions after analysis-only placement and dependency resolution; no byte masking or inline assembly.",
            "oracle_scope": "Only the original and newly compiled wrappers; dependencies hooked. Compares call identity/arguments, return bits, output memory, r0-r12, restored SP, preserved r4-r11 and stack bounds. Real billing, result type/layout and JNI remain UNVERIFIED."
        }, indent=2))


if __name__ == "__main__":
    main()
