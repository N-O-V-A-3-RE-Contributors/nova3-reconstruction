"""Bounded, offline TU *hypotheses* from Cyrus metadata and Premium bounds.

No instructions, raw symbols, source paths, assembler or objects are emitted.
The private worksheet is a staging API, not recovered original object layout.
See docs/progress/DONOR_TU_CATALOG.md for the schema and conservative rules.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import tempfile

from . import version_compare as vc

SCHEMA = "nova3.donor-tu-catalog/1"
SUMMARY_SCHEMA = "nova3.donor-tu-summary/1"
TOOL_VERSION = "1"
ROOT = Path(__file__).resolve().parents[1]
MAX_FUNCTIONS = 100_000
MAX_GROUP_MEMBERS = 256
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_SUMMARY_BYTES = 16 * 1024
TIMEOUT_SECONDS = 120
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class CatalogError(ValueError):
    """Insufficient, ambiguous or unsafe local evidence."""


def _hash(value: object) -> str:
    return vc.digest(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True).encode("ascii"))


def _text_hash(value: str) -> str:
    return vc.digest(value.encode("utf-8", errors="surrogateescape"))


def _scope(name: str) -> tuple[str, ...] | None:
    """Only simple complete Itanium nested scopes, never a namespace prefix.

    Templates, substitutions, operators, thunks and local names stay unknown.
    Parsing an identifier is not evidence that a class was compiled in one TU.
    """
    if not name.startswith("_ZN") or len(name) > 1024:
        return None
    pos, parts = 3, []
    while pos < len(name) and name[pos] in "KVRrO":
        pos += 1
    while pos < len(name):
        if name[pos] == "E":
            return tuple(parts[:-1]) if len(parts) >= 2 and pos + 1 < len(name) else None
        match = re.match(r"[1-9][0-9]{0,2}", name[pos:])
        if not match or len(parts) >= 16:
            return None
        length = int(match[0])
        pos += len(match[0])
        part = name[pos:pos + length]
        if len(part) != length or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part):
            return None
        parts.append(part)
        pos += length
    return None


def _names_at(edition: dict) -> dict[int, set[str]]:
    result = defaultdict(set)
    for name, definitions in edition["names"].items():
        for raw, _, _ in definitions:
            result[raw & ~1].add(name)
    return result


def _cu_index(edition: dict, names: dict[int, set[str]]) -> dict[int, list[dict]]:
    """Associate only concrete, exact-bound subprogram DIEs, not CU ranges.

    A CU's name alone, a declaration, or its enclosing PC interval cannot
    assign every intervening linked function to that CU. Range lists are not
    implemented here and cannot qualify a function for grouping.
    """
    dwarf = edition["dwarf"]
    units = {u["offset"]: u for u in dwarf["compile_units"]}
    result = defaultdict(list)
    linkage_hashes = {}
    for die in dwarf["function_dies"]:
        if die.get("declaration") or "low_pc" not in die:
            continue
        address = die["low_pc"] & ~1
        candidate = edition["candidates"].get(address)
        if candidate is None:
            continue
        cu = units[die["cu_offset"]]
        reason = None
        high = die.get("high_pc")
        form = die.get("high_pc_form")
        if die.get("has_ranges") or high is None:
            reason = "DWARF_RANGE_UNSUPPORTED"
        elif form == "DW_FORM_addr":
            end = high & ~1
        elif form in ("DW_FORM_data1", "DW_FORM_data2", "DW_FORM_data4",
                      "DW_FORM_data8", "DW_FORM_udata"):
            end = address + high
        else:
            reason = "DWARF_RANGE_UNSUPPORTED"
        if reason is None and end != address + candidate["size"]:
            reason = "DWARF_SYMBOL_EXTENT_CONFLICT"
        linkage = die.get("linkage_name_sha256")
        if linkage:
            if address not in linkage_hashes:
                linkage_hashes[address] = {_text_hash(n) for n in names.get(address, ())}
            if linkage not in linkage_hashes[address]:
                reason = "DWARF_LINKAGE_CONFLICT"
        result[address].append(dict(cu_offset=cu["offset"], die_offset=die["offset"],
                                    name_sha256=cu.get("name_sha256"),
                                    producer_sha256=cu.get("producer_sha256"),
                                    exclusion=reason))
    return result


def _ownership(index: dict, address: int) -> tuple[dict | None, list[str]]:
    rows = index.get(address, [])
    if not rows:
        return None, []
    reasons = {r["exclusion"] for r in rows if r["exclusion"]}
    if len({r["cu_offset"] for r in rows}) != 1:
        reasons.add("AMBIGUOUS_DWARF_CU")
    if reasons:
        return None, sorted(reasons)
    first = rows[0]
    evidence = {k: first[k] for k in ("cu_offset", "name_sha256", "producer_sha256")}
    evidence["subprogram_die_offsets"] = sorted({r["die_offset"] for r in rows})
    return evidence, []


def _boundary(edition: dict, candidate: dict) -> dict:
    return dict(reference_sha256=edition["sha256"], address=candidate["address"],
                raw_value=candidate["address"] | int(candidate["thumb"]),
                mode="thumb" if candidate["thumb"] else "arm", size=candidate["size"],
                file_offset=candidate["file_offset"], section_index=candidate["section_index"],
                window_sha256=candidate.get("sha256"),
                relocation_entries_in_range=candidate["relocation_entries_in_range"],
                exclusions=candidate["exclusions"], mode_evidence=candidate["mode_evidence"])


def _source_pins() -> dict:
    # No absolute paths or interpreter/host metadata in the summary.
    from . import indexer
    return dict(catalog_sha256=vc.digest(Path(__file__).read_bytes()),
                version_compare_sha256=vc.digest(Path(vc.__file__).read_bytes()),
                indexer_sha256=vc.digest(Path(indexer.__file__).read_bytes()))


def _validate_cu_tags(data: bytes, edition: dict) -> None:
    """The shared inventory hashes top-DIE names; require actual CU tags too."""
    if edition["dwarf"]["status"] == "absent":
        return
    try:
        from elftools.elf.elffile import ELFFile
        dwarf = ELFFile(io.BytesIO(data)).get_dwarf_info()
        seen = []
        for cu in dwarf.iter_CUs():
            if len(seen) >= vc.MAX_SECTIONS or cu.get_top_DIE().tag != "DW_TAG_compile_unit":
                raise CatalogError("DWARF top DIE is not a supported compile unit")
            seen.append(cu.cu_offset)
        if seen != [u["offset"] for u in edition["dwarf"]["compile_units"]]:
            raise CatalogError("DWARF compile unit inventory conflict")
    except CatalogError:
        raise
    except Exception:
        raise CatalogError("DWARF compile unit validation failed") from None


def build_catalog(retail: bytes, donor: bytes, *, retail_sha256: str = vc.RETAIL_SHA256,
                  donor_sha256: str = vc.DEBUG_SHA256, max_functions: int = MAX_FUNCTIONS,
                  max_group_members: int = MAX_GROUP_MEMBERS,
                  max_neighbor_gap: int = 256) -> dict:
    """Pure bounded API; alternate SHA pins permit source-owned synthetic tests.

    Each ELF is <=64 MiB (shared validated parser). At most 100k Premium entry
    records are accepted; no silent sampling. Scope/order groups require >=2
    functions. CU groups may be singletons. Oversize groups stay unclassified.
    Exact donor bytes are observations, never a source/object matching grade.
    """
    for pin in (retail_sha256, donor_sha256):
        if not isinstance(pin, str) or not _SHA.fullmatch(pin):
            raise CatalogError("invalid reference SHA-256 pin")
    for value, ceiling, label in ((max_functions, MAX_FUNCTIONS, "function bound"),
                                   (max_group_members, MAX_GROUP_MEMBERS, "member bound"),
                                   (max_neighbor_gap, 65536, "neighbor gap")):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise CatalogError("invalid " + label)
    report = vc.compare(retail, donor, retail_sha256=retail_sha256, debug_sha256=donor_sha256)
    _validate_cu_tags(retail, report["retail"])
    _validate_cu_tags(donor, report["debug"])
    return _catalog(report, max_functions, max_group_members, max_neighbor_gap)


def _catalog(report: dict, max_functions: int, max_members: int, max_gap: int) -> dict:
    r, d = report["retail"], report["debug"]
    if len(r["candidates"]) > max_functions:
        raise CatalogError("Premium function budget exceeded; no partial catalog")
    rn, dn = _names_at(r), _names_at(d)
    rc, dc = _cu_index(r, rn), _cu_index(d, dn)
    by_address = defaultdict(list)
    for pair in report["comparison"]["functions"]:
        for raw, _, _ in r["names"][pair["name"]]:
            by_address[raw & ~1].append(pair)
    rranks = {address: i for i, address in enumerate(sorted(r["candidates"]))}
    dranks = {address: i for i, address in enumerate(sorted(d["candidates"]))}
    functions, cu_buckets, scopes = [], defaultdict(list), {}
    for address, candidate in sorted(r["candidates"].items()):
        names = sorted(rn.get(address, ()))
        fid = "%s:%08x:%s" % (r["sha256"], address, "thumb" if candidate["thumb"] else "arm")
        row = dict(id=fid, retail=_boundary(r, candidate), donor=None,
                   symbol_alias_count=len(names), symbol_set_sha256=_hash(names),
                   donor_window_status="UNKNOWN", retail_cu=None, donor_cu=None,
                   group_id=None, confidence="none", reason_codes=[], state="UNCLASSIFIED")
        functions.append(row)
        reasons = row["reason_codes"]
        if candidate["exclusions"]:
            reasons.append("UNSAFE_RETAIL_RANGE")
            continue
        pairs = by_address.get(address, [])
        if not pairs:
            reasons.append("NO_SHARED_SYMBOL")
            continue
        if any(p["status"] not in ("exact_bytes", "different_bytes") for p in pairs):
            reasons.extend(sorted({"PAIR_" + p["status"].upper() for p in pairs
                                   if p["status"] not in ("exact_bytes", "different_bytes")}))
            continue
        donor_addresses = {p["debug_raw_value"] & ~1 for p in pairs}
        if len(donor_addresses) != 1:
            reasons.append("AMBIGUOUS_DONOR_MAPPING")
            continue
        da = next(iter(donor_addresses))
        row["donor"] = _boundary(d, d["candidates"][da])
        row["donor_window_status"] = pairs[0]["status"]
        reasons.append("UNIQUE_SYMBOL_BOUNDARY_PAIR")
        reasons.append("DONOR_WINDOW_EQUAL" if pairs[0]["status"] == "exact_bytes"
                       else "DONOR_WINDOW_DIFFERENT")
        if address != da:
            reasons.append("DONOR_ADDRESS_CHANGED")
        if candidate["size"] != d["candidates"][da]["size"]:
            reasons.append("DONOR_SIZE_CHANGED")
        rcu, rissues = _ownership(rc, address)
        dcu, dissues = _ownership(dc, da)
        row.update(retail_cu=rcu, donor_cu=dcu)
        if rissues or dissues:
            reasons.extend(sorted(set(rissues + dissues)))
            continue
        if (rcu and dcu and rcu["name_sha256"] and dcu["name_sha256"]
                and rcu["name_sha256"] != dcu["name_sha256"]):
            reasons.append("RETAIL_DONOR_CU_NAME_CONFLICT")
            continue
        if rcu or dcu:
            origin, cu = ("retail", rcu) if rcu else ("donor", dcu)
            reasons.append("EXACT_SUBPROGRAM_CU_ASSOCIATION")
            if not rcu:
                reasons.append("DONOR_OWNERSHIP_TRANSFER_INFERRED")
            cu_buckets[(origin, cu["cu_offset"])].append(row)
        else:
            all_names = names + sorted(dn.get(da, ()))
            parsed = {_scope(name) for name in all_names}
            if len(parsed) == 1 and None not in parsed:
                scopes[fid] = _hash(next(iter(parsed)))
            else:
                reasons.append("NO_USABLE_SCOPE_OR_CU")

    groups = []

    def add_group(members: list[dict], kind: str, evidence: dict, confidence: str):
        if len(members) > max_members:
            for row in members:
                row["reason_codes"].append("GROUP_MEMBER_LIMIT")
            return
        gid = "tu_" + _hash(dict(references=[r["sha256"], d["sha256"]], kind=kind,
                                 evidence=evidence, members=sorted(x["id"] for x in members)))
        group = dict(id=gid, kind=kind, confidence=confidence, state="INFERRED",
                     evidence=evidence, members=sorted(x["id"] for x in members),
                     reason_codes=sorted({reason for row in members for reason in row["reason_codes"]}
                                         | {"CU_HYPOTHESIS" if kind == "dwarf_cu" else "SCOPE_ORDER_HYPOTHESIS"}),
                     translation_unit_proven=False, original_object_layout_recovered=False,
                     relocations_recovered=False, source_match="NOT MATCHED")
        groups.append(group)
        for row in members:
            row.update(group_id=gid, confidence=confidence, state="INFERRED")
            row["reason_codes"].append("CU_HYPOTHESIS" if kind == "dwarf_cu" else "SCOPE_ORDER_HYPOTHESIS")

    for (origin, _), members in sorted(cu_buckets.items()):
        cu = members[0][origin + "_cu"]
        evidence = {k: cu[k] for k in ("cu_offset", "name_sha256", "producer_sha256")}
        add_group(members, "dwarf_cu", dict(origin=origin, **evidence),
                  "high" if origin == "retail" and cu["name_sha256"] else "medium")

    # Neighbor order only corroborates an identical *complete* symbol scope.
    # Rank includes excluded/unpaired entries, so these break a scope run.
    # Reordered builds, section transitions and intervening scopes break runs.
    run = []

    def flush():
        if len(run) >= 2:
            add_group(run, "scope_order", dict(scope_sha256=scopes[run[0]["id"]],
                      sections=[run[0]["retail"]["section_index"], run[0]["donor"]["section_index"]],
                      neighbor_order="consecutive_in_both_linked_images"), "low")
        elif run:
            run[0]["reason_codes"].append("SCOPE_WITHOUT_CORROBORATING_NEIGHBOR")

    for row in functions:
        if row["id"] not in scopes:
            flush()
            run = []
            continue
        if run:
            previous = run[-1]
            consecutive = scopes[row["id"]] == scopes[previous["id"]]
            for edition, ranks in (("retail", rranks), ("donor", dranks)):
                a, b = previous[edition], row[edition]
                consecutive &= (b["section_index"] == a["section_index"]
                                and ranks[b["address"]] == ranks[a["address"]] + 1
                                and 0 <= b["address"] - a["address"] - a["size"] <= max_gap)
            if not consecutive:
                flush()
                run = []
        run.append(row)
    flush()
    for row in functions:
        row["reason_codes"] = sorted(set(row["reason_codes"]))

    references = {
        "premium": dict(sha256=r["sha256"], edition_pin="premium_retail" if r["sha256"] == vc.RETAIL_SHA256
                        else "explicit_unrecognized_reference"),
        "donor": dict(sha256=d["sha256"], edition_pin="cyrus_donor" if d["sha256"] == vc.DEBUG_SHA256
                      else "explicit_unrecognized_reference"),
    }
    counts = dict(premium_entry_boundaries=len(functions), donor_entry_boundaries=len(d["candidates"]),
                  premium_defined_symbol_entries=sum(t["summary"]["defined_func_entries"]
                                                    for t in r["tables"] if t["kind"] == "symtab"),
                  donor_defined_symbol_entries=sum(t["summary"]["defined_func_entries"]
                                                  for t in d["tables"] if t["kind"] == "symtab"),
                  candidate_groups=len(groups), grouped_functions=sum(x["group_id"] is not None for x in functions),
                  unclassified_functions=sum(x["group_id"] is None for x in functions),
                  premium_compile_units=len(r["dwarf"]["compile_units"]),
                  donor_compile_units=len(d["dwarf"]["compile_units"]),
                  proven_original_tus=0, recovered_original_objects=0, source_matched_functions=0)
    return dict(schema=SCHEMA, tool_version=TOOL_VERSION, tool_source_hashes=_source_pins(),
                references=references, counts=counts,
                confidence_bins=dict(sorted(Counter(g["confidence"] for g in groups).items())),
                function_confidence_bins=dict(sorted(Counter(f["confidence"] for f in functions).items())),
                donor_window_counts=dict(sorted(Counter(f["donor_window_status"] for f in functions).items())),
                comparison_status_counts=report["comparison"]["summary"]["name_status_counts"],
                compiler=dict(identity="UNKNOWN", flags="UNKNOWN", source_match="NOT MATCHED"),
                compiler_comment_version_clues={"premium": r["compiler_evidence"]["gcc_comment_versions"],
                                               "donor": d["compiler_evidence"]["gcc_comment_versions"]},
                dwarf_backend_versions={role: e["dwarf"].get("backend_version")
                                        for role, e in (("premium", r), ("donor", d))},
                limits=dict(max_functions=max_functions, max_group_members=max_members,
                            max_neighbor_gap=max_gap, max_output_bytes=MAX_OUTPUT_BYTES),
                staging_contract=dict(automatic_staging_allowed=False, original_tu_proven=False,
                                      reference_kind="linked_function_boundaries_only",
                                      required_checks=["recheck_reference_sha256", "review_each_boundary_and_mode",
                                                       "review_aliases_and_dependencies", "build_relocation_worksheet",
                                                       "record_compiler_flags_source_hash", "independent_comparison"]),
                groups=sorted(groups, key=lambda g: g["id"]), functions=functions)


def public_summary(catalog: dict) -> dict:
    """Fixed allowlist: never forward function/group names, addresses or paths."""
    def counts(source, keys):
        result = {k: v for k, v in source.items() if k in keys}
        if any(type(v) is not int or v < 0 for v in result.values()):
            raise CatalogError("invalid summary count")
        return result

    references = {}
    for role in ("premium", "donor"):
        source = catalog["references"][role]
        sha = source["sha256"]
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise CatalogError("invalid summary reference hash")
        known_sha, label = ((vc.RETAIL_SHA256, "premium_retail") if role == "premium"
                            else (vc.DEBUG_SHA256, "cyrus_donor"))
        references[role] = dict(sha256=sha, edition_pin=label if sha == known_sha
                                else "explicit_unrecognized_reference")
    hashes = {k: catalog["tool_source_hashes"][k]
              for k in ("catalog_sha256", "version_compare_sha256", "indexer_sha256")}
    if any(not isinstance(v, str) or not _SHA.fullmatch(v) for v in hashes.values()):
        raise CatalogError("invalid summary tool hash")
    totals = counts(catalog["counts"], {
        "premium_entry_boundaries", "donor_entry_boundaries", "premium_defined_symbol_entries",
        "donor_defined_symbol_entries", "candidate_groups", "grouped_functions", "unclassified_functions",
        "premium_compile_units", "donor_compile_units", "proven_original_tus",
        "recovered_original_objects", "source_matched_functions"})
    return dict(schema=SUMMARY_SCHEMA, tool_version=TOOL_VERSION,
                tool_source_hashes=hashes, references=references, counts=totals,
                confidence_bins=counts(catalog["confidence_bins"], {"high", "medium", "low"}),
                function_confidence_bins=counts(catalog["function_confidence_bins"], {"high", "medium", "low", "none"}),
                donor_window_counts=counts(catalog["donor_window_counts"], {"exact_bytes", "different_bytes", "UNKNOWN"}))


def _read(path: Path) -> bytes:
    # Shared reader checks regular files, bounded reads and changes in size.
    return vc._read(path)


def _output_path(path: Path) -> Path:
    cache = ROOT / ".cache"
    if cache.is_symlink():
        raise CatalogError("cache root must not be a symlink")
    out = path.expanduser().resolve()
    if out == cache or not out.is_relative_to(cache) or out.suffix != ".json":
        raise CatalogError("outputs must be JSON files inside repository .cache")
    return out


def _write_json(path: Path, value: dict, byte_limit: int) -> None:
    """Stream JSON to a mode-0600 temporary file, fail before replacing on cap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".donor-tu-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            count = 0
            for part in json.JSONEncoder(sort_keys=True, ensure_ascii=True, separators=(",", ":")).iterencode(value):
                chunk = part.encode("ascii")
                count += len(chunk)
                if count + 1 > byte_limit:
                    raise CatalogError("JSON output byte budget exceeded")
                stream.write(chunk)
            stream.write(b"\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _run(config: dict) -> int:
    try:
        if sys.platform.startswith("linux"):
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            cap = 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (min(soft, cap) if soft >= 0 else cap, hard))
        out = _output_path(Path(config["out"]))
        summary_out = _output_path(Path(config["summary_out"])) if config["summary_out"] else None
        sources = [Path(config[k]) for k in ("retail", "donor")]
        outputs = [p for p in (out, summary_out) if p is not None]
        if len(set(outputs)) != len(outputs):
            raise CatalogError("worksheet and summary outputs must differ")
        for destination in outputs:
            if destination.exists():
                raise CatalogError("output already exists; choose a fresh cache filename")
            for source in sources:
                if source.resolve() == destination:
                    raise CatalogError("output must not alias an input")
        catalog = build_catalog(_read(sources[0]), _read(sources[1]),
                                retail_sha256=config["retail_sha256"], donor_sha256=config["donor_sha256"],
                                max_functions=config["max_functions"],
                                max_group_members=config["max_group_members"], max_neighbor_gap=config["max_neighbor_gap"])
        summary = public_summary(catalog)
        _write_json(out, catalog, MAX_OUTPUT_BYTES)
        if summary_out:
            _write_json(summary_out, summary, MAX_SUMMARY_BYTES)
        # stdout is public-safe aggregate metadata, including on Windows/macOS.
        print(json.dumps(summary, sort_keys=True))
        return 0
    except CatalogError as exc:
        # CatalogError messages are fixed source-owned text, never input values.
        print("Donor TU catalog failed: " + str(exc), file=sys.stderr)
        return 2
    except vc.CompareError as exc:
        known = {"SHA-256 mismatch": "REFERENCE_SHA256_MISMATCH",
                 "DWARF present: locally installed pyelftools is required": "DWARF_BACKEND_MISSING",
                 "DWARF parse failed; comparison blocked": "DWARF_PARSE_FAILED"}
        print("Donor TU catalog failed: " + known.get(str(exc), "ELF_OR_DWARF_EVIDENCE_REJECTED"), file=sys.stderr)
        return 2
    except Exception:
        # Backend exceptions may contain private paths or strings. Do not echo.
        print("Donor TU catalog failed: IO_RESOURCE_OR_INTERNAL_ERROR; no match claim.", file=sys.stderr)
        return 2


def _child(config: dict) -> None:
    raise SystemExit(_run(config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retail", required=True, type=Path)
    parser.add_argument("--donor", required=True, type=Path)
    parser.add_argument("--retail-sha256", default=vc.RETAIL_SHA256)
    parser.add_argument("--donor-sha256", default=vc.DEBUG_SHA256)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary-out", type=Path, help="optional public-safe summary, initially kept in .cache")
    parser.add_argument("--max-functions", type=int, default=MAX_FUNCTIONS)
    parser.add_argument("--max-group-members", type=int, default=MAX_GROUP_MEMBERS)
    parser.add_argument("--max-neighbor-gap", type=int, default=256)
    args = parser.parse_args(argv)
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    # spawn + parent timeout bounds backend eager DWARF parsing on all three
    # supported host OSes; no reliance on Unix-only signals or a live IDA.
    process = multiprocessing.get_context("spawn").Process(target=_child, args=(config,))
    process.start()
    process.join(TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        print("Donor TU catalog exceeded 120-second budget; private temporary output may remain.", file=sys.stderr)
        return 2
    return 0 if process.exitcode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
