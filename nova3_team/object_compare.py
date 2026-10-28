"""Offline ARM32 relocatable-object versus linked-ELF function-window checks.

Comparing linked bytes with an ET_REL object is *not* restoration of the
original .o.  Even equality proves only the selected code window; relocation
and ABI/data semantics require separate independent evidence.  Never execute
original game code or emit its instructions to output JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys

from .object_matching import MatchingError, select_functions


SCHEMA = "nova3.object-window-comparison/1"
MAX_OBJECT = 64 * 1024 * 1024
MAX_SECTIONS = 4096
MAX_SYMBOLS = 250_000


class CompareError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _span(data: bytes, offset: int, length: int) -> bytes:
    if type(offset) is not int or type(length) is not int or offset < 0 or length < 0:
        raise CompareError("invalid ELF span")
    if offset > len(data) or length > len(data) - offset:
        raise CompareError("ELF span outside file")
    return data[offset:offset + length]


def _word(data: bytes, offset: int) -> int:
    return struct.unpack("<I", _span(data, offset, 4))[0]


def _string(table: bytes, offset: int) -> str:
    if type(offset) is not int or not 0 <= offset < len(table):
        raise CompareError("invalid ELF string offset")
    end = table.find(b"\0", offset)
    if end < 0 or end - offset > 4096:
        raise CompareError("unterminated or oversized ELF symbol")
    try:
        return table[offset:end].decode("utf-8")
    except UnicodeDecodeError:
        raise CompareError("invalid symbol encoding") from None


def candidate_window(data: bytes, symbol: str) -> dict:
    """Parse exactly one defined STT_FUNC from ARM ELF32 ET_REL.

    Each relocation targeting the function's section is detected.  No attempt
    is made to recover its original link-time addend or resolved target.
    """
    if not isinstance(symbol, str) or not symbol or len(symbol) > 4096:
        raise CompareError("an exact candidate function symbol is required")
    if symbol.startswith("nova3_fn_"):
        raise CompareError("synthetic linked-byte assembly is not a source candidate")
    if len(data) > MAX_OBJECT or len(data) < 52:
        raise CompareError("invalid or oversized object file")
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise CompareError("candidate is not little-endian ELF32")
    kind, machine, version = struct.unpack("<HHI", _span(data, 16, 8))
    if (kind, machine, version) != (1, 40, 1):
        raise CompareError("candidate must be ARM32 ET_REL, not a linked binary")
    shoff = _word(data, 32)
    shentsize, shnum, shstrndx = struct.unpack("<HHH", _span(data, 46, 6))
    if shentsize != 40 or not 1 <= shnum <= MAX_SECTIONS or shstrndx >= shnum:
        raise CompareError("unsupported ELF section directory")
    sections = []
    for i in range(shnum):
        parts = struct.unpack("<IIIIIIIIII", _span(data, shoff + 40 * i, 40))
        sec = dict(index=i, name_offset=parts[0], type=parts[1], flags=parts[2],
                   offset=parts[4], size=parts[5], link=parts[6], info=parts[7],
                   entsize=parts[9])
        if sec["type"] != 8:
            _span(data, sec["offset"], sec["size"])
        sections.append(sec)
    names = sections[shstrndx]
    if names["type"] != 3:
        raise CompareError("missing section-name string table")
    name_data = _span(data, names["offset"], names["size"])
    for sec in sections:
        sec["name"] = _string(name_data, sec["name_offset"])
    candidates = []
    for table in sections:
        if table["type"] != 2:
            continue
        if table["entsize"] != 16 or table["size"] % 16:
            raise CompareError("malformed ELF symbol table")
        if table["link"] >= shnum or sections[table["link"]]["type"] != 3:
            raise CompareError("missing symbol-name string table")
        strings = sections[table["link"]]
        string_data = _span(data, strings["offset"], strings["size"])
        if table["size"] // 16 > MAX_SYMBOLS:
            raise CompareError("oversized ELF symbol table")
        for pos in range(table["offset"], table["offset"] + table["size"], 16):
            nameoff, value, size, info, other, shndx = struct.unpack("<IIIBBH", _span(data, pos, 16))
            if info & 15 != 2 or shndx == 0:
                continue
            if _string(string_data, nameoff) != symbol:
                continue
            if shndx >= shnum or size <= 0 or size > 1 << 20:
                raise CompareError("unsafe candidate function extent")
            sec = sections[shndx]
            if sec["type"] != 1 or not sec["flags"] & 4:
                raise CompareError("candidate function outside executable PROGBITS")
            start = value & ~1
            if start > sec["size"] or size > sec["size"] - start:
                raise CompareError("candidate function extends outside section")
            relocations = []
            for reloc in sections:
                if reloc["type"] not in (4, 9) or reloc["info"] != shndx:
                    continue
                unit = 8 if reloc["type"] == 9 else 12
                if reloc["entsize"] != unit or reloc["size"] % unit:
                    raise CompareError("malformed relocations")
                for rpos in range(reloc["offset"], reloc["offset"] + reloc["size"], unit):
                    roffset = _word(data, rpos)
                    if start <= roffset < start + size:
                        relocations.append(roffset - start)
            candidates.append(dict(bytes=_span(data, sec["offset"] + start, size),
                                   size=size, thumb=bool(value & 1),
                                   section=sec["name"], relocation_offsets=sorted(relocations)))
    if len(candidates) != 1:
        raise CompareError("candidate symbol absent or ambiguous: " + symbol)
    return candidates[0]


def compare_window(original: bytes, candidate: bytes, *, address: int,
                   symbol: str, expected_reference_sha256: str | None = None) -> dict:
    """Compare real source-compiled ARM function bytes with a bounded original window."""
    original_hash = _digest(original)
    if expected_reference_sha256 is not None and original_hash != expected_reference_sha256:
        raise CompareError("reference SHA-256 mismatch")
    try:
        selected, _ = select_functions(original, addresses={address}, limit=1)
    except MatchingError as exc:
        raise CompareError("reference function cannot be independently bounded") from exc
    if len(selected) != 1:
        raise CompareError("reference address has no unique bounded function")
    row = selected[0]
    obj = candidate_window(candidate, symbol)
    code = _span(original, row["file_offset"], row["size"])
    same_mode = obj["thumb"] == row["thumb"]
    same_size = obj["size"] == row["size"]
    raw_match = same_size and same_mode and code == obj["bytes"]
    if not same_mode:
        status = "ARM_THUMB_MODE_MISMATCH"
    elif not same_size:
        status = "FUNCTION_SIZE_MISMATCH"
    elif raw_match and obj["relocation_offsets"]:
        status = "BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS"
    elif raw_match:
        status = "LINKED_FUNCTION_WINDOW_EQUAL"
    else:
        status = "FUNCTION_BYTES_DIFFERENT"
    return dict(schema=SCHEMA, kind="source-candidate-to-linked-window",
                reference_sha256=original_hash, reference_address=row["address"],
                reference_size=row["size"], reference_window_sha256=row["sha256"],
                candidate_object_sha256=_digest(candidate), candidate_symbol=symbol,
                candidate_code_sha256=_digest(obj["bytes"]), candidate_size=obj["size"],
                candidate_section=obj["section"], candidate_thumb=obj["thumb"],
                original_thumb=row["thumb"], candidate_relocation_count=len(obj["relocation_offsets"]),
                code_window_equal=raw_match, status=status,
                original_object_recovered=False, original_relocations_recovered=False,
                original_compiler_verified=False, original_so_hash_matching_verified=False,
                source_matching_verified=False,
                evidence_scope="Linked-code window only; no ABI, data, linker or original .o proof")


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_OBJECT:
            raise CompareError("nonregular or oversized input")
        data = stream.read(MAX_OBJECT + 1)
        if len(data) != st.st_size:
            raise CompareError("file size changed while reading")
        return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True, help="Local original ARM ELF")
    p.add_argument("--address", type=lambda s: int(s, 0), required=True)
    p.add_argument("--object", type=Path, required=True, help="Locally compiled ARM32 ET_REL object")
    p.add_argument("--symbol", required=True, help="Exact source-owned candidate function symbol")
    p.add_argument("--expected-reference-sha256", help="Reject a nonmatching reference edition")
    p.add_argument("--out", type=Path, required=True, help="JSON under private repository .cache")
    args = p.parse_args(argv)
    try:
        cache = (Path(__file__).resolve().parents[1] / ".cache").resolve()
        target = args.out.expanduser().resolve()
        if not target.is_relative_to(cache) or target == cache or target.suffix != ".json":
            raise CompareError("output must be JSON under repository .cache")
        report = compare_window(_read(args.reference), _read(args.object),
                                address=args.address, symbol=args.symbol,
                                expected_reference_sha256=args.expected_reference_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(report["status"] + "; ORIGINAL OBJECT AND SOURCE MATCH UNVERIFIED")
    except (OSError, CompareError, ValueError) as exc:
        print("object compare: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
