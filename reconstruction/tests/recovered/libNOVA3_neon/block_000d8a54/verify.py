#!/usr/bin/env python3
"""Offline verification; never emits reference bytes or links reference code.

Run with the project's existing Python environment containing pyelftools and
Unicorn. Compiler/linker tools must already be installed. All build products
are temporary; stdout contains only aggregate evidence and hashes.
"""
import argparse
import ctypes as C
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import subprocess
import tempfile

EXPECTED = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
# wrapper, address, reconstructed dependency, original dependency, target
FUNCTIONS = [
    ("InAppBilling_GetItemTypeByID", 0xD8A54,
     "nova3_billing_get_item_type_by_id", "_ZN12InAppBilling15getItemTypeByIDEPKc", 0xD8810),
    ("InAppBilling_GetShopAttributeByName", 0xD8CAC,
     "nova3_billing_get_shop_attribute_by_name", "_ZN12InAppBilling22getShopAttributeByNameEPKc", 0xD8A68),
    ("InAppBilling_GetBillingAttByID", 0xD8F28,
     "nova3_billing_get_billing_att_by_id", "_ZN12InAppBilling17getBillingAttByIDEPKcS1_", 0xD8CC0),
    ("InAppBilling_GetAttByID", 0xD91A4,
     "nova3_billing_get_att_by_id", "_ZN12InAppBilling10getAttByIDEPKcS1_", 0xD8F3C),
]


def sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    if sha(args.reference) != EXPECTED:
        raise SystemExit("Reference SHA256 mismatch; refusing oracle")

    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1,
        UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
        UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
        UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12,
        UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

    root = Path(__file__).resolve().parents[5]
    source = root / "reconstruction/recovered/libNOVA3_neon/block_000d8a54.cpp"
    test = root / "reconstruction/tests/recovered/libNOVA3_neon/block_000d8a54_test.cpp"
    header = source.parent / "block_000d8a54/billing_forwarders.hpp"
    checks = []

    def run(command, **kwargs):
        result = subprocess.run([str(x) for x in command], cwd=root,
                                text=True, capture_output=True, timeout=60, **kwargs)
        checks.append({"command": [str(x) for x in command],
                       "exit_code": result.returncode,
                       "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        if result.returncode:
            raise RuntimeError(json.dumps(checks[-1]))
        return result.stdout.strip()

    common = ["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
    arm_flags = ["--target=armv7-none-eabi", "-march=armv7-a", "-marm",
                 "-mfloat-abi=soft", "-O2", "-fomit-frame-pointer",
                 "-fno-exceptions", "-fno-unwind-tables",
                 "-fno-asynchronous-unwind-tables", "-ffunction-sections"]
    with tempfile.TemporaryDirectory(prefix="nova3-task67-") as directory:
        tmp = Path(directory)
        run(["g++", *common, "-O2", "-c", source, "-o", tmp / "host.o"])
        run(["g++", *common, "-O2", source, test, "-o", tmp / "unit"])
        run([tmp / "unit"])
        run(["clang++", *common, "-O1", "-g", "-fsanitize=address,undefined",
             "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
             source, test, "-o", tmp / "sanitized"])
        # LeakSanitizer cannot run under the session's tracing restrictions.
        import os
        run([tmp / "sanitized"], env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        run(["g++", *common, "-O2", "-shared", "-fPIC", "-DNOVA3_ORACLE_LIBRARY",
             source, test, "-Wl,-z,defs", "-o", tmp / "host.so"])
        run(["clang++", *common, *arm_flags, "-c", source, "-o", tmp / "arm.o"])

        # Analysis-only placement resolves branch relocations to their observed
        # targets. Absolute dependency symbols contain no implementation/bytes.
        # This ELF is for comparison, not a runnable application or billing API.
        layout = "SECTIONS {\n"
        for name, address, _, _, _ in FUNCTIONS:
            layout += f" .text.{name} {address:#x} : {{ *(.text.{name}) }}\n"
        layout += " /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }\n}\n"
        (tmp / "placement.ld").write_text(layout)
        run(["ld.lld", "-T", tmp / "placement.ld", "--entry=" + FUNCTIONS[0][0],
             *[f"--defsym={dep}={target:#x}" for _, _, dep, _, target in FUNCTIONS],
             tmp / "arm.o", "-o", tmp / "placed.elf"])

        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        reference_regions = {}
        with args.reference.open("rb") as stream:
            elf = ELFFile(stream)
            assert elf["e_machine"] == "EM_ARM" and elf.elfclass == 32 and elf.little_endian
            symbols = elf.get_section_by_name(".symtab")
            for name, address, _, original_dep, target in FUNCTIONS:
                symbol, = symbols.get_symbol_by_name(name)
                assert symbol["st_value"] == address and symbol["st_size"] == 20
                assert symbol["st_info"]["type"] == "STT_FUNC" and address % 4 == 0
                dependency, = symbols.get_symbol_by_name(original_dep)
                assert dependency["st_value"] == target
                section = elf.get_section(symbol["st_shndx"])
                offset = address - section["sh_addr"]
                reference_regions[name] = section.data()[offset:offset + 20]
            segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
            ranges = []
            for segment in segments:
                low = segment["p_vaddr"] & ~4095
                high = (segment["p_vaddr"] + segment["p_memsz"] + 4095) & ~4095
                ranges.append((low, high))
            merged = []
            for low, high in sorted(ranges):
                if merged and low <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
                else:
                    merged.append((low, high))
            for low, high in merged:
                uc.mem_map(low, high - low)
            for segment in segments:
                uc.mem_write(segment["p_vaddr"], segment.data())

        comparisons = []
        with (tmp / "placed.elf").open("rb") as stream:
            elf = ELFFile(stream)
            for name, address, _, _, _ in FUNCTIONS:
                symbol, = elf.get_section_by_name(".symtab").get_symbol_by_name(name)
                section = elf.get_section(symbol["st_shndx"])
                offset = symbol["st_value"] - section["sh_addr"]
                rebuilt = section.data()[offset:offset + symbol["st_size"]]
                original = reference_regions[name]
                exact = rebuilt == original and symbol["st_value"] == address
                comparisons.append({"symbol": name, "address": hex(address),
                    "reference_size": len(original), "candidate_size": len(rebuilt),
                    "reference_region_sha256": hashlib.sha256(original).hexdigest(),
                    "candidate_region_sha256": hashlib.sha256(rebuilt).hexdigest(),
                    "exact_bytes_after_placement": exact})
                assert exact, "NOT MATCHED: " + name

        buf, stack, stop = 0x2000000, 0x3000000, 0x4000000
        uc.mem_map(buf, 4096)
        uc.mem_map(stack, 4096)
        uc.mem_map(stop, 4096)
        library = C.CDLL(str(tmp / "host.so"))
        callback_type = C.CFUNCTYPE(C.c_void_p, C.c_uint, C.c_void_p, C.c_void_p, C.c_void_p)
        library.nova3_test_set_callback.argtypes = [callback_type]
        library.nova3_test_set_callback.restype = None
        events = []
        source_events = []
        pattern = b""
        ignored_return = 0
        mutate = False

        @callback_type
        def source_hook(kind, output, first, second):
            source_events.append((kind, output or 0, first or 0, second or 0))
            if mutate:
                C.memmove(output, pattern, len(pattern))
            return ignored_return

        library.nova3_test_set_callback(source_hook)
        targets = {target: kind for kind, (_, _, _, _, target) in enumerate(FUNCTIONS)}

        def original_hook(machine, address, _size, _data):
            if address not in targets:
                return
            kind = targets[address]
            out = machine.reg_read(UC_ARM_REG_R0)
            first = machine.reg_read(UC_ARM_REG_R1)
            second = machine.reg_read(UC_ARM_REG_R2) if kind >= 2 else 0
            events.append((kind, out, first, second))
            if mutate:
                machine.mem_write(out, pattern)
            # Poison caller-saved argument/scratch registers.
            for reg in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12):
                machine.reg_write(reg, 0xDEADBEEF)
            machine.reg_write(UC_ARM_REG_R0, ignored_return)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        uc.hook_add(UC_HOOK_CODE, original_hook)
        rng = random.Random(67)
        preserved = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
        counts = {}
        for kind, (name, address, _, _, _) in enumerate(FUNCTIONS):
            function = getattr(library, name)
            function.argtypes = [C.c_void_p] * (2 if kind < 2 else 3)
            function.restype = C.c_void_p
            for case in range(512):
                initial = rng.randbytes(128)
                owned = C.create_string_buffer(initial, len(initial))
                displacement = 16 + case % 8
                null_output = case % 17 == 0
                output = 0 if null_output else buf + displacement
                source_output = 0 if null_output else C.addressof(owned) + displacement
                first = [0, buf + 512, 0xFFFFFFFF, output, rng.getrandbits(32)][case % 5]
                second = [0, first, output, buf + 1024, rng.getrandbits(32)][case % 5]
                source_first = source_output if first == output and output else first
                source_second = source_output if second == output and output else second
                mutate = not null_output and case % 2 == 1
                pattern = rng.randbytes(16)
                ignored_return = [0, 0xFFFFFFFF, 0x80000000, output, rng.getrandbits(32)][case % 5]
                uc.mem_write(buf, initial)
                uc.mem_write(stack, bytes([0xA5]) * 4096)
                sp = stack + 2048
                uc.reg_write(UC_ARM_REG_SP, sp)
                uc.reg_write(UC_ARM_REG_LR, stop)
                uc.reg_write(UC_ARM_REG_R0, output)
                uc.reg_write(UC_ARM_REG_R1, first)
                uc.reg_write(UC_ARM_REG_R2, second)
                sentinels = [rng.getrandbits(32) for _ in preserved]
                for reg, value in zip(preserved, sentinels):
                    uc.reg_write(reg, value)
                events.clear()
                source_events.clear()
                uc.emu_start(address, stop, count=32)
                assert uc.reg_read(UC_ARM_REG_PC) == stop
                assert uc.reg_read(UC_ARM_REG_R0) == output
                assert uc.reg_read(UC_ARM_REG_SP) == sp
                assert [uc.reg_read(r) for r in preserved] == sentinels
                assert bytes(uc.mem_read(stack, 2040)) == bytes([0xA5]) * 2040
                assert bytes(uc.mem_read(sp, 2048)) == bytes([0xA5]) * 2048
                assert events == [(kind, output, first, second if kind >= 2 else 0)]
                arguments = [source_output, source_first]
                if kind >= 2:
                    arguments.append(source_second)
                result = function(*arguments) or 0
                assert result == source_output
                assert source_events == [(kind, source_output, source_first,
                                          source_second if kind >= 2 else 0)]
                expected = bytearray(initial)
                if mutate:
                    expected[displacement:displacement + 16] = pattern
                assert owned.raw == bytes(uc.mem_read(buf, 128)) == bytes(expected)
            counts[name] = 512

        versions = {tool: subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
                    for tool in ("g++", "clang++", "ld.lld")}
        versions.update({name: importlib.metadata.version(name) for name in ("unicorn", "pyelftools")})
        import platform
        versions["python"] = platform.python_version()
        assert sha(args.reference) == EXPECTED, "reference changed during verification"
        result = {"reference_sha256": EXPECTED, "toolchain": versions,
                  "candidate_arm_object_sha256": sha(tmp / "arm.o"),
                  "candidate_placed_elf_sha256": sha(tmp / "placed.elf"),
                  "source_hashes": {str(p.relative_to(root)): sha(p)
                                    for p in (source, header, test, Path(__file__).resolve())},
                  "checks": checks, "assembly_comparisons": comparisons,
                  "differential_cases": counts,
                  "matching_scope": "Exact complete A32 wrapper symbol bytes after analysis-only address placement and dependency symbol resolution; original result type and real billing dependencies remain unreconstructed.",
                  "oracle_scope": "Original wrappers only, with dependency hooks; compares forwarding, output writes, return pointer, stack bounds and preserved registers. No JNI or actual billing behavior exercised."}
        # Temp paths are intentionally retained in commands for exact flags;
        # those build products cease to exist when this process returns.
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
