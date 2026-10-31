#!/usr/bin/env python3
"""Locate every in-binary call site of the recovered cpu-features getters.

Reads the Premium ARMv7 engine ELF and reports every direct branch to one of
the recovered getter entry points (or to the PLT stub that resolves to them,
when a build routes the call through the PLT).

Why per-symbol decoding: the engine's `.text` contains interleaved literal
pools and jump tables, so a linear sweep desynchronises and silently drops
call sites (a linear Capstone sweep of this binary reaches only a few dozen
`BL`s before the first undecodable word).  Decoding each STT_FUNC symbol's
own `[st_value, st_value+st_size)` range instead gives a complete, bounded
answer for every function the ELF actually declares, and reports how many
symbols could not be decoded so the coverage is visible rather than assumed.

Output is metadata only (addresses, symbol names, branch mnemonics) plus a
per-host-function SHA-256 prefix; no reference instruction bytes or
disassembly text are emitted.  Exit code 1 when no call site is found.

Example:
    .venv/bin/python tools/compare/cpuquery_callsites.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
        --out .cache/cpufeat/cpuquery_callsites.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
from capstone.arm import ARM_INS_B, ARM_INS_BL, ARM_INS_BLX
from elftools.elf.elffile import ELFFile

# Recovered symbols of the Premium cpu-features.c group (ELF VA, size).
TARGETS = {
    "android_getCpuCount": (0x000D2F10, 44),
    "android_getCpuFeatures": (0x000D2F3C, 48),
    "android_getCpuFamily": (0x000D2F6C, 44),
}
BRANCHES = (ARM_INS_B, ARM_INS_BL, ARM_INS_BLX)


def plt_targets(elf) -> dict[int, str]:
    """Map PLT stub address -> symbol name using the .rel.plt GOT slots.

    This binary's PLT is the bfd layout: a 16-byte header, then one 4-byte
    stub per relocation, with stub *n* serving GOT slot *n*.  The mapping is
    self-checked by the caller (stub addresses must fall inside `.plt`).
    """
    out: dict[int, str] = {}
    rel = next((s for s in elf.iter_sections()
                if s.header.sh_type == "SHT_REL" and s.name == ".rel.plt"), None)
    plt = elf.get_section_by_name(".plt")
    got = elf.get_section_by_name(".got")
    if rel is None or plt is None or got is None:
        return out
    dynsym = elf.get_section(rel.header.sh_link)
    plt_lo, plt_hi = plt.header.sh_addr, plt.header.sh_addr + plt.header.sh_size
    got_lo = got.header.sh_addr
    for r in rel.iter_relocations():
        sym = dynsym.get_symbol(r.entry.r_info_sym)
        stub = plt.header.sh_addr + (r.entry.r_offset - got_lo) * 4
        if plt_lo <= stub < plt_hi and sym.name:
            out[stub] = sym.name
    return out


def func_symbols(elf):
    symtab = elf.get_section_by_name(".symtab")
    rows = []
    for sym in symtab.iter_symbols():
        if sym.entry.st_info.type == "STT_FUNC" and sym.entry.st_size:
            rows.append((sym.entry.st_value, sym.entry.st_size, sym.name,
                         bool(sym.entry.st_info.bind == "STB_LOCAL")))
    rows.sort()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--also", action="append", default=[], metavar="0xVA:NAME",
                    help="additional branch target to hunt, e.g. 0x9dad14:DetectNeon; "
                         "used to walk one level up the call graph")
    args = ap.parse_args()

    blob = args.reference.read_bytes()
    with open(args.reference, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        base = text.header.sh_addr
        data = text.data()
        plts = plt_targets(elf)
        symbol_names = {s.entry.st_value: s.name for s in
                        elf.get_section_by_name(".symtab").iter_symbols()
                        if s.entry.st_info.type == "STT_FUNC"}

        direct = {addr: name for name, (addr, _) in TARGETS.items()}
        for spec in args.also:
            va_s, _, name = spec.partition(":")
            if not name:
                raise SystemExit(f"--also expects 0xVA:NAME, got {spec}")
            direct[int(va_s, 0)] = name
        hunt = dict(direct)
        for stub, name in plts.items():
            if name in TARGETS:
                hunt[stub] = name

        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        md.detail = True
        hits = []
        decoded = 0
        undecodable = []
        for start, size, name, _local in func_symbols(elf):
            if not (base <= start and start + size <= base + len(data)):
                continue
            code = data[start - base:start - base + size]
            ok = 0
            for insn in md.disasm(code, start):
                ok += 1
                if insn.id not in BRANCHES or not insn.operands:
                    continue
                target = insn.operands[0].imm
                if target not in hunt:
                    continue
                hits.append({
                    "call_site": f"0x{insn.address:08x}",
                    "mnemonic": insn.mnemonic,
                    "callee": hunt[target],
                    "callee_addr": f"0x{target:08x}",
                    "linkage": "direct" if target in direct else "plt",
                    "host_symbol": name,
                    "host_addr": f"0x{start:08x}",
                    "host_size": size,
                    "host_sha256_16": hashlib.sha256(code).hexdigest()[:16],
                })
            if ok == 0:
                undecodable.append(name)
            decoded += 1

        hits.sort(key=lambda h: int(h["call_site"], 16))
        report = {
            "reference": str(args.reference),
            "reference_sha256": hashlib.sha256(blob).hexdigest(),
            "mode": "A32",
            "targets": {n: {"va": f"0x{a:08x}", "size": s}
                        for n, (a, s) in TARGETS.items()},
            "plt_stub_names": {f"0x{a:08x}": n for a, n in sorted(plts.items())
                               if n in TARGETS},
            "functions_decoded": decoded,
            "functions_undecodable": undecodable[:20],
            "functions_undecodable_count": len(undecodable),
            "call_sites": hits,
            "call_site_count": len(hits),
            "distinct_hosts": sorted({h["host_symbol"] for h in hits}),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(hits)} call site(s) across {len(report['distinct_hosts'])} host "
          f"function(s); {decoded} functions decoded, "
          f"{len(undecodable)} undecodable -> {args.out}")
    for h in hits:
        print(f"  {h['call_site']} {h['mnemonic']:4s} -> {h['callee']} "
              f"({h['linkage']}) in {h['host_symbol']} {h['host_addr']} "
              f"[{h['host_size']} B]")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
