#!/usr/bin/env python3
"""Render the ladder's rank-1 unit as a cited ``## Next ladder unit`` block.

``tools/compare/source_correspondence_ladder.py`` ranks the tracked units of
``coordination/source_correspondence.json`` (schema
``nova3-source-correspondence/1``).  This module turns rank 1 into a small
Markdown block an operator can paste into ``docs/progress/NEXT.md`` for one
compile-and-compare cycle (NEXT.md step 4).  It is strictly read-only and writes
to stdout only; it never edits the correspondence file or NEXT.md.

Evidence rules:

* The address, subsystem, source ``file:line``, status, confidence and evidence
  ratio come from the correspondence JSON and are printed as recorded.
* The size comes **only** from the committed function ledger
  (``coordination/function_ledger.json``), matched by exact numeric address.  It
  is never read from the ELF.  A missing (or ambiguous) ledger entry prints
  ``size: UNKNOWN``; it is not an error.
* The four separate status axes (source present / host test / ARM object /
  instruction-window match) are fixed at ``UNKNOWN``.  This tool has no evidence
  for them, so it never promotes or infers them.
* Documents whose schema is not ``nova3-source-correspondence/1`` are refused.
  A row whose ``confidence`` is outside the committed vocabulary is refused.

Every input is limited to what an unprivileged tool may be trusted to report:
paths in the block are rendered as plain text (HTML and line-break characters are
escaped so a malformed row cannot inject markup), the correspondence is
validated by the ladder module itself, and the ledger is re-validated here so it
may be an arbitrary file.

Usage::

    python -m tools.compare.ladder_next_block
    python -m tools.compare.ladder_next_block --input fixture.json \\
        --ledger fixture_ledger.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tools.compare import source_correspondence_ladder as ladder

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ladder.DEFAULT_INPUT
DEFAULT_LEDGER = ROOT / "coordination" / "function_ledger.json"
LADDER_MODULE = "tools.compare.source_correspondence_ladder"

# The complete label set assigned by the committed correspondence generator
# (tools/source_ref/build_source_correspondence.py, including the ``or 'none'``
# fallback for rows without a match).  The counting order is the one in which
# docs/progress/SOURCE_CORRESPONDENCE.md lists the vocabulary.
CONFIDENCE_VOCABULARY = (
    "weak",
    "none",
    "definition_only",
    "unique_site",
    "strong",
    "signature_match",
    "file_hint",
)

# The four status axes are independent; this tool has evidence for none of them.
AXES = (
    ("source present", "UNKNOWN"),
    ("host test", "UNKNOWN"),
    ("ARM object", "UNKNOWN"),
    ("instruction-window match", "UNKNOWN"),
)

UNKNOWN = "UNKNOWN"
ADDRESS_PATTERN = re.compile(r"0x[0-9a-f]{8}")
_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "`": "&#96;",
    "|": "\\|",
}


class BlockError(ValueError):
    """The inputs cannot be rendered into a trustworthy block."""


def _parse_ledger_address(value: Any) -> int | None:
    """Return the numeric address of a ledger row, or ``None`` if unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFFFFFFFF else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip(), 0)
        except ValueError:
            return None
        return parsed if 0 <= parsed <= 0xFFFFFFFF else None
    return None


def load_ledger(path: Path) -> dict[int, list[Any]]:
    """Read the committed ledger and index its function sizes by address.

    Returns ``{address: [size, ...]}`` where each size is either a positive int
    or ``None``.  The caller decides whether several entries at one address are
    agreement (one size value) or ambiguity (``size: UNKNOWN``).  A missing file
    yields an empty index, because the block then simply prints ``UNKNOWN``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise BlockError(f"cannot read ledger {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BlockError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise BlockError(f"{path}: ledger top-level value must be an object")
    functions = document.get("functions")
    if not isinstance(functions, list):
        raise BlockError(f"{path}: ledger 'functions' must be an array")

    index: dict[int, list[Any]] = {}
    for position, row in enumerate(functions, start=1):
        if not isinstance(row, dict):
            raise BlockError(f"{path}: functions[{position}] must be an object")
        address = _parse_ledger_address(row.get("address"))
        if address is None:
            continue
        index.setdefault(address, []).append(_parse_ledger_size(row.get("size")))
    return index


def _parse_ledger_size(value: Any) -> int | None:
    """Return a positive byte count, or ``None`` for anything unusable."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def ledger_size(index: dict[int, list[Any]], address: int) -> int | None:
    """Return the ledger size for ``address``, or ``None`` if unknown/ambiguous.

    Several entries at one address are accepted only when all usable sizes agree;
    conflicting sizes are ambiguity, not a reason to pick one, because the ledger
    does not record which entry is authoritative.
    """
    known = {
        parsed
        for parsed in map(_parse_ledger_size, index.get(address, []))
        if parsed is not None
    }
    if len(known) != 1:
        return None
    (only,) = known
    return only


def safe(text: str) -> str:
    """Escape a row string for inline Markdown/HTML context."""
    out = "".join(
        char
        if char.isprintable()
        and char not in _ESCAPES
        else _ESCAPES.get(char, f"&#x{ord(char):04x};")
        for char in text
    )
    return out


def build_row(document: dict[str, Any]) -> dict[str, Any]:
    """Return the validated rank-1 ladder row, refusing unknown confidence.

    The vocabulary is checked on every tracked row, not only rank 1: the block
    cites the correspondence document as its evidence, so a label the committed
    generator cannot produce anywhere in that document is refused outright.
    """
    rows = ladder.build_ladder(document)
    if not rows:
        raise BlockError("tracked_units is empty; there is no next ladder unit")
    for row in rows:
        confidence = row["confidence"]
        if confidence not in CONFIDENCE_VOCABULARY:
            raise BlockError(
                f"tracked_units rank {row['rank']} ({row['unit']!r}): confidence "
                f"{confidence!r} is not in the committed vocabulary "
                f"{', '.join(CONFIDENCE_VOCABULARY)}"
            )
    return rows[0]


def _ledger_label(path: Path) -> str:
    """Render the ledger path relative to the repository when it lies inside it.

    Uses the literal argument rather than resolving symlinks, so a caller-supplied
    path is reported exactly as given (relative stays relative, an absolute path
    under the repository becomes repository-relative).
    """
    if not path.is_absolute():
        return safe(path.as_posix())
    absolute = os.path.abspath(path)
    root = os.path.abspath(ROOT)
    if os.path.commonpath([absolute, root]) == root:
        return safe(Path(os.path.relpath(absolute, root)).as_posix())
    return safe(str(path))


def render_block(
    row: dict[str, Any], *, size: int | None, ledger_path: Path = DEFAULT_LEDGER
) -> str:
    """Render the rank-1 row as a deterministic Markdown block."""
    address = row["address"]
    if not ADDRESS_PATTERN.fullmatch(address):
        raise BlockError(f"rank 1 address {address!r} is not 8 hex digits")
    address_value = int(address, 16)
    size_text = f"{size} bytes (0x{size:08x})" if size is not None else UNKNOWN
    file: str = row["file"]
    line = row["line"]
    unit = safe(row["unit"])

    lines = [
        f"## Next ladder unit: {unit} ({address})",
        "",
        f"- address: {address} (rank 1); numeric {address_value}",
        f"- size: {size_text} (source: {_ledger_label(ledger_path)}; "
        "the ELF was not read)",
        f"- subsystem: {safe(row['subsystem'])}",
        f"- source: {safe(file)}:{line}",
        f"- correspondence: status {safe(row['status'])}, "
        f"confidence {safe(row['confidence'])}, "
        f"evidence {safe(row['evidence'])}",
        "- status axes:",
    ]
    lines.extend(f"  - {label}: {value}" for label, value in AXES)
    lines += [
        "",
        "Claims: this block records a source-correspondence *hypothesis* only. "
        "The axes above are not evidence of compilation, testing or a "
        "machine-code match.",
        "",
        "Verify:",
        "",
        f"1. `python -m {LADDER_MODULE} --next` (the correspondence input above)",
        f"2. compile `{safe(file)}` as one unit with the recorded toolchain, "
        "then compare the instruction window independently.",
        "",
        "The operator pastes this block into docs/progress/NEXT.md manually; "
        "this tool never edits it.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.ladder_next_block",
        description=(
            "Render the source-correspondence ladder's rank-1 unit as a "
            "'## Next ladder unit' Markdown block. Size comes from the "
            "committed function ledger only; status axes stay UNKNOWN. "
            "Prints to stdout and never edits NEXT.md or the correspondence file."
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
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER,
        help=(
            "function ledger JSON used only for the size lookup (default: "
            "repository coordination/function_ledger.json)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = ladder.load_document(args.input)
        row = build_row(document)
        index = load_ledger(args.ledger)
        size = ledger_size(index, int(row["address"], 16))
        text = render_block(row, size=size, ledger_path=args.ledger)
    except (ladder.CorrespondenceError, BlockError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
