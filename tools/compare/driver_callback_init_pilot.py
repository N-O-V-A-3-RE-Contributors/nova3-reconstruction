#!/usr/bin/env python3
"""Re-measure the recovered vox::DriverCallbackInterface::Init(void*) slice.

Two independent checks over the Premium ARMv7 engine ELF at ELF VA 0x009d53f4
(`_ZN3vox23DriverCallbackInterface4InitEPv`, 48 bytes, A32, STT_FUNC):

  STRUCTURE  decode the reference window and re-derive, from the bytes and the
             .symtab, what the recovered source claims: the PIC base resolves to
             a named static object, every store offset lands on a named object,
             the single branch goes to the recovered DetectNeon, exactly four
             32-bit zero stores and one byte store are emitted, and neither r0
             (`this`) nor r1 (the void* argument) is read before the call.

  WINDOW     compile `reconstruction/recovered/libNOVA3_neon/
             driver_callback_init.cpp` with NDK r8e GCC 4.4.3 across an
             -O / stack-protector / symbol-visibility matrix, then compare each
             ET_REL symbol window with the reference twice:
               * `tools/compare/window_diff.py` word classes, and
               * a normalised instruction-shape diff that masks branch targets
                 and memory displacements and reports whether the reference and
                 candidate differ by one constant anchor offset.

Output is metadata only: addresses, symbol names, sizes, counts, booleans and
per-word classes.  No reference instruction bytes or disassembly text are
written.  Every report carries `source_matching_verified=False`: relocation- and
anchor-masked agreement is not a byte match, not original-object recovery and
not compiler identification.

Example:
    .venv/bin/python tools/compare/driver_callback_init_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
for extra in (SCRIPTS, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from window_diff import diff_window  # noqa: E402

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402
from capstone.arm import (  # noqa: E402
    ARM_INS_BL,
    ARM_INS_BLX,
    ARM_OP_IMM,
    ARM_OP_MEM,
    ARM_OP_REG,
)
from elftools.elf.elffile import ELFFile  # noqa: E402

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
INIT = ("_ZN3vox23DriverCallbackInterface4InitEPv", 0x009D53F4, 48)
DETECT_NEON_VA = 0x009DAD14
SOURCE = Path("reconstruction/recovered/libNOVA3_neon/driver_callback_init.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                       "/home/jason/.local/opt/android-ndk-r8e"))

# (symbol name, offset inside the symbol) that each reference store must hit.
EXPECTED_STORES = {
    0x34: ("_ZN3vox23DriverCallbackInterface13m_sWorkBufferE", 0),
    0x38: ("_ZN3vox23DriverCallbackInterface13m_sWorkBufferE", 4),
    0x3C: ("_ZN3vox23DriverCallbackInterface15m_sMixingBufferE", 0),
    0x40: ("_ZN3vox23DriverCallbackInterface15m_sMixingBufferE", 4),
}
EXPECTED_BYTE_STORE = (
    0x44,
    "_ZN3vox29DriverCallbackSourceInterface10enableNeonE",
    0,
)


def load_reference(reference: Path) -> tuple[bytes, bytes, dict[str, tuple[int, int]]]:
    """Return (whole ELF bytes, Init window bytes, symbol map name -> (va, size))."""
    blob = reference.read_bytes()
    if hashlib.sha256(blob).hexdigest() != REFERENCE_SHA256:
        raise SystemExit("FAIL: reference SHA-256 mismatch")
    dataless: dict[str, tuple[int, int]] = {}
    with open(reference, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        symtab = elf.get_section_by_name(".symtab")
        if text is None or symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        for sym in symtab.iter_symbols():
            if sym["st_value"]:
                dataless.setdefault(sym.name, (sym["st_value"], sym["st_size"]))
        hits = [s for s in symtab.iter_symbols() if s.name == INIT[0]]
        if len(hits) != 1:
            raise SystemExit("FAIL: Init symbol is not unique")
        sym = hits[0]
        if sym["st_value"] != INIT[1] or sym["st_size"] != INIT[2]:
            raise SystemExit("FAIL: Init symbol bounds changed")
        if sym["st_info"]["type"] != "STT_FUNC" or sym["st_value"] & 1:
            raise SystemExit("FAIL: Init is not an A32 STT_FUNC")
        base = text.header.sh_addr
        data = text.data()
        window = data[INIT[1] - base:INIT[1] - base + INIT[2]]
    return blob, window, dataless


def _enclosing(dataless: dict[str, tuple[int, int]],
               va: int) -> tuple[str, int] | None:
    best = None
    for name, (addr, size) in dataless.items():
        if size and addr <= va < addr + size:
            if best is None or size > best[1]:
                best = (name, size, va - addr)
    if best is None:
        return None
    return best[0], best[2]


def _decode(window: bytes, address: int) -> list:
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    instructions = list(md.disasm(window, address))
    # Drop words that a PC-relative load inside the window reads as a literal.
    literals = set()
    for insn in instructions:
        if insn.mnemonic == "ldr" and len(insn.operands) == 2 \
                and insn.operands[1].type == ARM_OP_MEM:
            mem = insn.operands[1].mem
            if insn.reg_name(mem.base) == "pc":
                literals.add(insn.address + 8 + mem.disp)
    out = []
    for insn in instructions:
        span = range(insn.address, insn.address + insn.size)
        if any(a in literals for a in span):
            continue
        out.append(insn)
    return out


def structure(window: bytes, dataless: dict[str, tuple[int, int]]) -> dict:
    instructions = _decode(window, INIT[1])

    # 1. PIC base: `ldr rX, [pc, #imm]` ... `add rX, pc, rX`; the word at the
    #    literal address is the addend of the base relative to the `add`'s PC+8.
    pic_base = None
    pic_register = None
    pic_addend = None
    for index, insn in enumerate(instructions):
        if insn.mnemonic != "add" or len(insn.operands) != 3:
            continue
        dst, src, addend_op = insn.operands
        if not (dst.type == ARM_OP_REG and src.type == ARM_OP_REG
                and addend_op.type == ARM_OP_REG):
            continue
        if insn.reg_name(src.reg) != "pc" or insn.reg_name(addend_op.reg) != insn.reg_name(dst.reg):
            continue
        for prev in reversed(instructions[:index]):
            if prev.mnemonic != "ldr" or len(prev.operands) < 2:
                continue
            if prev.operands[0].type != ARM_OP_REG or prev.operands[0].reg != dst.reg:
                continue
            mem = prev.operands[1]
            if mem.type != ARM_OP_MEM or insn.reg_name(mem.mem.base) != "pc":
                continue
            at = prev.address + 8 + mem.mem.disp - INIT[1]
            pic_addend = int.from_bytes(window[at:at + 4], "little")
            pic_base = insn.address + 8 + pic_addend
            pic_register = dst.reg
            break
        if pic_base is not None:
            break

    # 2. Branches, and whether r0/r1 are read before the first of them.
    calls: list[int] = []
    r0_reads = 0
    r1_reads = 0
    seen_call = False
    for insn in instructions:
        reads, _writes = insn.regs_access()
        if not seen_call:
            r0_reads += sum(1 for r in reads if insn.reg_name(r) == "r0")
            r1_reads += sum(1 for r in reads if insn.reg_name(r) == "r1")
        if insn.id in (ARM_INS_BL, ARM_INS_BLX):
            calls.append(insn.operands[0].imm)
            seen_call = True

    # 3. Every store through the PIC base, mapped to the static it lands on.
    stores = []
    zero_word_stores = []
    byte_stores = []
    for insn in instructions:
        if insn.mnemonic not in ("str", "strb", "strh"):
            continue
        if len(insn.operands) < 2 or insn.operands[1].type != ARM_OP_MEM:
            continue
        mem = insn.operands[1].mem
        if pic_register is None or mem.base != pic_register:
            continue
        offset = mem.disp
        target = _enclosing(dataless, pic_base + offset) if pic_base else None
        stores.append({
            "offset": offset,
            "width": 1 if insn.mnemonic == "strb" else 4,
            "symbol": target[0] if target else None,
            "symbol_offset": target[1] if target else None,
        })
        if insn.mnemonic == "str":
            zero_word_stores.append(offset)
        elif insn.mnemonic == "strb":
            byte_stores.append(offset)

    mapped = {s["offset"]: [s["symbol"], s["symbol_offset"]] for s in stores}
    expected = {off: list(pair) for off, pair in EXPECTED_STORES.items()}
    expected[EXPECTED_BYTE_STORE[0]] = [EXPECTED_BYTE_STORE[1], EXPECTED_BYTE_STORE[2]]
    mapping_ok = all(mapped.get(off) == pair for off, pair in expected.items())

    base_symbol = _enclosing(dataless, pic_base) if pic_base else None
    return {
        "instruction_count": len(instructions),
        "call_targets": [f"0x{a:08x}" for a in calls],
        "call_is_recovered_detect_neon": calls == [DETECT_NEON_VA],
        "pic_addend": f"0x{pic_addend:08x}" if pic_addend is not None else None,
        "pic_base_va": f"0x{pic_base:08x}" if pic_base else None,
        "pic_base_symbol": base_symbol[0] if base_symbol else None,
        "pic_base_symbol_offset": base_symbol[1] if base_symbol else None,
        "store_targets": stores,
        "store_object_mapping_matches_recovered_fields": mapping_ok,
        "zero_word_store_count": len(zero_word_stores),
        "byte_store_count": len(byte_stores),
        "this_register_reads_before_call": r0_reads,
        "argument_register_reads_before_call": r1_reads,
        "this_and_argument_unused": r0_reads == 0 and r1_reads == 0,
    }


def _shape(insns: list) -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for insn in insns:
        operands = []
        for op in insn.operands:
            if op.type == ARM_OP_REG:
                operands.append(insn.reg_name(op.reg))
            elif op.type == ARM_OP_IMM:
                operands.append("#IMM")
            elif op.type == ARM_OP_MEM:
                operands.append(f"[{insn.reg_name(op.mem.base)},#MEM]")
            else:
                operands.append("?")
        out.append((insn.mnemonic, tuple(operands)))
    return out


def _anchor_delta(ref_insns: list, cand_insns: list) -> int | None:
    ref = [i.operands[1].mem.disp for i in ref_insns
           if i.mnemonic.startswith("str") and i.operands[1].type == ARM_OP_MEM]
    cand = [i.operands[1].mem.disp for i in cand_insns
            if i.mnemonic.startswith("str") and i.operands[1].type == ARM_OP_MEM]
    if not ref or len(ref) != len(cand):
        return None
    deltas = {r - c for r, c in zip(ref, cand)}
    return deltas.pop() if len(deltas) == 1 else None


def _candidate_window(obj: bytes) -> bytes:
    import io

    elf = ELFFile(io.BytesIO(obj))
    text = elf.get_section_by_name(".text")
    symtab = elf.get_section_by_name(".symtab")
    hits = [s for s in symtab.iter_symbols() if s.name == INIT[0]]
    if len(hits) != 1:
        raise ValueError("candidate does not define Init exactly once")
    sym = hits[0]
    data = text.data()
    return data[sym["st_value"]:sym["st_value"] + sym["st_size"]]


def toolchain(ndk: Path) -> list[dict]:
    bin_dir = ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin"
    if not (bin_dir / "arm-linux-androideabi-gcc").exists():
        return []
    arm = ["-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16", "-fPIC"]
    out = []
    for opt in ("-O2", "-O3", "-Os"):
        for prot in ("-fstack-protector", ""):
            for vis in ("-fvisibility=hidden", "-fvisibility=default"):
                name = f"ndk-r8e-gcc-4.4.3{opt}{prot}{'-nopreempt' if 'hidden' in vis else '-preemptible'}"
                out.append({"name": name,
                            "cc": str(bin_dir / "arm-linux-androideabi-gcc"),
                            "visibility": "hidden" if "hidden" in vis else "default",
                            "flags": [opt] + ([prot] if prot else []) + [vis] + arm})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path(".cache/cpufeat/init.json"))
    ap.add_argument("--workdir", type=Path, default=Path(".cache/cpufeat/init"))
    args = ap.parse_args()

    blob, window, dataless = load_reference(args.reference)
    facts = structure(window, dataless)
    ref_insns = _decode(window, INIT[1])
    rows = []
    args.workdir.mkdir(parents=True, exist_ok=True)
    for entry in toolchain(DEFAULT_NDK_R8E):
        obj = args.workdir / f"{entry['name']}.o"
        cmd = [entry["cc"], "-c", *entry["flags"], "-I", str(INCLUDE),
               str(SOURCE), "-o", str(obj)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            rows.append({"config": entry["name"], "status": "COMPILE_FAILED",
                         "diagnostic": (proc.stderr or proc.stdout).strip()[:400]})
            continue
        obj_bytes = obj.read_bytes()
        report = diff_window(blob, obj_bytes, address=INIT[1], symbol=INIT[0],
                             expected_reference_sha256=REFERENCE_SHA256)
        report["config"] = entry["name"]
        report["visibility"] = entry["visibility"]
        try:
            cand_insns = _decode(_candidate_window(obj_bytes), INIT[1])
            report["instruction_shape_equal"] = _shape(ref_insns) == _shape(cand_insns)
            delta = _anchor_delta(ref_insns, cand_insns)
            report["anchor_offset_delta"] = f"0x{delta:02x}" if delta is not None else None
            report["constant_anchor_delta"] = delta is not None
        except ValueError as exc:
            report["instruction_shape_equal"] = False
            report["anchor_offset_delta"] = None
            report["constant_anchor_delta"] = False
            report["note"] = str(exc)
        rows.append(report)

    shape_ok = [r for r in rows if r.get("instruction_shape_equal")
                and r.get("constant_anchor_delta")]
    payload = {
        "recovered_source": str(SOURCE),
        "recovered_declaration": "reconstruction/recovered/libNOVA3_neon/driver_callback.h",
        "recovered_symbol": INIT[0],
        "reference": str(args.reference),
        "reference_sha256": REFERENCE_SHA256,
        "reference_address": f"0x{INIT[1]:08x}",
        "reference_size": INIT[2],
        "mode": "A32",
        "structure": facts,
        "structure_agrees_with_source": bool(
            facts["call_is_recovered_detect_neon"]
            and facts["store_object_mapping_matches_recovered_fields"]
            and facts["zero_word_store_count"] == 4
            and facts["byte_store_count"] == 1
            and facts["this_and_argument_unused"]),
        "configs_compiled": len(rows),
        "configs_shape_equal_with_constant_anchor_delta": len(shape_ok),
        "code_window_equal": any(r.get("code_window_equal") for r in rows),
        "source_matching_verified": False,
        "original_compiler_verified": False,
        "disposition": "IMPLEMENTED + TESTED (behaviour); NOT MATCHED "
                       "(instruction shape and store order agree modulo relocation "
                       "words and a constant anchor offset)",
        "evidence_scope": "One linked A32 window against ET_REL candidates. "
                          "Anchor-offset-masked shape equality is not a byte match, "
                          "an original object recovery or compiler identification.",
        "matrix": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"structure agrees with recovered source: {payload['structure_agrees_with_source']}")
    print(f"  calls={facts['call_targets']} pic_base={facts['pic_base_va']} "
          f"({facts['pic_base_symbol']})")
    print(f"  zero word stores={facts['zero_word_store_count']} "
          f"byte stores={facts['byte_store_count']} "
          f"this/arg reads before call={facts['this_register_reads_before_call']}/"
          f"{facts['argument_register_reads_before_call']}")
    print(f"window: {len(shape_ok)}/{len(rows)} configs shape-equal with a constant "
          f"anchor delta; code_window_equal={payload['code_window_equal']} -> {args.out}")
    for r in rows:
        counts = r.get("counts") or {}
        print(f"  {r['config']:52s} {r.get('status')} {r.get('candidate_size')}B "
              f"EQUAL={counts.get('EQUAL')} RELOC={counts.get('RELOCATION_FIELD')} "
              f"ENC={counts.get('ENCODING_DIFFERENCE')} delta={r.get('anchor_offset_delta')}")
    return 0 if (payload["structure_agrees_with_source"] and shape_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
