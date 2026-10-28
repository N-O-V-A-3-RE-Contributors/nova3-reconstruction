"""Offline, one-function ARM REL diagnostic; never writes or executes ELF code.

Supports little-endian EABI5 A32 R_ARM_CALL (direct ARM BL, no veneers or
interworking) and R_ARM_REL32. Supplied symbol values are explicit *candidate
link assumptions*, not recovered original relocations. Reports hashes/counts,
never instruction streams. A separate linked ELF is required for a linked
window grade. No compiler, behaviour, original-object or whole-library grade
can be inferred from it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

from .object_compare import (CompareError, MAX_OBJECT, MAX_SECTIONS, MAX_SYMBOLS,
                             _read, _span, _string, candidate_window)
from .object_matching import MatchingError, select_functions


class ProbeError(ValueError):
    pass


TYPES = {3: "R_ARM_REL32", 28: "R_ARM_CALL", 42: "R_ARM_PREL31"}
MAX_RELOCATIONS = 250_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signed(value: int, bits: int) -> int:
    return (value ^ (1 << (bits - 1))) - (1 << (bits - 1))


class ArmElf:
    """Bounded metadata reader. Extended ELF numbering and RELA are unsupported."""

    def __init__(self, data: bytes):
        self.data = data
        if not 52 <= len(data) <= MAX_OBJECT or data[:7] != b"\x7fELF\x01\x01\x01":
            raise ProbeError("expected bounded little-endian ELF32")
        kind, machine, version = struct.unpack("<HHI", _span(data, 16, 8))
        if kind not in (1, 2, 3) or machine != 40 or version != 1:
            raise ProbeError("expected ARM ET_REL/ET_EXEC/ET_DYN")
        self.kind = kind
        (_, self.phoff, shoff, flags, ehsize, self.phsize, self.phnum,
         shsize, shnum, names) = struct.unpack("<4I6H", _span(data, 24, 28))
        if ehsize != 52 or flags >> 24 != 5:
            raise ProbeError("expected ARM EABI5 header")
        if shsize != 40 or not 1 <= shnum <= MAX_SECTIONS or names >= shnum:
            raise ProbeError("unsupported ELF section directory")
        self.sections = []
        for n in range(shnum):
            s = struct.unpack("<10I", _span(data, shoff + n * 40, 40))
            sec = dict(index=n, name_offset=s[0], type=s[1], flags=s[2], address=s[3],
                       offset=s[4], size=s[5], link=s[6], info=s[7], entsize=s[9])
            if sec["type"] != 8:
                _span(data, sec["offset"], sec["size"])
            self.sections.append(sec)
        strings = self.sections[names]
        if strings["type"] != 3:
            raise ProbeError("invalid section string table")
        table = _span(data, strings["offset"], strings["size"])
        for sec in self.sections:
            sec["name"] = _string(table, sec["name_offset"])
        self.tables = {}

    def symbols(self, index: int) -> list[dict]:
        if index in self.tables:
            return self.tables[index]
        if not 0 <= index < len(self.sections):
            raise ProbeError("invalid relocation symbol table index")
        sec = self.sections[index]
        if (sec["type"] != 2 or sec["entsize"] != 16 or sec["size"] % 16
                or sec["size"] // 16 > MAX_SYMBOLS):
            raise ProbeError("invalid symbol table")
        if not 0 <= sec["link"] < len(self.sections):
            raise ProbeError("invalid symbol string table index")
        strings = self.sections[sec["link"]]
        if strings["type"] != 3:
            raise ProbeError("invalid symbol string table")
        names = _span(self.data, strings["offset"], strings["size"])
        result = []
        for offset in range(sec["offset"], sec["offset"] + sec["size"], 16):
            name, value, size, info, _, section = struct.unpack(
                "<IIIBBH", _span(self.data, offset, 16))
            result.append(dict(name=_string(names, name), value=value, size=size,
                               type=info & 15, section=section))
        self.tables[index] = result
        return result

    def function(self, name: str) -> tuple[dict, dict]:
        found = []
        for sec in self.sections:
            if sec["type"] == 2:
                found.extend(s for s in self.symbols(sec["index"])
                             if s["name"] == name and s["type"] == 2 and s["section"])
        if len(found) != 1 or not 0 < found[0]["section"] < len(self.sections):
            raise ProbeError("candidate function absent or ambiguous")
        symbol = found[0]
        return symbol, self.sections[symbol["section"]]

    def mapped_offset(self, address: int, size: int) -> int:
        if self.kind == 1 or self.phsize != 32 or not 0 < self.phnum <= 4096:
            raise ProbeError("linked window requires file-backed PT_LOAD mapping")
        found = []
        for n in range(self.phnum):
            kind, offset, va, _, filesz, memsz, flags, _ = struct.unpack(
                "<8I", _span(self.data, self.phoff + n * 32, 32))
            if kind == 1:
                if filesz > memsz or va + memsz > 1 << 32:
                    raise ProbeError("invalid PT_LOAD extent")
                _span(self.data, offset, filesz)
                if va <= address and address + size <= va + filesz:
                    if not flags & 1:
                        raise ProbeError("function PT_LOAD is not executable")
                    found.append(offset + address - va)
        if len(found) != 1:
            raise ProbeError("linked function has no unique PT_LOAD mapping")
        return found[0]


def object_relocations(data: bytes, symbol: str) -> tuple[dict, list[dict], list[dict]]:
    window = candidate_window(data, symbol)
    elf = ArmElf(data)
    fn, section = elf.function(symbol)
    if window["thumb"] or fn["value"] % 4 or fn["size"] % 4:
        raise ProbeError("only aligned A32 candidate functions are supported")
    start, end = fn["value"], fn["value"] + fn["size"]
    inside, outside = [], []
    count = 0
    for sec in elf.sections:
        if sec["type"] not in (4, 9):
            continue
        if sec["type"] == 4:
            raise ProbeError("RELA is unsupported; implicit ARM REL addends required")
        if (sec["entsize"] != 8 or sec["size"] % 8
                or not 0 < sec["info"] < len(elf.sections)):
            raise ProbeError("invalid REL table or target section")
        count += sec["size"] // 8
        if count > MAX_RELOCATIONS:
            raise ProbeError("too many relocations")
        symbols = elf.symbols(sec["link"])
        target = elf.sections[sec["info"]]
        for pos in range(sec["offset"], sec["offset"] + sec["size"], 8):
            offset, info = struct.unpack("<II", _span(data, pos, 8))
            index, kind = info >> 8, info & 255
            if index >= len(symbols):
                raise ProbeError("invalid relocation symbol index")
            if offset % 4 or offset + 4 > target["size"]:
                raise ProbeError("relocation offset outside target or unaligned")
            sym = symbols[index]
            symbol_name = sym["name"]
            if not symbol_name and sym["type"] == 3 and 0 < sym["section"] < len(elf.sections):
                symbol_name = elf.sections[sym["section"]]["name"]
            row = dict(section=target["name"], section_offset=offset,
                       type=TYPES.get(kind, f"UNSUPPORTED_{kind}"),
                       symbol=symbol_name)
            if target["index"] == section["index"] and start <= offset < end:
                if offset + 4 > end:
                    raise ProbeError("relocation crosses function boundary")
                if kind not in (3, 28):
                    raise ProbeError("unsupported function relocation")
                if symbols[index]["section"] != 0 or not symbols[index]["name"]:
                    raise ProbeError("only explicitly mapped external symbols supported")
                row["offset"] = offset - start
                row["symbol_type"] = symbols[index]["type"]
                inside.append(row)
            else:
                outside.append(row)
    offsets = [r["offset"] for r in inside]
    if len(offsets) != len(set(offsets)):
        raise ProbeError("overlapping relocation sites")
    return window, sorted(inside, key=lambda r: r["offset"]), outside


def relocate(code: bytes, relocations: list[dict], address: int,
             targets: dict[str, int]) -> tuple[bytes, list[dict]]:
    """AAELF32 REL: CALL uses sign-extended imm24<<2, REL32 signed word.

    CALL X=S+A-P. The BL pipeline bias is already in its implicit addend
    (normally -8); do not subtract another 8. REL32 encodes ((S+A)|T)-P mod
    2**32 without an overflow check. For STT_FUNC, the low symbol bit supplies
    T and is removed from S. Thumb CALL/interworking is deliberately rejected.
    """
    if type(address) is not int or address % 4 or not 0 <= address < 1 << 32:
        raise ProbeError("invalid A32 link address")
    if address + len(code) > 1 << 32:
        raise ProbeError("function address overflow")
    if set(targets) != {r["symbol"] for r in relocations}:
        raise ProbeError("unresolved symbol or unused target mapping")
    for value in targets.values():
        if type(value) is not int or not 0 <= value < 1 << 32:
            raise ProbeError("symbol address outside ELF32")
    result, rows, occupied = bytearray(code), [], set()
    for r in relocations:
        offset = r["offset"]
        if type(offset) is not int or offset % 4 or not 0 <= offset <= len(code) - 4:
            raise ProbeError("relocation offset outside function or unaligned")
        if offset in occupied:
            raise ProbeError("overlapping relocation sites")
        occupied.add(offset)
        word = struct.unpack_from("<I", code, offset)[0]
        place, target = address + offset, targets[r["symbol"]]
        if r["type"] == "R_ARM_CALL":
            if word >> 28 == 15 or word & 0x0f000000 != 0x0b000000:
                raise ProbeError("R_ARM_CALL requires A32 BL; BLX is unsupported")
            if target & 1:
                raise ProbeError("Thumb CALL target requires unsupported interworking")
            if target % 4:
                raise ProbeError("unaligned ARM CALL target")
            addend = signed((word & 0xffffff) << 2, 26)
            value = target + addend - place
            if value % 4 or not -(1 << 25) <= value < 1 << 25:
                raise ProbeError("CALL overflow requires unsupported veneer")
            encoded = (word & 0xff000000) | ((value >> 2) & 0xffffff)
        elif r["type"] == "R_ARM_REL32":
            addend = signed(word, 32)
            thumb = (target & 1) if r.get("symbol_type") == 2 else 0
            base = target & ~1 if r.get("symbol_type") == 2 else target
            value = ((base + addend) | thumb) - place
            encoded = value & 0xffffffff
        else:
            raise ProbeError("unsupported function relocation")
        struct.pack_into("<I", result, offset, encoded)
        rows.append(dict(r, addend=addend, place=place, symbol_value=target,
                         expression_value=value))
    return bytes(result), rows


def linked_window(data: bytes, address: int) -> tuple[bytes, dict]:
    elf = ArmElf(data)
    if elf.kind not in (2, 3):
        raise ProbeError("linked input must be ET_EXEC or ET_DYN")
    rows, _ = select_functions(data, addresses={address}, limit=1)
    if len(rows) != 1:
        raise ProbeError("no unique independently bounded linked function")
    row = rows[0]
    if row["thumb"] or address % 4:
        raise ProbeError("Thumb linked function is unsupported")
    offset = elf.mapped_offset(address, row["size"])
    if offset != row["file_offset"]:
        raise ProbeError("section and PT_LOAD function mappings disagree")
    return _span(data, offset, row["size"]), row


def comparison(a: bytes, b: bytes, equal_status: str, unequal_status: str) -> dict:
    different = [n for n in range(max(len(a), len(b)))
                 if a[n:n + 1] != b[n:n + 1]]
    return dict(status=unequal_status if different else equal_status,
                equal=not different, compared_bytes=max(len(a), len(b)),
                differing_byte_count=len(different), differing_offsets=different,
                expected_sha256=sha256(a), actual_sha256=sha256(b))


def probe(reference: bytes, obj: bytes, *, address: int, symbol: str,
          targets: dict[str, int], expected_reference_sha256: str,
          expected_window_sha256: str, linked: bytes | None = None) -> dict:
    if sha256(reference) != expected_reference_sha256:
        raise ProbeError("reference SHA-256 mismatch")
    original, row = linked_window(reference, address)
    if sha256(original) != expected_window_sha256:
        raise ProbeError("reference window SHA-256 mismatch")
    window, relocations, outside = object_relocations(obj, symbol)
    if len(window["bytes"]) != len(original):
        raise ProbeError("candidate/reference size mismatch")
    resolved, applied = relocate(window["bytes"], relocations, address, targets)
    report = dict(schema="nova3.relocation-probe/1", address=address, size=len(original),
                  reference_sha256=sha256(reference), reference_file_offset=row["file_offset"],
                  reference_window_sha256=sha256(original), candidate_object_sha256=sha256(obj),
                  candidate_symbol=symbol, candidate_thumb=False,
                  raw=comparison(original, window["bytes"], "RAW_EQUAL", "RAW_MISMATCH"),
                  resolved=comparison(original, resolved, "RESOLVED_WINDOW_EQUAL",
                                      "RESOLVED_WINDOW_MISMATCH"),
                  relocations=applied, outside_function_relocations=outside,
                  policy="All bytes compared; no masking. Explicit candidate symbol mappings; "
                         "direct A32 CALL only. Outside-window relocations not applied.",
                  original_object_match="NOT_AVAILABLE", historical_compiler_verified=False,
                  whole_so_sha_match=False, behavior_verified=False,
                  original_prelink_relocations_recovered=False,
                  relocated_linked_window="NOT_CHECKED")
    if linked is not None:
        code, mapped = linked_window(linked, address)
        consistent = comparison(resolved, code, "LINKER_AGREES", "LINKER_DISAGREES")
        actual = comparison(original, code, "RELOCATED_LINKED_WINDOW_MATCH",
                            "RELOCATED_LINKED_WINDOW_MISMATCH")
        report.update(linked_sha256=sha256(linked), linked_file_offset=mapped["file_offset"],
                      linker_vs_resolved=consistent, linked_vs_reference=actual)
        report["relocated_linked_window"] = (
            actual["status"] if consistent["equal"] else "LINKER_DISAGREES")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--object", type=Path, required=True)
    parser.add_argument("--linked", type=Path)
    parser.add_argument("--address", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--target", action="append", default=[], metavar="SYMBOL=VALUE")
    parser.add_argument("--expected-reference-sha256", required=True)
    parser.add_argument("--expected-window-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        targets = {}
        for item in args.target:
            name, value = item.split("=", 1)
            if not name or name in targets:
                raise ProbeError("empty or duplicate target")
            targets[name] = int(value, 0)
        result = probe(_read(args.reference), _read(args.object), address=args.address,
                       symbol=args.symbol, targets=targets,
                       expected_reference_sha256=args.expected_reference_sha256,
                       expected_window_sha256=args.expected_window_sha256,
                       linked=_read(args.linked) if args.linked else None)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ProbeError, CompareError, MatchingError, ValueError, OSError):
        # Avoid publishing paths or ELF content through exception text.
        print("relocation probe rejected input; check ELF, hashes, mapping and supported relocations",
              file=sys.stderr)
        return 2
    return 0  # The diagnostic ran; this is not a match gate.


if __name__ == "__main__":
    raise SystemExit(main())
