"""One-window, offline structural diagnostics for the retained GCC443 pilot.

Never executes a compiler/linker, resolves relocations, or emits instructions.
The report is pinned as an input, not trusted as a comparison oracle. Objects,
source and the Premium window are independently checked before reporting.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys

from . import historical_compile_pilot as hp
from .historical_toolchain import _read_bytes, digest
from .object_compare import MAX_SYMBOLS, _span, candidate_window, compare_window
from .relocation_probe import ArmElf, MAX_RELOCATIONS, linked_window

SCHEMA = "nova3.gcc-layout-diagnostics/1"
SOURCE_SHA256 = "ba1d258b34b4742333a9a4382fb76f8aeda47091c9a67a5ce90a049afd292a42"
MAX_REPORT = 2 * 1024 * 1024
MAX_FUNCTION = 4096
ROLES = {"g_once": "once_state", "g_cpuCount": "cpu_count",
         "android_cpuInit": "initializer", "pthread_once": "once_service"}
RELOCATIONS = {3: "R_ARM_REL32", 27: "R_ARM_PLT32", 28: "R_ARM_CALL",
               42: "R_ARM_PREL31", 96: "R_ARM_GOT_PREL"}
BINDINGS = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}
VISIBILITY = {0: "DEFAULT", 1: "INTERNAL", 2: "HIDDEN", 3: "PROTECTED"}


class DiagnosticError(ValueError):
    pass


def _hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise DiagnosticError("invalid SHA-256 pin")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("duplicate report field")
        result[key] = value
    return result


def _prior(data: bytes, expected: str, index: int) -> tuple[dict, dict]:
    if len(data) > MAX_REPORT or digest(data) != _hash(expected):
        raise DiagnosticError("prior report hash or size mismatch")
    report = json.loads(data, object_pairs_hook=_unique)
    if not isinstance(report, dict):
        raise DiagnosticError("invalid pilot report")
    fixed = dict(schema=hp.SCHEMA, status="NOT_MATCHED",
                 reference_sha256=hp.REFERENCE_SHA256, reference_address=hp.ADDRESS,
                 reference_size=hp.SIZE, reference_window_sha256=hp.WINDOW_SHA256,
                 reference_thumb=False, source_sha256=SOURCE_SHA256)
    if any(type(report.get(k)) is not type(v) or report[k] != v for k, v in fixed.items()):
        raise DiagnosticError("stale or incompatible pilot report")
    candidate = report.get("candidate")
    if (not isinstance(candidate, dict) or candidate.get("candidate") != "gcc443_noarg"
            or candidate.get("symbol") != hp.NOARG_SYMBOL or candidate.get("compatible") is not True
            or candidate.get("abi") != "NO_ARGUMENT_GLOBAL_STATE_HYPOTHESIS"):
        raise DiagnosticError("wrong source candidate")
    items = report.get("profiles")
    if (not isinstance(items, list) or not 4 <= len(items) <= 8
            or type(index) is not int or not 0 <= index < len(items)):
        raise DiagnosticError("invalid profile selection")
    if any(not isinstance(item, dict) or type(item.get("index")) is not int
           or item["index"] != n for n, item in enumerate(items)):
        raise DiagnosticError("ambiguous profile index")
    item = items[index]
    if (item.get("status") != "COMPARED_NOT_CERTIFIED" or item.get("symbol") != hp.NOARG_SYMBOL
            or item.get("source_sha256") != SOURCE_SHA256
            or item.get("reference_sha256") != hp.REFERENCE_SHA256
            or not isinstance(item.get("compile"), dict)
            or item["compile"].get("status") != "COMPILE_FINISHED"
            or type(item["compile"].get("exit_code")) is not int
            or item["compile"]["exit_code"] != 0):
        raise DiagnosticError("profile is not a completed pinned compile")
    requested = hp.profiles(8)[index]
    if item.get("requested_flags") != requested or item.get("flags") != hp.gcc443_flags(requested):
        raise DiagnosticError("unexpected pilot flag hypothesis")
    for field in ("object_sha256", "compiler_sha256", "compiler_version_sha256"):
        _hash(item.get(field))
    pins = report.get("tool_pins", {})
    if (not isinstance(pins, dict) or not isinstance(pins.get("compiler"), dict)
            or pins["compiler"].get("sha256") != item["compiler_sha256"]):
        raise DiagnosticError("inconsistent compiler pin")
    # Check the full recorded invocation, not just a possibly stale flags list.
    # These strings are never executed, followed as paths, or copied to output.
    command = item.get("command")
    if (not isinstance(command, list) or not 20 <= len(command) <= 64
            or any(not isinstance(s, str) or len(s) > 4096 or "\0" in s for s in command)
            or command[0] != pins["compiler"].get("path")
            or not command[1].startswith("-B/") or not command[2].startswith("--sysroot=/")
            or command[3:8] != ["-nostdinc", "-nostdinc++", "-x", "c++", "-pedantic-errors"]
            or command[8:8 + len(item["flags"])] != item["flags"]
            or command[-4:-1] != ["-c", report.get("source"), "-o"]):
        raise DiagnosticError("recorded command disagrees with flags or source")
    includes = command[8 + len(item["flags"]):-4]
    if (not 4 <= len(includes) <= 18 or len(includes) % 2
            or includes[::2] != ["-isystem"] * (len(includes) // 2)
            or any(not Path(s).is_absolute() for s in includes[1::2])):
        raise DiagnosticError("unexpected recorded header options")
    headers = report.get("headers")
    trees = headers.get("trees") if isinstance(headers, dict) else None
    if (not isinstance(trees, list) or len(trees) != len(includes) // 2
            or any(not isinstance(t, dict) for t in trees)
            or [t.get("path") for t in trees] != [includes[-1], *includes[1:-2:2]]):
        raise DiagnosticError("recorded header manifest disagrees with command")
    for tree in trees:
        _hash(tree.get("sha256"))
    metadata = report.get("compiler_metadata")
    if not isinstance(metadata, dict) or metadata.get("compiler_executable_sha256") != item["compiler_sha256"]:
        raise DiagnosticError("inconsistent compiler metadata")
    queries = metadata.get("queries", {})
    if (not isinstance(queries, dict)
            or any(not isinstance(queries.get(k), dict) for k in ("version", "target", "gcc_version"))
            or queries["version"].get("stdout_sha256") != item["compiler_version_sha256"]
            or queries["target"].get("stdout") != "arm-linux-androideabi\n"
            or queries["gcc_version"].get("stdout") != "4.4.3\n"):
        raise DiagnosticError("unexpected compiler identity or target record")
    for name in ("version", "target", "gcc_version"):
        query = queries[name]
        if (type(query.get("exit_code")) is not int or query["exit_code"] != 0
                or not isinstance(query.get("stdout"), str)
                or digest(query["stdout"].encode()) != query.get("stdout_sha256")):
            raise DiagnosticError("inconsistent compiler identity query")
    return report, item


def _symbols(elf: ArmElf):
    """Reuse the bounded ELF reader; add binding/visibility metadata only."""
    for table in elf.sections:
        if table["type"] != 2:
            continue
        for n, symbol in enumerate(elf.symbols(table["index"])):
            info, other = _span(elf.data, table["offset"] + 16 * n + 12, 2)
            yield dict(symbol, binding=BINDINGS.get(info >> 4, "OTHER"),
                       visibility=VISIBILITY[other & 3])


def _bounded_elf(data: bytes) -> ArmElf:
    elf = ArmElf(data)
    # Aliased section ranges can otherwise multiply the reader's per-table
    # limits. Apply aggregate budgets before any function/symbol scan.
    if sum(s["size"] // 16 for s in elf.sections if s["type"] == 2) > MAX_SYMBOLS:
        raise DiagnosticError("aggregate symbol budget exceeded")
    if sum(s["size"] // 8 for s in elf.sections if s["type"] in (4, 9)) > MAX_RELOCATIONS:
        raise DiagnosticError("aggregate relocation budget exceeded")
    return elf


def _layout(elf: ArmElf, symbol: str) -> dict:
    fn, section = elf.function(symbol)
    start, size = fn["value"], fn["size"]
    if (start % 4 or not 0 < size <= MAX_FUNCTION or size % 4
            or section["type"] != 1 or not section["flags"] & 4):
        raise DiagnosticError("expected bounded aligned A32 function")
    relative = start - (section["address"] if elf.kind != 1 else 0)
    _span(elf.data, section["offset"] + relative, size)
    if relative < 0 or relative + size > section["size"]:
        raise DiagnosticError("function outside executable section")
    end = start + size
    marks, roles = {}, {}
    for sym in _symbols(elf):
        if sym["name"] in ROLES:
            role = ROLES[sym["name"]]
            row = dict(binding=sym["binding"], visibility=sym["visibility"],
                       defined=sym["section"] != 0)
            if role in roles and roles[role] != row:
                raise DiagnosticError("conflicting dependency binding")
            roles[role] = row
        tag = sym["name"].split(".", 1)[0]
        if tag in ("$a", "$t", "$d") and sym["section"] == section["index"]:
            at = sym["value"]
            if at < end:
                section_start = section["address"] if elf.kind != 1 else 0
                if (at < section_start or sym["type"] != 0 or sym["size"] != 0
                        or sym["binding"] != "LOCAL"):
                    raise DiagnosticError("invalid ARM mapping symbol")
                if at in marks and marks[at] != tag:
                    raise DiagnosticError("conflicting mapping symbols")
                marks[at] = tag
    # Mapping symbols describe code/data, not instruction semantics. Missing
    # entry evidence stays UNKNOWN; do not count literal words as instructions.
    before = [at for at in marks if at <= start]
    counts = None
    if before:
        active = marks[max(before)]
        if active != "$a":
            raise DiagnosticError("mapping symbol contradicts A32 entry")
        boundaries = [(start, active)] + sorted((at, tag) for at, tag in marks.items()
                                               if start < at < end)
        counts = {"a32_code_bytes": 0, "mapped_data_bytes": 0}
        for n, (at, tag) in enumerate(boundaries):
            stop = boundaries[n + 1][0] if n + 1 < len(boundaries) else end
            if tag == "$t" or at % 4 or stop % 4:
                raise DiagnosticError("mixed or unaligned function mapping")
            counts["a32_code_bytes" if tag == "$a" else "mapped_data_bytes"] += stop - at
    return dict(size=size, mode="A32", mapping_status="KNOWN" if counts else "UNKNOWN",
                mapped_extents=counts, dependency_bindings=roles,
                eabi_version=5, float_abi_header_bits=(struct.unpack_from("<I", elf.data, 36)[0] >> 9) & 3)


def _relocations(elf: ArmElf, symbol: str) -> dict:
    """Inventory ARM REL metadata, including GOT/PLT types the resolver rejects.

    No addends or symbol values are read or applied. All names are normalized
    to fixed roles; arbitrary ELF names cannot escape into the report.
    """
    fn, section = elf.function(symbol)
    counts, roles, occupied = Counter(), Counter(), set()
    total, outside, undefined = 0, 0, set()
    for sec in elf.sections:
        if sec["type"] not in (4, 9):
            continue
        if (sec["type"] != 9 or sec["entsize"] != 8 or sec["size"] % 8
                or not 0 < sec["info"] < len(elf.sections)):
            raise DiagnosticError("unsupported relocation table")
        total += sec["size"] // 8
        if total > MAX_RELOCATIONS:
            raise DiagnosticError("relocation budget exceeded")
        symbols = elf.symbols(sec["link"])
        target = elf.sections[sec["info"]]
        for pos in range(sec["offset"], sec["offset"] + sec["size"], 8):
            offset, info = struct.unpack("<II", _span(elf.data, pos, 8))
            index, kind = info >> 8, info & 255
            if index >= len(symbols) or offset % 4 or offset + 4 > target["size"]:
                raise DiagnosticError("invalid relocation site or symbol")
            if target["index"] != section["index"] or not fn["value"] <= offset < fn["value"] + fn["size"]:
                outside += 1
                continue
            if offset + 4 > fn["value"] + fn["size"] or offset in occupied:
                raise DiagnosticError("overlapping or crossing relocation site")
            occupied.add(offset)
            sym = symbols[index]
            counts[RELOCATIONS.get(kind, "OTHER")] += 1
            roles[ROLES.get(sym["name"], "OTHER")] += 1
            if sym["section"] == 0:
                undefined.add((sec["link"], index))
    return dict(site_count=len(occupied), type_counts=dict(sorted(counts.items())),
                dependency_site_counts=dict(sorted(roles.items())),
                unresolved_external_symbol_count=len(undefined), outside_function_site_count=outside,
                resolution="NOT_ATTEMPTED")


def diagnose(reference: bytes, obj: bytes, source: bytes, prior: bytes, *,
             prior_sha256: str, profile_index: int) -> dict:
    """Pure bounded diagnostic API; fixed production pins may be mocked in tests."""
    if (len(reference) > 64 * 1024 * 1024 or len(obj) > 64 * 1024 * 1024
            or len(source) > 65536):
        raise DiagnosticError("input exceeds diagnostic bounds")
    report, item = _prior(prior, prior_sha256, profile_index)
    if digest(source) != SOURCE_SHA256 or digest(obj) != item["object_sha256"]:
        raise DiagnosticError("source or candidate object changed since pilot")
    if digest(reference) != hp.REFERENCE_SHA256:
        raise DiagnosticError("reference SHA-256 mismatch")
    candidate_elf, reference_elf = _bounded_elf(obj), _bounded_elf(reference)
    # Independently bound section + symbol + PT_LOAD and check the fixed window.
    original, _ = linked_window(reference, hp.ADDRESS)
    if len(original) != hp.SIZE or digest(original) != hp.WINDOW_SHA256:
        raise DiagnosticError("fixed Premium A32 window mismatch")
    window = candidate_window(obj, hp.NOARG_SYMBOL)
    if window["thumb"]:
        raise DiagnosticError("candidate is not A32")
    raw = compare_window(reference, obj, address=hp.ADDRESS, symbol=hp.NOARG_SYMBOL,
                         expected_reference_sha256=hp.REFERENCE_SHA256)
    recorded = item.get("comparison")
    fields = ("status", "candidate_size", "candidate_thumb", "original_thumb",
              "reference_size", "reference_address", "reference_sha256",
              "reference_window_sha256", "candidate_object_sha256", "candidate_code_sha256",
              "candidate_relocation_count", "code_window_equal")
    if (not isinstance(recorded, dict) or not isinstance(recorded.get("raw"), dict)
            or any(type(recorded["raw"].get(k)) is not type(raw[k])
                   or recorded["raw"][k] != raw[k] for k in fields)):
        raise DiagnosticError("stale pilot comparison")
    candidate = _layout(candidate_elf, hp.NOARG_SYMBOL)
    # Confirm the named reference symbol is the fixed address, not an alias
    # elsewhere in the library that happens to have the same size.
    ref_fn, _ = reference_elf.function(hp.NOARG_SYMBOL)
    if ref_fn["value"] != hp.ADDRESS:
        raise DiagnosticError("reference name/address mismatch")
    reference_layout = _layout(reference_elf, hp.NOARG_SYMBOL)
    relocations = _relocations(candidate_elf, hp.NOARG_SYMBOL)
    if relocations["site_count"] != raw["candidate_relocation_count"]:
        raise DiagnosticError("relocation inventory disagrees")
    categories = []
    if not raw["code_window_equal"]:
        categories.append("RAW_WINDOW_NOT_EQUAL")
    delta = candidate["size"] - reference_layout["size"]
    if delta:
        categories.append("FUNCTION_SIZE_DIFFERENCE")
    extent_delta = None
    if candidate["mapped_extents"] is not None and reference_layout["mapped_extents"] is not None:
        extent_delta = {key: candidate["mapped_extents"][key] - reference_layout["mapped_extents"][key]
                        for key in candidate["mapped_extents"]}
        if candidate["mapped_extents"] != reference_layout["mapped_extents"]:
            categories.append("CODE_OR_LITERAL_EXTENT_DIFFERENCE")
    else:
        categories.append("CODE_DATA_SPLIT_UNKNOWN")
    binding_difference = any(
        row["binding"] != reference_layout["dependency_bindings"].get(role, {}).get("binding")
        for role, row in candidate["dependency_bindings"].items()
        if role in reference_layout["dependency_bindings"])
    if binding_difference:
        categories.append("DEPENDENCY_BINDING_DIFFERENCE")
    if any(row["visibility"] != reference_layout["dependency_bindings"].get(role, {}).get("visibility")
           for role, row in candidate["dependency_bindings"].items()
           if role in reference_layout["dependency_bindings"]):
        categories.append("DEPENDENCY_VISIBILITY_DIFFERENCE")
    if candidate["float_abi_header_bits"] != reference_layout["float_abi_header_bits"]:
        categories.append("FLOAT_ABI_HEADER_DIFFERENCE")
    if relocations["type_counts"].get("R_ARM_GOT_PREL"):
        categories.append("CANDIDATE_GOT_PREL_RELOCATIONS")
        if binding_difference:
            categories.append("PIC_BINDING_HYPOTHESIS_REQUIRES_CONTROLLED_LINK")
    if any(kind not in ("R_ARM_CALL", "R_ARM_REL32") for kind in relocations["type_counts"]):
        categories.append("RELOCATIONS_OUTSIDE_EXISTING_RESOLVER")
    return dict(schema=SCHEMA, status="DIAGNOSTIC_ONLY_NOT_MATCHED", profile_index=profile_index,
                prior_report_sha256=prior_sha256, source_sha256=SOURCE_SHA256,
                compiler_sha256=item["compiler_sha256"],
                compiler_version_sha256=item["compiler_version_sha256"],
                candidate_object_sha256=digest(obj), reference_sha256=hp.REFERENCE_SHA256,
                reference_window_sha256=hp.WINDOW_SHA256, reference_address=hp.ADDRESS,
                requested_flags=item["requested_flags"], flags=item["flags"],
                header_tree_sha256=[t["sha256"] for t in report["headers"]["trees"]],
                candidate=candidate, reference=reference_layout, size_delta=delta,
                mapped_extent_delta=extent_delta,
                relocations=relocations, difference_categories=categories,
                claims=hp._claims(report["candidate"]), root_cause="UNKNOWN",
                linked_stage="NOT_PERFORMED", original_prelink_relocations="UNKNOWN")


def run(*, reference: Path, obj: Path, source: Path, prior_report: Path,
        prior_sha256: str, profile_index: int, out: Path) -> dict:
    # Explicit inputs only: no discovery, report-supplied paths, subprocesses,
    # symlinks, or writes outside a fresh ignored checkout-local cache directory.
    out = hp._output(out)
    paths = [reference, obj, source, prior_report]
    if any(any(p.is_symlink() for p in (path, *path.parents)) for path in paths):
        raise DiagnosticError("symlinked input unsupported")
    result = diagnose(_read_bytes(reference, 64 * 1024 * 1024),
                      _read_bytes(obj, 64 * 1024 * 1024), _read_bytes(source, 65536),
                      _read_bytes(prior_report, MAX_REPORT), prior_sha256=prior_sha256,
                      profile_index=profile_index)
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    hp._save(out, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "object", "source", "prior-report", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--prior-report-sha256", required=True)
    parser.add_argument("--profile-index", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        run(reference=args.reference, obj=args.object, source=args.source,
            prior_report=args.prior_report, prior_sha256=args.prior_report_sha256,
            profile_index=args.profile_index, out=args.out)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("GCC layout diagnostic rejected input; check private pins, bounds and pilot provenance",
              file=sys.stderr)
        return 2
    print("DIAGNOSTIC_ONLY_NOT_MATCHED; private structural report retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
