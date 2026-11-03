#!/usr/bin/env python3
"""Read-only textual inventory of three Glitch build-configuration macros.

The scanner records every ``#define``/``#undef`` occurrence of
``GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL``, ``GLITCH_FAST_MATH`` and
``ROUNDING_ERROR_64`` in the supplied headers.  It deliberately does not run
the C preprocessor and does not evaluate conditionals: a directive inside
``#if 0`` is still an occurrence, and ``#define GLITCH_FAST_MATH 0`` is
reported as a definition whose replacement token is ``0``.  This is an
inventory of source text, not a C configuration evaluation.

``--assert-recovered-config`` applies only the build configuration recorded in
``docs/progress/NEXT.md`` from machine-code evidence:

* ``GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL`` is defined;
* ``GLITCH_FAST_MATH`` is not defined; and
* ``ROUNDING_ERROR_64`` is numerically ``1e-8``.

The final textual directive wins for that check (``#define`` sets a macro,
``#undef`` clears it); no conditional semantics are invented.  The tool only
opens files for reading.  It never writes files, invokes a compiler, invokes a
preprocessor, or executes any other program.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys

SCHEMA = "nova3-glitch-build-config-inventory/1"
MACROS = (
    "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL",
    "GLITCH_FAST_MATH",
    "ROUNDING_ERROR_64",
)
ROUNDING_ERROR_64 = 1e-8
HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".inc"})

_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(define|undef)\b(.*)$", re.DOTALL)
_MACRO_NAME = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)(.*)$", re.DOTALL)
_NUMERIC = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


class ScanError(ValueError):
    """An input path could not be inventoried as readable source text."""


def _strip_comments(text: str) -> str:
    """Blank C/C++ comments while preserving newlines and line numbering.

    The scanner must not mistake a commented-out directive for a live one.
    String and character literals are respected so ``"//"`` does not start a
    comment and an escaped quote does not end one.  Newlines inside block
    comments are retained, so reported line numbers remain the physical source
    line numbers.
    """
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < len(text) and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.extend((" ", " "))
            i += 2
            while i < len(text):
                if text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    out.extend((" ", " "))
                    i += 2
                    break
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _logical_lines(text: str):
    """Yield ``(first_physical_line, logical_text)`` with continuations joined."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        first = i + 1
        parts = [lines[i]]
        while parts[-1].rstrip().endswith("\\"):
            parts[-1] = parts[-1].rstrip()[:-1]
            i += 1
            if i >= len(lines):
                break
            parts.append(lines[i])
        yield first, "\n".join(parts)
        i += 1


def _replacement_token(rest: str) -> str:
    """Return the exact source tokens after the macro name, trimmed at the ends."""
    return rest.strip()


def scan_text(text: str, file_name: str, *, first_order: int = 1) -> list[dict]:
    """Inventory matching directives in one source text, in textual order."""
    records: list[dict] = []
    order = first_order
    cleaned = _strip_comments(text)
    for line_number, logical in _logical_lines(cleaned):
        match = _DIRECTIVE.match(logical)
        if match is None:
            continue
        directive = match.group(1)
        name_match = _MACRO_NAME.match(match.group(2))
        if name_match is None:
            continue
        macro = name_match.group(1)
        if macro not in MACROS:
            continue
        replacement = "" if directive == "undef" else _replacement_token(
            name_match.group(2)
        )
        records.append(
            {
                "file": file_name,
                "line": line_number,
                "directive": directive,
                "macro": macro,
                "replacement": replacement,
                "defined": directive == "define",
                "order": order,
            }
        )
        order += 1
    return records


def _header_files(paths: list[str | os.PathLike[str]]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found = [
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in HEADER_SUFFIXES
            ]
            files.extend(sorted(found, key=lambda item: str(item)))
        elif path.is_file():
            files.append(path)
        else:
            raise ScanError(f"not a readable file or directory: {raw}")
    return files


def scan_paths(paths: list[str | os.PathLike[str]]) -> list[dict]:
    """Read files (never writing) and return all records in deterministic order."""
    records: list[dict] = []
    for path in _header_files(paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ScanError(f"cannot read {path}: {exc}") from exc
        records.extend(scan_text(text, os.fspath(path), first_order=len(records) + 1))
    return records


def inventory(paths: list[str | os.PathLike[str]]) -> dict:
    """Return a JSON-serialisable, read-only inventory."""
    return {
        "schema": SCHEMA,
        "macros": list(MACROS),
        "records": scan_paths(paths),
        "preprocessor_invoked": False,
    }


def _numeric_value(token: str) -> float | None:
    candidate = token.strip()
    while len(candidate) >= 2 and candidate[0] == "(" and candidate[-1] == ")":
        candidate = candidate[1:-1].strip()
    # C/C++ floating suffixes do not change the numeric value.
    candidate = candidate.rstrip("fFlLuU")
    if not _NUMERIC.fullmatch(candidate):
        return None
    try:
        return float(candidate)
    except ValueError:
        return None


def evaluate_recovered_config(records: list[dict]) -> list[str]:
    """Return failures against the NEXT.md machine-code-derived configuration.

    Only the three named macros participate.  A later directive overrides an
    earlier one textually; no preprocessor conditionals or value semantics are
    evaluated.  Therefore ``#define GLITCH_FAST_MATH 0`` is a definition and
    fails the "not defined" requirement.
    """
    state: dict[str, dict] = {}
    for record in records:
        macro = record["macro"]
        if macro not in MACROS:
            continue
        if record["directive"] == "define":
            state[macro] = {
                "defined": True,
                "replacement": record["replacement"],
                "file": record["file"],
                "line": record["line"],
                "order": record["order"],
            }
        elif record["directive"] == "undef":
            state[macro] = {
                "defined": False,
                "replacement": None,
                "file": record["file"],
                "line": record["line"],
                "order": record["order"],
            }

    failures: list[str] = []

    matrix = state.get("GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL")
    if matrix is None or not matrix["defined"]:
        failures.append(
            "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL: expected defined, observed "
            "not defined"
        )

    fast = state.get("GLITCH_FAST_MATH")
    if fast is not None and fast["defined"]:
        failures.append(
            "GLITCH_FAST_MATH: expected not defined, observed defined at "
            f"{fast['file']}:{fast['line']} with replacement "
            f"{fast['replacement']!r}"
        )

    rounding = state.get("ROUNDING_ERROR_64")
    if rounding is None or not rounding["defined"]:
        failures.append(
            "ROUNDING_ERROR_64: expected a definition numerically equal to "
            "1e-8, observed not defined"
        )
    else:
        value = _numeric_value(rounding["replacement"])
        if value is None:
            failures.append(
                "ROUNDING_ERROR_64: expected a definition numerically equal to "
                f"1e-8, observed non-numeric replacement "
                f"{rounding['replacement']!r}"
            )
        elif not math.isclose(value, ROUNDING_ERROR_64, rel_tol=0.0, abs_tol=0.0):
            failures.append(
                "ROUNDING_ERROR_64: expected a definition numerically equal to "
                f"1e-8, observed {rounding['replacement']!r} ({value!r})"
            )

    return failures


def _print_records(records: list[dict]) -> None:
    for record in records:
        suffix = "" if record["directive"] == "undef" else f" {record['replacement']}"
        print(
            f"{record['file']}:{record['line']}: "
            f"#{record['directive']} {record['macro']}{suffix}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.glitch_build_config_inventory",
        description=(
            "Inventory textual #define/#undef occurrences of the three Glitch "
            "build-configuration macros. No preprocessor or compiler is run."
        ),
        epilog=(
            "Recorded macros: GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL, "
            "GLITCH_FAST_MATH, ROUNDING_ERROR_64. Directory inputs are searched "
            "recursively for header suffixes. The --assert-recovered-config "
            "check uses only the NEXT.md values: "
            "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL defined, GLITCH_FAST_MATH not "
            "defined, and ROUNDING_ERROR_64 numerically 1e-8."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="header file or directory to scan (directory scanning is recursive)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the inventory as JSON instead of one line per record",
    )
    parser.add_argument(
        "--assert-recovered-config",
        action="store_true",
        help="exit nonzero unless the NEXT.md machine-code configuration matches",
    )
    args = parser.parse_args(argv)

    try:
        report = inventory(args.paths)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.assert_recovered_config:
        failures = evaluate_recovered_config(report["records"])
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        print(
            "OK: GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL defined; "
            "GLITCH_FAST_MATH not defined; ROUNDING_ERROR_64 == 1e-8"
        )

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    else:
        _print_records(report["records"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
