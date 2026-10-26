"""Offline function dependency components and heuristic reconstruction effort.

``cluster_functions`` accepts the task_queue v1 ledger, with optional per-function
``instruction_count`` (nonnegative int), ``cyclomatic_complexity`` (positive int)
and ``globals`` (list of opaque, binary-local global identities). Missing/null
metrics remain unknown. Counts describe observations, not estimated code size.
No metric is inferred from symbol byte size, block count, or data references.

Calls/dependencies are directed canonical function IDs (SHA-256:even ARM32 VA).
Components are *weakly* connected: cycles and recursion are supported. Explicit
shared globals also connect functions, within a binary only. Unknown callees
are reported as boundaries; they never join otherwise unrelated components.
Aliases at the same binary/address merge; conflicting known metrics fail.

Effort v1 = instructions + 8 * sum(complexity - 1) + 4 * shared_global_links.
For a global used by k distinct functions, links = k - 1, avoiding quadratic
hub costs. Repeated refs/aliases do not add effort. This is an uncalibrated
prioritization heuristic, NOT hours or evidence of recovered behaviour. Missing
metrics produce a null score and a separately labelled known-metric subtotal.
Cluster IDs depend only on sorted membership; output order is deterministic.

Adapters read existing inventory SQLite (functions/edges) or Ghidra export
metadata. SQLite lacks effort metrics in the current schema; these stay unknown.
Ghidra calls are hex virtual addresses rebased by ghidra_image_base, never file
offsets. Instructions are counted but never emitted. Ghidra's basic_blocks lack
CFG edges, so do not establish cyclomatic complexity. Globals must be explicit;
arbitrary READ/WRITE refs are not assumed to be global variables.

CLI prints allowlisted IDs/counts only, never names, paths, instructions, C or
opaque global identities. It does not discover evidence files, execute code,
access a network, publish results, or upgrade verification. JSON input requires
a separately obtained trusted digest (byte integrity, not authorship):

    python -m nova3_team.clustering ledger.json --format ledger --index-sha256 HEX
    python -m nova3_team.clustering exports.json --format ghidra --index-sha256 HEX
    python -m nova3_team.clustering analysis_db/index.sqlite --format sqlite

Ghidra JSON may be one export or a list. SQLite is opened read-only and is not
authenticated; reports cover supplied observations only, not whole-program
dependency completeness. No original game content is needed by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from nova3_team.task_queue import QueueError, function_id, load_ledger


MAX_FUNCTIONS = 100_000
MAX_LINKS = 1_000_000
MAX_METRIC = 2**32 - 1
_ID = re.compile(r"[0-9a-f]{64}:[0-9a-f]{8}\Z")
_HEX = re.compile(r"(?:0x)?[0-9a-fA-F]{1,8}\Z")
EFFORT_MODEL = {"version": 1, "unit": "heuristic_points",
                "instruction_weight": 1, "excess_complexity_weight": 8,
                "shared_global_link_weight": 4}


class ClusteringError(ValueError):
    """Invalid index; diagnostics deliberately omit raw input contents."""


def _identity(binary, address):
    try:
        return function_id(binary, address)
    except QueueError:
        raise ClusteringError("invalid binary digest or canonical ARM32 address") from None


def _dependency(value):
    if (not isinstance(value, str) or not _ID.fullmatch(value)
            or int(value[-8:], 16) & 1):
        raise ClusteringError("invalid canonical dependency ID")
    return value


def _metric(value, *, positive=False):
    if value is not None and (type(value) is not int
                             or not int(positive) <= value <= MAX_METRIC):
        raise ClusteringError("metric must be a bounded integer or null")
    return value


def _list(value):
    if not isinstance(value, list) or len(value) > MAX_LINKS:
        raise ClusteringError("references must be a bounded list")
    return value


def _records(ledger):
    if (not isinstance(ledger, dict) or type(ledger.get("schema_version")) is not int
            or ledger["schema_version"] != 1):
        raise ClusteringError("unsupported ledger schema_version")
    rows = ledger.get("functions")
    if not isinstance(rows, list) or len(rows) > MAX_FUNCTIONS:
        raise ClusteringError("functions must be a bounded list")
    records = {}
    link_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ClusteringError("function record must be an object")
        ident = _identity(row.get("binary_sha256"), row.get("address"))
        deps = _list(row.get("depends_on", []))
        globals_ = row.get("globals")
        if globals_ is not None:
            _list(globals_)
            if any(not isinstance(g, str) or not g or len(g) > 256 for g in globals_):
                raise ClusteringError("global identities must be bounded nonempty text")
        link_count += len(deps) + len(globals_ or [])
        if link_count > MAX_LINKS:
            raise ClusteringError("index exceeds reference limit")
        normalized = {
            "instruction_count": _metric(row.get("instruction_count")),
            "cyclomatic_complexity": _metric(row.get("cyclomatic_complexity"), positive=True),
            "globals": None if globals_ is None else set(globals_),
            "depends_on": {_dependency(dep) for dep in deps},
        }
        # Retain only structural alias metadata, never arbitrary record payloads.
        for field in ("size", "thumb"):
            value = row.get(field)
            if value is not None:
                if field == "size":
                    _metric(value)
                    if row["address"] + value > 2**32:
                        raise ClusteringError("function extent exceeds ARM32 address space")
                elif type(value) is not bool:
                    raise ClusteringError("thumb must be boolean or null")
            normalized[field] = value
        if ident not in records:
            records[ident] = normalized
            continue
        previous = records[ident]
        for field in ("instruction_count", "cyclomatic_complexity", "size", "thumb"):
            old, new = previous[field], normalized[field]
            if old is not None and new is not None and old != new:
                raise ClusteringError("conflicting known alias metadata")
            if old is None:
                previous[field] = new
        previous["depends_on"].update(normalized["depends_on"])
        if globals_ is not None:
            if previous["globals"] is None:
                previous["globals"] = set()
            previous["globals"].update(globals_)
    return records


def cluster_functions(ledger):
    """Return deterministic, JSON-ready components; no I/O or input mutation.

    Unknown metrics do not silently become zero. ``known_effort_points`` is a
    subtotal of supplied metrics, not a calibrated bound on human effort.
    ``effort_points`` is populated only when every member has all three metrics.
    Globals are binary-local even if their opaque strings happen to agree.
    """
    records = _records(ledger)
    parent = {ident: ident for ident in records}
    sizes = dict.fromkeys(records, 1)

    def root(ident):
        while parent[ident] != ident:
            parent[ident] = parent[parent[ident]]
            ident = parent[ident]
        return ident

    def join(left, right):
        left, right = root(left), root(right)
        if left != right:
            if sizes[left] < sizes[right]:
                left, right = right, left
            parent[right] = left
            sizes[left] += sizes[right]

    global_users = {}
    for ident, row in records.items():
        for dep in row["depends_on"]:
            if dep in records:
                join(ident, dep)
        for global_id in row["globals"] or ():
            users = global_users.setdefault((ident[:64], global_id), [])
            if users:
                join(ident, users[0])
            users.append(ident)

    groups = {}
    for ident in sorted(records):
        groups.setdefault(root(ident), []).append(ident)
    shared = {}
    for users in global_users.values():
        if len(users) > 1:
            counts = shared.setdefault(root(users[0]), [0, 0])
            counts[0] += 1
            counts[1] += len(users) - 1

    clusters = []
    for members in sorted(groups.values()):
        missing = {field: sum(records[i][field] is None for i in members)
                   for field in ("instruction_count", "cyclomatic_complexity", "globals")}
        instructions = sum(records[i]["instruction_count"] or 0 for i in members)
        complexity = sum(max(0, (records[i]["cyclomatic_complexity"] or 1) - 1)
                         for i in members)
        shared_count, shared_links = shared.get(root(members[0]), [0, 0])
        subtotal = instructions + 8 * complexity + 4 * shared_links
        unresolved = sorted({dep for i in members for dep in records[i]["depends_on"]
                             if dep not in records})
        membership = json.dumps(members, separators=(",", ":")).encode("ascii")
        clusters.append({
            "cluster_id": "nova3-cluster-v1-" + hashlib.sha256(membership).hexdigest(),
            "function_ids": members,
            "function_count": len(members),
            "internal_dependency_count": sum(dep in records for i in members
                                             for dep in records[i]["depends_on"]),
            "unresolved_dependencies": unresolved,
            "known_instruction_count": instructions,
            "known_excess_complexity": complexity,
            "shared_global_count": shared_count,
            "shared_global_links": shared_links,
            "missing_metrics": missing,
            "effort_complete": not any(missing.values()),
            "known_effort_points": subtotal,
            "effort_points": None if any(missing.values()) else subtotal,
        })
    return {"schema_version": 1, "method": "weak_dependency_components_v1",
            "coverage": "supplied_metadata_only", "matching_claim": "NOT MATCHED",
            "effort_model": dict(EFFORT_MODEL), "function_count": len(records),
            "cluster_count": len(clusters), "clusters": clusters}


def _ghidra_address(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ClusteringError("Ghidra call address must be ARM32 hexadecimal text")
    return int(value, 16)


def ledger_from_ghidra(exports):
    """Reduce actual export_batch.py records to metadata, without copying dumps.

    No guessed callee names or cross-library linking. Rebasing must be explicit;
    address-space prefixes/indirect targets cannot be resolved and fail closed.
    """
    if isinstance(exports, dict):
        exports = [exports]
    if not isinstance(exports, list) or len(exports) > MAX_FUNCTIONS:
        raise ClusteringError("Ghidra exports must be a bounded list")
    rows = []
    for export in exports:
        if not isinstance(export, dict):
            raise ClusteringError("Ghidra export must be an object")
        ident = _identity(export.get("binary_sha256"), export.get("address"))
        architecture = export.get("architecture")
        if not isinstance(architecture, str) or not architecture.startswith("ARM:LE:32:"):
            raise ClusteringError("Ghidra export must specify an ARM little-endian 32-bit language")
        base = export.get("ghidra_image_base")
        if type(base) is not int or not 0 <= base < 2**32:
            raise ClusteringError("Ghidra image base must be explicit ARM32 integer")
        if export.get("byte_mapping_validated") is not True:
            raise ClusteringError("Ghidra byte mapping must be validated")
        if (type(export.get("ghidra_address")) is not int
                or export["address"] + base != export["ghidra_address"]
                or not 0 <= export["ghidra_address"] < 2**32):
            raise ClusteringError("Ghidra address does not match explicit image base")
        deps = []
        for call in _list(export.get("calls", [])):
            if not isinstance(call, dict):
                raise ClusteringError("Ghidra call must be an object")
            target = _ghidra_address(call.get("to")) - base
            # Ghidra exports are ARM/Thumb; clear only the function-pointer tag.
            deps.append(_identity(ident[:64], target & ~1))
        instructions = export.get("instructions")
        if instructions is not None:
            _list(instructions)
        count = _metric(export.get("instruction_count"))
        if instructions is not None:
            if count is not None and count != len(instructions):
                raise ClusteringError("instruction count conflicts with Ghidra listing")
            count = len(instructions)
        rows.append({"binary_sha256": ident[:64], "address": export["address"],
                     "depends_on": deps, "instruction_count": count,
                     "cyclomatic_complexity": export.get("cyclomatic_complexity"),
                     "globals": export.get("globals"), "size": export.get("symbol_size"),
                     "thumb": export.get("thumb")})
    ledger = {"schema_version": 1, "functions": rows}
    _records(ledger)
    return ledger


def load_sqlite_index(path):
    """Read existing inventory.py functions/edges in mode=ro; no sidecar exports.

    Source IDs may contain aliases/names; they are used only to resolve edges,
    then discarded. An orphan call source is an error. Unknown callees remain
    explicit IDs in the source binary; no cross-binary address guessing occurs.
    """
    db = None
    try:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        # Keep both SELECTs in one snapshot if the local index is being updated.
        db.execute("BEGIN")
        rows, sources = [], {}
        for source, binary, address, size, thumb in db.execute(
                "SELECT id,binary_hash,address,size,thumb FROM functions"):
            if len(rows) >= MAX_FUNCTIONS:
                raise ClusteringError("SQLite function limit exceeded")
            if not isinstance(source, str) or source in sources:
                raise ClusteringError("invalid or duplicate SQLite source ID")
            if type(thumb) is not int or thumb not in (0, 1):
                raise ClusteringError("SQLite Thumb state must be zero or one")
            ident = _identity(binary, address)
            row = {"binary_sha256": ident[:64], "address": address,
                   "size": size, "thumb": bool(thumb), "depends_on": []}
            rows.append(row)
            sources[source] = row
        edge_count = 0
        for source, target in db.execute(
                "SELECT source_id,target_address FROM edges WHERE kind='CALL'"):
            edge_count += 1
            if edge_count > MAX_LINKS:
                raise ClusteringError("SQLite call limit exceeded")
            if source not in sources:
                raise ClusteringError("SQLite call source is absent from functions")
            row = sources[source]
            row["depends_on"].append(_identity(row["binary_sha256"], target))
        ledger = {"schema_version": 1, "functions": rows}
        _records(ledger)
        return ledger
    except (sqlite3.Error, OSError, ValueError) as exc:
        if isinstance(exc, ClusteringError):
            raise
        raise ClusteringError("cannot read valid SQLite inventory") from None
    finally:
        if db is not None:
            db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index")
    parser.add_argument("--format", choices=("ledger", "ghidra", "sqlite"), default="ledger")
    parser.add_argument("--index-sha256", help="trusted digest, required for JSON inputs")
    args = parser.parse_args(argv)
    try:
        if args.format == "sqlite":
            if args.index_sha256 is not None:
                raise ClusteringError("SQLite digest checking is not supported")
            ledger = load_sqlite_index(args.index)
        else:
            ledger = load_ledger(args.index, expected_sha256=args.index_sha256)
            if args.format == "ghidra":
                ledger = ledger_from_ghidra(ledger)
        report = cluster_functions(ledger)
    except (ClusteringError, QueueError) as exc:
        print(f"clustering: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
