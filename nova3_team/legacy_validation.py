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
        functions.append({"address": address, "reported_grade": grade,
                          "reported_machine_match": _match_declaration(evidence, fn)})
    return {"file": path, "source_path": _root_source(evidence),
            "source_sha256": _declared_source_sha(evidence),
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
                               "source_sha256_check": observed})
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Read source-owned C++ and existing evidence from this checkout")
    parser.add_argument("--out", type=Path, help="Write local metadata-only JSON under .cache/")
    parser.add_argument("--require-verified", action="store_true",
                        help="Exit nonzero until at least one independently verified target match exists")
    args = parser.parse_args(argv)
    report = inventory(args.repo)
    if args.out:
        repo = args.repo.resolve()
        destination = args.out.expanduser().resolve()
        try:
            relative = destination.relative_to(repo)
        except ValueError:
            relative = None
        if relative is None or not relative.parts or relative.parts[0] != ".cache":
            parser.error("--out must be inside this checkout's ignored .cache/ directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 2 if args.require_verified and not report["summary"]["verified_target_machine_matches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
