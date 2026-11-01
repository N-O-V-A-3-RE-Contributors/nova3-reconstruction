#!/usr/bin/env python3
"""Resolve ARM/A32 PC-relative literal-pool references inside one function.

Given an ELF and a function VA range, this tool decodes the instructions with
Capstone, follows the `LDR Rn, [PC, #imm]` / `ADD Rn, PC, Rm` idiom used by
GCC for position-independent addressing, and reports what each literal really
points at: a named symbol, a section, or an ASCII string.

It exists because a decompiler view of a shared object hides the difference
between "this function reads a struct field" and "this function reads a
file-scope object declared next door".  The Premium engine still carries a
symbol table, so those two cases are distinguishable from static evidence
alone, and this tool prints that evidence instead of guessing.

Usage:
    literal_map.py <elf> <start_va> <end_va> [--json out.json]

Nothing is written outside the requested output path.  The ELF is opened
read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_REG, ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC
from elftools.elf.elffile import ELFFile


PRINTABLE = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


@dataclass
class Symbol:
    name: str
    addr: int
    size: int
    vtype: str
    bind: str
    section: str


@dataclass
class Ref:
    insn_va: int
    insn_text: str
    literal_va: int
    literal_word: int
    target_va: Optional[int]
    resolution: str  # "symbol", "section", "string", "unresolved"
    symbol: Optional[str] = None
    section: Optional[str] = None
    string: Optional[str] = None
    # How the address was formed: "pc+literal" (direct) or
    # "indirect" (literal is a pointer slot loaded again later).
    kind: str = "pc+literal"


class Literals:
    def __init__(self, path: str) -> None:
        self.path = path
        self.fh = open(path, "rb")
        self.elf = ELFFile(self.fh)
        self.symbols: List[Symbol] = []
        self._load_symbols()

    def close(self) -> None:
        self.fh.close()

    def _load_symbols(self) -> None:
        for sec in self.elf.iter_sections():
            if not sec.name.startswith((".symtab", ".dynsym")):
                continue
            symtab = self.elf.get_section_by_name(sec.name)
            for sym in symtab.iter_symbols():
                info_type = sym["st_info"]["type"]
                if info_type in ("STT_NOTYPE", "STT_FILE", "STT_SECTION"):
                    continue
                secidx = sym["st_shndx"]
                secname = ""
                if isinstance(secidx, int) and 0 < secidx < self.elf.num_sections():
                    secname = self.elf.get_section(secidx).name
                self.symbols.append(
                    Symbol(
                        name=sym.name,
                        addr=sym["st_value"],
                        size=sym["st_size"],
                        vtype=info_type,
                        bind=sym["st_info"]["bind"],
                        section=secname,
                    )
                )
        # Prefer named, sized objects when several cover one address.
        self.symbols.sort(key=lambda s: (s.addr, -len(s.name)))

    def section_of(self, va: int) -> Optional[str]:
        for sec in self.elf.iter_sections():
            start = sec["sh_addr"]
            size = sec["sh_size"]
            if size and start <= va < start + size:
                return sec.name
        return None

    def read_word(self, va: int) -> Optional[int]:
        data = self.read_bytes(va, 4)
        if data is None or len(data) != 4:
            return None
        return int.from_bytes(data, "little")

    def read_bytes(self, va: int, size: int) -> Optional[bytes]:
        for sec in self.elf.iter_sections():
            start = sec["sh_addr"]
            if sec["sh_type"] == "SHT_NOBITS":
                continue
            if size and start <= va and va + size <= start + sec["sh_size"]:
                return sec.data()[va - start : va - start + size]
        return None

    def read_cstring(self, va: int, limit: int = 64) -> Optional[str]:
        data = self.read_bytes(va, limit)
        if not data:
            return None
        end = data.find(b"\x00")
        if end <= 0:
            return None
        chunk = data[:end]
        if not all(b in PRINTABLE for b in chunk):
            return None
        try:
            return chunk.decode("ascii")
        except UnicodeDecodeError:
            return None

    def symbol_at(self, va: int) -> Optional[Symbol]:
        best = None
        for sym in self.symbols:
            if sym.addr == va and sym.size > 0:
                return sym
            if sym.addr <= va < sym.addr + max(sym.size, 1) and sym.size > 0:
                best = best or sym
        return best

    def disasm(self, start: int, end: int):
        sec = None
        for s in self.elf.iter_sections():
            if s["sh_type"] != "SHT_NOBITS" and s["sh_size"] and \
               s["sh_addr"] <= start < s["sh_addr"] + s["sh_size"]:
                sec = s
                break
        if sec is None:
            raise SystemExit(f"address 0x{start:x} is not inside a loadable file-backed section")
        base = sec["sh_addr"]
        code = sec.data()[start - base : end - base]
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)
        md.detail = True
        return list(md.disasm(code, start))


def analyze(lit: Literals, start: int, end: int) -> List[Ref]:
    insns = lit.disasm(start, end)
    # Map register index -> (target_or_None, literal_va, insn_va, insn_text)
    pending: Dict[int, Ref] = {}
    refs: List[Ref] = []

    def resolve(va: Optional[int], literal_word: int, ref: Ref) -> None:
        if va is None:
            ref.target_va = None
            ref.resolution = "unresolved"
            return
        ref.target_va = va
        sym = lit.symbol_at(va)
        secname = lit.section_of(va)
        ref.section = secname
        text = lit.read_cstring(va)
        if sym is not None:
            ref.symbol = sym.name
            ref.resolution = "symbol"
        elif text is not None:
            ref.string = text
            ref.resolution = "string"
        elif secname is not None:
            ref.resolution = "section"
        else:
            ref.resolution = "unresolved"

    for insn in insns:
        mnem = insn.mnemonic
        ops = insn.operands
        if mnem.startswith("ldr") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                and ops[1].mem.base == ARM_REG_PC:
            literal_va = (insn.address + 8) + _s32(insn.operands[1].mem.disp)
            word = lit.read_word(literal_va)
            ref = Ref(
                insn_va=insn.address,
                insn_text=f"{insn.mnemonic} {insn.op_str}",
                literal_va=literal_va,
                literal_word=word if word is not None else 0,
                target_va=None,
                resolution="unresolved",
                kind="pc+literal",
            )
            refs.append(ref)
            if ops[0].type == ARM_OP_REG:
                pending[ops[0].reg] = ref
        elif mnem in ("add", "sub") and len(ops) == 3 and ops[0].type == ARM_OP_REG \
                and ops[1].type == ARM_OP_REG and ops[1].reg == ARM_REG_PC \
                and ops[2].type == ARM_OP_REG and ops[2].reg in pending:
            ref = pending.pop(ops[2].reg)
            sign = 1 if mnem == "add" else -1
            ref.target_va = (insn.address + 8) + sign * ref.literal_word
            resolve(ref.target_va, ref.literal_word, ref)
            pending.pop(ops[2].reg, None)
        elif ops and any(o.type == ARM_OP_REG and o.reg in pending for o in ops):
            # The literal was consumed as data (dereferenced, stored, compared).
            for o in ops:
                if o.type == ARM_OP_REG and o.reg in pending:
                    ref = pending.pop(o.reg)
                    ref.target_va = ref.literal_word
                    resolve(ref.literal_word, ref.literal_word, ref)
                    ref.kind = "indirect"
        if mnem in ("bl", "b", "bx", "blx") or mnem.startswith("pop") or mnem.startswith("b"):
            for key in [k for k in pending if k not in
                        [o.reg for o in ops if o.type == ARM_OP_REG]]:
                ref = pending.pop(key)
                ref.target_va = ref.literal_word
                resolve(ref.literal_word, ref.literal_word, ref)
                ref.kind = "indirect"

    for key, ref in list(pending.items()):
        ref.target_va = ref.literal_word
        resolve(ref.literal_word, ref.literal_word, ref)
        ref.kind = "indirect"
    return refs


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("elf")
    ap.add_argument("start", type=lambda s: int(s, 0))
    ap.add_argument("end", type=lambda s: int(s, 0))
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args(argv[1:])

    lit = Literals(args.elf)
    try:
        refs = analyze(lit, args.start, args.end)
    finally:
        lit.close()

    for ref in refs:
        tgt = "-" if ref.target_va is None else f"0x{ref.target_va:08x}"
        detail = ref.symbol or (f'"{ref.string}"' if ref.string else (ref.section or ""))
        print(
            f"0x{ref.insn_va:08x} {ref.insn_text:<28} lit@0x{ref.literal_va:08x}"
            f" word=0x{ref.literal_word:08x} -> {tgt} {ref.kind:<11} "
            f"{ref.resolution:<10} {detail}"
        )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump([asdict(r) for r in refs], fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
