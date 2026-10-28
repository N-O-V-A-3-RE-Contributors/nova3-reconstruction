"""Offline ARM ELF linked-layout and relocation-evidence worksheet.

Dynamic relocations and segments in a linked .so are observable.  Original
translation-unit .o files, their discarded/static relocations and the exact
linker script generally are not.  This inventory never asserts otherwise.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys


SCHEMA = "nova3.linker-worksheet/1"
MAX_INPUT = 128 * 1024 * 1024
MAX_SECTIONS = 4096
MAX_RELOCATIONS = 500_000
_RELOC_TYPES = {
    0: "R_ARM_NONE", 2: "R_ARM_ABS32", 3: "R_ARM_REL32",
    10: "R_ARM_THM_CALL", 21: "R_ARM_GLOB_DAT", 22: "R_ARM_JUMP_SLOT",
    23: "R_ARM_RELATIVE", 28: "R_ARM_CALL", 29: "R_ARM_JUMP24",
    30: "R_ARM_THM_JUMP24", 42: "R_ARM_PREL31",
}
_INTERESTING = (".text", ".rodata", ".data", ".bss", ".got", ".plt",
                ".ARM.exidx", ".ARM.extab", ".init_array", ".fini_array",
                ".dynamic", ".rel", ".rela", ".gnu.hash", ".hash")


class WorksheetError(ValueError):
    pass


def _span(data: bytes, offset: int, size: int) -> bytes:
    if type(offset) is not int or type(size) is not int or offset < 0 or size < 0:
        raise WorksheetError("invalid ELF span")
    if offset > len(data) or size > len(data) - offset:
        raise WorksheetError("ELF span exceeds file")
    return data[offset:offset + size]


def _name(table: bytes, offset: int) -> str:
    if type(offset) is not int or not 0 <= offset < len(table):
        raise WorksheetError("invalid section name offset")
    end = table.find(b"\0", offset)
    if end < 0 or end - offset > 256:
        raise WorksheetError("invalid or oversized section name")
    raw = table[offset:end]
    if not re.fullmatch(rb"[\x20-\x7e]*", raw):
        raise WorksheetError("unsafe section name encoding")
    return raw.decode("ascii")


def worksheet(data: bytes) -> dict:
    """Describe only linked ELF layout/relocations, never original opcode bytes."""
    if not 52 <= len(data) <= MAX_INPUT:
        raise WorksheetError("input ELF is empty or oversized")
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise WorksheetError("expected ELF32 little-endian ARM")
    (kind, machine, version, entry, phoff, shoff, flags, ehsize,
     phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack(
         "<HHIIIIIHHHHHH", _span(data, 16, 36))
    if (kind not in (2, 3) or machine != 40 or version != 1 or
            flags >> 24 != 5 or ehsize != 52):
        raise WorksheetError("expected linked ELF32 ARM EABI5 ET_EXEC/ET_DYN")
    if (shentsize != 40 or not 1 <= shnum <= MAX_SECTIONS or shstrndx >= shnum
            or not shoff):
        raise WorksheetError("unsupported ELF section directory")
    raw_sections = [struct.unpack("<10I", _span(data, shoff + i * 40, 40))
                    for i in range(shnum)]
    if raw_sections[0][1] != 0 or raw_sections[shstrndx][1] != 3:
        raise WorksheetError("invalid ELF section-name table")
    names = _span(data, raw_sections[shstrndx][4], raw_sections[shstrndx][5])
    sections = []
    for index, values in enumerate(raw_sections):
        nameoff, typ, attrs, va, offset, size, link, info, align, entsize = values
        if typ != 8:
            _span(data, offset, size)
        if va + size > 1 << 32:
            raise WorksheetError("section virtual address overflow")
        sections.append(dict(index=index, name=_name(names, nameoff), type=typ,
                             flags=attrs, address=va, file_offset=offset,
                             size=size, alignment=align, link=link,
                             info=info, entry_size=entsize))
    segments = []
    if phnum:
        if not phoff or phentsize != 32 or phnum > MAX_SECTIONS:
            raise WorksheetError("unsupported program headers")
        for index in range(phnum):
            (typ, offset, va, paddr, filesz, memsz, attrs, align) = struct.unpack(
                "<8I", _span(data, phoff + 32 * index, 32))
            if filesz > memsz or va + memsz > 1 << 32:
                raise WorksheetError("invalid ELF program segment")
            if typ == 1:
                _span(data, offset, filesz)
                allocated = [sec["name"] for sec in sections if sec["flags"] & 2
                             and sec["size"] and va <= sec["address"]
                             and sec["address"] + sec["size"] <= va + memsz]
                segments.append(dict(index=index, virtual_address=va,
                                     file_offset=offset, file_size=filesz,
                                     memory_size=memsz, flags=attrs, alignment=align,
                                     allocated_sections=allocated))
    relocation_tables = []
    total_relocations = 0
    for sec in sections:
        if sec["type"] not in (4, 9):
            continue
        width = 12 if sec["type"] == 4 else 8
        if sec["entry_size"] != width or sec["size"] % width:
            raise WorksheetError("malformed relocation table")
        count = sec["size"] // width
        total_relocations += count
        if total_relocations > MAX_RELOCATIONS:
            raise WorksheetError("too many relocation entries")
        if sec["link"] >= len(sections) or sections[sec["link"]]["type"] not in (2, 11):
            raise WorksheetError("relocation symbol-table link is invalid")
        counts: Counter[str] = Counter()
        sample = []
        for pos in range(sec["file_offset"], sec["file_offset"] + sec["size"], width):
            roffset, info = struct.unpack("<II", _span(data, pos, 8))
            rtype = info & 0xff
            label = _RELOC_TYPES.get(rtype, "R_ARM_TYPE_%d" % rtype)
            counts[label] += 1
            if len(sample) < 16:
                sample.append(dict(virtual_offset=roffset, type=label))
        relocation_tables.append(dict(name=sec["name"], format="RELA" if width == 12 else "REL",
                                      count=count, type_counts=dict(sorted(counts.items())),
                                      sample=sample, original_object_relocations=False))
    notable = [dict(name=s["name"], address=s["address"], size=s["size"],
                    file_offset=s["file_offset"], alignment=s["alignment"], flags=s["flags"])
               for s in sections if any(s["name"] == prefix or s["name"].startswith(prefix + ".")
                                        for prefix in _INTERESTING)]
    return dict(schema=SCHEMA, reference_sha256=hashlib.sha256(data).hexdigest(),
                elf_type="ET_DYN" if kind == 3 else "ET_EXEC", e_flags=flags,
                entry_address=entry, loaded_segments=segments,
                relevant_sections=notable, relocation_tables=relocation_tables,
                total_linked_relocations=total_relocations,
                original_compiler_verified=False, original_object_files_recovered=False,
                original_prelink_relocations_recovered=False,
                original_linker_script_recovered=False, whole_so_sha256_matched=False,
                caveat="Linked relocations, address spaces and section headers are not the original per-TU .o relocation records or linker script.")


def experimental_ld(report: dict) -> str:
    """Write an unmistakably non-production hypothesis, not a linker recreation."""
    by_name = {r["name"]: r["address"] for r in report.get("relevant_sections", [])}
    # Fixed section names prevent untrusted ELF names becoming linker-language.
    targets = ((".text", "*(.text .text.*)"),
               (".rodata", "*(.rodata .rodata.*)"),
               (".ARM.exidx", "*(.ARM.exidx .ARM.exidx.*)"),
               (".data", "*(.data .data.*)"),
               (".bss", "*(.bss .bss.* COMMON)"))
    lines = ["/* NOVA3 EXPERIMENTAL LAYOUT ONLY; NOT original linker script. */",
             "/* NO ELF/ABI/relocation/full SHA match claim. DO NOT ship. */",
             "SECTIONS", "{"]
    for name, selectors in targets:
        if name in by_name:
            lines.append("  %s 0x%08x : { %s }" % (name, by_name[name], selectors))
    lines.extend(["}", ""])
    return "\n".join(lines)


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_INPUT:
            raise WorksheetError("input must be a bounded regular ELF file")
        data = stream.read(MAX_INPUT + 1)
        if len(data) != st.st_size:
            raise WorksheetError("input changed size while reading")
        return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="JSON under ignored repo .cache")
    parser.add_argument("--experimental-ld", action="store_true",
                        help="Also write NON-PRODUCTION layout hypothesis beside JSON")
    args = parser.parse_args(argv)
    try:
        cache = (Path(__file__).resolve().parents[1] / ".cache").resolve()
        out = args.out.expanduser().resolve()
        if not out.is_relative_to(cache) or out == cache or out.suffix != ".json":
            raise WorksheetError("output must be JSON under private repository .cache")
        report = worksheet(_read(args.reference))
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.experimental_ld:
            ld = out.with_suffix(".experimental.ld")
            ld.write_text(experimental_ld(report), encoding="ascii")
            report["experimental_linker_script_generated"] = True
            report["experimental_linker_script_name"] = ld.name
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("linked ELF worksheet: %d relocations; original .o/.ld/full SHA NOT RECONSTRUCTED" %
              report["total_linked_relocations"])
    except (OSError, WorksheetError, ValueError) as exc:
        print("linker worksheet: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
