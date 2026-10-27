#!/usr/bin/env python3
"""Offline task64 verification. No downloads or original executable exports.

The fixed-address link is a comparison probe, NOT a deployable billing module:
its absolute dependency symbols stand for unresolved original callees. Only the
four wrapper bodies are compared. The optional research oracle reads each tiny
wrapper directly into isolated emulator memory; it never executes billing code.
Production and ordinary host tests have no reference-binary dependency.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import tempfile

REFERENCE_HASH = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / "reconstruction/recovered/libNOVA3_neon/block_000d60d0.cpp"
HEADER = SOURCE.parent / "block_000d60d0/billing_forwarders.h"
TEST = ROOT / "reconstruction/tests/recovered/libNOVA3_neon/block_000d60d0_test.cpp"
COMMON = ["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
ARM_FLAGS = ["--target=armv7a-linux-androideabi", "-marm", "-O2",
             "-fomit-frame-pointer", "-fno-pic", "-ffunction-sections",
             "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables",
             "-fno-asynchronous-unwind-tables"]
PREFIX = "_ZN5nova39recovered14block_000d60d0"
FUNCTIONS = [
    (0xD60D0, 12, "native_init", PREFIX + "11native_initEPvS2_S2_",
     "Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeInit",
     "nova3_billing_init_dependency", 0xD58C0,
     "_ZN12InAppBilling4initEP7_jclassP8_jobject"),
    (0xD6BC8, 4, "restore_transactions", PREFIX + "20restore_transactionsEv",
     "InAppBilling_RestoreTransactions", "nova3_billing_restore_dependency",
     0xD6A98, "_ZN12InAppBilling19restoreTransactionsEv"),
    (0xD6E30, 4, "send_notify_confirmation",
     PREFIX + "24send_notify_confirmationEPKc",
     "InAppBilling_SendNotifyConfirmation", "nova3_billing_notify_dependency",
     0xD6CDC, "_ZN12InAppBilling22sendNotifyConfirmationEPKc"),
    (0xD6F88, 4, "get_item_list", PREFIX + "13get_item_listEPKc",
     "InAppBilling_GetItemList", "nova3_billing_item_list_dependency",
     0xD6E34, "_ZN12InAppBilling11getItemListEPKc"),
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_va(elf, address, size):
    for segment in elf.iter_segments():
        offset = address - segment["p_vaddr"]
        if (segment["p_type"] == "PT_LOAD" and offset >= 0
                and offset + size <= segment["p_filesz"]):
            elf.stream.seek(segment["p_offset"] + offset)
            data = elf.stream.read(size)
            require(len(data) == size, "Truncated ELF segment")
            return data
    raise RuntimeError(f"Unmapped function address {address:#x}")


def selected_symbols(elf, names):
    table = elf.get_section_by_name(".symtab")
    require(table is not None, "Missing symbol table")
    found = {}
    for symbol in table.iter_symbols():
        if symbol.name in names:
            require(symbol.name not in found, "Ambiguous symbol")
            found[symbol.name] = (symbol["st_value"], symbol["st_size"],
                                  symbol["st_info"]["type"])
    require(set(found) == set(names), "Missing required symbols")
    return found


def run(command, checks, environment=None):
    import os
    result = subprocess.run([str(x) for x in command], cwd=ROOT,
                            capture_output=True, text=True,
                            env=None if environment is None else dict(os.environ, **environment))
    checks.append({"command": [str(x) for x in command],
                   "environment": environment or {}, "exit_code": result.returncode,
                   "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
    require(result.returncode == 0, f"Command failed: {command[0]}: {result.stderr}")
    return result.stdout.strip()


def oracle(reference_bodies, candidate_bodies):
    import unicorn
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                   UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                                   UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                                   UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC, UC_ARM_REG_CPSR)
    registers = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                 UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                 UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                 UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR]
    stop = 0x10000000
    stack_base = 0x20000000
    stack = bytes((i * 37 + 11) & 255 for i in range(4096))
    randomizer = random.Random(64)
    transcript = hashlib.sha256()
    counts = {}
    for function, original, candidate in zip(FUNCTIONS, reference_bodies, candidate_bodies):
        address, _, name, _, _, _, target, _ = function

        def make_machine(body):
            uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
            for page in {address & ~4095, target & ~4095, stop}:
                uc.mem_map(page, 4096)
            uc.mem_map(stack_base, 4096)
            uc.mem_write(address, body)
            calls = []
            expected_result = [0]

            def hook(machine, pc, size, data):
                if pc == stop:
                    machine.emu_stop()
                elif pc == target:
                    calls.append([machine.reg_read(r) for r in registers])
                    machine.reg_write(UC_ARM_REG_R0, expected_result[0])
                    # Dependencies may clobber caller-saved registers.
                    for reg in [UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12]:
                        machine.reg_write(reg, 0xBAD00000 + reg)
                    machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
                else:
                    require(address <= pc < address + len(body), "Unexpected oracle control flow")

            uc.hook_add(UC_HOOK_CODE, hook)
            return uc, calls, expected_result

        machines = [make_machine(original), make_machine(candidate)]
        for case in range(512):
            initial = [randomizer.getrandbits(32) for _ in registers]
            # Deliberately unmapped, null, aliased and high-bit arguments.
            if case < 8:
                initial[:3] = [0xF1234567 if (case >> i) & 1 else 0 for i in range(3)]
            elif case < 16:
                initial[1] = initial[2] = initial[0]
            initial[-2:] = [stack_base + 2048, stop]
            result = [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF][case % 5]
            traces = []
            for machine, calls, expected_result in machines:
                calls.clear()
                expected_result[0] = result
                machine.reg_write(UC_ARM_REG_CPSR, 0x10)  # ARM user mode
                for reg, value in zip(registers, initial):
                    machine.reg_write(reg, value)
                machine.mem_write(stack_base, stack)
                machine.emu_start(address, stop + 4, count=16)
                require(machine.reg_read(UC_ARM_REG_PC) == stop, "Oracle did not return")
                require(len(calls) == 1, "Not exactly one dependency call")
                expected_call = initial.copy()
                if name == "native_init":
                    expected_call[:2] = initial[1:3]
                require(calls[0] == expected_call, "Wrong forwarding register state")
                final = [machine.reg_read(r) for r in registers]
                require(final[0] == result, "Dependency result not forwarded")
                require(final[4:12] == initial[4:12], "Callee-saved registers changed")
                require(final[-2:] == initial[-2:], "Stack or return address changed")
                require(bytes(machine.mem_read(stack_base, 4096)) == stack, "Stack modified")
                traces.append([calls.copy(), final])
            require(traces[0] == traces[1], "Reference/candidate boundary trace differs")
            transcript.update(json.dumps([name, case, traces[0]], separators=(",", ":")).encode())
        counts[name] = 512
    return {"unicorn_version": unicorn.__version__, "cases": counts,
            "total_cases": sum(counts.values()), "transcript_sha256": transcript.hexdigest(),
            "scope": "Wrapper instructions only; every downstream billing call replaced by a register-recording test hook. No JNI or billing implementation tested."}


def verify(reference, include_oracle, sanitized):
    from elftools.elf.elffile import ELFFile
    import elftools
    import capstone
    require(digest(reference) == REFERENCE_HASH, "Reference SHA256 mismatch; refusing comparison")
    report = {"reference_sha256": REFERENCE_HASH, "checks": [], "functions": [],
              "source_hashes": {str(p.relative_to(ROOT)): digest(p)
                                for p in [SOURCE, HEADER, TEST, Path(__file__)]},
              "versions": {"pyelftools": elftools.__version__, "capstone": capstone.__version__},
              "full_library_matching": "NOT MATCHED", "full_native_ABI": "UNVERIFIED"}
    checks = report["checks"]
    for tool in ["g++", "clang++", "ld.lld"]:
        report["versions"][tool] = run([tool, "--version"], checks).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix="nova3-task64-") as temporary:
        work = Path(temporary)
        host = work / "host-test"
        run(["g++", *COMMON, "-O2", "-DNDEBUG", SOURCE, TEST, "-o", host], checks)
        run([host], checks)
        run(["g++", *COMMON, "-O2", "-c", SOURCE, "-o", work / "host.o"], checks)
        if sanitized:
            sanitized_test = work / "sanitized-test"
            run(["clang++", *COMMON, "-O1", "-g", "-fsanitize=address,undefined",
                 "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", SOURCE, TEST,
                 "-o", sanitized_test], checks)
            run([sanitized_test], checks, {"ASAN_OPTIONS": "detect_leaks=0"})
        obj = work / "candidate.o"
        run(["clang++", *COMMON, *ARM_FLAGS, "-c", SOURCE, "-o", obj], checks)
        # The linker, not a byte-normalizing script, resolves branch relocations.
        # Absolute symbols are comparison-only; no original callee is linked.
        script = work / "probe.ld"
        # Use explicit input section syntax, avoiding accidental orphan placement.
        sections = "\n".join(f".probe{i} {a:#x} : {{ KEEP(*(.text.{m})) }}"
                             for i, (a, _, _, m, _, _, _, _) in enumerate(FUNCTIONS))
        script.write_text("SECTIONS {\n" + sections + "\n}\n")
        linked = work / "probe.elf"
        run(["ld.lld", "-m", "armelf_linux_eabi", "-T", script, "-e", FUNCTIONS[0][3],
             *[f"--defsym={dependency}={target:#x}" for _, _, _, _, _, dependency, target, _ in FUNCTIONS],
             obj, "-o", linked], checks)
        report["arm_candidate_object_sha256"] = digest(obj)
        report["arm_link_probe_sha256"] = digest(linked)
        original_bodies, candidate_bodies = [], []
        with reference.open("rb") as original_file, linked.open("rb") as candidate_file:
            original_elf, candidate_elf = ELFFile(original_file), ELFFile(candidate_file)
            require(original_elf["e_machine"] == "EM_ARM" and original_elf.little_endian,
                    "Unexpected original architecture")
            names = {name for f in FUNCTIONS for name in [f[4], f[7]]}
            original_symbols = selected_symbols(original_elf, names)
            candidate_symbols = selected_symbols(candidate_elf, {f[3] for f in FUNCTIONS})
            decoder = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
            decoder.detail = True
            for address, size, name, mangled, symbol, dependency, target, target_symbol in FUNCTIONS:
                require(original_symbols[symbol] == (address, size, "STT_FUNC"), "Reference boundary mismatch")
                require(original_symbols[target_symbol][0] == target, "Reference dependency mismatch")
                require(candidate_symbols[mangled] == (address, size, "STT_FUNC"), "Candidate boundary mismatch")
                original = read_va(original_elf, address, size)
                candidate = read_va(candidate_elf, address, size)
                require(original == candidate, f"NOT MATCHED: {name}")
                instructions = list(decoder.disasm(original, address))
                expected_count = 3 if name == "native_init" else 1
                require(len(instructions) == expected_count, "Unexpected instruction count")
                last = instructions[-1]
                require(last.mnemonic == "b" and last.operands[0].imm == target, "Tail target mismatch")
                if name == "native_init":
                    transfers = [[i.mnemonic, i.op_str] for i in instructions[:-1]]
                    require(transfers == [["mov", "r0, r1"], ["mov", "r1, r2"]], "Argument transfer mismatch")
                original_bodies.append(original)
                candidate_bodies.append(candidate)
                report["functions"].append({"address": f"0x{address:08x}", "size_bytes": size,
                    "symbol": symbol, "source_entry": name, "dependency": dependency,
                    "dependency_address": f"0x{target:08x}", "dependency_symbol": target_symbol,
                    "reference_region_sha256": hashlib.sha256(original).hexdigest(),
                    "candidate_region_sha256": hashlib.sha256(candidate).hexdigest(),
                    "compared_bytes": size, "instruction_count": len(instructions),
                    "matching": "EXACT FUNCTION-BODY MATCH IN FIXED-ADDRESS LINK PROBE",
                    "scope": "All bytes compared without masking or normalization; absolute dependency symbols resolved by LLD. Export names, return types, downstream callees and full-library integration excluded."})
        if include_oracle:
            report["oracle"] = oracle(original_bodies, candidate_bodies)
    require(digest(reference) == REFERENCE_HASH, "Reference changed during comparison")
    report["reference_sha256_verified_after"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument("--sanitizers", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.reference, args.oracle, args.sanitizers)
    except (RuntimeError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized)
    print(json.dumps({"function_body_matches": len(report["functions"]),
                      "compared_bytes": sum(f["compared_bytes"] for f in report["functions"]),
                      "oracle_cases": report.get("oracle", {}).get("total_cases", 0),
                      "full_library_matching": report["full_library_matching"],
                      "full_native_ABI": report["full_native_ABI"]}))


if __name__ == "__main__":
    main()
