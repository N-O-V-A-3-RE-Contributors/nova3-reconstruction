"""Offline, deterministic reconstruction tasks; no publishing or binary access.

Input contract (v1)::

    {"schema_version": 1, "functions": [
        {"binary_sha256": "<64 hex digits>", "address": 4096,
         "size": 32, "thumb": true,
         "depends_on": ["<binary sha256>:00002000"]}]}

Addresses are canonical even ARM virtual addresses, not file offsets; Thumb is
explicit. Dependencies are prerequisite function IDs, NOT raw Ghidra call refs.
An adapter must resolve those references using the source ledger's provenance.
Unknown sizes, unresolved dependencies, conflicting aliases and cycles fail
closed. Identical aliases merge their dependency sets. Extra ledger fields are
ignored and never copied into task specifications, diagnostics or issue bodies.

``load_ledger`` checks a SHA-256 obtained through a trusted, separate channel.
This pins local bytes; it does not establish their authorship or authenticate a
self-reported digest. ``generate_tasks`` is the in-memory API for an already
authenticated ledger. The operator supplies scopes separately: a JSON mapping
of function IDs to exact reconstruction source/test filenames. Scopes are
lexical allocations, not a checkout/symlink security boundary.
Only exact source/text filenames under reconstruction/engine, reconstruction/tests
or reconstruction/android are accepted (at most 16 per function). Each function
may have at most 128 direct prerequisites, including merged alias references.

Each function becomes one task, bounded by max_function_bytes; max_tasks limits
the emitted dependency-ordered prefix. IDs hash the normalized metadata, scope
and contract version, so changed allocations do not dedup against old work.
Completed function IDs satisfy prerequisites. Existing task IDs suppress only
repeat emission; dependents still reference them. A scheduler must wait for
those tasks to finish and serialize overlapping file allocations. This module
does not change a verification grade or claim machine-code equivalence.

Local preview (stdout only; emits allowlisted metadata, never raw records)::

    python -m nova3_team.task_queue ledger.json --ledger-sha256 TRUSTED_DIGEST \
        --scopes scopes.json --max-tasks 20 --max-function-bytes 4096
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import hmac
import json
from pathlib import Path
import re
import sys


MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_FUNCTIONS = 100_000
MAX_DEPENDENCIES = 128
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_FUNCTION_ID = re.compile(r"[0-9a-f]{64}:[0-9a-f]{8}\Z")
_TASK_ID = re.compile(r"nova3-function-v1-[0-9a-f]{64}\Z")
_PATH = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+\Z")
_ROOTS = ("reconstruction/engine/", "reconstruction/tests/", "reconstruction/android/")
_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
             ".java", ".kt", ".py", ".xml", ".glsl", ".vert", ".frag"}


class QueueError(ValueError):
    """Invalid or unbounded input. Messages deliberately omit input contents."""


def _positive_integer(value, field):
    if type(value) is not int or value <= 0:
        raise QueueError(f"{field} must be a positive integer")
    return value


def _digest(value):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise QueueError("binary/ledger SHA-256 must contain 64 hex digits")
    return value.lower()


def function_id(binary_sha256, address):
    """Canonical identity; reject Thumb-tagged/unaligned or non-ARM32 addresses."""
    digest = _digest(binary_sha256)
    if type(address) is not int or not 0 <= address <= 0xFFFFFFFE or address & 1:
        raise QueueError("address must be a canonical even ARM32 virtual address")
    return f"{digest}:{address:08x}"


def _check_function_id(value):
    if (not isinstance(value, str) or not _FUNCTION_ID.fullmatch(value)
            or int(value[-8:], 16) & 1):
        raise QueueError("invalid canonical function ID")
    return value


def _id_set(values, *, task_ids=False):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise QueueError("dedup/completion IDs must be a collection")
    for value in values:
        if task_ids:
            if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
                raise QueueError("invalid existing task ID")
        else:
            _check_function_id(value)
    return set(values)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QueueError("duplicate JSON object key")
        result[key] = value
    return result


def _read_json(path, expected_sha256=None):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise QueueError("JSON input exceeds size limit")
        if expected_sha256 is not None and not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(), _digest(expected_sha256)):
            raise QueueError("ledger SHA-256 does not match trusted digest")
        return json.loads(raw, object_pairs_hook=_json_object)
    except QueueError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise QueueError("cannot read valid bounded JSON input") from None


def load_ledger(path, *, expected_sha256):
    """Read local JSON only after checking its caller-supplied trusted digest."""
    # Validate before any file access; None must not disable digest verification.
    return _read_json(path, _digest(expected_sha256))


def _paths(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise QueueError("each scope must allocate 1 to 16 exact filenames")
    for path in value:
        if (not isinstance(path, str) or len(path) > 240 or not _PATH.fullmatch(path)
                or not path.startswith(_ROOTS) or Path(path).suffix not in _SUFFIXES
                or any(part in {"AGENTS.md", "CMakeLists.txt"} for part in path.split("/"))):
            raise QueueError("scope must contain exact reconstruction source/test filenames")
    return sorted(set(value))


def _records(ledger):
    if (not isinstance(ledger, dict) or type(ledger.get("schema_version")) is not int
            or ledger["schema_version"] != 1):
        raise QueueError("unsupported ledger schema_version (expected 1)")
    rows = ledger.get("functions")
    if not isinstance(rows, list) or len(rows) > MAX_FUNCTIONS:
        raise QueueError("ledger functions must be a bounded list")
    records = {}
    for row in rows:
        if not isinstance(row, dict):
            raise QueueError("function record must be an object")
        ident = function_id(row.get("binary_sha256"), row.get("address"))
        size = _positive_integer(row.get("size"), "function size")
        if row["address"] + size > 0x100000000:
            raise QueueError("function extent exceeds ARM32 address space")
        if type(row.get("thumb")) is not bool:
            raise QueueError("function Thumb state must be explicit boolean")
        dependencies = row.get("depends_on", [])
        if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
            raise QueueError("dependencies must be a bounded list of function IDs")
        record = {"binary_sha256": ident[:64], "address": row["address"],
                  "size": size, "thumb": row["thumb"]}
        deps = {_check_function_id(dep) for dep in dependencies}
        if ident in records:
            if records[ident][0] != record:
                raise QueueError("conflicting metadata for aliased function")
            records[ident][1].update(deps)
            if len(records[ident][1]) > MAX_DEPENDENCIES:
                raise QueueError("merged alias dependencies exceed task bound")
        else:
            records[ident] = (record, deps)
    return records


def generate_tasks(ledger, *, scopes, max_tasks=50, max_function_bytes=4096,
                   existing_task_ids=(), completed_function_ids=()):
    """Return JSON-compatible tasks from authenticated metadata, without I/O.

    ``scopes`` maps canonical function IDs to lists of exact filenames. All
    unfinished records need a scope, including previously generated tasks.
    Validate the entire graph before truncating; no partial result on error.
    Cycles require a separately reviewed/co-scoped task, not a guessed order.
    """
    _positive_integer(max_tasks, "max_tasks")
    _positive_integer(max_function_bytes, "max_function_bytes")
    records = _records(ledger)
    if not isinstance(scopes, dict):
        raise QueueError("scopes must map function IDs to exact filenames")
    allocations = {_check_function_id(key): _paths(value) for key, value in scopes.items()}
    completed = _id_set(completed_function_ids)
    existing = _id_set(existing_task_ids, task_ids=True)
    active = records.keys() - completed
    prerequisites = {}
    dependents = {ident: [] for ident in active}
    task_ids = {}
    for ident in sorted(active):
        record, deps = records[ident]
        if ident not in allocations:
            raise QueueError("unfinished function has no explicit file allocation")
        if record["size"] > max_function_bytes:
            raise QueueError("function exceeds task byte bound; needs separate scoping")
        if deps - records.keys() - completed:
            raise QueueError("unresolved prerequisite; provide ledger metadata or explicit completion")
        prerequisites[ident] = deps - completed
        for dep in prerequisites[ident]:
            dependents[dep].append(ident)
        # Scope and dependency changes invalidate old task IDs, not other tasks.
        contract = {"version": 1, "function": record, "allowed_paths": allocations[ident],
                    "depends_on": sorted(deps)}
        payload = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("ascii")
        task_ids[ident] = "nova3-function-v1-" + hashlib.sha256(payload).hexdigest()
    pending = {ident: len(deps) for ident, deps in prerequisites.items()}
    ready = [ident for ident, count in pending.items() if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        ident = heapq.heappop(ready)
        ordered.append(ident)
        for child in dependents[ident]:
            pending[child] -= 1
            if pending[child] == 0:
                heapq.heappush(ready, child)
    if len(ordered) != len(active):
        raise QueueError("cyclic prerequisites; require a reviewed bounded group task")
    result = []
    for ident in ordered:
        if task_ids[ident] in existing:
            continue
        dependencies = sorted(task_ids[dep] for dep in prerequisites[ident])
        title = f"Reconstruct function {ident[-8:]} ({ident[:12]})"
        paths = allocations[ident]
        body = ("Priority: 50\nAllowed paths: " + ", ".join(paths)
                + "\nDepends on: " + (", ".join(dependencies) or "none")
                + f"\n\nReconstruct function {ident} from locally authenticated evidence."
                + " Use independent source and reproducible tests within the allocated files."
                + " Never upload function bytes, decompiler output or game assets."
                + " Report unverified behaviour; this task carries no matching claim.")
        result.append({"schema_version": 1, "task_id": task_ids[ident], "title": title,
                       "priority": 50, "function_id": ident, "function": records[ident][0],
                       "allowed_paths": paths, "depends_on": dependencies, "body": body})
        if len(result) == max_tasks:
            break
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", help="locally prepared v1 metadata JSON")
    parser.add_argument("--ledger-sha256", required=True, help="digest from trusted provenance")
    parser.add_argument("--scopes", required=True, help="local function-ID to filenames JSON map")
    parser.add_argument("--max-tasks", type=int, default=50)
    parser.add_argument("--max-function-bytes", type=int, default=4096)
    parser.add_argument("--existing-task-id", action="append", default=[])
    parser.add_argument("--completed-function-id", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        ledger = load_ledger(args.ledger, expected_sha256=args.ledger_sha256)
        scopes = _read_json(args.scopes)
        tasks = generate_tasks(ledger, scopes=scopes, max_tasks=args.max_tasks,
                               max_function_bytes=args.max_function_bytes,
                               existing_task_ids=args.existing_task_id,
                               completed_function_ids=args.completed_function_id)
    except QueueError as exc:
        print(f"task queue: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(tasks, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
