#!/usr/bin/env python3
"""Read-only freshness gate for a compare pilot's candidate object.

The pilots in ``tools/compare`` rebuild a candidate object from recovered
``.cpp`` sources and diff a function window against the Premium ELF.  The
measured object is only meaningful for the exact source revision that produced
it: an uncommitted experiment left in a ``.cpp`` file (see
``docs/progress/NEXT.md``) silently changes the candidate.  Nothing in the
pilot report records which source revision produced the measured size.

This helper fingerprints files that already exist -- nothing more.  It never
compiles, patches, relinks, writes, deletes or renames any path, and it never
parses ELF files or spawns child processes.

Status rules (``build_manifest``):

* ``stale``      at least one source is strictly newer than the object; the
                 newer sources are listed in ``newer_sources`` and the process
                 exits 2 so a window match is not published from this state.
* ``fresh``      the object is at least as new as every source.
* ``no-object``  the object is missing, so no object can vouch for the sources.
* ``no-sources`` no source path was given.
* ``error``      a path is missing or a timestamp is unknown.

CLI::

    python -m tools.compare.pilot_preflight \
        --sources a.cpp b.cpp --object a.o [--json]

The JSON block (paste into a pilot report) has keys ``schema``
(``nova3-pilot-preflight/1``), ``status``, ``sources`` (sorted path -> SHA-256
map), ``object`` (path, sha256, mtime when present), ``newer_sources`` and
``exits``.\n"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional

SCHEMA = "nova3-pilot-preflight/1"

STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_NO_OBJECT = "no-object"
STATUS_NO_SOURCES = "no-sources"
STATUS_ERROR = "error"

EXIT = {
    STATUS_FRESH: 0,
    STATUS_STALE: 2,
    STATUS_NO_OBJECT: 3,
    STATUS_NO_SOURCES: 4,
    STATUS_ERROR: 5,
}

_CHUNK = 1 << 20


def sha256_file(path: os.PathLike | str) -> str:
    """SHA-256 hex digest of a regular file's bytes (read-only, streamed)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_float(value: object) -> Optional[float]:
    """Coerce a timestamp to float; ``None`` means unknown."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def stat_mtime(path: os.PathLike | str) -> Optional[float]:
    """Current ``st_mtime`` of an existing regular file, else ``None``."""
    try:
        if os.path.isfile(path):
            return os.stat(path).st_mtime
    except OSError:
        pass
    return None


def _relpath(path: os.PathLike | str) -> str:
    return str(Path(path))


def build_manifest(
    paths: Iterable[os.PathLike | str],
    mtimes: Mapping[os.PathLike | str, object],
    object_path: Optional[os.PathLike | str] = None,
    object_mtime: object = None,
    hashes: Optional[Mapping[os.PathLike | str, str]] = None,
    check_exists: bool = True,
) -> dict:
    """Compare source mtimes against one cached object mtime.

    ``paths`` are the source files about to be compiled; ``mtimes`` maps each
    source path to its mtime (missing key or ``None`` = unknown).  The object
    may be named either as ``object_path`` with ``object_mtime``, or by giving
    its mtime under ``path`` in ``mtimes``.

    ``hashes`` optionally supplies pre-computed SHA-256 digests for the same
    paths; otherwise files are hashed from disk when they exist and the
    missing digest is left ``None``.

    With ``check_exists=False`` the function touches no path at all (no stat,
    no read): ``object.exists`` is taken as true and callers must pass
    ``mtimes``/``hashes`` for everything they care about.  This is the pure
    mode used with synthetic dicts.  The default inspects existing files to
    report ``exists``, ``sha256`` and ``missing_sources``.  Nothing is ever
    opened for writing.
    """
    source_paths = [_relpath(p) for p in paths]
    manifest: dict = {
        "schema": SCHEMA,
        "status": STATUS_FRESH,
        "sources": {},
        "object": None,
        "newer_sources": [],
        "missing_sources": [],
        "unknown_mtime": [],
        "exits": dict(EXIT),
    }

    if not source_paths:
        manifest["status"] = STATUS_NO_SOURCES
        return manifest

    if object_path is not None:
        obj_name = _relpath(object_path)
        if object_mtime is not None:
            obj_mtime = object_mtime
        else:
            obj_mtime = mtimes.get(obj_name, mtimes.get(object_path))
            if obj_mtime is None and check_exists:
                # Fall back to the filesystem only when nothing was supplied.
                obj_mtime = stat_mtime(obj_name)
    else:
        obj_name = None
        obj_mtime = None
    obj_mtime_f = _as_float(obj_mtime)

    source_hashes: dict[str, Optional[str]] = {}
    source_mtimes: dict[str, Optional[float]] = {}
    for name in source_paths:
        digest = None
        if hashes is not None:
            raw = hashes.get(name, hashes.get(Path(name)))  # type: ignore[call-overload]
            digest = None if raw is None else str(raw)
        if digest is None and check_exists and os.path.isfile(name):
            digest = sha256_file(name)
        source_hashes[name] = digest
        mtime = mtimes.get(name, mtimes.get(Path(name)))
        if mtime is None and check_exists and os.path.isfile(name):
            mtime = stat_mtime(name)
        source_mtimes[name] = _as_float(mtime)

    manifest["sources"] = dict(sorted(source_hashes.items()))
    manifest["source_mtimes"] = dict(sorted(source_mtimes.items()))

    object_block: Optional[dict] = None
    if obj_name is not None:
        object_block = {
            "path": obj_name,
            "exists": os.path.isfile(obj_name) if check_exists else True,
            "sha256": None,
            "mtime": obj_mtime_f,
        }
        if hashes is not None:
            raw = hashes.get(obj_name, hashes.get(Path(obj_name)))  # type: ignore[call-overload]
            object_block["sha256"] = None if raw is None else str(raw)
        elif object_block["exists"] and check_exists:
            object_block["sha256"] = sha256_file(obj_name)
    manifest["object"] = object_block

    for name, mtime in source_mtimes.items():
        if mtime is None:
            manifest["unknown_mtime"].append(name)
        elif check_exists and not os.path.isfile(name):
            manifest["missing_sources"].append(name)

    object_exists = object_block is not None and object_block["exists"]

    manifest["missing_sources"].sort()

    if obj_name is None:
        manifest["status"] = STATUS_NO_OBJECT
    elif not object_exists:
        manifest["status"] = STATUS_NO_OBJECT
    elif obj_mtime_f is None:
        manifest["status"] = STATUS_ERROR
        manifest["unknown_mtime"].insert(0, obj_name)
    else:
        newer = sorted(
            name
            for name, mtime in source_mtimes.items()
            if mtime is not None and mtime > obj_mtime_f
        )
        manifest["newer_sources"] = newer
        if newer:
            manifest["status"] = STATUS_STALE
        elif manifest["unknown_mtime"]:
            manifest["status"] = STATUS_ERROR
        else:
            manifest["status"] = STATUS_FRESH

    return manifest


def _render_text(manifest: dict) -> str:
    lines = [f"pilot-preflight: {manifest['status']}"]
    obj = manifest.get("object")
    if obj:
        lines.append(
            "object: {path} sha256={sha} mtime={mtime}".format(
                path=obj["path"],
                sha=obj["sha256"] or "unknown",
                mtime=obj["mtime"],
            )
        )
    for name, digest in manifest["sources"].items():
        lines.append(f"source: {name} sha256={digest or 'unknown'}")
    for name in manifest["newer_sources"]:
        lines.append(f"newer-source: {name}")
    if manifest["status"] == STATUS_STALE:
        lines.append(
            "result withheld: rebuild the candidate object from these sources "
            "before reporting a window match"
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.compare.pilot_preflight",
        description=(
            "Read-only SHA-256/mtime manifest for a pilot's sources and its "
            "cached object. Prints 'stale' and exits 2 when any source is "
            "newer than the object; never compiles or modifies files."
        ),
    )
    ap.add_argument(
        "--sources",
        nargs="?",
        default=None,
        metavar="PATH",
        help=(
            "source .cpp file the pilot is about to compile (documented "
            "spelling: `--sources a.cpp`). Use --source PATH once per file "
            "for several sources."
        ),
    )
    ap.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="PATH",
        help="add one source file (repeatable)",
    )
    ap.add_argument(
        "--object",
        required=True,
        metavar="PATH",
        help="candidate object the pilot measured",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit the JSON block (schema nova3-pilot-preflight/1)",
    )
    args = ap.parse_args(argv)

    source_paths = [str(p) for p in args.source]
    if args.sources:
        source_paths.insert(0, str(args.sources))
    if not source_paths:
        ap.error("at least one source is required (--sources PATH or --source PATH)")
    obj = str(args.object)
    mtimes: dict[str, Optional[float]] = {}
    for name in source_paths + [obj]:
        try:
            mtimes[name] = os.stat(name).st_mtime if os.path.isfile(name) else None
        except OSError:
            mtimes[name] = None

    manifest = build_manifest(source_paths, mtimes, object_path=obj)
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(_render_text(manifest))
    return EXIT[manifest["status"]]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
