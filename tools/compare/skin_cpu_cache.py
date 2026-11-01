#!/usr/bin/env python3
"""Re-derive the cpu-features consumer inside CSoftwareSkinTechnique::init.

The open cpu-features item in `docs/progress/NEXT.md` was: "recover the NEON gate
in glitch::collada::detail::CSoftwareSkinTechnique::init (0x00739630), bounded by
the direct android_getCpuFeatures call at 0x00739764 and the branch that consumes
it — or record that the gate has no separable behaviour and stop."

This script answers that question from the reference bytes.  Three bounded,
independent checks on the Premium ARMv7 engine ELF:

  STRUCTURE  decode the init() window and re-derive from the instructions only:
             the PIC anchor resolves to one absolute VA; the anchor is read as a
             64-bit pair; the pair is reduced with one `orrs` whose only consumer
             is a conditional branch; that branch target calls the recovered
             `android_getCpuFeatures` entry (0x000d2f3c) and stores r0:r1 back to
             the same anchor before jumping back to the fall-through path; and the
             loaded pair is never read after the join.  Together that is a lazy
             cache fill, not a feature gate.

  CONSUMERS  decode every bounded STT_FUNC separately (a per-symbol sweep, so a
             literal pool in one function cannot desynchronise the next) and
             report every ldr-PIC-literal + `add pc` resolution of the same
             absolute anchor VA, with the containing symbol and the access class.
             The register map is reset on any register write and on branches;
             this is a bounded scan, not a full data-flow analysis.

  READERS    classify each hit as load / store / load+store, so "who consumes the
             cache" is a measured count rather than an assumption.

Output is metadata only: addresses, symbol names, instruction mnemonics, counts
and booleans.  No reference instruction bytes or disassembly text are written by
this tool.  Exit code 1 when a claim does not reproduce.

Example:
    .venv/bin/python tools/compare/skin_cpu_cache.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import (
    ARM_INS_ADD,
    ARM_INS_B,
    ARM_INS_BL,
    ARM_INS_BLX,
    ARM_INS_LDM,
    ARM_INS_LDR,
    ARM_INS_LDRB,
    ARM_INS_LDRD,
    ARM_INS_LDRSB,
    ARM_INS_STR,
    ARM_INS_STRB,
    ARM_INS_STRD,
    ARM_INS_STRH,
    ARM_OP_MEM,
    ARM_OP_REG,
    ARM_REG_PC,
)
from elftools.elf.elffile import ELFFile

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

INIT_SYMBOL = (
    "_ZN6glitch7collada6detail22CSoftwareSkinTechnique4initERNS0_11SSkinBufferE"
    "PNS_5scene11CMeshBufferEPNS_5video12IVideoDriverEbRKNS0_20SProcessBufferConfigE"
)
INIT_VA = 0x00739630
INIT_SIZE = 1028
GETTER_FEATURES_VA = 0x000D2F3C
WINDOW_LO = 0x00739630
WINDOW_HI = 0x00739680

LOAD_MNEMONICS = (ARM_INS_LDR, ARM_INS_LDRB, ARM_INS_LDRSB, ARM_INS_LDRD)
STORE_MNEMONICS = (ARM_INS_STR, ARM_INS_STRB, ARM_INS_STRH, ARM_INS_STRD)
FLAG_ONLY = {"cmp", "cmn", "tst", "teq"}

LDR_PC_RE = re.compile(r"^(\w+),\s*\[pc,\s*#(-?0x[0-9a-fA-F]+)\]$")
ADD_PC_RE = re.compile(r"^(\w+),\s*pc,\s*(\w+)$")


def open_elf(path: Path):
    fh = open(path, "rb")
    return fh, ELFFile(fh)


def text_section(elf):
    s = elf.get_section_by_name(".text")
    if s is None:
        raise SystemExit("missing .text")
    return s


def function_bounds(elf, text):
    """Sorted (va, end, name) for bounded A32 STT_FUNC symbols inside .text."""
    lo = text["sh_addr"]
    hi = lo + text["sh_size"]
    out = []
    symtab = elf.get_section_by_name(".symtab")
    if symtab is None:
        raise SystemExit("missing .symtab")
    for sym in symtab.iter_symbols():
        if sym["st_info"]["type"] != "STT_FUNC":
            continue
        va, size = sym["st_value"], sym["st_size"]
        if size == 0 or va < lo or va + size > hi or (va & 1):
            continue
        out.append((va, va + size, sym.name))
    out.sort()
    return out


def decode_func(md, text_data, base, va, end):
    return md.disasm(text_data[va - base:end - base], va)


def pic_resolutions(md, text_data, base, va, end):
    """Resolutions of the ldr-PIC-literal + `add pc` idiom inside one function."""
    regs: dict[str, int | None] = {}
    hits: list[tuple[int, int, str, str]] = []
    for insn in decode_func(md, text_data, base, va, end):
        m = LDR_PC_RE.match(insn.op_str) if insn.mnemonic == "ldr" else None
        if m:
            lit_va = insn.address + 8 + int(m.group(2), 16)
            off = lit_va - base
            regs[m.group(1)] = (int.from_bytes(text_data[off:off + 4], "little")
                                if 0 <= off <= len(text_data) - 4 else None)
            continue
        if insn.mnemonic == "add":
            m = ADD_PC_RE.match(insn.op_str)
            if m and regs.get(m.group(2)) is not None:
                hits.append((insn.address, insn.address + 8 + regs[m.group(2)],
                             m.group(1), m.group(1)))
                regs[m.group(1)] = None
                continue
        if insn.id in (ARM_INS_BL, ARM_INS_BLX) or insn.mnemonic in ("b", "bx"):
            regs.clear()
            continue
        # conservative invalidation: every register named as a destination
        for name in re.findall(r"\b(r\d+|sb|sl|fp|ip|sp|lr)\b", insn.op_str):
            if name in regs and not insn.op_str.startswith(name):
                regs[name] = None
        dest = insn.op_str.split(",")[0].strip()
        if dest in regs:
            regs[dest] = None
    return hits


def classify_access(md, text_data, base, start_va, reg, span=0x40):
    """Classify accesses through `reg` shortly after a resolution."""
    seen: set[str] = set()
    code = text_data[start_va - base: start_va - base + span]
    for insn in md.disasm(code, start_va):
        if insn.address == start_va:
            continue  # the resolution instruction itself
        if insn.mnemonic in ("b", "bl", "bx", "blx"):
            break
        if insn.id == ARM_INS_LDM and insn.op_str.startswith(reg + ","):
            seen.add("load")
        if re.search(r"\[" + reg + r"(?:\]|,)", insn.op_str):
            if insn.id in LOAD_MNEMONICS:
                seen.add("load")
            elif insn.id in STORE_MNEMONICS:
                seen.add("store")
        dest = insn.op_str.split(",")[0].strip()
        if dest == reg and insn.id not in LOAD_MNEMONICS + STORE_MNEMONICS:
            break
    if seen == {"load"}:
        return "load"
    if seen == {"store"}:
        return "store"
    if seen == {"load", "store"}:
        return "load+store"
    return "none"


def first_uses(md, text_data, base, va, regs, limit=40):
    """For each register in `regs`: ('write'|'read', addr) at its first use."""
    out: dict[str, tuple[str, int]] = {}
    remaining = set(regs)
    code = text_data[va - base: va - base + limit * 4]
    for insn in md.disasm(code, va):
        names = re.findall(r"\b(r\d+|sb|sl|fp|ip)\b", insn.op_str)
        if not names:
            continue
        if insn.mnemonic in FLAG_ONLY:
            reads = set(names)
            writes: set[str] = set()
        elif insn.mnemonic.startswith("str"):
            reads = set(names)
            writes = set()
        else:
            writes = {names[0]}
            reads = set(names[1:])
        for r in sorted(remaining):
            if r in writes:
                out[r] = ("write", insn.address)
                remaining.discard(r)
            elif r in reads:
                out[r] = ("read", insn.address)
                remaining.discard(r)
        if insn.mnemonic in ("b", "bx") or not remaining:
            break
    for r in remaining:
        out[r] = ("unused", -1)
    return out


def structure_check(md, text_data, base):
    """Re-derive the lazy-cache claims from the init() window."""
    facts: dict = {"anchor_va": None, "anchor_offset_in_init": None,
                   "anchor_resolved_at": None, "pair_regs": None,
                   "join_uses": None, "call_block": None}
    reg_map: dict[str, int | None] = {}
    anchor_reg = None
    pair_load = None
    orr_addr = None
    branch = None

    code = text_data[WINDOW_LO - base: WINDOW_HI - base]
    for insn in md.disasm(code, WINDOW_LO):
        m = LDR_PC_RE.match(insn.op_str) if insn.mnemonic == "ldr" else None
        if m:
            lit_va = insn.address + 8 + int(m.group(2), 16)
            off = lit_va - base
            reg_map[m.group(1)] = (int.from_bytes(text_data[off:off + 4], "little")
                                   if 0 <= off <= len(text_data) - 4 else None)
            continue
        if anchor_reg is None:
            if insn.mnemonic == "add":
                m = ADD_PC_RE.match(insn.op_str)
                if m and reg_map.get(m.group(2)) is not None:
                    facts["anchor_va"] = insn.address + 8 + reg_map[m.group(2)]
                    facts["anchor_offset_in_init"] = insn.address - INIT_VA
                    facts["anchor_resolved_at"] = insn.address
                    anchor_reg = m.group(1)
            continue
        if insn.mnemonic == "ldm" and insn.op_str.startswith(anchor_reg + ",") and \
                pair_load is None:
            names = re.findall(r"\b(r\d+|sb|sl|fp|ip)\b", insn.op_str)[1:]
            pair_load = {"addr": insn.address, "registers": len(names),
                         "width": 4 * len(names)}
            facts["pair_regs"] = names
            continue
        if insn.mnemonic == "orrs" and pair_load is not None and orr_addr is None:
            orr_addr = insn.address
            continue
        if orr_addr is not None and branch is None and insn.mnemonic in ("beq", "bne"):
            branch = {"addr": insn.address, "mnemonic": insn.mnemonic,
                      "target": int(insn.op_str.lstrip("#"), 16)
                      if insn.op_str.startswith("#") else None}
            continue

    facts["pair_load"] = pair_load
    facts["orrs_addr"] = orr_addr
    facts["branch"] = branch
    facts["call"] = None
    facts["store"] = None
    facts["join_branch"] = None

    if branch is not None and branch["target"] is not None:
        # Decode the branch target block on its own: the call path is out of
        # line, past a return block, so a linear sweep cannot reach it in order.
        target = branch["target"]
        block = []
        for insn in md.disasm(text_data[target - base: target - base + 0x20], target):
            block.append(insn)
            if insn.mnemonic in ("b", "bx") or insn.id in (ARM_INS_BL, ARM_INS_BLX) \
                    and len(block) > 3:
                pass
            if insn.mnemonic == "b" or insn.mnemonic == "bx":
                break
        facts["call_block"] = {
            "addr": f"0x{target:08x}",
            "mnemonics": [i.mnemonic for i in block],
        }
        bl = next((i for i in block if i.id in (ARM_INS_BL, ARM_INS_BLX)), None)
        if bl is not None:
            facts["call"] = {"addr": bl.address,
                             "target": int(bl.op_str.lstrip("#"), 16)}
        store = next((i for i in block if i.mnemonic == "strd" and
                      anchor_reg and anchor_reg in i.op_str), None)
        if store is not None:
            facts["store"] = {"addr": store.address}
        jb = next((i for i in block if i.mnemonic == "b"), None)
        if jb is not None:
            facts["join_branch"] = {"addr": jb.address,
                                    "target": int(jb.op_str.lstrip("#"), 16)}

    if pair_load is not None:
        facts["join_uses"] = first_uses(md, text_data, base, branch["addr"] + 4
                                        if branch else pair_load["addr"],
                                        facts["pair_regs"] or [])
    return facts


def gate_check(md, text_data, base, resolve_addr, anchor_va):
    """Re-derive the consumer's own mask test from the skin() window.

    The reader in `skin()` resolves the same anchor and immediately tests a bit of
    the cached 64-bit feature word.  This decodes that short window and reports
    the mask actually applied, the condition it feeds, and whether the branch is
    a real two-way split.
    """
    lo = resolve_addr - 0x1c
    window = text_data[lo - base: lo - base + 0x60]
    insns = [(i.address, i.mnemonic, i.op_str) for i in md.disasm(window, lo)]
    facts: dict = {"window": [f"0x{lo:08x}", f"0x{lo + 0x60:08x}"]}

    add = next((i for i in insns if i[1] == "add" and i[0] == resolve_addr), None)
    anchor_reg = add[2].split(",")[0].strip() if add else None
    facts["anchor_register"] = anchor_reg

    ldrd = next((i for i in insns if i[1] == "ldrd" and anchor_reg and
                 f"[{anchor_reg}]" in i[2]), None)
    if ldrd is None:
        facts["pair"] = None
        return facts
    regs = [r.strip() for r in ldrd[2].split("[")[0].split(",") if r.strip()]
    facts["pair"] = {"addr": f"0x{ldrd[0]:08x}", "registers": regs}

    # low half masked with an immediate, high half masked with a register whose
    # value is set by a preceding `mov <reg>, #0`
    low_mask = None
    high_mask = None
    for addr, mnem, ops in insns:
        if mnem != "and":
            continue
        parts = [p.strip() for p in ops.split(",")]
        if len(parts) != 3:
            continue
        if parts[1] == regs[0]:
            entry = {"addr": f"0x{addr:08x}"}
            if parts[2].startswith("#"):
                entry["immediate"] = parts[2]
            else:
                entry["register"] = parts[2]
                setter = next((i for i in insns if i[1] == "mov" and
                               i[2].split(",")[0].strip() == parts[2]), None)
                entry["resolved"] = (# setter's immediate, when it is one
                    setter[2].split(",")[1].strip()
                    if setter and setter[2].split(",")[1].strip().startswith("#")
                    else None)
            low_mask = entry
        elif parts[1] == regs[1]:
            high_mask = {"addr": f"0x{addr:08x}", "register": parts[2]}
    facts["low_mask"] = low_mask
    facts["high_mask"] = high_mask

    if high_mask is not None:
        reg = high_mask["register"]
        setter = next((i for i in insns
                       if i[1] == "mov" and i[2].split(",")[0].strip() == reg), None)
        facts["high_mask_setter"] = (
            {"addr": f"0x{setter[0]:08x}", "source": setter[2].split(",")[1].strip()}
            if setter else None)

    orrs = next((i for i in insns if i[1] in ("orrs", "orrs.w")), None)
    facts["orrs"] = {"addr": f"0x{orrs[0]:08x}", "operands": orrs[2]} if orrs else None

    branch = None
    if orrs is not None:
        branch = next((i for i in insns if i[0] > orrs[0] and i[1].startswith("b")
                       and i[1] not in ("b", "bl", "bx")), None)
    facts["branch"] = ({"addr": f"0x{branch[0]:08x}", "mnemonic": branch[1],
                        "target": branch[2]} if branch else None)
    facts["two_way_split"] = bool(branch) and branch[2] != f"#{branch[0] + 4:#x}"
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    ref = Path(args.reference)
    fh, elf = open_elf(ref)
    sha = hashlib.sha256(ref.read_bytes()).hexdigest()
    text = text_section(elf)
    base = text["sh_addr"]
    text_data = text.data()
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = False

    facts = structure_check(md, text_data, base)
    anchor = facts["anchor_va"]
    call = facts.get("call")
    join_uses = facts.get("join_uses") or {}

    struct = {
        "anchor_va": f"0x{anchor:08x}" if anchor is not None else None,
        "anchor_offset_in_init": facts["anchor_offset_in_init"],
        "anchor_resolved_at": (f"0x{facts['anchor_resolved_at']:08x}"
                               if facts["anchor_resolved_at"] else None),
        "pair_load_width": facts["pair_load"]["width"] if facts["pair_load"] else None,
        "pair_registers": facts["pair_regs"],
        "orrs_present": facts["orrs_addr"] is not None,
        "branch": facts["branch"],
        "call_addr": f"0x{call['addr']:08x}" if call else None,
        "call_target": f"0x{call['target']:08x}" if call else None,
        "call_target_is_getter": bool(call) and call["target"] == GETTER_FEATURES_VA,
        "stores_pair_back_to_anchor": facts["store"] is not None,
        "store_addr": (f"0x{facts['store']['addr']:08x}"
                       if facts["store"] else None),
        "rejoins_fallthrough": bool(facts["join_branch"]) and bool(facts["branch"]) and
        facts["join_branch"]["target"] == facts["branch"]["addr"] + 4,
        "pair_uses_after_join": {r: {"kind": k, "addr": (f"0x{a:08x}" if a >= 0
                                                         else None)}
                                 for r, (k, a) in join_uses.items()},
    }

    checks = {
        "reference_sha_matches": sha == REFERENCE_SHA256,
        "anchor_resolved": anchor is not None,
        "pair_load_is_8_bytes": struct["pair_load_width"] == 8,
        "zero_test_present": struct["orrs_present"] and
        bool(struct["branch"]) and struct["branch"]["mnemonic"] == "beq",
        "branch_skips_to_the_call": bool(struct["branch"]) and
        struct["branch"]["target"] is not None and
        struct["branch"]["target"] == (call["addr"] if call else -1),
        "call_target_is_getter": struct["call_target_is_getter"],
        "stores_result_back": struct["stores_pair_back_to_anchor"],
        "rejoins_fallthrough": struct["rejoins_fallthrough"],
        "pair_not_read_after_join": bool(join_uses) and
        all(k == "write" for k, _ in join_uses.values()),
    }

    anchor_symbol = None
    symtab = elf.get_section_by_name(".symtab")
    if symtab is not None and anchor is not None:
        for sym in symtab.iter_symbols():
            if sym["st_info"]["type"] == "STT_OBJECT" and sym["st_value"] == anchor:
                anchor_symbol = {"name": sym.name, "size": sym["st_size"],
                                 "bind": sym["st_info"]["bind"]}

    funcs = function_bounds(elf, text)
    consumers = []
    for fva, fend, name in funcs:
        for addr, target, reg, _ in pic_resolutions(md, text_data, base, fva, fend):
            if target != anchor:
                continue
            consumers.append({
                "resolve_addr": f"0x{addr:08x}",
                "access": classify_access(md, text_data, base, addr, reg),
                "function": name,
                "function_va": f"0x{fva:08x}",
                "function_size": fend - fva,
            })

    readers = [c for c in consumers if c["access"] in ("load", "load+store")]
    gate = None
    gate_reader = next((c for c in readers if c["function_va"] != f"0x{INIT_VA:08x}"),
                       None)
    if gate_reader is not None:
        gate = gate_check(md, text_data, base,
                          int(gate_reader["resolve_addr"], 16), anchor)
        gate["reader_function"] = gate_reader["function"]
        gate["reader_resolve_addr"] = gate_reader["resolve_addr"]
        checks["gate_pair_load"] = bool(gate.get("pair"))
        lm = gate.get("low_mask") or {}
        checks["gate_masks_low_half_with_bit_2"] = (
            (lm.get("immediate") or lm.get("resolved")) == "#4")
        checks["gate_high_mask_is_zero"] = (
            bool(gate.get("high_mask_setter")) and
            gate["high_mask_setter"]["source"] == "#0")
        checks["gate_condition_is_a_two_way_split"] = bool(gate.get("two_way_split"))

    report = {
        "tool": "skin_cpu_cache",
        "reference": str(ref),
        "reference_sha256": sha,
        "init_symbol": INIT_SYMBOL,
        "init_va": f"0x{INIT_VA:08x}",
        "init_size": INIT_SIZE,
        "anchor_symbol": anchor_symbol,
        "structure": struct,
        "checked": checks,
        "all_claims_reproduce": all(checks.values()),
        "consumers": consumers,
        "consumer_count": len(consumers),
        "consumer_functions": sorted({c["function"] for c in consumers}),
        "reader_count": len(readers),
        "reader_functions": sorted({c["function"] for c in readers}),
        "gate": gate,
        "scan_limits": ("per-symbol A32 sweep with a conservative register map; "
                        "values dropped on register writes and branches; not a "
                        "full data-flow analysis; Thumb functions excluded"),
        "disposition": (
            "init_lazily_fills_a_8_byte_cpuFeatures_cache_and_skin_tests_bit_2 "
            "of it inside two unreconstructed functions; neither call site is a "
            "separable unit"
            if all(checks.values()) else "claims_did_not_reproduce"),
        "source_matching_verified": False,
    }

    text_out = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text_out + "\n")
    print(text_out)
    return 0 if report["all_claims_reproduce"] else 1


if __name__ == "__main__":
    sys.exit(main())
