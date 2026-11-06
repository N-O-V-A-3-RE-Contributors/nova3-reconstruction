#!/usr/bin/env python3
"""Per-subsystem coverage summary for the tracked source-correspondence units.

Reads the correspondence document (schema ``nova3-source-correspondence/1``)
through :func:`tools.compare.source_correspondence_ladder.load_document` and
:func:`tools.compare.source_correspondence_ladder.build_ladder`, groups the
ladder rows by their subsystem label, and prints one deterministic Markdown
table in the documented subsystem order:

1. ``vox``
2. ``glitch::core``
3. ``glitch::video``
4. ``gameplay/other``

Each row reports the subsystem's tracked-unit count, a count per declared
status, a count per confidence level, and the next (lowest) ladder rank still
in that subsystem.  The status and confidence columns cover every name
declared in the document's aggregate ``status`` / ``confidence`` objects, so
each set of counts sums exactly to that subsystem's tracked count, and the
total row sums to ``len(tracked_units)``.  Status and confidence names are
listed in sorted order.

The tool only reports the correspondence inventory.  A row being tracked does
not mean it is implemented, tested or verified; nothing here promotes a unit
or claims that a unit is reconstructed.

It is strictly read-only: the input file is opened for reading only and never
rewritten.  Output is byte-identical for identical input.

Usage::

    python -m tools.compare.ladder_coverage_summary
    python -m tools.compare.ladder_coverage_summary --input fixture.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from tools.compare.source_correspondence_ladder import (
    CORE,
    DEFAULT_INPUT,
    OTHER,
    SUBSYSTEM_ORDER,
    VIDEO,
    VOX,
    CorrespondenceError,
    build_ladder,
    load_document,
)

# (label, help text) in the documented subsystem order.
SUBSYSTEM_ROWS: tuple[tuple[str, str], ...] = (
    (VOX, "NOVA3/extern/vox/"),
    (CORE, "NOVA3/extern/glitch/include/glitch/core/"),
    (
        VIDEO,
        "NOVA3/extern/glitch/include/glitch/video/, "
        "NOVA3/extern/glitch/source/glitch/video/",
    ),
    (OTHER, "everything else (gameplay, third-party helpers, ...)"),
)
assert [label for label, _ in SUBSYSTEM_ROWS] == sorted(
    SUBSYSTEM_ORDER, key=SUBSYSTEM_ORDER.__getitem__
)

TITLE = "Ladder step-4 subsystem coverage (tracked correspondence units only)"
NOTE = (
    "Inventory only: a tracked unit is not implemented, tested or verified. "
    "Counts include every status/confidence name declared in the document."
)


def _declared(document: dict[str, Any], key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise CorrespondenceError(f"'{key}' must be an object of counts")
    # The ladder validates status counts, but not the confidence aggregate.
    # Validate before sorting so hand-built documents also fail cleanly.
    for name, count in value.items():
        if not isinstance(name, str) or not name.strip():
            raise CorrespondenceError(f"'{key}' names must be non-empty strings")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CorrespondenceError(
                f"{key} count {name!r} must be a non-negative integer, got {count!r}"
            )
    return sorted(value)


def build_summary(document: dict[str, Any]) -> dict[str, Any]:
    """Return the per-subsystem counts for a validated document.

    The result carries the ordered subsystem rows, the turn key order, the
    totals and the number of tracked units.
    """
    rows = build_ladder(document)
    statuses = _declared(document, "status")
    confidences = _declared(document, "confidence")

    summary_rows: list[dict[str, Any]] = []
    by_subsystem = {label: [] for label, _ in SUBSYSTEM_ROWS}
    for row in rows:
        if row["confidence"] not in confidences:
            raise CorrespondenceError(
                f"tracked confidence {row['confidence']!r} is not declared in 'confidence'"
            )
        by_subsystem[row["subsystem"]].append(row)

    for label, _ in SUBSYSTEM_ROWS:
        members = by_subsystem[label]
        status_counts = {name: 0 for name in statuses}
        confidence_counts = {name: 0 for name in confidences}
        for member in members:
            # build_ladder validates status membership; confidence membership
            # is checked above before counting any rows.
            status_counts[member["status"]] += 1
            confidence_counts[member["confidence"]] += 1
        summary_rows.append(
            {
                "subsystem": label,
                "tracked": len(members),
                "status": status_counts,
                "confidence": confidence_counts,
                "next_rank": members[0]["rank"] if members else None,
            }
        )

    totals = {
        "subsystem": "total",
        "tracked": len(rows),
        "status": {name: 0 for name in statuses},
        "confidence": {name: 0 for name in confidences},
        "next_rank": None,
    }
    for summary_row in summary_rows:
        for name, count in summary_row["status"].items():
            totals["status"][name] = totals["status"].get(name, 0) + count
        for name, count in summary_row["confidence"].items():
            totals["confidence"][name] = totals["confidence"].get(name, 0) + count

    return {
        "rows": summary_rows,
        "totals": totals,
        "statuses": statuses,
        "confidences": confidences,
        "tracked_units": len(rows),
    }


def _cell(value: Any) -> str:
    text = "-" if value is None else str(value)
    return text.replace("|", "\\|")


def render_markdown(summary: dict[str, Any]) -> str:
    """Serialise the summary deterministically as a Markdown table."""
    statuses: list[str] = list(summary["statuses"])
    confidences: list[str] = list(summary["confidences"])

    header = (
        ["subsystem", "tracked"]
        + [f"status:{name}" for name in statuses]
        + [f"confidence:{name}" for name in confidences]
        + ["next rank"]
    )
    lines = [
        f"# {TITLE}",
        "",
        f"> {NOTE}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|",
    ]
    for row in summary["rows"] + [summary["totals"]]:
        cells = [row["subsystem"], row["tracked"]]
        cells += [row["status"].get(name, 0) for name in statuses]
        cells += [row["confidence"].get(name, 0) for name in confidences]
        cells.append(row["next_rank"])
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines += [
        "",
        "Legend: next rank is the lowest ladder rank (1-based) still in that "
        "subsystem; '-' means the subsystem has no tracked units. Ladder order: "
        + " -> ".join(f"`{label}` ({paths})" for label, paths in SUBSYSTEM_ROWS)
        + ".",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.ladder_coverage_summary",
        description=(
            "Print a deterministic Markdown table of tracked correspondence "
            "units per ladder subsystem (vox, glitch::core, glitch::video, "
            "gameplay/other) with status, confidence and next-rank counts. "
            "Read-only; reports inventory only and claims no reconstruction."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_document(args.input)
        summary = build_summary(document)
    except (CorrespondenceError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
