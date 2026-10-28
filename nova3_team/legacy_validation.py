"""Read-only inventory of source recovered before verified compiler matching.

This module does not analyse proprietary binaries or assert code equivalence.
It correlates source-owned C++ with existing public evidence, checks whether
the evidence still describes the current source, and identifies where local
compiler/objdiff revalidation is needed. Reports contain no executable bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


SCHEMA = "nova3.legacy-revalidation/1"
PLAN_SCHEMA = "nova3.legacy-revalidation-plan/1"
BLOCK_RE = re.compile(r"block_([0-9a-fA-F]{8})\.cpp$")
HASH_RE = re.compile(r"[a-fA-F0-9]{64}\Z")
GRADE_ORDER = {"hypothesis": 0, "tested": 1, "behavioural": 2, "matching": 3}
SOURCE_ROOT = "reconstruction/recovered"
EVIDENCE_ROOT = "coordination/evidence"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_repo_path(value: object) -> str | None:
    """Accept only relative, nonescaping, repository-visible source paths."""
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in ("..", ".") for part in value.split("/")):
        return None
    return path.as_posix()


def _address(value: object) -> str | None:
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value, 0)
        except ValueError:
            return None
    else:
        return None
    if not 0 <= number <= 0xffffffff:
        return None
    return f"0x{number:08x}"


def _root_source(evidence: dict) -> str | None:
    for container, field in (("source", "path"), ("build_provenance", "source_path")):
        value = evidence.get(container)
        if isinstance(value, dict):
            resolved = _safe_repo_path(value.get(field))
            if resolved and resolved.startswith(SOURCE_ROOT + "/") and resolved.endswith(".cpp"):
                return resolved
    return None


def _declared_source_sha(evidence: dict) -> str | None:
    for container, field in (("source", "sha256"), ("build_provenance", "source_sha256")):
        value = evidence.get(container)
        if isinstance(value, dict):
            sha = value.get(field)
            if isinstance(sha, str) and HASH_RE.fullmatch(sha):
                return sha.lower()
    return None


def _match_declaration(evidence: dict, function: dict) -> str:
    compiler_evidence = evidence.get("compiler_evidence")
    if not isinstance(compiler_evidence, dict):
        compiler_evidence = {}
    for claim in (function.get("matching_claim"), function.get("assembly_comparison"),
                  evidence.get("matching_claim"),
                  compiler_evidence.get("assembly_comparison")):
        if isinstance(claim, str):
            value = claim.strip().upper().replace(" ", "_")
            if value in ("MATCHED", "NOT_MATCHED", "MISMATCH", "UNKNOWN"):
                return value
    return "UNREPORTED"


def _metadata(evidence: dict, path: str) -> dict:
    functions = []
    for fn in evidence.get("functions", []):
        if not isinstance(fn, dict):
            continue
        address = _address(fn.get("address", fn.get("elf_va")))
        if address is None:
            continue
        grade = fn.get("verification_grade")
        grade = grade.strip().lower() if isinstance(grade, str) else "unreported"
        if grade not in GRADE_ORDER:
            grade = "unreported"
        size = fn.get("size_bytes", fn.get("size"))
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= 16_777_216:
            size = None
        functions.append({"address": address, "reported_grade": grade,
                          "reported_machine_match": _match_declaration(evidence, fn),
                          "known_reference_size_bytes": size})
    reference = evidence.get("reference")
    reference_sha = reference.get("sha256") if isinstance(reference, dict) else None
    if not isinstance(reference_sha, str) or not HASH_RE.fullmatch(reference_sha):
        reference_sha = None
    return {"file": path, "source_path": _root_source(evidence),
            "source_sha256": _declared_source_sha(evidence),
            "reference_sha256": reference_sha.lower() if reference_sha else None,
            "functions": functions}


def inventory(root: Path) -> dict:
    """Audit all recovered C++ blocks, without reading or executing an APK/ELF.

    Source correspondence and claimed grades are metadata, not independently
    trusted proof. Even a historical `MATCHED` declaration cannot be promoted
    until target compiler flags and original bytes have been verified locally.
    """
    root = Path(root).resolve(strict=True)
    source_dir = root / SOURCE_ROOT
    evidence_dir = root / EVIDENCE_ROOT
    if not source_dir.is_dir():
        raise ValueError(f"Missing {SOURCE_ROOT}")

    issues = []
    evidence_by_source: dict[str, list[dict]] = {}
    evidence_by_block_address: dict[str, list[dict]] = {}
    if evidence_dir.is_dir():
        for path in sorted(evidence_dir.glob("*.json")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or path.stat().st_size > 2_000_000:
                issues.append({"evidence": relative, "reason": "unsafe_or_oversized_file"})
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                issues.append({"evidence": relative, "reason": "invalid_json"})
                continue
            if not isinstance(document, dict):
                issues.append({"evidence": relative, "reason": "invalid_schema"})
                continue
            record = _metadata(document, relative)
            if record["source_path"]:
                evidence_by_source.setdefault(record["source_path"], []).append(record)
            elif record["functions"]:
                # Older evidence may omit the recovered filename. Correlating a
                # block's entry address is only a hypothesis, never proven TU ownership.
                for fn in record["functions"]:
                    evidence_by_block_address.setdefault(fn["address"], []).append(record)

    blocks = []
    for path in sorted(source_dir.rglob("block_*.cpp")):
        if path.is_symlink() or not path.is_file():
            continue
        match = BLOCK_RE.fullmatch(path.name)
        if not match:
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        sha = _sha(data)
        start = "0x" + match.group(1).lower()
        explicit = evidence_by_source.get(relative, [])
        records = explicit if explicit else evidence_by_block_address.get(start, [])
        association = "EXPLICIT_SOURCE_PATH" if explicit else (
            "INFERRED_FROM_BLOCK_ADDRESS" if records else "NO_EVIDENCE")
        provenance = []
        all_functions = {}
        review_claim = False
        for evidence in records:
            declared = evidence["source_sha256"]
            observed = ("MATCH" if declared == sha else "MISMATCH") if declared else "UNRECORDED"
            provenance.append({"file": evidence["file"],
                               "source_sha256_check": observed,
                               "reference_sha256": evidence["reference_sha256"]})
            for fn in evidence["functions"]:
                address = fn["address"]
                existing = all_functions.get(address)
                if existing is None or (GRADE_ORDER.get(fn["reported_grade"], -1)
                                        > GRADE_ORDER.get(existing["reported_grade"], -1)):
                    all_functions[address] = fn
                if fn["reported_machine_match"] == "MATCHED":
                    review_claim = True
        source_changed = any(p["source_sha256_check"] == "MISMATCH" for p in provenance)
        blocks.append({
            "source_path": relative,
            "source_sha256": sha,
            "entry_address_hypothesis": start,
            "evidence_association": association,
            "evidence": provenance,
            "reported_function_evidence": [all_functions[a] for a in sorted(all_functions)],
            "historical_machine_match": "REVIEW_REQUIRED" if review_claim else "NOT_ESTABLISHED",
            "current_target_machine_match": "NOT_VERIFIED",
            "original_compiler_flags_verified": False,
            "revalidation_status": ("SOURCE_CHANGED_SINCE_EVIDENCE" if source_changed else
                                    "REVIEW_MATCH_CLAIM" if review_claim else
                                    "REVALIDATION_REQUIRED"),
        })

    summary = {
        "recovered_source_blocks": len(blocks),
        "with_explicit_evidence": sum(b["evidence_association"] == "EXPLICIT_SOURCE_PATH" for b in blocks),
        "with_inferred_evidence": sum(b["evidence_association"] == "INFERRED_FROM_BLOCK_ADDRESS" for b in blocks),
        "without_evidence": sum(b["evidence_association"] == "NO_EVIDENCE" for b in blocks),
        "with_stale_source_hash": sum(b["revalidation_status"] == "SOURCE_CHANGED_SINCE_EVIDENCE" for b in blocks),
        "verified_target_machine_matches": 0,
        "all_blocks_require_new_standard_review": True,
    }
    return {"schema": SCHEMA, "summary": summary,
            "compiler_evidence": {
                "reported_reference_comment": "GCC 4.4.3 observed; not an exact complete build profile",
                "historical_flags_verified": False,
                "historical_linker_and_relocations_verified": False,
                "full_shared_library_sha256_match_verified": False,
            },
            "blocks": blocks,
            "evidence_read_warnings": issues}


PLAN_CHECKS = [
    "Validate the private original ELF's SHA-256 against the reference evidence; never upload it",
    "Cross-check ELF virtual address, file offset, function length, ARM/Thumb mode and data islands with Ghidra or IDA",
    "Establish the historical GCC/NDK candidate and independently record target, C++ ABI, flags and linker versions",
    "Compile this source block into a local ARM relocatable object, preserve compiler/flags and candidate hashes",
    "Compare instruction windows and relocation semantics with objdiff; do not mistake synthetic ASM bytes for source matching",
    "Run independent behavioural regressions and record unresolved dependencies before upgrading any verification grade",
]


def revalidation_plan(report: dict) -> dict:
    """Create a reproducible worklist, explicitly not a byte-match verdict."""
    if report.get("schema") != SCHEMA:
        raise ValueError("unsupported legacy inventory schema")
    tasks = []
    for block in report["blocks"]:
        refs = sorted({entry["reference_sha256"] for entry in block["evidence"]
                       if entry.get("reference_sha256")})
        funcs = block["reported_function_evidence"]
        sizes = [fn.get("known_reference_size_bytes") for fn in funcs]
        known_sizes = all(isinstance(size, int) and size > 0 for size in sizes) and bool(sizes)
        total = sum(sizes) if known_sizes else None
        tasks.append({
            "source_path": block["source_path"],
            "source_sha256": block["source_sha256"],
            "entry_address_hypothesis": block["entry_address_hypothesis"],
            "known_function_ranges": [
                {"address": fn["address"], "size_bytes": fn.get("known_reference_size_bytes")}
                for fn in funcs
            ],
            "known_total_reference_bytes": total,
            "reference_sha256": refs[0] if len(refs) == 1 else None,
            "reference_sha256_conflict": len(refs) > 1,
            "evidence_association": block["evidence_association"],
            "evidence_paths": [entry["file"] for entry in block["evidence"]],
            "historical_machine_match": block["historical_machine_match"],
            "current_target_machine_match": "NOT_VERIFIED",
            "source_revalidation_status": block["revalidation_status"],
            "requires_private_original": True,
            "checks_required_before_match_claim": list(PLAN_CHECKS),
        })
    rank = {"EXPLICIT_SOURCE_PATH": 0, "INFERRED_FROM_BLOCK_ADDRESS": 1, "NO_EVIDENCE": 2}
    tasks.sort(key=lambda task: (
        rank.get(task["evidence_association"], 3),
        task["known_total_reference_bytes"] if task["known_total_reference_bytes"] is not None else 1 << 62,
        task["source_path"],
    ))
    for number, task in enumerate(tasks, start=1):
        task["review_priority"] = number
    return {"schema": PLAN_SCHEMA,
            "ordering": "explicit source evidence first, then inferred associations; smaller known reference windows first",
            "candidate_original_compiler": "GCC 4.4.3 (.comment evidence only; flags unverified)",
            "target_machine_matches_automatically_claimed": 0,
            "tasks": tasks}


def _private_destination(repo: Path, raw: Path, parser: argparse.ArgumentParser) -> Path:
    destination = raw.expanduser().resolve()
    try:
        relative = destination.relative_to(repo)
    except ValueError:
        relative = None
    if relative is None or not relative.parts or relative.parts[0] != ".cache":
        parser.error("report paths must remain in this checkout's ignored .cache/ directory")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Read source-owned C++ and existing evidence from this checkout")
    parser.add_argument("--out", type=Path, help="Write local metadata-only JSON under .cache/")
    parser.add_argument("--plan-out", type=Path,
                        help="Write an ordered compiler/objdiff revalidation worklist under .cache/")
    parser.add_argument("--require-verified", action="store_true",
                        help="Exit nonzero until at least one independently verified target match exists")
    args = parser.parse_args(argv)
    report = inventory(args.repo)
    repo = args.repo.resolve()
    destinations = {}
    for label, raw in (("audit", args.out), ("plan", args.plan_out)):
        if raw is not None:
            destinations[label] = _private_destination(repo, raw, parser)
    if len(set(destinations.values())) != len(destinations):
        parser.error("--out and --plan-out must use different paths")
    for name, destination in destinations.items():
        payload = report if name == "audit" else revalidation_plan(report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 2 if args.require_verified and not report["summary"]["verified_target_machine_matches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
