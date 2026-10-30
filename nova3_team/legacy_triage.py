"""Bounded offline triage of legacy source hypotheses; never a match validator.

Only source and JSON metadata are read. No compiler, ELF, log, assembly,
decompiler, network service or historical evidence writer is invoked.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat

from . import legacy_validation as legacy
from . import verification_gate as gate

SCHEMA = "nova3.legacy-triage/1"
PREMIUM_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
MAX_BLOCKS = 256
MAX_ENTRIES = 4096
CATEGORIES = {
    "CONFLICTING_EVIDENCE": "Resolve source/reference/identity conflicts before compiling.",
    "RESEARCH_ONLY": "Establish explicit source provenance and a separately reviewed edition/ELF pin.",
    "COMPILER_ABI_GATE_NEEDED": "Record identified compiler, flags, ABI and source-bound build/test evidence.",
    "PRIVATE_ORIGINAL_COMPARISON_NEEDED": "Recheck original-local function bounds, mode, relocations and comparison; metadata is insufficient.",
}


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise gate.GateError(code)


def _files(root: Path, base: str, recursive: bool) -> list[str]:
    """Bounded descriptor walk; reject links, including intermediate directories."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in base.split("/"):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                _need(base == legacy.EVIDENCE_ROOT, "missing_source_root")
                return []
            os.close(fd)
            fd = child
        names = []
        visited = 0

        def walk(directory: int, prefix: str, depth: int) -> None:
            nonlocal visited
            _need(depth <= 8, "directory_depth_limit")
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    _need(visited <= MAX_ENTRIES, "inventory_entry_limit")
                    info = entry.stat(follow_symlinks=False)
                    _need(not stat.S_ISLNK(info.st_mode), "symlink_in_inventory")
                    name = prefix + "/" + entry.name
                    if stat.S_ISDIR(info.st_mode) and recursive:
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory)
                        try:
                            walk(child, name, depth + 1)
                        finally:
                            os.close(child)
                    elif (legacy.BLOCK_RE.fullmatch(entry.name) if recursive else
                          entry.name.endswith(".json")):
                        _need(stat.S_ISREG(info.st_mode), "nonregular_inventory_file")
                        names.append(name)
                        _need(len(names) <= MAX_BLOCKS, "inventory_file_limit")

        walk(fd, base, 0)
        return sorted(names)
    finally:
        os.close(fd)


def _hashes(values: list[object]) -> list[str]:
    return sorted({gate._hash(v) for v in values if v is not None})


def _record(root: Path, path: str) -> dict:
    data = gate._read(root, path)
    document = gate._load(data)
    _need(isinstance(document, dict), "invalid_evidence")
    functions = document.get("functions", [])
    _need(isinstance(functions, list) and len(functions) <= MAX_ENTRIES
          and all(isinstance(f, dict) for f in functions), "invalid_functions")
    for f in functions:
        value = f.get("address", f.get("elf_va"))
        _need(type(value) is not bool and legacy._address(value) is not None, "invalid_address")
    paths, hashes = [], []
    for container, field, sha in (("source", "path", "sha256"),
                                  ("build_provenance", "source_path", "source_sha256")):
        value = document.get(container)
        _need(container not in document or isinstance(value, dict), "invalid_source_metadata")
        if isinstance(value, dict):
            if field in value:
                p = value[field]
                _need(legacy._safe_repo_path(p) == p and isinstance(p, str)
                      and p.startswith(legacy.SOURCE_ROOT + "/")
                      and legacy.BLOCK_RE.fullmatch(Path(p).name) is not None,
                      "invalid_declared_source_path")
                paths.append(p)
            # Some old build records have an unbound per-file hash map here.
            # It is not the scalar source identity understood by the inventory.
            if sha in value and (field in value or not isinstance(value[sha], dict)):
                hashes.append(gate._hash(value[sha]))
    reference = document.get("reference", {})
    _need(isinstance(reference, dict), "invalid_reference")
    _need("edition" not in reference or reference["edition"] in
          ("premium", "freemium", "cyrus"), "invalid_reference_edition")
    refs = _hashes([reference.get(k) for k in ("sha256", "expected_sha256", "observed_sha256")])
    result = legacy._metadata(document, path)
    # Deliberately project only typed metadata. Arbitrary prose, paths to local
    # originals, tool commands, logs and instruction-like fields are discarded.
    result.update(evidence_sha256=hashlib.sha256(data).hexdigest(),
                  source_paths=sorted(set(paths)), source_hashes=sorted(set(hashes)),
                  reference_hashes=refs,
                  reported_edition=reference.get("edition"),
                  metadata_presence={
                      "compile": any(document.get(k) for k in (
                          "build_provenance", "builds", "builds_and_checks", "compiler_evidence", "toolchain")),
                      "test": any(document.get(k) for k in (
                          "tests", "unit_tests", "checks", "reference_free_verification", "verification")),
                      "comparison": any(document.get(k) for k in (
                          "machine_code_comparison", "matching_evidence", "oracle", "native_oracle")),
                      "provenance": any(document.get(k) for k in (
                          "analysis_provenance", "provenance", "evidence_basis", "source")),
                  },
                  positive_match_claim=_positive_claim(document),
                  function_details=[{
                      "address": legacy._address(f.get("address", f.get("elf_va"))),
                      "size": (f.get("size_bytes", f.get("size")) if
                               type(f.get("size_bytes", f.get("size"))) is int and
                               0 < f.get("size_bytes", f.get("size")) <= 16_777_216 else None),
                      "thumb": f.get("thumb") if type(f.get("thumb")) is bool else None,
                  } for f in functions])
    return result


def _positive_claim(document: object) -> bool:
    # Claims are quarantined, not validated. Avoid interpreting free prose.
    positive = {"MATCHED", "MATCHING", "VERIFIED", "ORIGINAL_OBJECT_MATCH", "MATCHED_ARM",
                "MATCHED_LIBRARY_SHA256", "LINKED_FUNCTION_WINDOW_EQUAL",
                "BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS", "RELOCATED_LINKED_WINDOW_MATCH"}
    fields = {"matching", "matching_claim", "assembly_comparison", "machine_code_match",
              "verification_grade", "source_matching_verified", "exact_bytes", "code_window_equal",
              "grade", "status", "current_target_machine_match", "machine_match",
              "original_object_recovered", "original_so_hash_matching_verified"}
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in fields and (value is True or isinstance(value, str)
                                      and value.strip().upper().replace(" ", "_") in positive):
                    return True
                if isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _donors(root: Path, path: str | None, expected: str) -> tuple[set[int], dict | None]:
    """Reported donor cues only; this does not reopen either original ELF."""
    if path is None:
        return set(), None
    data = gate._read(root, path)
    d = gate._load(data)
    _need(isinstance(d, dict) and d.get("schema") == "nova3.donor-plan/1", "invalid_donor_plan")
    _need(gate._hash(d.get("retail_sha256")) == expected, "donor_reference_mismatch")
    gate._hash(d.get("debug_sha256"))
    rows = d.get("candidates")
    _need(isinstance(rows, list) and len(rows) <= MAX_BLOCKS, "invalid_donor_count")
    seen, exact = set(), set()
    for row in rows:
        _need(isinstance(row, dict), "invalid_donor_row")
        address = gate._integer(row.get("retail_address"), 0, 0xffffffff)
        _need(address not in seen, "duplicate_donor_identity")
        seen.add(address)
        for field in ("source_matching_verified", "original_compiler_verified", "original_object_recovered"):
            _need(row.get(field) is False, "false_donor_promotion")
        _need(row.get("donor_byte_status") in {"exact_bytes", "different_bytes"}, "invalid_donor_status")
        if row["donor_byte_status"] == "exact_bytes":
            exact.add(address)
    return exact, {"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                   "retail_sha256": expected, "debug_sha256": gate._hash(d["debug_sha256"]),
                   "binary_truth_rechecked": False}


def _dependencies(root: Path, source_path: str, includes: list[str]) -> list[dict]:
    """Check literal sibling includes only, without inventing a build graph."""
    result = []
    for include in includes:
        path = posixpath.join(posixpath.dirname(source_path), include)
        row = {"include": include, "path": None, "sha256": None, "status": "UNKNOWN_NOT_READ"}
        # Do not collapse '..' across an unexamined directory or symlink and
        # then describe the normalized target as a resolved C++ dependency.
        if legacy._safe_repo_path(path) == path and path.startswith(legacy.SOURCE_ROOT + "/") and Path(path).suffix in {
                ".h", ".hpp", ".hxx", ".c", ".cc", ".cpp"}:
            row["path"] = path
            try:
                data = gate._read(root, path)
            except FileNotFoundError:
                row["status"] = "MISSING_LOCAL_INCLUDE"
            else:
                row.update(status="PRESENT_HASHED", sha256=hashlib.sha256(data).hexdigest())
        result.append(row)
    return result


def triage(repo: Path, *, expected_reference_sha256: str = PREMIUM_SHA256,
           edition: str = "premium", gate_manifest: str | None = None,
           donor_plan: str | None = None) -> dict:
    """Return a private worksheet and an aggregate-only public summary.

    Pins are operator-selected identities, never proof that an ELF was opened.
    Legacy filename/address associations remain hypotheses. IDs track paths
    across source changes; source and evidence digests pin each observation.
    """
    root = Path(repo).resolve(strict=True)
    _need(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
          and os.open in os.supports_dir_fd and os.scandir in os.supports_fd,
          "secure_inventory_unavailable")
    expected = gate._hash(expected_reference_sha256)
    _need(edition in {"premium", "freemium", "cyrus"}, "invalid_edition")
    _need(expected != PREMIUM_SHA256 or edition == "premium", "edition_pin_conflict")
    source_paths = _files(root, legacy.SOURCE_ROOT, True)
    records = [_record(root, p) for p in _files(root, legacy.EVIDENCE_ROOT, False)]
    _need(sum(len(r["functions"]) for r in records) <= MAX_ENTRIES, "function_inventory_limit")
    explicit, inferred = defaultdict(list), defaultdict(list)
    for r in records:
        if r["source_paths"]:
            for p in r["source_paths"]:
                explicit[p].append(r)
        else:
            for address in sorted({f["address"] for f in r["functions"]}):
                inferred[address].append(r)
    checked = {}
    gate_origin = None
    if gate_manifest:
        manifest_bytes = gate._read(root, gate_manifest)
        manifest = gate._load(manifest_bytes)
        checked_report = gate.check(root, gate_manifest, expected_reference_sha256=expected)
        # Retain no unchecked claim from a concurrently edited manifest.
        _need(gate._read(root, gate_manifest) == manifest_bytes and checked_report["metadata_valid"],
              "gate_manifest_changed")
        checked = {c["source"]["path"]: c for c in manifest["claims"]}
        _need(set(checked) <= set(source_paths), "gate_source_outside_inventory")
        gate_origin = {"path": gate_manifest, "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
    donors, donor_origin = _donors(root, donor_plan, expected)
    blocks = []
    for path in source_paths:
        source = gate._read(root, path)
        source_sha = hashlib.sha256(source).hexdigest()
        entry = "0x" + legacy.BLOCK_RE.fullmatch(Path(path).name).group(1).lower()
        recs = explicit.get(path) or inferred.get(entry, [])
        association = ("EXPLICIT_SOURCE_PATH" if explicit.get(path) else
                       "INFERRED_FROM_BLOCK_ADDRESS" if recs else "NO_EVIDENCE")
        hashes = sorted({h for r in recs for h in r["source_hashes"]})
        refs = sorted({h for r in recs for h in r["reference_hashes"]})
        conflicts = set()
        if any(h != source_sha for h in hashes):
            conflicts.add("SOURCE_HASH_MISMATCH")
        if refs and refs != [expected]:
            conflicts.add("REFERENCE_PIN_CONFLICT")
        if any(len(r["source_paths"]) > 1 for r in recs):
            conflicts.add("CONFLICTING_SOURCE_PATHS")
        if any(r["reported_edition"] not in (None, edition) for r in recs):
            conflicts.add("EDITION_PIN_CONFLICT")
        if any(r["positive_match_claim"] for r in recs):
            conflicts.add("UNRECHECKED_POSITIVE_MATCH_CLAIM")
        details = defaultdict(set)
        for r in recs:
            if len({f["address"] for f in r["function_details"]}) != len(r["function_details"]):
                conflicts.add("DUPLICATE_FUNCTION_IDENTITY")
            for f in r["function_details"]:
                size = f["size"]
                _need(size is None or type(size) is int and 0 < size <= 16_777_216,
                      "invalid_function_size")
                details[f["address"]].add((size, f["thumb"]))
        if any(len({v[0] for v in values if v[0] is not None}) > 1 or
               len({v[1] for v in values if v[1] is not None}) > 1 for values in details.values()):
            conflicts.add("CONFLICTING_FUNCTION_METADATA")
        claim = checked.get(path)
        if claim:
            _need(gate._hash(claim["source"]["sha256"]) == source_sha, "gate_source_changed")
        comparison_binding = "NOT_REPORTED"
        if claim and "comparison" in claim:
            comparison = claim["comparison"]
            address = legacy._address(comparison.get("reference_address", comparison.get("address")))
            size = comparison.get("reference_size", comparison.get("size"))
            mode = comparison.get("original_thumb", comparison.get("candidate_thumb"))
            comparison_binding = "UNKNOWN_NO_FUNCTION_METADATA"
            if details:
                comparison_binding = "CONSISTENT_METADATA_ONLY"
                if address not in details or any(
                        known_size is not None and known_size != size or
                        known_mode is not None and known_mode != mode
                        for known_size, known_mode in details.get(address, ())):
                    comparison_binding = "CONFLICT"
                    conflicts.add("COMPARISON_FUNCTION_IDENTITY_CONFLICT")
        includes = sorted({m.decode("ascii") for m in re.findall(
            rb'^\s*#\s*include\s*"([A-Za-z0-9_./-]+)"', source, re.MULTILINE)})
        _need(len(includes) <= 64, "include_count_limit")
        dependencies = _dependencies(root, path, includes)
        # Literal quoted includes are a bounded effort cue, not a dependency
        # graph or proof that macro/system includes are resolved.
        category = ("CONFLICTING_EVIDENCE" if conflicts else "RESEARCH_ONLY"
                    if not recs or association != "EXPLICIT_SOURCE_PATH" or hashes != [source_sha]
                    or refs != [expected] else "PRIVATE_ORIGINAL_COMPARISON_NEEDED"
                    if claim and "build" in claim and "comparison" in claim
                    and comparison_binding == "CONSISTENT_METADATA_ONLY"
                    else "COMPILER_ABI_GATE_NEEDED")
        blocks.append({
            "id": "legacy-" + hashlib.sha256(path.encode()).hexdigest()[:24],
            "source_path": path, "source_sha256": source_sha,
            "source_hash_status": "MISMATCH" if "SOURCE_HASH_MISMATCH" in conflicts else
                                  "CURRENT" if hashes == [source_sha] else "UNRECORDED",
            "source_bytes": len(source), "entry_address_hypothesis": entry,
            "edition_pin": edition, "expected_reference_sha256": expected,
            "reported_reference_sha256": refs, "reference_binary_rechecked": False,
            "evidence_association": association,
            "evidence": [{"path": r["file"], "sha256": r["evidence_sha256"],
                          "declared_source_sha256": r["source_hashes"],
                          "reported_reference_sha256": r["reference_hashes"]} for r in recs],
            "reported_function_evidence": [f for r in recs for f in r["functions"]],
            "reported_function_details": [f for r in recs for f in r["function_details"]],
            "metadata_presence": {k: bool(any(r["metadata_presence"][k] for r in recs))
                                  for k in ("compile", "test", "comparison", "provenance")},
            "gate_metadata_valid": bool(claim),
            "reported_gate_grade": claim["grade"] if claim else None,
            "build_metadata_valid": bool(claim and "build" in claim),
            "comparison_metadata_valid": bool(claim and "comparison" in claim),
            "comparison_function_binding": comparison_binding,
            "compile_and_test_execution_rechecked": False,
            "donor_cue": "REPORTED_EXACT_NOT_RECHECKED" if int(entry, 16) in donors else "UNKNOWN",
            "verified_donor_cue": False,
            "quoted_include_hypotheses": includes, "local_include_checks": dependencies,
            "dependency_resolution": "OTHER_DEPENDENCIES_UNKNOWN",
            "category": category, "conflicts": sorted(conflicts),
            "next_proof_step": CATEGORIES[category],
            "machine_match": "NOT MATCHED", "proof_grades": {g: "NOT_VERIFIED" for g in sorted(gate.PROOF_GRADES)},
        })
    # Same entry in multiple source paths cannot establish unique ownership.
    identities = Counter(b["entry_address_hypothesis"] for b in blocks)
    for b in blocks:
        if identities[b["entry_address_hypothesis"]] > 1:
            b["conflicts"].append("DUPLICATE_BLOCK_IDENTITY")
            b.update(category="CONFLICTING_EVIDENCE", next_proof_step=CATEGORIES["CONFLICTING_EVIDENCE"])
    blocks.sort(key=lambda b: (b["category"] == "CONFLICTING_EVIDENCE",
                              b["evidence_association"] != "EXPLICIT_SOURCE_PATH",
                              sum(d["status"] != "PRESENT_HASHED" for d in b["local_include_checks"]),
                              len(b["quoted_include_hypotheses"]), b["source_bytes"], b["id"]))
    # No reported donor cue breaks a tie: no donor bytes were independently
    # rechecked here. Verified donor priority remains unavailable/UNKNOWN.
    tasks = [{"id": b["id"], "priority": i, "source_path": b["source_path"],
              "source_sha256": b["source_sha256"], "edition_pin": edition,
              "reference_sha256": expected, "evidence_association": b["evidence_association"],
              "reported_reference_sha256": b["reported_reference_sha256"],
              "reported_function_details": b["reported_function_details"],
              "evidence": b["evidence"], "category": b["category"],
              "conflicts": b["conflicts"],
              "donor_cue": b["donor_cue"], "verified_donor_cue": False,
              "local_include_checks": b["local_include_checks"],
              "estimated_effort_cues": {"source_bytes": b["source_bytes"],
                                        "quoted_include_count": len(b["quoted_include_hypotheses"])},
              "next_proof_step": b["next_proof_step"], "requires_private_original": True,
              "private_original_status": "UNKNOWN_NOT_OPENED", "machine_match": "NOT MATCHED",
              "acceptance": list(legacy.PLAN_CHECKS)} for i, b in enumerate(blocks, 1)]
    summary = {
        "schema": SCHEMA, "recovered_source_blocks": len(blocks),
        "categories": {c: sum(b["category"] == c for b in blocks) for c in CATEGORIES},
        "explicit_mappings": sum(b["evidence_association"] == "EXPLICIT_SOURCE_PATH" for b in blocks),
        "inferred_mappings": sum(b["evidence_association"] == "INFERRED_FROM_BLOCK_ADDRESS" for b in blocks),
        "without_evidence": sum(b["evidence_association"] == "NO_EVIDENCE" for b in blocks),
        "current_source_hashes": sum(b["source_hash_status"] == "CURRENT" for b in blocks),
        "stale_source_hashes": sum(b["source_hash_status"] == "MISMATCH" for b in blocks),
        "unrecorded_source_hashes": sum(b["source_hash_status"] == "UNRECORDED" for b in blocks),
        "gate_metadata_valid_blocks": len(checked),
        "conflict_counts": dict(sorted(Counter(c for b in blocks for c in b["conflicts"]).items())),
        "orphan_explicit_mappings": len(set(explicit) - set(source_paths)),
        "reported_metadata_presence": {k: sum(b["metadata_presence"][k] for b in blocks)
                                       for k in ("compile", "test", "comparison", "provenance")},
        "independently_verified_original_machine_matches": 0,
        "original_binary_truth_rechecked": False, "verified_donor_cues": 0,
        "inventory_empty": not blocks, "machine_match": "NOT MATCHED",
    }
    return {"schema": SCHEMA, "summary": summary, "blocks": blocks, "future_task_inputs": tasks,
            "gate_manifest": gate_origin, "donor_plan": donor_origin,
            "orphan_explicit_evidence": [
                {"source_path": p, "evidence_paths": sorted(r["file"] for r in explicit[p])}
                for p in sorted(set(explicit) - set(source_paths))],
            "ordering": "non-conflicting, explicit provenance, fewer unresolved local includes, fewer quoted includes, smaller source, stable ID; donor verification unavailable",
            "automated_compiles": 0, "github_tasks_created": 0}


def _write_private(root: Path, path: str, report: dict) -> None:
    # One new worksheet directly under ignored .cache, no clobbers or links.
    _need(re.fullmatch(r"\.cache/[A-Za-z0-9_-]+\.json", path) is not None, "invalid_output_path")
    ignore = gate._read(root, ".gitignore").decode("utf-8").splitlines()
    _need(any(line.strip() in {"/.cache/", ".cache/"} for line in ignore)
          and not any(line.lstrip().startswith("!") for line in ignore), "cache_ignore_not_established")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(".cache", mode=0o700, dir_fd=fd)
        except FileExistsError:
            pass
        child = os.open(".cache", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            output = os.open(path.split("/")[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=child)
            with os.fdopen(output, "w", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write("\n")
        finally:
            os.close(child)
    finally:
        os.close(fd)


def _rejected() -> None:
    print(json.dumps({"schema": SCHEMA, "error": "legacy_triage_rejected",
                      "independently_verified_original_machine_matches": 0, "machine_match": "NOT MATCHED"}))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes rejected values, which may be private paths
        # or pasted tool output. Keep argument failures as opaque as I/O errors.
        _rejected()
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--expected-reference-sha256", default=PREMIUM_SHA256)
    parser.add_argument("--edition", choices=("premium", "freemium", "cyrus"), default="premium")
    parser.add_argument("--gate-manifest", help="Optional repository-relative verification_gate manifest")
    parser.add_argument("--donor-plan", help="Optional repository-relative donor metadata; never verified here")
    parser.add_argument("--out", help="New private .cache/NAME.json worksheet; existing files rejected")
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = triage(args.repo, expected_reference_sha256=args.expected_reference_sha256,
                        edition=args.edition, gate_manifest=args.gate_manifest, donor_plan=args.donor_plan)
        if args.out:
            _write_private(args.repo.resolve(strict=True), args.out, report)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _rejected()
        return 2
    print(json.dumps(report["summary"], sort_keys=True))
    return 2 if args.require_verified or report["summary"]["inventory_empty"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
