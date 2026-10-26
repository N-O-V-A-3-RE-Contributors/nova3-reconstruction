"""Read-only ARM ELF function-symbol ledger, schema ``nova3.function-ledger/1``.

Run ``python -m nova3_team.indexer BINARY [--ghidra-export EXPORT.json ...]``.
JSON goes to stdout; inputs are never changed. No third-party packages are needed.
Only ELF32 EM_ARM ET_EXEC/ET_DYN defined STT_FUNC symbols in executable,
file-backed sections are inventoried. Stripped functions, DWARF-only functions,
PLT entries and instruction discovery are outside this exporter's scope.

Addresses/sizes are integers in ELF virtual-address space (not file offsets).
Thumb entry bits are removed from addresses and retained in ``thumb`` and each
symbol's ``raw_value``. IDs are SHA256:8-digit-address:arm-or-thumb; same-entry,
same-mode aliases share an ID. ``name`` is the lexically first nonempty name;
``aliases`` contains the other names. ``size`` is the largest observed symbol
size, never inferred from adjacent symbols. Conflicting sizes are flagged and
all symbol observations retained. Zero-sized symbols remain zero-sized.

Optional tools/ghidra/export_batch.py records contribute reported outgoing calls
only after hash, architecture, address, mode, size and rebase checks. ``calls``
is null without a record and a (possibly empty) list with a record. Call sites
and RAM destinations are translated by subtracting ghidra_image_base; external
destinations retain their location string and have a null target_address.
Byte-mapping flags are reports, not independent verification. Missing/indirect
calls may remain undiscovered even with an export. No pseudocode, instructions,
binary bytes, local paths or incoming non-call references enter the ledger.

Ordering is deterministic. Records remain INVENTORIED; inventory_complete is
always false. This format makes no reconstruction or machine-code match claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from collections.abc import Iterable, Mapping


SCHEMA = "nova3.function-ledger/1"
EXPORTER_VERSION = "1"
_U32 = (1 << 32) - 1


class LedgerError(ValueError):
    """Malformed/unsupported ELF or inconsistent supplemental evidence."""


def _check_span(data: bytes, offset: int, size: int) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise LedgerError("ELF range extends beyond file")


def _span(data: bytes, offset: int, size: int) -> bytes:
    _check_span(data, offset, size)
    return data[offset:offset + size]


def _string(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        raise LedgerError("ELF string offset out of range")
    end = data.find(b"\0", offset)
    if end < 0:
        raise LedgerError("unterminated ELF string")
    # Preserve distinct non-UTF8 symbol names through JSON round trips.
    return data[offset:end].decode("utf-8", errors="surrogateescape")


def _symbols(data: bytes) -> tuple[dict, list[dict], dict]:
    ident = _span(data, 0, 16)
    if ident[:4] != b"\x7fELF" or ident[4] != 1 or ident[5] not in (1, 2) or ident[6] != 1:
        raise LedgerError("expected ELF32 with a supported byte order/version")
    endian = "<" if ident[5] == 1 else ">"
    header = struct.unpack(endian + "HHIIIIIHHHHHH", _span(data, 16, 36))
    kind, machine, version, _, _, shoff, flags, ehsize, _, _, entsize, count, names = header
    if machine != 40 or kind not in (2, 3) or version != 1 or ehsize != 52:
        raise LedgerError("expected ARM ELF32 ET_EXEC/ET_DYN (ET_REL is not supported)")
    metadata = {"architecture": "ARM", "elf_class": 32,
                "endianness": "little" if ident[5] == 1 else "big",
                "elf_type": "ET_EXEC" if kind == 2 else "ET_DYN", "flags": flags}
    counts = {"symbol_tables": 0, "defined_function_symbols": 0,
              "undefined_function_symbols": 0, "excluded_function_symbols": 0}
    if shoff == 0:
        if count or names:
            raise LedgerError("section count/names without a section table")
        return metadata, [], counts
    if entsize < 40:
        raise LedgerError("invalid ELF section header size")

    def section(index: int) -> tuple:
        return struct.unpack(endian + "10I", _span(data, shoff + index * entsize, 40))

    first = section(0)
    count = count or first[5]  # ELF extended section count.
    names = first[6] if names == 0xffff else names
    if count == 0:
        raise LedgerError("empty section table at a nonzero offset")
    _check_span(data, shoff, count * entsize)
    sections = [section(i) for i in range(count)]
    if sections[0][1] != 0:
        raise LedgerError("section zero must be SHT_NULL")
    if names >= count or (names and sections[names][1] != 3):
        raise LedgerError("invalid section-name string table")
    name_data = _span(data, sections[names][4], sections[names][5]) if names else b"\0"
    observations = []
    for table_index, table in enumerate(sections):
        if table[1] not in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
            continue
        _, _, _, _, offset, size, link, _, _, entry_size = table
        if entry_size < 16 or size % entry_size:
            raise LedgerError("invalid ELF symbol table entry size")
        if link >= count or sections[link][1] != 3:
            raise LedgerError("symbol table does not link to a string table")
        _check_span(data, offset, size)
        strings = _span(data, sections[link][4], sections[link][5])
        table_name = _string(name_data, table[0])
        counts["symbol_tables"] += 1
        for index in range(size // entry_size):
            name, value, length, info, _, section_index = struct.unpack(
                endian + "IIIBBH", _span(data, offset + index * entry_size, 16))
            if info & 15 != 2:  # STT_FUNC only; do not guess functions from labels.
                continue
            if section_index == 0:
                counts["undefined_function_symbols"] += 1
                continue
            if section_index == 0xffff:
                raise LedgerError("SHN_XINDEX function symbols are not supported")
            if section_index >= 0xff00:  # ABS/COMMON/reserved: no mapped code evidence.
                counts["excluded_function_symbols"] += 1
                continue
            if section_index >= count:
                raise LedgerError("function symbol section index out of range")
            sec = sections[section_index]
            if not sec[2] & 4 or sec[1] != 1:  # SHF_EXECINSTR, SHT_PROGBITS
                counts["excluded_function_symbols"] += 1
                continue
            address = value & ~1
            relative = address - sec[3]
            if relative < 0 or relative >= sec[5] or length > sec[5] - relative:
                raise LedgerError("function symbol extends outside its executable section")
            if sec[3] + sec[5] > 1 << 32:
                raise LedgerError("executable section overflows ARM32 address space")
            _check_span(data, sec[4], sec[5])
            observations.append({"address": address, "thumb": bool(value & 1),
                                 "name": _string(strings, name), "size": length,
                                 "raw_value": value, "file_offset": sec[4] + relative,
                                 "section_index": section_index, "table": table_name,
                                 "table_index": table_index, "symbol_index": index})
            counts["defined_function_symbols"] += 1
    return metadata, observations, counts


def _integer(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _U32:
        raise LedgerError(f"{field} must be an unsigned ARM32 integer")
    return value


def _location(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        raise LedgerError("Ghidra reference location must be a nonempty string")
    # Ghidra strings are hexadecimal, including strings containing digits only.
    match = re.fullmatch(r"(?:ram:)?(?:0x)?([0-9a-fA-F]+)", value)
    if match:
        return _integer(int(match[1], 16), "reference address")
    if re.fullmatch(r"(?!ram:)[A-Za-z_][A-Za-z_0-9]*:(?:0x)?[0-9a-fA-F]+", value):
        return None
    raise LedgerError("unsupported Ghidra reference location")


def _add_export(ledger: dict, functions: dict, record: Mapping) -> None:
    if not isinstance(record, Mapping):
        raise LedgerError("Ghidra export must be a JSON object")
    if record.get("binary_sha256") != ledger["binary"]["sha256"]:
        raise LedgerError("Ghidra export binary hash mismatch")
    byte_order = "LE" if ledger["binary"]["endianness"] == "little" else "BE"
    arch = record.get("architecture")
    if not isinstance(arch, str) or not arch.startswith(f"ARM:{byte_order}:32:"):
        raise LedgerError("Ghidra export architecture mismatch")
    address = _integer(record.get("address"), "address")
    thumb = record.get("thumb")
    if type(thumb) not in (int, bool) or thumb not in (0, 1):
        raise LedgerError("Ghidra thumb must be boolean or 0/1")
    function = functions.get((address, bool(thumb)))
    if function is None:
        raise LedgerError("Ghidra export has no matching ELF function symbol")
    if _integer(record.get("raw_value"), "raw_value") != (address | int(thumb)):
        raise LedgerError("Ghidra raw_value/mode mismatch")
    size = _integer(record.get("symbol_size"), "symbol_size")
    if not any(s["size"] == size and s["name"] == record.get("symbol")
               for s in function["symbols"]):
        raise LedgerError("Ghidra export symbol name/size mismatch")
    base = _integer(record.get("ghidra_image_base"), "ghidra_image_base")
    if _integer(record.get("ghidra_address"), "ghidra_address") != address + base:
        raise LedgerError("Ghidra address/image-base mismatch")
    calls = record.get("calls")
    if not isinstance(calls, list):
        raise LedgerError("Ghidra export must contain a calls array")
    normalized = []
    for call in calls:
        if not isinstance(call, Mapping) or not isinstance(call.get("type"), str):
            raise LedgerError("invalid Ghidra call reference")
        source = _location(call.get("from"))
        target = _location(call.get("to"))
        if source is None or not address <= source - base < address + max(size, 1):
            raise LedgerError("Ghidra call site outside exported function")
        if target is not None and target < base:
            raise LedgerError("Ghidra RAM call destination precedes image base")
        normalized.append({"from_address": source - base,
                           "target_address": target - base if target is not None else None,
                           "target_location": call["to"], "type": call["type"]})
    source = {}
    for key in ("ghidra_version", "analysis_scope"):
        value = record.get(key)
        if value is not None and not isinstance(value, str):
            raise LedgerError(f"Ghidra {key} must be a string")
        source[key] = value
    reported = record.get("byte_mapping_validated")
    if reported is not None and type(reported) is not bool:
        raise LedgerError("Ghidra byte_mapping_validated must be boolean")
    source["byte_mapping_reported_validated"] = reported
    source["image_base"] = base
    source["symbol"] = record["symbol"]
    source["symbol_size"] = size
    function["calls"] = (function["calls"] or []) + normalized
    function["call_sources"].append(source)


def build_ledger(data: bytes, ghidra_exports: Iterable[Mapping] = ()) -> dict:
    """Build deterministic metadata from ELF bytes and optional Ghidra records.

    Raise LedgerError for unsupported/malformed ELF or mismatched evidence;
    no partial ledger is returned. Supplemental records are not disassembled
    or authenticated by this function. Callers must keep local output private.
    """
    metadata, observations, counts = _symbols(data)
    digest = hashlib.sha256(data).hexdigest()
    metadata.update(sha256=digest, size_bytes=len(data))
    functions = {}
    for symbol in observations:
        key = symbol["address"], symbol["thumb"]
        if key not in functions:
            mode = "thumb" if key[1] else "arm"
            functions[key] = {"id": f"{digest}:{key[0]:08x}:{mode}",
                              "address": key[0], "thumb": key[1], "symbols": [],
                              "calls": None, "call_sources": [], "state": "INVENTORIED"}
        functions[key]["symbols"].append({k: v for k, v in symbol.items()
                                           if k not in ("address", "thumb")})
    for function in functions.values():
        symbols = function["symbols"]
        symbols.sort(key=lambda s: (s["table_index"], s["symbol_index"]))
        names = sorted({s["name"] for s in symbols if s["name"]})
        sizes = {s["size"] for s in symbols}
        function.update(name=names[0] if names else None, aliases=names[1:],
                        size=max(sizes), size_conflict=len(sizes) > 1)
    ledger = {"schema": SCHEMA, "exporter_version": EXPORTER_VERSION,
              "binary": metadata, "coverage": dict(counts, inventory_complete=False,
                  discovery="defined STT_FUNC symbols in executable file-backed sections",
                  call_discovery="reported outgoing Ghidra references only; may be incomplete"),
              "functions": [functions[k] for k in sorted(functions)]}
    for record in ghidra_exports:
        _add_export(ledger, functions, record)
    for function in functions.values():
        for field in ("calls", "call_sources"):
            if function[field] is not None:
                unique = {json.dumps(item, sort_keys=True): item for item in function[field]}
                function[field] = [unique[key] for key in sorted(unique)]
    ledger["coverage"]["function_count"] = len(functions)
    ledger["coverage"]["functions_with_call_exports"] = sum(
        f["calls"] is not None for f in functions.values())
    return ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("binary", type=Path, help="local ARM32 ELF (read only)")
    parser.add_argument("--ghidra-export", type=Path, action="append", default=[],
                        help="local export_batch.py JSON; repeat for multiple records")
    args = parser.parse_args(argv)
    try:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in args.ghidra_export]
        ledger = build_ledger(args.binary.read_bytes(), records)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(2, f"indexer: {exc}\n")
    json.dump(ledger, sys.stdout, indent=2, sort_keys=True, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
