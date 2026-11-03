#!/usr/bin/env python3
"""Deterministic next-unit ladder for the source-correspondence table.

Reads ``coordination/source_correspondence.json`` (schema
``nova3-source-correspondence/1``), validates its aggregate ``status`` counts
against ``functions`` and every row in ``tracked_units``, then ranks the tracked
units for the next compile-and-compare cycle.

The subsystem order is the one documented in ``docs/progress/NEXT.md`` step 4:

1. ``NOVA3/extern/vox/``
2. ``NOVA3/extern/glitch/include/glitch/core/``
3. ``NOVA3/extern/glitch/include/glitch/video/`` or
   ``NOVA3/extern/glitch/source/glitch/video/``
4. gameplay / other

Within a subsystem rows are ordered by numeric address.  Ranks are stable for a
given document.  The tool is strictly read-only: it never rewrites or appends to
the correspondence file.

Usage::

    python -m tools.compare.source_correspondence_ladder
    python -m tools.compare.source_correspondence_ladder --next
    python -m tools.compare.source_correspondence_ladder --input fixture.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "nova3-source-correspondence/1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "coordination" / "source_correspondence.json"

REQUIRED_UNIT_KEYS = (
    "address",
    "unit",
    "file",
    "line",
    "status",
    "confidence",
    "evidence",
)

# Output labels and their documented order.  The labels are stable API; the
# path prefixes below decide the class.
VOX = "vox"
CORE = "glitch::core"
VIDEO = "glitch::video"
OTHER = "gameplay/other"
SUBSYSTEM_ORDER = {VOX: 0, CORE: 1, VIDEO: 2, OTHER: 3}

VOX_PREFIX = "NOVA3/extern/vox/"
CORE_PREFIX = "NOVA3/extern/glitch/include/glitch/core/"
VIDEO_PREFIXES = (
    "NOVA3/extern/glitch/include/glitch/video/",
    "NOVA3/extern/glitch/source/glitch/video/",
)


class CorrespondenceError(ValueError):
    """The correspondence document is malformed or internally inconsistent."""


def _normalise_path(path: str) -> str:
    """Normalise only path separators and a leading ``./``; keep case."""
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def classify_subsystem(file: str) -> str:
    """Return the documented ladder class for a tracked source file."""
    path = _normalise_path(file)
    if path.startswith(VOX_PREFIX):
        return VOX
    if path.startswith(CORE_PREFIX):
        return CORE
    if path.startswith(VIDEO_PREFIXES):
        return VIDEO
    return OTHER


def parse_address(value: Any, *, where: str) -> int:
    """Parse a tracked-unit address into an unsigned 32-bit integer."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CorrespondenceError(
            f"{where}: 'address' must be an integer or a string, got "
            f"{type(value).__name__}"
        )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise CorrespondenceError(f"{where}: 'address' is empty")
        try:
            address = int(text, 0)
        except ValueError as exc:
            raise CorrespondenceError(
                f"{where}: 'address' is not a number: {value!r}"
            ) from exc
    else:
        address = value
    if address < 0 or address > 0xFFFFFFFF:
        raise CorrespondenceError(
            f"{where}: 'address' {value!r} is outside the 32-bit range"
        )
    return address


def _require_string(row: dict[str, Any], key: str, *, where: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise CorrespondenceError(f"{where}: '{key}' must be a non-empty string")
    return value


def validate_document(document: Any) -> None:
    """Validate schema, status totals and every ``tracked_units`` row."""
    if not isinstance(document, dict):
        raise CorrespondenceError(
            f"top-level JSON value must be an object, got "
            f"{type(document).__name__}"
        )

    schema = document.get("schema")
    if schema != SCHEMA:
        raise CorrespondenceError(
            f"schema must be {SCHEMA!r}, got {schema!r}"
        )

    functions = document.get("functions")
    if isinstance(functions, bool) or not isinstance(functions, int):
        raise CorrespondenceError(
            "'functions' must be an integer, got "
            f"{type(functions).__name__}"
        )
    if functions < 0:
        raise CorrespondenceError(f"'functions' must be non-negative, got {functions}")

    status = document.get("status")
    if not isinstance(status, dict):
        raise CorrespondenceError("'status' must be an object of counts")
    status_total = 0
    for name, count in status.items():
        if not isinstance(name, str) or not name:
            raise CorrespondenceError("'status' contains an empty status name")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CorrespondenceError(
                f"status count {name!r} must be a non-negative integer, got "
                f"{count!r}"
            )
        status_total += count
    if status_total != functions:
        raise CorrespondenceError(
            "inconsistent status totals: status counts sum to "
            f"{status_total}, but functions is {functions}"
        )

    units = document.get("tracked_units")
    if not isinstance(units, list):
        raise CorrespondenceError("'tracked_units' must be an array")

    for index, row in enumerate(units, start=1):
        where = f"tracked_units[{index}]"
        if not isinstance(row, dict):
            raise CorrespondenceError(
                f"{where}: row must be an object, got {type(row).__name__}"
            )
        missing = [key for key in REQUIRED_UNIT_KEYS if key not in row]
        if missing:
            raise CorrespondenceError(
                f"{where}: missing required key(s): {', '.join(missing)}"
            )
        if "unit" in row and isinstance(row["unit"], str) and row["unit"].strip():
            where = f"{where} ({row['unit']})"
        parse_address(row["address"], where=where)
        for key in ("unit", "file", "status", "confidence", "evidence"):
            _require_string(row, key, where=where)

        line = row["line"]
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise CorrespondenceError(
                f"{where}: 'line' must be a positive integer, got {line!r}"
            )
        if row["status"] not in status:
            raise CorrespondenceError(
                f"{where}: status {row['status']!r} is not declared in "
                "'status' counts"
            )


def load_document(path: Path) -> dict[str, Any]:
    """Read and validate the correspondence document without modifying it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CorrespondenceError(f"cannot read {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorrespondenceError(f"{path}: invalid JSON: {exc}") from exc
    validate_document(document)
    return document


def build_ladder(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated tracked units in documented ladder order.

    ``document`` is validated first, so callers may pass a parsed object without
    trusting it.  The returned rows carry the requested stable fields.
    """
    validate_document(document)

    ordered: list[tuple[int, int, int, dict[str, Any]]] = []
    for input_index, row in enumerate(document["tracked_units"]):
        address = parse_address(
            row["address"],
            where=f"tracked_units[{input_index + 1}]",
        )
        subsystem = classify_subsystem(row["file"])
        ordered.append(
            (SUBSYSTEM_ORDER[subsystem], address, input_index, row)
        )

    ordered.sort(key=lambda item: (item[0], item[1], item[2]))

    ladder: list[dict[str, Any]] = []
    for rank, (_, address, _, row) in enumerate(ordered, start=1):
        ladder.append(
            {
                "rank": rank,
                "subsystem": classify_subsystem(row["file"]),
                "address": f"0x{address:08x}",
                "unit": row["unit"],
                "file": row["file"],
                "line": row["line"],
                "status": row["status"],
                "confidence": row["confidence"],
                "evidence": row["evidence"],
            }
        )
    return ladder


def render(rows: Iterable[dict[str, Any]], *, only_next: bool = False) -> str:
    """Serialise rows deterministically as JSON with a trailing newline."""
    payload: Any = list(rows)
    if only_next:
        payload = payload[0] if payload else None
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.source_correspondence_ladder",
        description=(
            "Validate coordination/source_correspondence.json and print the "
            "deterministic next-unit ladder (vox, glitch::core, glitch::video, "
            "gameplay/other; numeric address within each class)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=(
            "correspondence JSON to read (default: repository "
            "coordination/source_correspondence.json)"
        ),
    )
    parser.add_argument(
        "--next",
        action="store_true",
        help="print only the first ladder row (the next unit) as a JSON object",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_document(args.input)
        rows = build_ladder(document)
        if args.next and not rows:
            raise CorrespondenceError("tracked_units is empty; no next unit")
    except CorrespondenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(render(rows, only_next=args.next))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
