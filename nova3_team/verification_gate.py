"""Read-only public metadata/source-identity gate; never a binary-truth verifier.

Input is an explicit bounded claim manifest, not a task queue. Comparison
records are strict public-safe projections of the existing producer schemas.
No executable, original ELF, toolchain, log or attestation artifact is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .compiler_matrix import SCHEMA as MATRIX_SCHEMA, _SAFE_FLAGS
from .legacy_validation import BLOCK_RE, HASH_RE, SOURCE_ROOT, _safe_repo_path
from .object_compare import SCHEMA as OBJECT_SCHEMA

SCHEMA = "nova3.source-verification-gate/1"
PROBE_SCHEMA = "nova3.relocation-probe/1"
MAX_FILE = 1024 * 1024
MAX_CLAIMS = 32
PROOF_GRADES = frozenset({"ORIGINAL_OBJECT_MATCH", "HISTORICAL_COMPILER_VERIFIED",
                          "SOURCE_BEHAVIOR_VERIFIED", "WHOLE_SO_HASH_MATCH"})
PUBLIC_GRADES = frozenset({"NOT_VERIFIED", "RESEARCH_SOURCE", "HOST_TESTED_SOURCE",
                           "RAW_MISMATCH", "FUNCTION_BYTES_DIFFERENT",
                           "RELOCATED_LINKED_WINDOW_MATCH"})
_ATTESTATION_FIELDS = {
    "ORIGINAL_OBJECT_MATCH": {"original_object_sha256", "candidate_object_sha256"},
    "HISTORICAL_COMPILER_VERIFIED": {"compiler_executable_sha256", "sysroot_sha256",
                                     "compiler_version_sha256", "flags_sha256"},
    "SOURCE_BEHAVIOR_VERIFIED": {"oracle_report_sha256", "test_report_sha256"},
    "WHOLE_SO_HASH_MATCH": {"original_so_sha256", "candidate_so_sha256"},
}


class GateError(ValueError):
    """Fixed diagnostic codes only: never echo input or private filesystem paths."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GateError(code)


def _keys(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    _require(isinstance(value, dict), "invalid_schema")
    _require(required <= value.keys() and value.keys() <= required | optional,
             "invalid_fields")
    return value


def _hash(value: object) -> str:
    _require(isinstance(value, str) and HASH_RE.fullmatch(value) is not None, "invalid_sha256")
    return value.lower()


def _integer(value: object, low: int, high: int) -> int:
    _require(type(value) is int and low <= value <= high, "invalid_integer")
    return value


def _text(value: object, limit: int = 128) -> None:
    _require(isinstance(value, str) and 0 < len(value) <= limit
             and value.isascii() and all(32 <= ord(c) < 127 for c in value), "invalid_text")


def _read(root: Path, name: object) -> bytes:
    """Walk beneath root with directory descriptors; reject symlinks at every hop."""
    safe = _safe_repo_path(name)
    _require(safe is not None and safe == name and len(safe) <= 512
             and ":" not in safe and all(ord(c) >= 32 for c in safe), "unsafe_path")
    _require(hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd,
             "secure_read_unavailable")
    parts = safe.split("/")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE,
                     "nonregular_or_oversized_file")
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
            _require(len(data) == before.st_size and before.st_size == after.st_size
                     and before.st_mtime_ns == after.st_mtime_ns, "file_changed_during_read")
            return data
    finally:
        os.close(fd)


def _pairs(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise GateError("invalid_json_constant")


def _load(data: bytes) -> dict:
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, GateError):
            raise
        raise GateError("invalid_json") from None
    pending = [(result, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        _require(depth <= 16 and nodes <= 10000, "json_complexity_limit")
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    return result


def _build(build: object, source_sha: str, reference_sha: str) -> None:
    # Source/profile/experiment field names come from compiler_matrix. This is
    # an envelope, not an assertion that a compiler_matrix report was replayed.
    _keys(build, {"producer_schema", "source_sha256", "reference_sha256",
                  "compiler_version", "compiler_version_sha256", "flags",
                  "compile_exit_code", "test_exit_code", "tests_passed", "log_sha256"}, {"object_sha256"})
    _require(build["producer_schema"] == MATRIX_SCHEMA, "unsupported_build_producer")
    _require(_hash(build["source_sha256"]) == source_sha, "build_source_mismatch")
    _require(_hash(build["reference_sha256"]) == reference_sha, "reference_identity_mismatch")
    _hash(build["compiler_version_sha256"])
    _hash(build["log_sha256"])
    if "object_sha256" in build:
        _hash(build["object_sha256"])
    _text(build["compiler_version"])
    _require(isinstance(build["flags"], list) and len(build["flags"]) <= 64, "invalid_flags")
    for flag in build["flags"]:
        _text(flag)
        _require(_SAFE_FLAGS.fullmatch(flag) is not None, "unsupported_compile_flag")
    _integer(build["compile_exit_code"], 0, 255)
    _integer(build["test_exit_code"], 0, 255)
    _integer(build["tests_passed"], 0, 1000000)


def _comparison(value: object, equal_status: str, different_status: str, size: int) -> bool:
    _keys(value, {"status", "equal", "compared_bytes", "differing_byte_count",
                  "expected_sha256", "actual_sha256"})
    _require(type(value["equal"]) is bool, "invalid_boolean")
    _require(_integer(value["compared_bytes"], 1, MAX_FILE) == size, "comparison_size_mismatch")
    count = _integer(value["differing_byte_count"], 0, size)
    equal = _hash(value["expected_sha256"]) == _hash(value["actual_sha256"])
    _require(equal == value["equal"] == (count == 0), "contradictory_comparison")
    _require(value["status"] == (equal_status if equal else different_status), "false_match_promotion")
    return equal


def _machine(evidence: object, reference_sha: str) -> set[str]:
    _require(isinstance(evidence, dict), "invalid_comparison")
    schema = evidence.get("schema")
    common = {"schema", "reference_sha256", "reference_window_sha256",
              "candidate_object_sha256", "candidate_symbol", "candidate_thumb"}
    if schema == OBJECT_SCHEMA:
        _keys(evidence, common | {"kind", "reference_address", "reference_size",
              "candidate_code_sha256", "candidate_size", "original_thumb",
              "candidate_relocation_count", "code_window_equal", "status",
              "original_object_recovered", "original_relocations_recovered",
              "original_compiler_verified", "original_so_hash_matching_verified",
              "source_matching_verified"})
        _require(evidence["kind"] == "source-candidate-to-linked-window", "synthetic_candidate")
        for field in ("original_object_recovered", "original_relocations_recovered",
                      "original_compiler_verified", "original_so_hash_matching_verified",
                      "source_matching_verified"):
            _require(evidence[field] is False, "false_match_promotion")
        address = _integer(evidence["reference_address"], 0, 0xffffffff)
        size = _integer(evidence["reference_size"], 1, MAX_FILE)
        candidate_size = _integer(evidence["candidate_size"], 1, MAX_FILE)
        relocations = _integer(evidence["candidate_relocation_count"], 0, MAX_FILE)
        _require(type(evidence["original_thumb"]) is bool
                 and type(evidence["code_window_equal"]) is bool, "invalid_boolean")
        same_mode = evidence["candidate_thumb"] == evidence["original_thumb"]
        candidate_code_sha = _hash(evidence["candidate_code_sha256"])
        window_sha = _hash(evidence["reference_window_sha256"])
        equal = same_mode and size == candidate_size and candidate_code_sha == window_sha
        expected = ("ARM_THUMB_MODE_MISMATCH" if not same_mode else
                    "FUNCTION_SIZE_MISMATCH" if size != candidate_size else
                    "BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS" if equal and relocations else
                    "LINKED_FUNCTION_WINDOW_EQUAL" if equal else "FUNCTION_BYTES_DIFFERENT")
        _require(evidence["status"] == expected and evidence["code_window_equal"] == equal,
                 "contradictory_comparison")
        statuses = {expected}
        thumb = evidence["original_thumb"]
    elif schema == PROBE_SCHEMA:
        linked = evidence.get("relocated_linked_window") != "NOT_CHECKED"
        _keys(evidence, common | {"address", "size", "raw", "resolved",
              "original_object_match", "historical_compiler_verified", "whole_so_sha_match",
              "behavior_verified", "original_prelink_relocations_recovered", "relocated_linked_window"},
              {"linked_sha256", "linker_vs_resolved", "linked_vs_reference"} if linked else set())
        _require(evidence["original_object_match"] == "NOT_AVAILABLE", "false_match_promotion")
        for field in ("historical_compiler_verified", "whole_so_sha_match", "behavior_verified",
                      "original_prelink_relocations_recovered"):
            _require(evidence[field] is False, "false_match_promotion")
        address = _integer(evidence["address"], 0, 0xffffffff)
        size = _integer(evidence["size"], 1, MAX_FILE)
        _require(evidence["candidate_thumb"] is False, "unsupported_probe_mode")
        thumb = False
        _comparison(evidence["raw"], "RAW_EQUAL", "RAW_MISMATCH", size)
        _comparison(evidence["resolved"], "RESOLVED_WINDOW_EQUAL", "RESOLVED_WINDOW_MISMATCH", size)
        for field in ("raw", "resolved"):
            _require(_hash(evidence[field]["expected_sha256"]) ==
                     _hash(evidence["reference_window_sha256"]), "window_identity_mismatch")
        statuses = {evidence["raw"]["status"]}
        if linked:
            _require({"linked_sha256", "linker_vs_resolved", "linked_vs_reference"} <= evidence.keys(),
                     "missing_linker_stage")
            _hash(evidence["linked_sha256"])
            agrees = _comparison(evidence["linker_vs_resolved"], "LINKER_AGREES", "LINKER_DISAGREES", size)
            matches = _comparison(evidence["linked_vs_reference"], "RELOCATED_LINKED_WINDOW_MATCH",
                                  "RELOCATED_LINKED_WINDOW_MISMATCH", size)
            _require(_hash(evidence["linker_vs_resolved"]["expected_sha256"]) ==
                     _hash(evidence["resolved"]["actual_sha256"]) and
                     _hash(evidence["linker_vs_resolved"]["actual_sha256"]) ==
                     _hash(evidence["linked_vs_reference"]["actual_sha256"]) and
                     _hash(evidence["linked_vs_reference"]["expected_sha256"]) ==
                     _hash(evidence["reference_window_sha256"]), "stage_identity_mismatch")
            expected = (evidence["linked_vs_reference"]["status"] if agrees else "LINKER_DISAGREES")
            _require(evidence["relocated_linked_window"] == expected, "false_match_promotion")
            if agrees and matches:
                statuses.add("RELOCATED_LINKED_WINDOW_MATCH")
    else:
        raise GateError("unsupported_comparison_schema")
    _require(_hash(evidence["reference_sha256"]) == reference_sha, "reference_identity_mismatch")
    _hash(evidence["reference_window_sha256"])
    _hash(evidence["candidate_object_sha256"])
    _text(evidence["candidate_symbol"])
    _require(not evidence["candidate_symbol"].startswith("nova3_fn_"), "synthetic_candidate")
    _require(type(evidence["candidate_thumb"]) is bool, "invalid_boolean")
    _require(address % (2 if thumb else 4) == 0 and size % (2 if thumb else 4) == 0
             and address + size <= 0x100000000, "invalid_instruction_window")
    return statuses


def _attestations(records: object, source_sha: str, reference_sha: str) -> list[str]:
    _require(isinstance(records, list) and len(records) <= len(PROOF_GRADES), "invalid_attestations")
    seen = set()
    for record in records:
        _require(isinstance(record, dict) and isinstance(record.get("grade"), str)
                 and record["grade"] in PROOF_GRADES, "unsupported_attestation")
        grade = record["grade"]
        _require(grade not in seen, "duplicate_attestation")
        seen.add(grade)
        _keys(record, {"grade", "source_sha256", "reference_sha256", "report_sha256"}
              | _ATTESTATION_FIELDS[grade])
        for key, value in record.items():
            if key.endswith("_sha256"):
                _hash(value)
        _require(_hash(record["source_sha256"]) == source_sha, "attestation_source_mismatch")
        _require(_hash(record["reference_sha256"]) == reference_sha, "reference_identity_mismatch")
        if grade == "ORIGINAL_OBJECT_MATCH":
            _require(_hash(record["original_object_sha256"]) == _hash(record["candidate_object_sha256"]),
                     "false_match_promotion")
        if grade == "WHOLE_SO_HASH_MATCH":
            _require(_hash(record["original_so_sha256"]) == reference_sha ==
                     _hash(record["candidate_so_sha256"]), "false_match_promotion")
    return sorted(seen)


def check(repo: Path, manifest: str, *, expected_reference_sha256: str) -> dict:
    """Validate metadata consistency only. All proof grades remain NOT_VERIFIED."""
    reference_sha = _hash(expected_reference_sha256)
    root = Path(repo).resolve(strict=True)
    document = _load(_read(root, manifest))
    _keys(document, {"schema", "reference_sha256", "claims"})
    _require(document["schema"] == SCHEMA, "unsupported_manifest_schema")
    _require(_hash(document["reference_sha256"]) == reference_sha, "reference_identity_mismatch")
    claims = document["claims"]
    _require(isinstance(claims, list) and 1 <= len(claims) <= MAX_CLAIMS, "invalid_claim_count")
    seen = set()
    rows = []
    for claim in claims:
        _keys(claim, {"source", "grade"}, {"build", "comparison", "original_local_attestations"})
        source = _keys(claim["source"], {"path", "sha256"})
        path = _safe_repo_path(source["path"])
        _require(path is not None and path.startswith(SOURCE_ROOT + "/")
                 and BLOCK_RE.fullmatch(Path(path).name) is not None, "invalid_source_path")
        _require(path not in seen, "duplicate_or_conflicting_source")
        seen.add(path)
        data = _read(root, source["path"])
        source_sha = _hash(source["sha256"])
        _require(hashlib.sha256(data).hexdigest() == source_sha, "source_hash_drift")
        # Conservative exclusion, not a complete C++ authenticity/security audit.
        _require(re.search(rb"\b(?:asm|__asm|__asm__)\b|\.(?:byte|incbin)\b|##|"
                           rb"#\s*include[^\n]*\.(?:s|S|asm|inc)[\">]",
                           data.replace(b"\\\n", b"").replace(b"\\\r\n", b"")) is None,
                 "synthetic_or_inline_assembly_source")
        grade = claim["grade"]
        _require(isinstance(grade, str) and grade in PUBLIC_GRADES, "uncheckable_or_unknown_grade")
        if "build" in claim:
            _build(claim["build"], source_sha, reference_sha)
        if grade == "HOST_TESTED_SOURCE" or grade in {
                "RAW_MISMATCH", "FUNCTION_BYTES_DIFFERENT", "RELOCATED_LINKED_WINDOW_MATCH"}:
            build = claim.get("build")
            _require(build is not None and build["compile_exit_code"] == 0, "missing_successful_build")
            if grade == "HOST_TESTED_SOURCE":
                _require(build["test_exit_code"] == 0 and build["tests_passed"] > 0,
                         "missing_successful_tests")
        statuses = _machine(claim["comparison"], reference_sha) if "comparison" in claim else set()
        if "comparison" in claim:
            build = claim.get("build")
            _require(build is not None and build["compile_exit_code"] == 0
                     and "object_sha256" in build and
                     _hash(build["object_sha256"]) ==
                     _hash(claim["comparison"]["candidate_object_sha256"]), "build_object_mismatch")
        if grade in {"RAW_MISMATCH", "FUNCTION_BYTES_DIFFERENT", "RELOCATED_LINKED_WINDOW_MATCH"}:
            _require(grade in statuses, "unsupported_grade_promotion")
        attestations = _attestations(claim.get("original_local_attestations", []), source_sha, reference_sha)
        rows.append({"metadata_grade": grade, "source_sha256_current": True,
                     "reported_comparison_statuses": sorted(statuses),
                     "original_local_attestations_not_rechecked": attestations,
                     "proof_grades": {g: "NOT_VERIFIED" for g in sorted(PROOF_GRADES)}})
    return {"schema": SCHEMA, "metadata_valid": True, "source_claims_checked": len(rows),
            "independently_verified_source_matches": 0, "binary_truth_rechecked": False,
            "claims": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", required=True, help="Explicit repo-relative public-safe JSON")
    parser.add_argument("--expected-reference-sha256", required=True,
                        help="Separately reviewed ELF identity pin; no original ELF is opened")
    parser.add_argument("--require-verified", action="store_true",
                        help="Fail closed: metadata alone cannot satisfy source verification")
    args = parser.parse_args(argv)
    try:
        report = check(args.repo, args.manifest, expected_reference_sha256=args.expected_reference_sha256)
    except (GateError, OSError, ValueError, RecursionError):
        # Do not interpolate exceptions, filenames, symbols, compiler flags or input.
        print(json.dumps({"metadata_valid": False, "error": "evidence_gate_rejected",
                          "independently_verified_source_matches": 0}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 2 if args.require_verified else 0


if __name__ == "__main__":
    raise SystemExit(main())
