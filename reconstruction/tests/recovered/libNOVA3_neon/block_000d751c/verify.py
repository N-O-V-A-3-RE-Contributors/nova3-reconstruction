#!/usr/bin/env python3
"""Offline task66 proof; keep generated objects/reports in a disposable directory.

Requires existing clang++, ld.lld, pyelftools, Capstone and Unicorn. No downloads.
Reference instructions are read into memory only, never printed or saved. The
candidate link binds *undefined* dependency symbols to observed addresses solely
to compare branch relocation results. It is not a runnable billing application.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import struct
import subprocess

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_ARM_REG_CPSR,
)

REFERENCE_HASH = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
FUNCTIONS = (
    (0xD751C, "_Z30InAppBilling_ShowPendingDialogv", 0xD737C,
     "_ZN12InAppBilling10showDialogEv"),
    (0xD76CC, "InAppBilling_BuyItem", 0xD7520,
     "_ZN12InAppBilling7buyItemEPKcyS1_"),
    (0xD7BF0, "InAppBilling_GetLastResults", 0xD79FC,
     "_ZN12InAppBilling14getLastResultsEPc"),
    (0xD7E58, "InAppBilling_FillTypeArray", 0xD7BF4,
     "_ZN12InAppBilling13fillTypeArrayEPPPc"),
)
REGISTERS = (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_CPSR,
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def symbols(elf):
    """Decode ELF32 symbol metadata without emitting binary excerpts."""
    section = elf.get_section_by_name(".symtab")
    require(section is not None, "missing symbol table")
    names = elf.get_section(section["sh_link"]).data()
    data = section.data()
    result = {}
    arm_starts = set()
    for offset in range(0, len(data), section["sh_entsize"]):
        name_offset, value, size, info, _, _ = struct.unpack_from("<IIIBBH", data, offset)
        name = names[name_offset:names.find(b"\0", name_offset)].decode()
        if name == "$a":
            arm_starts.add(value)
        if info & 15 == 2:
            result[name] = (value, size)
    return result, arm_starts


def at(elf, address, size):
    for section in elf.iter_sections():
        if section["sh_flags"] & 2 and section["sh_type"] != "SHT_NOBITS":
            begin = section["sh_addr"]
            if begin <= address and address + size <= begin + section["sh_size"]:
                return section.data()[address - begin:address - begin + size]
    raise RuntimeError("unmapped ELF virtual address")


def transport_probe(code, address, destination, cases, ignore_r1=False):
    """Execute just the forwarding instruction, stopping before its dependency.

    Arbitrary bit patterns deliberately test raw register transport, not validity
    of inputs to the unresolved billing methods. Pointer targets stay unmapped.
    """
    emulator = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    emulator.mem_map(address & ~4095, 4096)
    emulator.mem_write(address, code)
    stack = 0x2001000
    emulator.mem_map(stack - 4096, 8192)
    observations = []
    for values, payload in cases:
        emulator.reg_write(UC_ARM_REG_CPSR, values[-1])
        for register, value in zip(REGISTERS[:-1], values[:-1]):
            emulator.reg_write(register, value)
        emulator.mem_write(stack, payload)
        emulator.emu_start(address, destination, count=16)
        observed = tuple(emulator.reg_read(register) for register in REGISTERS)
        require(emulator.reg_read(UC_ARM_REG_PC) == destination,
                "wrapper failed to reach its declared dependency")
        checked = tuple(index for index in range(len(REGISTERS))
                        if not (ignore_r1 and index == 1))
        require(all(observed[index] == values[index] for index in checked),
                "wrapper changed argument/preserved registers, flags, SP or LR")
        require(bytes(emulator.mem_read(stack, len(payload))) == payload,
                "wrapper changed the caller stack")
        observations.append(tuple(observed[index] for index in checked))
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--require-all-matched", action="store_true",
                        help="fail after reporting if any wrapper differs in machine code")
    args = parser.parse_args()
    require(digest(args.reference) == REFERENCE_HASH, "reference SHA-256 mismatch")
    root = Path(__file__).resolve().parents[5]
    build = args.build_dir.resolve()
    require(build.is_relative_to(Path("/tmp")), "use a disposable build directory under /tmp")
    build.mkdir(parents=True, exist_ok=True)
    source = Path("reconstruction/recovered/libNOVA3_neon/block_000d751c.cpp")
    test = Path("reconstruction/tests/recovered/libNOVA3_neon/block_000d751c_test.cpp")
    header = source.with_suffix("") / "billing_forwarders.hpp"
    commands = []

    def run(command, env=None):
        commands.append(command)
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, env=env)
        require(result.returncode == 0,
                "command failed: " + repr(command) + "\n" + result.stdout + result.stderr)
        return result.stdout.strip()

    compiler = run(["clang++", "--version"]).splitlines()[0]
    linker = run(["ld.lld", "--version"])
    common = ["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
    host = build / "forwarders-test"
    run(["clang++", *common, "-O2", str(source), str(test), "-o", str(host)])
    host_output = run([str(host)])
    sanitized = build / "forwarders-sanitized"
    run(["clang++", *common, "-O1", "-g", "-fsanitize=address,undefined",
         "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
         str(source), str(test), "-o", str(sanitized)])
    # LeakSanitizer needs process tracing unavailable in the worker sandbox.
    # Keep address/undefined-behavior instrumentation enabled without changing
    # host security settings. Leak detection is outside this test's scope.
    sanitizer_environment = {"ASAN_OPTIONS": "detect_leaks=0"}
    sanitizer_output = run([str(sanitized)], env={**os.environ, **sanitizer_environment})

    arm_object = build / "forwarders-arm.o"
    arm_flags = ["--target=armv7a-linux-gnueabi", *common, "-O2", "-marm",
                 "-march=armv7-a", "-mfloat-abi=softfp", "-fno-pic", "-fno-pie",
                 "-ffunction-sections", "-fno-exceptions", "-fno-rtti",
                 "-fno-unwind-tables", "-fno-asynchronous-unwind-tables"]
    run(["clang++", *arm_flags, "-c", str(source), "-o", str(arm_object)])
    # This script contains only symbol addresses, never reference instructions.
    script = ["SECTIONS {"]
    for index, (address, name, _, _) in enumerate(FUNCTIONS):
        script.append(f" .wrapper_{index} 0x{address:x} : {{ *(.text.{name}) }}")
    script.append(" /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }")
    script.append("}")
    for _, _, target, name in FUNCTIONS:
        script.append(f"{name} = 0x{target:x};")
    linker_script = build / "comparison-only.ld"
    linker_script.write_text("\n".join(script) + "\n")
    linked = build / "comparison-only.elf"
    run(["ld.lld", "-m", "armelf_linux_eabi", "--build-id=none", "--no-undefined",
         "-e", FUNCTIONS[0][1], "-T", str(linker_script), str(arm_object),
         "-o", str(linked)])

    rng = random.Random(66)
    cases = []
    for index in range(512):
        values = [rng.getrandbits(32) for _ in range(13)]
        # Cover null, extreme and aliasing register patterns as well as random.
        if index < 4:
            values = [index * 0x55555555] * 13
        values += [0x2001000, 0x3000000, 0x10 | ((index % 16) << 28)]
        payload = rng.randbytes(64)  # Includes BuyItem's third stack argument.
        cases.append((tuple(values), payload))
    results = []
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    with args.reference.open("rb") as original, linked.open("rb") as candidate:
        reference, recovered = ELFFile(original), ELFFile(candidate)
        require(reference.elfclass == 32 and reference.little_endian and
                reference["e_machine"] == "EM_ARM", "unexpected reference architecture")
        original_symbols, arm_starts = symbols(reference)
        candidate_symbols, _ = symbols(recovered)
        for address, name, destination, target_name in FUNCTIONS:
            require(original_symbols[name] == (address, 4), "reference boundary differs")
            require(original_symbols[target_name][0] == destination, "reference target differs")
            require(address in arm_starts, "ARM mapping symbol missing")
            candidate_address, candidate_size = candidate_symbols[name]
            require(candidate_address == address and 0 < candidate_size <= 64,
                    "unexpected candidate boundary")
            expected = at(reference, address, 4)
            actual = at(recovered, address, candidate_size)
            decoded = list(decoder.disasm(expected, address))
            require(len(decoded) == 1 and decoded[0].mnemonic == "b" and
                    int(decoded[0].op_str.lstrip("#"), 0) == destination,
                    "reference is not the expected unconditional ARM tail branch")
            observations = transport_probe(expected, address, destination, cases)
            # AAPCS aligns BuyItem's 64-bit argument to r2/r3, leaving r1
            # unspecified. Clang emits a redundant stack-argument load/store
            # using r1. Accept that register difference for *typed boundary*
            # behaviour only; explicitly retain the failed byte comparison.
            buy_item = name == "InAppBilling_BuyItem"
            if buy_item:
                observations = [tuple(value for index, value in enumerate(observation)
                                      if index != 1) for observation in observations]
            candidate_observations = transport_probe(
                actual, address, destination, cases, ignore_r1=buy_item)
            require(observations == candidate_observations, "boundary transport differs")
            results.append(dict(address=f"0x{address:08x}", symbol=name,
                                destination=f"0x{destination:08x}", target_symbol=target_name,
                                reference_symbol_size=4, compiled_symbol_size=candidate_size,
                                comparison=("MATCHED: 4/4 bytes after explicit address binding"
                                            if expected == actual else "NOT MATCHED"),
                                reference_code_sha256=hashlib.sha256(expected).hexdigest(),
                                candidate_code_sha256=hashlib.sha256(actual).hexdigest(),
                                ignored_caller_saved_registers=["r1"] if buy_item else [],
                                differential_boundary_cases=len(cases)))
    require(digest(args.reference) == REFERENCE_HASH, "reference changed during verification")
    report = dict(
        task=66, reference_sha256=REFERENCE_HASH, reference_unchanged=True,
        compiler=compiler, linker=linker,
        packages={name: importlib.metadata.version(name)
                  for name in ("pyelftools", "capstone", "unicorn")},
        source_hashes={str(path): digest(root / path) for path in
                       (source, header, test, Path(__file__).resolve().relative_to(root))},
        arm_flags=arm_flags, commands=commands, host_test=host_output,
        sanitizer_test=sanitizer_output, sanitizer_environment=sanitizer_environment,
        functions=results,
        total_differential_cases=4 * len(cases),
        machine_code_matched_functions=sum(item["comparison"].startswith("MATCHED:")
                                          for item in results),
        scope="Four forwarding boundaries only; dependencies intercepted before execution.",
        limitations=["Original source return types UNVERIFIED; Result is r0 transport only.",
                     "Callee billing/JNI implementations NOT RECONSTRUCTED.",
                     "Comparison link binds target addresses and wrapper locations explicitly.",
                     "BuyItem NOT MATCHED: Clang adds a stack copy and changes unused r1.",
                     "BuyItem behavioural comparison requires normal writable AAPCS stack.",
                     "No original compiler identity, full ABI, billing or game equivalence claim."])
    (build / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if args.require_all_matched:
        require(report["machine_code_matched_functions"] == len(FUNCTIONS),
                "NOT MATCHED: at least one wrapper differs in machine code")


if __name__ == "__main__":
    main()
