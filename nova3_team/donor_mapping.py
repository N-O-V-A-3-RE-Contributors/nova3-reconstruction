"""Offline, evidence-bounded Cyrus/Premium symbol and DWARF donor planning.

This takes *metadata* from ``version_compare``; neither ELF is imported into
the working tree and no original machine-code bytes are emitted.  A matching
name or compilation-unit hint is not reconstructed source or a proven TU.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

from .object_matching import group_hint
from .version_compare import SCHEMA as VERSION_SCHEMA


SCHEMA = "nova3.donor-plan/1"
MAX_ROWS = 100_000
MAX_SELECT = 512
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class DonorError(ValueError):
    """Missing, inconsistent or unsupported donor metadata."""


def _hexhash(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise DonorError("invalid reference SHA-256 metadata")
    return value


def _unit_map(edition: dict) -> dict[int, list[dict]]:
    dwarf = edition.get("dwarf")
    if not isinstance(dwarf, dict):
        raise DonorError("missing DWARF summary")
    units = dwarf.get("compile_units", [])
    dies = dwarf.get("function_dies", [])
    if not isinstance(units, list) or not isinstance(dies, list) or len(dies) > MAX_ROWS:
        raise DonorError("invalid or oversized DWARF summary")
    meta = {u["offset"]: u for u in units if isinstance(u, dict)
            and type(u.get("offset")) is int and u["offset"] >= 0}
    by_pc: dict[int, list[dict]] = defaultdict(list)
    for die in dies:
        if not isinstance(die, dict) or die.get("declaration"):
            continue
        pc, offset = die.get("low_pc"), die.get("cu_offset")
        if type(pc) is not int or type(offset) is not int or pc < 0 or offset not in meta:
            continue
        cu = meta[offset]
        row = {"cu_offset": offset}
        for attr in ("name_sha256", "producer_sha256"):
            if attr in cu:
                row[attr] = _hexhash(cu[attr])
        by_pc[pc & ~1].append(row)
    return by_pc


def _cu_at(mapping: dict[int, list[dict]], address: int) -> tuple[str, dict | None]:
    units = mapping.get(address & ~1, [])
    distinct = {u["cu_offset"]: u for u in units}
    if not distinct:
        return "MISSING_DWARF_CU", None
    if len(distinct) != 1:
        return "AMBIGUOUS_DWARF_CU", None
    return "INFERRED_DWARF_CU", next(iter(distinct.values()))


def _ida_index(data: dict | None, retail_hash: str) -> dict[int, dict]:
    if data is None:
        return {}
    if data.get("schema") != "nova3.ida-headless-boundaries/1":
        raise DonorError("unsupported IDA metadata schema")
    if data.get("input_sha256") != retail_hash:
        raise DonorError("IDA input SHA-256 does not match Premium reference")
    rows = data.get("functions")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise DonorError("invalid or oversized IDA function index")
    by_addr: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("image_relative")) is not int:
            continue
        addr = row["image_relative"] & ~1
        if addr in by_addr:
            # Multiple IDA entries at the same address are ambiguous.
            by_addr[addr] = {"ambiguous": True}
        else:
            by_addr[addr] = row
    return by_addr


def plan_donors(report: dict, *, limit: int = 64, ida: dict | None = None) -> dict:
    """Select safe high-value donor hypotheses with no matching claim.

    Retail stays authoritative.  Only exact unique symbol correspondences with
    machine-code byte evidence qualify as donor candidates.  DWARF compilation
    units are suggestions, not recovered original object-file ownership.
    """
    if not isinstance(report, dict) or report.get("schema") != VERSION_SCHEMA:
        raise DonorError("unsupported Cyrus/Premium comparison schema")
    if type(limit) is not int or not 1 <= limit <= MAX_SELECT:
        raise DonorError("limit must be between 1 and 512")
    retail, debug, comparison = (report.get(x) for x in ("retail", "debug", "comparison"))
    if not all(isinstance(x, dict) for x in (retail, debug, comparison)):
        raise DonorError("comparison lacks edition evidence")
    rhash, dhash = _hexhash(retail.get("sha256")), _hexhash(debug.get("sha256"))
    rows = comparison.get("functions")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise DonorError("invalid or oversized compared function inventory")
    retail_cus, debug_cus = _unit_map(retail), _unit_map(debug)
    ida_index = _ida_index(ida, rhash)
    status_counts = Counter()
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("status"), str):
            raise DonorError("malformed comparison function row")
        status_counts[row["status"]] += 1
        if row["status"] not in ("exact_bytes", "different_bytes"):
            continue
        name = row.get("name")
        rv, dv = row.get("retail_raw_value"), row.get("debug_raw_value")
        rs, ds = row.get("retail_size"), row.get("debug_size")
        if (not isinstance(name, str) or not name or len(name) > 1024
                or any(type(v) is not int for v in (rv, dv, rs, ds))
                or not (0 <= rv < 1 << 32 and 0 <= dv < 1 << 32
                        and 0 < rs <= 1 << 22 and 0 < ds <= 1 << 22)):
            raise DonorError("unsafe donor symbol metadata")
        _hexhash(row.get("retail_sha256"))
        _hexhash(row.get("debug_sha256"))
        rstate, rcu = _cu_at(retail_cus, rv)
        dstate, dcu = _cu_at(debug_cus, dv)
        # Only real observed retail CU evidence can displace a C++ class hint.
        group = ("retail_cu_%08x" % rcu["cu_offset"] if rcu else group_hint(name))
        ida_row = ida_index.get(rv & ~1)
        ida_state = ("NO_IDA_RECORD" if ida_row is None else
                     "AMBIGUOUS_IDA_RECORD" if ida_row.get("ambiguous") else
                     "IDA_BYTE_MAPPING_VERIFIED" if ida_row.get("byte_mapping_verified") is True else
                     "IDA_METADATA_ONLY")
        candidates.append(dict(name=name, retail_address=rv & ~1,
                               debug_address=dv & ~1, retail_size=rs, debug_size=ds,
                               donor_byte_status=row["status"],
                               group_hint=group, translation_unit_proven=False,
                               retail_cu_status=rstate, retail_cu=rcu,
                               debug_cu_status=dstate, debug_cu=dcu,
                               ida_status=ida_state,
                               original_object_recovered=False,
                               original_compiler_verified=False,
                               source_matching_verified=False))
    # Prefer unchanged debug/retail code and smaller bounded slices for a first
    # compiler-validation pilot, but do not conflate identical bytes with source.
    candidates.sort(key=lambda x: (x["donor_byte_status"] != "exact_bytes",
                                   x["retail_size"], x["retail_address"], x["name"]))
    selected = candidates[:limit]
    return dict(schema=SCHEMA, retail_sha256=rhash, debug_sha256=dhash,
                selection="exact_unique_names_first_smallest_functions",
                total_pairs=len(rows), qualifying_pairs=len(candidates),
                skipped_pairs=len(rows)-len(candidates),
                selected_pairs=len(selected), status_counts=dict(sorted(status_counts.items())),
                candidates=selected, verified_decompilation_count=0,
                caveat="DWARF/IDA groupings are metadata hypotheses; byte identity across game builds is not C++ reconstruction or original-object proof.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, required=True,
                   help="Private JSON from nova3_team.version_compare")
    p.add_argument("--ida", type=Path, help="Optional private IDA metadata JSON")
    p.add_argument("--out", type=Path, required=True, help="Private JSON under repo .cache")
    p.add_argument("--limit", type=int, default=64)
    args = p.parse_args(argv)
    try:
        # Fail closed on paths outside ignored cache: these reports can contain
        # proprietary symbol metadata and must not be accidentally checked in.
        cache = (Path(__file__).resolve().parents[1] / ".cache").resolve()
        target = args.out.expanduser().resolve()
        if not target.is_relative_to(cache) or target == cache or target.suffix != ".json":
            raise DonorError("output must be .json under repository .cache")
        report = json.loads(args.report.expanduser().read_text(encoding="utf-8"))
        ida = json.loads(args.ida.expanduser().read_text(encoding="utf-8")) if args.ida else None
        result = plan_donors(report, limit=args.limit, ida=ida)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("selected %d donor hypotheses; SOURCE MATCH UNVERIFIED" % result["selected_pairs"])
    except (DonorError, OSError, ValueError, TypeError) as exc:
        print("donor mapping: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
