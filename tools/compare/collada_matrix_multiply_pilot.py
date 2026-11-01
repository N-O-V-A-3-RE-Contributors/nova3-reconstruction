#!/usr/bin/env python3
"""Re-measure the recovered glitch::core::operator*<float, collada::SMatrix>.

Reference: Premium ARMv7 engine ELF,
`_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_`,
ELF VA 0x006e7e60, 564 bytes, A32, STT_FUNC.

  STRUCTURE  decode the reference window and re-derive from the bytes + .symtab
             what the recovered source claims, rather than trusting the source
             comment:
               * the result is returned through sret (r0 is copied to a
                 callee-saved register and the object is written through it);
               * the only `bl` targets `CMatrix4<float>::CMatrix4(eConstructor)`
                 with this = the sret pointer and r1 = 0;
               * the CMatrix4 operand is loaded at exactly twelve byte offsets and
                 the SMatrix operand at exactly twelve, and neither load touches
                 the fourth float of a row (byte 0x0c/0x1c/0x2c/0x3c);
               * the result is written at all sixteen float slots in ascending
                 byte order, the three fourth-column stores of rows 0..2 come from
                 one zero constant, and the (3,3) store comes from a
                 `vmov.f32 #1.0`;
               * twelve vmul, twenty-four vmla, three vadd -- i.e. a fully
                 unrolled 3x3 dot product per element plus the three-term
                 translation row with its extra addend.

  WINDOW     compile reconstruction/recovered/libNOVA3_neon/
             collada_matrix_multiply.cpp with NDK r8e GCC 4.4.3 for -O2/-O3/-Os
             at -fvisibility=hidden (and default visibility as a negative
             control), then diff the 0x6e7e60 window with
             tools/compare/window_diff.py.

The report carries `source_matching_verified=False`: a relocation-masked window
agreement is not a byte match, not compiler identification and not a whole-.so
SHA-256 match.  No reference instruction bytes or disassembly text are emitted.

Example:
    .venv/bin/python tools/compare/collada_matrix_multiply_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
for extra in (SCRIPTS, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from window_diff import diff_window  # noqa: E402

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

TARGET = ("_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_",
          0x006E7E60, 564)

CTOR = "_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE"

# Byte offsets the two operands are loaded from, in the reference.
CMATRIX_LOAD_OFFSETS = sorted({0x00, 0x10, 0x20, 0x04, 0x14, 0x24,
                               0x08, 0x18, 0x28, 0x30, 0x34, 0x38})
SMATRIX_LOAD_OFFSETS = sorted({0x00, 0x04, 0x08, 0x10, 0x14, 0x18,
                               0x20, 0x24, 0x28, 0x30, 0x34, 0x38})
SKIPPED_LANES = {0x0C, 0x1C, 0x2C, 0x3C}

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/collada_matrix_multiply.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                      "/home/jason/.local/opt/android-ndk-r8e"))

BASE_FLAGS = [
    "-c", "-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16",
    "-fno-exceptions", "-fno-rtti", "-fno-pic",
]
CONFIGS = [
    ("-O2", "-fvisibility=hidden"),
    ("-O3", "-fvisibility=hidden"),
    ("-Os", "-fvisibility=hidden"),
    ("-O2", "-fvisibility=default"),
]


def gxx(ndk: Path) -> Path:
    p = (ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
         "arm-linux-androideabi-g++")
    if not p.exists():
        raise SystemExit(f"FAIL: NDK r8e g++ not found at {p}")
    return p


def symbol_bytes(obj: Path, wanted: str) -> tuple[bytes, int]:
    with open(obj, "rb") as fh:
        elf = ELFFile(fh)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            raise SystemExit(f"FAIL: {obj} has no .symtab")
        for sym in symtab.iter_symbols():
            if sym.name == wanted and sym["st_info"]["type"] == "STT_FUNC":
                sec = elf.get_section(sym["st_shndx"])
                off = sym["st_value"] - sec["sh_addr"]
                return sec.data()[off:off + sym["st_size"]], sym["st_size"]
    raise SystemExit(f"FAIL: {wanted} missing from {obj}")


def load_reference(reference: Path):
    blob = reference.read_bytes()
    if hashlib.sha256(blob).hexdigest() != REFERENCE_SHA256:
        raise SystemExit("FAIL: reference SHA-256 mismatch")
    bounds: dict[str, tuple[int, int]] = {}
    with open(reference, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        symtab = elf.get_section_by_name(".symtab")
        if text is None or symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        hits = []
        for sym in symtab.iter_symbols():
            if sym["st_value"] and sym["st_size"]:
                bounds.setdefault(sym.name, (sym["st_value"], sym["st_size"]))
            if sym.name == TARGET[0]:
                hits.append(sym)
        if len(hits) != 1:
            raise SystemExit("FAIL: operator* symbol is not unique")
        sym = hits[0]
        if sym["st_value"] != TARGET[1] or sym["st_size"] != TARGET[2]:
            raise SystemExit(f"FAIL: operator* bounds changed "
                             f"(0x{sym['st_value']:08x}/{sym['st_size']})")
        if sym["st_info"]["type"] != "STT_FUNC" or sym["st_value"] & 1:
            raise SystemExit("FAIL: operator* is not an A32 STT_FUNC")
        base = text.header.sh_addr
        data = text.data()
        window = data[TARGET[1] - base:TARGET[1] - base + TARGET[2]]
    return blob, window, bounds


def enclosing(bounds: dict[str, tuple[int, int]], va: int):
    best = None
    for name, (addr, size) in bounds.items():
        if size and addr <= va < addr + size:
            if best is None or size > best[1]:
                best = (name, size)
    return best[0] if best else None


def structure_report(window: bytes, bounds: dict[str, tuple[int, int]]) -> dict:
    base = TARGET[1]
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(window, base))
    facts: dict[str, object] = {"instructions_decoded": len(insns)}
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        facts[name] = bool(ok)
        if not ok:
            failures.append(name)

    def reg(insn, index):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_REG:
            return insn.reg_name(insn.operands[index].reg)
        return None

    def mem(insn, index):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_MEM:
            return insn.operands[index].mem
        return None

    def imm(insn, index=1):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_IMM:
            return insn.operands[index].imm
        return None

    calls = []
    call_index = {}
    for i, insn in enumerate(insns):
        if insn.mnemonic in ("bl", "blx"):
            target = imm(insn, 0)
            name = enclosing(bounds, target) if target is not None else None
            calls.append(name)
            call_index[name] = i
    facts["call_targets"] = calls
    check("exactly_one_call", len(calls) == 1)
    check("call_is_the_matrix_constructor", calls == [CTOR])

    # sret: r0 is moved into a callee-saved register and the object is written
    # through that register.
    sret_reg = None
    for insn in insns[:8]:
        if insn.mnemonic == "mov" and reg(insn, 0) and reg(insn, 1) == "r0":
            sret_reg = reg(insn, 0)
    facts["sret_register"] = sret_reg
    check("sret_pointer_is_saved", sret_reg is not None)

    ctor_i = call_index.get(CTOR)
    ctor_arg = None
    if ctor_i is not None:
        for insn in insns[max(0, ctor_i - 4):ctor_i]:
            if insn.mnemonic == "mov" and reg(insn, 0) == "r1":
                ctor_arg = imm(insn)
    facts["constructor_argument"] = ctor_arg
    check("constructor_argument_is_0", ctor_arg == 0)

    arg_regs = {}
    for insn in insns[:8]:
        if insn.mnemonic == "mov" and reg(insn, 1) in ("r1", "r2"):
            arg_regs[reg(insn, 1)] = reg(insn, 0)
    facts["operand_registers"] = arg_regs
    check("both_operands_saved", set(arg_regs) == {"r1", "r2"})
    lhs_reg = arg_regs.get("r1")
    rhs_reg = arg_regs.get("r2")

    lhs_offsets = []
    rhs_offsets = []
    for insn in insns:
        if insn.mnemonic != "vldr":
            continue
        m = mem(insn, 1)
        if m is None:
            continue
        base_reg = insn.reg_name(m.base)
        if lhs_reg and base_reg == lhs_reg:
            lhs_offsets.append(m.disp)
        elif rhs_reg and base_reg == rhs_reg:
            rhs_offsets.append(m.disp)
    lhs_offsets.sort()
    rhs_offsets.sort()
    facts["cmatrix_load_count"] = len(lhs_offsets)
    facts["smatrix_load_count"] = len(rhs_offsets)
    facts["cmatrix_load_offsets"] = [hex(x) for x in sorted(set(lhs_offsets))]
    facts["smatrix_load_offsets"] = [hex(x) for x in sorted(set(rhs_offsets))]
    check("cmatrix_touches_only_the_observed_twelve_offsets",
          sorted(set(lhs_offsets)) == CMATRIX_LOAD_OFFSETS)
    check("smatrix_touches_only_the_observed_twelve_offsets",
          sorted(set(rhs_offsets)) == SMATRIX_LOAD_OFFSETS)
    check("no_load_of_a_skipped_fourth_lane", not (
        (set(lhs_offsets) | set(rhs_offsets)) & SKIPPED_LANES))
    # Every result element re-loads its operands: 9 linear elements x 3 loads of
    # each operand = 27 + 27, and the 3 translation-row elements load 4 from the
    # CMatrix4 (three columns plus its own translation) and 3 from the SMatrix.
    check("cmatrix_is_reloaded_per_result_element", len(lhs_offsets) == 39)
    check("smatrix_is_reloaded_per_result_element", len(rhs_offsets) == 36)

    stores = [(i, mem(i, 1)) for i in insns if i.mnemonic == "vstr"
              and sret_reg and mem(i, 1) is not None
              and i.reg_name(mem(i, 1).base) == sret_reg]
    offsets = [m.disp for _, m in stores]
    facts["result_store_offsets"] = [hex(x) for x in offsets]
    check("all_sixteen_result_slots_written",
          len(offsets) == 16 and sorted(offsets) == list(range(0, 0x40, 4)))

    # The three 0x0c/0x1c/0x2c stores and the 0x3c store use constants: those
    # instructions read neither operand register (no `vldr` of the value just
    # before them), which is what "literal 0.0 / 1.0" means in the source.
    const_stores = [i for i, m in stores if m.disp in (0x0C, 0x1C, 0x2C, 0x3C)]
    facts["constant_store_sites"] = len(const_stores)
    check("four_constant_stores", len(const_stores) == 4)
    check("result_33_is_one", any(i.mnemonic in ("vmov", "vmov.f32")
                                  and i.op_str.replace(" ", "").startswith("s")
                                  and "#1.0" in i.op_str.replace(" ", "")
                                  for i in insns))
    zero_literal_loads = [i for i in insns if i.mnemonic == "vldr"
                          and mem(i, 1) is not None and i.reg_name(mem(i, 1).base) == "pc"]
    facts["pc_relative_literal_loads"] = len(zero_literal_loads)
    check("zero_constant_comes_from_the_literal_pool", len(zero_literal_loads) == 1)

    counts = {mn: sum(1 for i in insns if i.mnemonic == mn)
              for mn in ("vmul.f32", "vmla.f32", "vadd.f32", "vsub.f32")}
    facts["vfp_counts"] = counts
    check("twelve_multiplies", counts["vmul.f32"] == 12)
    check("twenty_four_multiply_accumulates", counts["vmla.f32"] == 24)
    check("three_translation_adds", counts["vadd.f32"] == 3)
    check("no_subtracts", counts["vsub.f32"] == 0)

    facts["failures"] = failures
    facts["structure_agrees_with_source"] = not failures
    return facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--ndk", default=str(DEFAULT_NDK_R8E))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    ref = Path(args.reference)
    blob, window, bounds = load_reference(ref)
    compiler = gxx(Path(args.ndk))
    structure = structure_report(window, bounds)

    rows = []
    with tempfile.TemporaryDirectory(prefix="collada-matrix-pilot-") as tmp:
        for cfg in CONFIGS:
            tag = "_".join(c.strip("-").replace("=", "") for c in cfg)
            obj = Path(tmp) / f"candidate_{tag}.o"
            cmd = [str(compiler), *BASE_FLAGS, *cfg, f"-I{INCLUDE}",
                   str(SOURCE), "-o", str(obj)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            row: dict = {"config": " ".join(cfg), "compile_rc": proc.returncode}
            if proc.returncode != 0:
                row["error"] = (proc.stderr.strip().splitlines() or ["failed"])[-1]
                rows.append(row)
                continue
            data, cand_size = symbol_bytes(obj, TARGET[0])
            diff = diff_window(blob, obj.read_bytes(), address=TARGET[1],
                               symbol=TARGET[0],
                               expected_reference_sha256=REFERENCE_SHA256)
            row.update({
                "status": diff["status"],
                "reference_size": diff["reference_size"],
                "candidate_size": diff["candidate_size"],
                "words_compared": diff["words_compared"],
                "counts": diff["counts"],
                "code_window_equal": diff["code_window_equal"],
                "instruction_stream_equal": diff["instruction_stream_equal"],
            })
            rows.append(row)

    matched = any(r.get("code_window_equal") for r in rows)
    report = {
        "tool": "collada_matrix_multiply_pilot",
        "reference": str(ref),
        "reference_sha256": REFERENCE_SHA256,
        "symbol": TARGET[0],
        "va": f"0x{TARGET[1]:08x}",
        "reference_size": TARGET[2],
        "source": str(SOURCE),
        "compiler": str(compiler),
        "flags": BASE_FLAGS,
        "configs": [" ".join(c) for c in CONFIGS],
        "structure": structure,
        "rows": rows,
        "byte_equal_in_any_config": matched,
        "scope": ("one 564-byte window; relocation-masked agreement is not a "
                  "byte match, not compiler identification and not a "
                  "whole-.so SHA-256 match"),
        "source_matching_verified": False,
    }

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0 if structure["structure_agrees_with_source"] else 1


if __name__ == "__main__":
    sys.exit(main())
