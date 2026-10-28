"""Bounded offline Cyrus/Premium ELF comparison; never execute either input.

Only aggregate results go to stdout. The full deterministic report (symbol
metadata and digests, never instruction bytes or source paths) stays in .cache.
DWARF inspection uses optional, locally installed pyelftools; no installation
or network access is attempted. A present but unreadable DWARF is a blocker.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import sys
import tempfile

from .indexer import LedgerError, _check_span, _span, _string, _symbols

ROOT = Path(__file__).resolve().parents[1]
RETAIL_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
DEBUG_SHA256 = "d6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167"
MAX_BYTES = 64 * 1024 * 1024
MAX_SECTIONS = 4096
MAX_SYMBOLS = 500_000
MAX_TOTAL_NAME_CHARS = 32 * 1024 * 1024
MAX_DIES = 500_000
MAX_LINE_ROWS = 500_000
MAX_FUNCTION_BYTES = 4 * 1024 * 1024
MAX_DEBUG_BYTES = 16 * 1024 * 1024
# pyelftools eagerly decodes a line program before exposing rows. Bound its
# input as well as its output; our observed tables are under 8 KiB.
MAX_LINE_BYTES = 256 * 1024
SCHEMA = "nova3.version-compare/1"


class CompareError(ValueError):
    """Unsafe, malformed, unsupported or missing evidence; no match claim."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _union(ranges):
    result = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _intersection(left, right):
    result, i, j = [], 0, 0
    left, right = _union(left), _union(right)
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        if min(a[1], b[1]) > max(a[0], b[0]):
            result.append([max(a[0], b[0]), min(a[1], b[1])])
        if a[1] <= b[1]:
            i += 1
        else:
            j += 1
    return result


def _layout(data):
    """Preflight all bounded tables before invoking the shared symbol parser."""
    if len(data) > MAX_BYTES:
        raise CompareError("ELF exceeds 64 MiB bound")
    if _span(data, 0, 7) != b"\x7fELF\x01\x01\x01":
        raise CompareError("expected ELF32 little-endian")
    h = struct.unpack("<HHIIIIIHHHHHH", _span(data, 16, 36))
    kind, machine, version, _, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, names = h
    if (kind not in (2, 3) or machine != 40 or version != 1 or ehsize != 52
            or flags >> 24 != 5):
        raise CompareError("expected ARM ELF32 EABI5 ET_DYN/ET_EXEC")
    if not shoff or shsize != 40 or phnum == 0xffff:
        raise CompareError("missing/unsupported ELF header tables")
    first = struct.unpack("<10I", _span(data, shoff, 40))
    shnum = shnum or first[5]
    names = first[6] if names == 0xffff else names
    if not 1 <= shnum <= MAX_SECTIONS or not 0 < names < shnum:
        raise CompareError("invalid/oversize section count or name table")
    _check_span(data, shoff, shnum * shsize)
    raw = [struct.unpack("<10I", _span(data, shoff + i * 40, 40)) for i in range(shnum)]
    if raw[0][1] or raw[names][1] != 3:
        raise CompareError("invalid section zero or section-name table")
    strings = _span(data, raw[names][4], raw[names][5])
    sections, total_symbols = [], 0
    for i, s in enumerate(raw):
        name, typ, attrs, va, offset, size, link, info, align, entsize = s
        if typ not in (0, 8):
            _check_span(data, offset, size)
        if va + size > 1 << 32:
            raise CompareError("section address overflow")
        if typ in (2, 11):
            if entsize != 16 or size % 16 or link >= shnum or raw[link][1] != 3:
                raise CompareError("malformed symbol table")
            total_symbols += size // 16
        section_name = _string(strings, name)
        if len(section_name) > 256:
            raise CompareError("oversize section name")
        if section_name.startswith((".zdebug",)) or attrs & 0x800:
            raise CompareError("compressed sections are unsupported")
        if section_name.startswith(".debug") and size > MAX_DEBUG_BYTES:
            raise CompareError("oversize DWARF section")
        if section_name.startswith(".debug") and typ != 1:
            raise CompareError("DWARF section is not file-backed PROGBITS")
        if section_name == ".debug_line" and size > MAX_LINE_BYTES:
            raise CompareError("oversize DWARF line program")
        sections.append(dict(index=i, name=section_name, type=typ, flags=attrs,
                             address=va, offset=offset, size=size, link=link,
                             info=info, entry_size=entsize))
    if total_symbols > MAX_SYMBOLS:
        raise CompareError("oversize symbol inventory")
    for field in ("address", "offset"):
        last_end = -1
        executable = (s for s in sections if s["type"] == 1 and s["flags"] & 4 and s["size"])
        for section in sorted(executable, key=lambda s: s[field]):
            if section[field] < last_end:
                raise CompareError("overlapping executable sections")
            last_end = section[field] + section["size"]
    segments = []
    if phnum:
        if phsize != 32 or phnum > MAX_SECTIONS:
            raise CompareError("unsupported program headers")
        _check_span(data, phoff, phnum * phsize)
        for i in range(phnum):
            typ, offset, va, _, filesz, memsz, attrs, _ = struct.unpack(
                "<8I", _span(data, phoff + i * 32, 32))
            _check_span(data, offset, filesz)
            if filesz > memsz or va + memsz > 1 << 32:
                raise CompareError("malformed program segment")
            if typ == 1:
                segments.append(dict(offset=offset, address=va, size=filesz, flags=attrs))
    return sections, segments


def _table_evidence(data, sections):
    tables, mappings, relocations = [], defaultdict(list), []
    name_chars = 0
    for sec in sections:
        typ, size, offset = sec["type"], sec["size"], sec["offset"]
        if typ in (4, 9):
            entry_size = 12 if typ == 4 else 8
            if sec["entry_size"] != entry_size or size % entry_size:
                raise CompareError("malformed relocation table")
            if (sec["link"] >= len(sections) or sections[sec["link"]]["type"] not in (2, 11)
                    or sec["info"] >= len(sections)):
                raise CompareError("invalid relocation table links")
            if len(relocations) + size // entry_size > MAX_SYMBOLS:
                raise CompareError("relocation budget exceeded")
            symbol_count = sections[sec["link"]]["size"] // 16
            if any(struct.unpack_from("<I", data, p + 4)[0] >> 8 >= symbol_count
                   for p in range(offset, offset + size, entry_size)):
                raise CompareError("invalid relocation symbol index")
            relocations.extend(struct.unpack_from("<I", data, p)[0]
                               for p in range(offset, offset + size, entry_size))
        if typ not in (2, 11):
            continue
        strings_sec = sections[sec["link"]]
        strings = _span(data, strings_sec["offset"], strings_sec["size"])
        rows, undefined, reserved = [], 0, 0
        for pos in range(offset, offset + size, 16):
            n, value, length, info, _, shndx = struct.unpack_from("<IIIBBH", data, pos)
            name = _string(strings, n)
            name_chars += len(name)
            if name_chars > MAX_TOTAL_NAME_CHARS:
                raise CompareError("total symbol-name budget exceeded")
            if len(name) > 16384:
                raise CompareError("oversize symbol name")
            if info & 15 == 0 and name.split(".", 1)[0] in ("$a", "$t", "$d"):
                if 0 < shndx < len(sections):
                    mappings[shndx].append((value & ~1, name[1]))
            if info & 15 != 2:
                continue
            if shndx == 0:
                undefined += 1
                continue
            if shndx == 0xffff:
                raise CompareError("extended symbol section indices unsupported")
            if shndx >= 0xff00:
                reserved += 1
            elif shndx >= len(sections):
                raise CompareError("invalid symbol section index")
            rows.append(dict(name=name, raw_value=value, size=length, section_index=shndx))
        names = {r["name"] for r in rows if r["name"]}
        tables.append(dict(index=sec["index"], name=sec["name"],
                           kind="symtab" if typ == 2 else "dynsym", rows=rows,
                           summary=dict(defined_func_entries=len(rows),
                                        undefined_func_entries=undefined,
                                        reserved_func_entries=reserved,
                                        distinct_entry_addresses=len({r["raw_value"] & ~1 for r in rows}),
                                        distinct_names=len(names),
                                        distinct_mangled_names=sum(n.startswith("_Z") for n in names))))
    return tables, {k: sorted(set(v)) for k, v in mappings.items()}, sorted(relocations)


def _candidates(data, observations, mappings, segments):
    groups = defaultdict(list)
    for row in observations:
        groups[row["address"]].append(row)
    # Reject BOTH sides of every partial overlap. Equal entry aliases are one group.
    overlap = set()
    extents = sorted((addr, addr + max(r["size"] for r in rows))
                     for addr, rows in groups.items() if any(r["size"] for r in rows))
    owner, cluster_end = None, -1
    for start, end in extents:
        if start < cluster_end:
            overlap.add(owner)
            overlap.add(start)
        if end > cluster_end:
            owner, cluster_end = start, end
    result = {}
    for addr, rows in sorted(groups.items()):
        signatures = {(r["thumb"], r["size"], r["file_offset"], r["section_index"]) for r in rows}
        reasons = []
        if len(signatures) != 1:
            reasons.append("conflicting_alias_bounds_or_modes")
        thumb, size, offset, shndx = sorted(signatures)[0]
        if not size or size > MAX_FUNCTION_BYTES:
            reasons.append("zero_or_oversize_range")
        if addr in overlap:
            reasons.append("overlapping_function_ranges")
        if addr % (2 if thumb else 4) or size % (2 if thumb else 4):
            reasons.append("misaligned_range")
        marks = mappings.get(shndx, [])
        stop = bisect_right(marks, (addr, "z"))
        if stop:
            at = marks[stop-1][0]
            first = bisect_right(marks, (at-1, "z"))
            states = {mode for loc, mode in marks[first:stop]}
            if states != {"t" if thumb else "a"}:
                reasons.append("mapping_symbol_mode_conflict")
        end = bisect_right(marks, (addr + size - 1, "z"))
        if any(mode in ("a", "t") and mode != ("t" if thumb else "a")
               for loc, mode in marks[stop:end]):
            reasons.append("mixed_instruction_modes")
        loads = [s for s in segments if s["address"] <= addr
                 and addr + max(size, 1) <= s["address"] + s["size"]]
        if len(loads) != 1 or not loads[0]["flags"] & 1 or (
                loads[0]["offset"] + addr - loads[0]["address"] != offset):
            reasons.append("missing_or_ambiguous_executable_load_mapping")
        result[addr] = dict(address=addr, thumb=thumb, size=size, file_offset=offset,
                            section_index=shndx, exclusions=sorted(reasons),
                            mode_evidence="ARM EABI STT_FUNC entry bit; mapping symbols checked when present")
        if not reasons:
            result[addr]["sha256"] = digest(data[offset:offset + size])
    return result


def _dwarf(data, sections):
    sizes = {s["name"]: s["size"] for s in sections if s["name"].startswith(".debug")}
    report = dict(status="absent", section_sizes=sizes,
                  section_sha256={s["name"]: digest(_span(data, s["offset"], s["size"]))
                                  for s in sections if s["name"].startswith(".debug")},
                  compile_units=[], named_types=[],
                  function_dies=[], counts=dict(compile_units=0, dies=0, named_types=0,
                  function_dies=0, concrete_function_dies=0, line_rows=0,
                  line_unique_addresses=0, line_executable_bytes=0,
                  unique_named_types=0, named_type_tag_counts={}))
    if not sizes.get(".debug_info"):
        return report
    try:
        import elftools
        from elftools.elf.elffile import ELFFile
    except ImportError:
        raise CompareError("DWARF present: locally installed pyelftools is required") from None
    report.update(status="parsed", backend="pyelftools", backend_version=elftools.__version__)
    counts, line_ranges, line_addresses = report["counts"], [], set()
    executable = [(s["address"], s["address"] + s["size"])
                  for s in sections if s["type"] == 1 and s["flags"] & 4]
    try:
        dw = ELFFile(io.BytesIO(data)).get_dwarf_info()
        for cu in dw.iter_CUs():
            counts["compile_units"] += 1
            if counts["compile_units"] > MAX_SECTIONS:
                raise CompareError("DWARF CU budget exceeded")
            unit = dict(offset=cu.cu_offset, length=cu["unit_length"], version=cu["version"],
                        named_type_dies=0, function_dies=0, line_rows=0)
            # Deliberately omit CU names, comp_dir, producer and file tables.
            report["compile_units"].append(unit)
            for key in ("DW_AT_name", "DW_AT_producer"):
                attr = cu.get_top_DIE().attributes.get(key)
                if attr and isinstance(attr.value, bytes):
                    unit[key[6:] + "_sha256"] = digest(attr.value)
            for die in cu.iter_DIEs():
                counts["dies"] += 1
                if counts["dies"] > MAX_DIES:
                    raise CompareError("DWARF DIE budget exceeded")
                attrs = die.attributes
                name = attrs.get("DW_AT_name")
                if name and die.tag and (die.tag.endswith("_type") or die.tag == "DW_TAG_typedef"):
                    value = name.value
                    if not isinstance(value, bytes):
                        raise CompareError("unsupported DWARF name form")
                    report["named_types"].append(dict(cu_offset=cu.cu_offset, offset=die.offset,
                                                       tag=die.tag, name_sha256=digest(value)))
                    counts["named_types"] += 1
                    unit["named_type_dies"] += 1
                if die.tag == "DW_TAG_subprogram":
                    declaration = bool(attrs.get("DW_AT_declaration") and attrs["DW_AT_declaration"].value)
                    row = dict(cu_offset=cu.cu_offset, offset=die.offset, declaration=declaration)
                    for key in ("DW_AT_low_pc", "DW_AT_high_pc"):
                        if key in attrs:
                            if type(attrs[key].value) is not int or not 0 <= attrs[key].value <= 1 << 32:
                                raise CompareError("invalid ARM32 DWARF PC attribute")
                            row[key[6:]] = attrs[key].value
                            row[key[6:] + "_form"] = attrs[key].form
                    if "DW_AT_ranges" in attrs:
                        row["has_ranges"] = True
                    linkage = attrs.get("DW_AT_linkage_name") or attrs.get("DW_AT_MIPS_linkage_name")
                    if linkage and isinstance(linkage.value, bytes):
                        row["linkage_name_sha256"] = digest(linkage.value)
                    if name and isinstance(name.value, bytes):
                        row["name_sha256"] = digest(name.value)
                    report["function_dies"].append(row)
                    counts["function_dies"] += 1
                    unit["function_dies"] += 1
                    if not declaration and ("low_pc" in row or row.get("has_ranges")):
                        counts["concrete_function_dies"] += 1
            program = dw.line_program_for_CU(cu)
            previous = None
            if program:
                for entry in program.get_entries():
                    if not entry.state:
                        continue
                    counts["line_rows"] += 1
                    unit["line_rows"] += 1
                    if counts["line_rows"] > MAX_LINE_ROWS:
                        raise CompareError("DWARF line row budget exceeded")
                    state = entry.state
                    address = state.address & ~1
                    if previous is not None:
                        if address < previous:
                            raise CompareError("nonmonotonic DWARF line sequence")
                        line_ranges.append((previous, address))
                    if not state.end_sequence:
                        line_addresses.add(address)
                    previous = None if state.end_sequence else address
                if previous is not None:
                    raise CompareError("unterminated DWARF line sequence")
        counts["line_unique_addresses"] = len(line_addresses)
        counts["unique_named_types"] = len({t["name_sha256"] for t in report["named_types"]})
        counts["named_type_tag_counts"] = dict(sorted(Counter(t["tag"] for t in report["named_types"]).items()))
        report["line_executable_ranges"] = _intersection(line_ranges, executable)
        counts["line_executable_bytes"] = sum(b - a for a, b in report["line_executable_ranges"])
    except CompareError:
        raise
    except Exception:
        # Backend exceptions may contain private debug strings/paths; never echo.
        raise CompareError("DWARF parse failed; comparison blocked") from None
    return report


def inspect(data: bytes, expected_sha256: str) -> dict:
    if len(data) > MAX_BYTES:
        raise CompareError("ELF exceeds 64 MiB bound")
    if digest(data) != expected_sha256:
        raise CompareError("SHA-256 mismatch")
    try:
        sections, segments = _layout(data)
        if len({s["name"] for s in sections}) != len(sections):
            raise CompareError("duplicate section names are unsupported")
        tables, mappings, relocations = _table_evidence(data, sections)
        # Validate cumulative string expansion before the shared parser decodes
        # names again. Repeated offsets must not expand a small strtab unboundedly.
        metadata, observations, _ = _symbols(data)
        if sum(t["kind"] == "symtab" for t in tables) != 1:
            raise CompareError("exactly one static symbol table required")
        candidates = _candidates(data, observations, mappings, segments)
        for row in candidates.values():
            row["relocation_entries_in_range"] = (bisect_right(relocations, row["address"] + row["size"] - 1)
                                                    - bisect_right(relocations, row["address"] - 1))
        names = defaultdict(set)
        for table in tables:
            if table["kind"] == "symtab":
                for row in table["rows"]:
                    if row["name"]:
                        names[row["name"]].add((row["raw_value"], row["size"], row["section_index"]))
        dwarf = _dwarf(data, sections)
        covered = dwarf.get("line_executable_ranges", [])
        ends = [end for _, end in covered]
        def line_covers_entry(address):
            i = bisect_right(ends, address)
            return i < len(covered) and covered[i][0] <= address < covered[i][1]
        dwarf["counts"]["symbol_entries_with_line_coverage"] = sum(
            line_covers_entry(address) for address in candidates)
        dwarf["counts"]["function_die_low_pc_at_symbol_entry"] = sum(
            (row.get("low_pc", -1) & ~1) in candidates
            for row in dwarf["function_dies"] if not row["declaration"])
        comments = b"\0".join(_span(data, s["offset"], s["size"])
                                for s in sections if s["name"] == ".comment")
        return dict(sha256=expected_sha256, size=len(data), architecture=metadata,
                    compiler_evidence=dict(gcc_comment_versions=sorted(set(
                        v.decode("ascii") for v in re.findall(rb"GCC:[^\x00\n]*?([0-9]+\.[0-9]+\.[0-9]+)", comments))),
                        flags="UNKNOWN", original_source_sha256="UNKNOWN"),
                    tables=tables, candidates=candidates,
                    names={n: sorted(v) for n, v in sorted(names.items())},
                    summary=dict(discovered_executable_entry_addresses=len(candidates),
                                 safe_candidate_ranges=sum(not c["exclusions"] for c in candidates.values()),
                                 relocation_entries=len(relocations),
                                 executable_section_bytes=sum(b-a for a,b in _union(
                                     (s["address"], s["address"]+s["size"]) for s in sections
                                     if s["type"] == 1 and s["flags"] & 4))),
                    dwarf=dwarf)
    except LedgerError:
        raise CompareError("malformed ELF symbol/section evidence") from None


def compare(retail: bytes, debug: bytes, *, retail_sha256=RETAIL_SHA256,
            debug_sha256=DEBUG_SHA256) -> dict:
    """Pure API; explicit alternate hashes are for synthetic tests, never CLI flags."""
    r, d = inspect(retail, retail_sha256), inspect(debug, debug_sha256)
    rn, dn = set(r["names"]), set(d["names"])
    shared = rn & dn
    # A same-name definition is not a safe donor when aliases imply a split or
    # merge between the two binaries. Reject the entire non-bijective mapping.
    forward, reverse = defaultdict(set), defaultdict(set)
    for name in sorted(shared):
        if len(r["names"][name]) == len(d["names"][name]) == 1:
            a, b = r["names"][name][0], d["names"][name][0]
            forward[a[0] & ~1].add(b[0] & ~1)
            reverse[b[0] & ~1].add(a[0] & ~1)
    records, unique_pairs = [], {}
    for name in sorted(shared):
        row = dict(name=name)
        if len(r["names"][name]) != 1 or len(d["names"][name]) != 1:
            row["status"] = "ambiguous_name"
        else:
            rr, dr = r["names"][name][0], d["names"][name][0]
            a, b = r["candidates"].get(rr[0] & ~1), d["candidates"].get(dr[0] & ~1)
            row.update(retail_raw_value=rr[0], debug_raw_value=dr[0],
                       retail_size=rr[1], debug_size=dr[1], size_delta=dr[1]-rr[1])
            def corresponds(candidate, symbol):
                return candidate and (candidate["address"] | int(candidate["thumb"]),
                                      candidate["size"], candidate["section_index"]) == tuple(symbol)
            if not a or not b:
                row["status"] = "unmapped_range"
            elif a["exclusions"] or b["exclusions"]:
                row.update(status="excluded_range", retail_exclusions=a["exclusions"],
                           debug_exclusions=b["exclusions"])
            elif not corresponds(a, rr) or not corresponds(b, dr):
                row["status"] = "unmapped_range"
            elif len(forward[a["address"]]) != 1 or len(reverse[b["address"]]) != 1:
                row["status"] = "ambiguous_alias_mapping"
            elif a["thumb"] != b["thumb"]:
                row["status"] = "mode_mismatch"
            else:
                row.update(status="exact_bytes" if a["sha256"] == b["sha256"] else "different_bytes",
                           retail_sha256=a["sha256"], debug_sha256=b["sha256"])
                unique_pairs[(a["address"], b["address"])] = row["status"]
        records.append(row)
    mangled_r, mangled_d = {n for n in rn if n.startswith("_Z")}, {n for n in dn if n.startswith("_Z")}
    statuses = Counter(row["status"] for row in records)
    mangled_statuses = Counter(row["status"] for row in records if row["name"].startswith("_Z"))
    rd, dd = r["dwarf"]["section_sha256"], d["dwarf"]["section_sha256"]
    shared_debug_sections = sorted(rd.keys() & dd.keys())
    rt = {(t["tag"], t["name_sha256"]) for t in r["dwarf"]["named_types"]}
    dt = {(t["tag"], t["name_sha256"]) for t in d["dwarf"]["named_types"]}
    report = dict(schema=SCHEMA, tool_version="2", tool_source_sha256=digest(Path(__file__).read_bytes()),
                  retail=r, debug=d, comparison=dict(
        summary=dict(shared_exact_names=len(shared), retail_only_names=len(rn-dn),
                     debug_only_names=len(dn-rn), shared_mangled_names=len(mangled_r & mangled_d),
                     retail_only_mangled_names=len(mangled_r-mangled_d),
                     debug_only_mangled_names=len(mangled_d-mangled_r),
                     unambiguous_names=sum(x["status"] != "ambiguous_name" for x in records),
                     size_different_names=sum(x.get("size_delta", 0) != 0 for x in records),
                     size_different_mangled_names=sum(x.get("size_delta", 0) != 0 for x in records
                                                      if x["name"].startswith("_Z")),
                     name_status_counts=dict(sorted(statuses.items())),
                     mangled_name_status_counts=dict(sorted(mangled_statuses.items())),
                     alias_deduplicated_pair_counts=dict(sorted(Counter(unique_pairs.values()).items()))),
        retail_only_names=sorted(rn-dn), debug_only_names=sorted(dn-rn), functions=records),
        dwarf_comparison=dict(shared_sections=len(shared_debug_sections),
                              identical_sections=[n for n in shared_debug_sections if rd[n] == dd[n]],
                              different_sections=[n for n in shared_debug_sections if rd[n] != dd[n]],
                              retail_only_sections=sorted(rd.keys() - dd.keys()),
                              debug_only_sections=sorted(dd.keys() - rd.keys()),
                              shared_named_type_tag_name_pairs=len(rt & dt),
                              retail_only_named_type_tag_name_pairs=len(rt-dt),
                              debug_only_named_type_tag_name_pairs=len(dt-rt)),
        recommendation="retail-first with debug annotation donor",
        recommendation_basis=[
            "Premium is the designated behavior target; exact-name donors need per-function ABI/semantic review.",
            "Debug function DIEs: %d; symbol entry addresses: %d; entries with line coverage: %d." % (
                d["dwarf"]["counts"]["function_dies"], len(d["candidates"]),
                d["dwarf"]["counts"]["symbol_entries_with_line_coverage"]),
            "Identical shared DWARF sections: %d/%d; debug-first coverage advantage is not established." % (
                sum(rd[n] == dd[n] for n in shared_debug_sections), len(shared_debug_sections))],
        caveats=["Exact linked byte-window equality is a lower bound; not gameplay or source equivalence.",
                 "Relocations, address layout and compiler flags can cause false negatives; bytes are not normalized.",
                 "Identical windows can reference different external code/data; dependencies need independent review.",
                 "STT_FUNC bounds/entry bits are ELF evidence, not independent instruction discovery.",
                 "Aliases deduplicate pairs; repeated same-name distinct definitions are ambiguous.",
                 "DWARF counts include runtime/library CUs and declarations; types are not automatically transferable.",
                 "Chronology, original source hashes, full compiler identity and flags are UNKNOWN; source reconstruction NOT MATCHED."])
    # A debug-first decision needs semantic and ABI evidence beyond this inventory.
    # Keep the actual target authoritative; donor annotations require per-function review.
    return report


def _read(path):
    # O_NONBLOCK prevents a FIFO from hanging before the fstat regular-file check.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise CompareError("input is nonregular or oversize")
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or len(data) != info.st_size:
            raise CompareError("input changed size or exceeded bound")
        return data


def _output_path(path):
    cache = ROOT / ".cache"
    if cache.is_symlink():
        raise CompareError("cache root must not be a symlink")
    out = path.expanduser().resolve()
    if not out.is_relative_to(cache) or out == cache or out.suffix != ".json":
        raise CompareError("output must be a JSON file under repository .cache")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retail", type=Path, required=True)
    parser.add_argument("--debug", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / ".cache/cyrus-vs-retail.json")
    args = parser.parse_args(argv)
    old_handler = None
    old_memory_limit = None
    temp_path = None
    try:
        out = _output_path(args.out)
        for source in (args.retail, args.debug):
            if source.resolve() == out or (out.exists() and os.path.samefile(source, out)):
                raise CompareError("output must not alias an input")
        if hasattr(signal, "SIGALRM"):
            def expired(*_):
                raise CompareError("120-second comparison budget exceeded")
            old_handler = signal.signal(signal.SIGALRM, expired)
            signal.alarm(120)
        if sys.platform.startswith("linux"):
            import resource
            old_memory_limit = resource.getrlimit(resource.RLIMIT_AS)
            soft, hard = old_memory_limit
            cap = 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (min(cap, soft) if soft >= 0 else cap, hard))
        report = compare(_read(args.retail), _read(args.debug))
        serialized = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".version-compare-", dir=out.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(temp_path, out)
        print(json.dumps(dict(status="complete", summary=report["comparison"]["summary"],
                              dwarf={k: report[k]["dwarf"]["counts"] for k in ("retail", "debug")},
                              recommendation=report["recommendation"]), sort_keys=True))
        return 0
    except (CompareError, OSError, MemoryError) as exc:
        message = str(exc) if isinstance(exc, CompareError) else "local input/output unavailable"
        print("BLOCKED: " + message, file=sys.stderr)
        return 2
    finally:
        if old_memory_limit is not None:
            resource.setrlimit(resource.RLIMIT_AS, old_memory_limit)
        if old_handler is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


if __name__ == "__main__":
    sys.exit(main())
