#!/usr/bin/env python3
"""Read-only extractor for build variables in Android.mk / Application.mk.

The tool parses makefile *text* only.  It never expands ``$(...)`` references,
never evaluates make functions, never follows ``include`` directives (the one
exception is recognised as a module boundary, see below) and never starts a
shell or subprocess.  Values are reported exactly as written, so a reference
such as ``$(LOCAL_PATH)/include`` stays unexpanded and in its original order.

Supported syntax
----------------
* ``NAME := value``  immediate assignment (replaces the previous value)
* ``NAME = value``   assignment, treated like ``:=`` because nothing is
                     expanded or deferred by this parser
* ``NAME += value``  append, separated from the previous value by one space
* ``NAME ?= value``  assign only when the variable is still unset
* ``\\`` at end of line: continuation; the backslash, the newline and the
  leading whitespace of the following line are replaced by one space
* ``#`` comments (``\\#`` is a literal ``#``)
* ``include $(CLEAR_VARS)`` ends the current module and begins a fresh local
  variable scope.  Every other ``include`` line is ignored, not followed.

Tracked variables
-----------------
Module scope: ``LOCAL_MODULE``, ``LOCAL_CFLAGS``, ``LOCAL_CPPFLAGS``,
``LOCAL_C_INCLUDES``, ``LOCAL_ARM_MODE``, ``LOCAL_ARM_NEON``.
Global scope: ``APP_CFLAGS``, ``APP_CPPFLAGS``, ``APP_STL``, ``APP_PLATFORM``,
``APP_ABI``.

Report schema (stable, one object per run)::

    {
      "schema": "nova3.android-mk-build-vars/1",
      "inputs": ["<path as given>", ...],
      "modules": [
        {"source": "<path>", "LOCAL_MODULE": "name", "LOCAL_CFLAGS": "-O2",
         ... all six LOCAL keys, null when never assigned ...}
      ],
      "globals": {
        "APP_CFLAGS": null, ... all five APP keys, null when never assigned ...
      }
    }

A module is emitted for each ``include $(CLEAR_VARS)`` block that assigns at
least one tracked ``LOCAL_*`` variable; empty blocks are skipped.  Module order
is source order.  ``null`` means "never assigned"; ``""`` means explicitly
assigned an empty value.

Usage::

    python -m tools.compare.android_mk_build_vars jni/Android.mk jni/Application.mk
    python -m tools.compare.android_mk_build_vars ... --output build-vars.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterator, Sequence

SCHEMA = "nova3.android-mk-build-vars/1"

LOCAL_VARIABLES = (
    "LOCAL_MODULE",
    "LOCAL_CFLAGS",
    "LOCAL_CPPFLAGS",
    "LOCAL_C_INCLUDES",
    "LOCAL_ARM_MODE",
    "LOCAL_ARM_NEON",
)
APP_VARIABLES = (
    "APP_CFLAGS",
    "APP_CPPFLAGS",
    "APP_STL",
    "APP_PLATFORM",
    "APP_ABI",
)

_CLEAR_VARS_RE = re.compile(r"^include\s+\$\(CLEAR_VARS\)\s*$")
_ASSIGN_RE = re.compile(
    r"^(?:(?:export|override)\s+)*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*"
    r"(?P<op>\+=|:=|\?=|=)\s*"
    r"(?P<value>.*)$"
)


def _has_continuation(line: str) -> bool:
    """True when *line* ends in an odd number of backslashes."""
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


def _strip_comment(line: str) -> str:
    """Remove an unescaped ``#`` comment and unescape ``\\#``."""
    out: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] == "#":
            out.append("#")
            index += 2
            continue
        if char == "#":
            break
        out.append(char)
        index += 1
    return "".join(out)


def iter_logical_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(first_physical_line_number, joined_line)`` for *text*.

    Backslash continuations are folded into a single logical line before
    comment removal, matching make's rule that a comment can continue across a
    backslash-newline.
    """
    physical = text.splitlines()
    index = 0
    while index < len(physical):
        first_line = index + 1
        pieces: list[str] = []
        line = physical[index]
        while True:
            continued = _has_continuation(line)
            if continued:
                line = line[:-1].rstrip()
            pieces.append(line)
            if not continued or index + 1 >= len(physical):
                break
            index += 1
            line = physical[index].lstrip()
        yield first_line, " ".join(pieces)
        index += 1


def _apply(values: dict[str, str | None], name: str, operator: str,
           value: str) -> None:
    """Apply one assignment to a variable scope without expanding anything."""
    current = values[name]
    if operator == "?=" and current is not None:
        return
    if operator == "+=" and current:
        values[name] = f"{current} {value}" if value else current
        return
    values[name] = value


def _new_locals() -> dict[str, str | None]:
    return {name: None for name in LOCAL_VARIABLES}


def _parse_source(text: str, source: str,
                  globals_: dict[str, str | None]) -> list[tuple[str, dict[str, str | None]]]:
    """Parse one makefile's text and return its non-empty module records."""
    modules: list[tuple[str, dict[str, str | None]]] = []
    current: dict[str, str | None] | None = None

    for _line_number, logical in iter_logical_lines(text):
        line = _strip_comment(logical).strip()
        if not line:
            continue
        if _CLEAR_VARS_RE.match(line):
            if current is not None and any(value is not None
                                           for value in current.values()):
                modules.append((source, current))
            current = _new_locals()
            continue

        match = _ASSIGN_RE.match(line)
        if match is None:
            continue
        name = match.group("name")
        operator = match.group("op")
        value = match.group("value").strip()

        if name in APP_VARIABLES:
            _apply(globals_, name, operator, value)
        elif name in LOCAL_VARIABLES:
            if current is None:
                current = _new_locals()
            _apply(current, name, operator, value)

    if current is not None and any(value is not None for value in current.values()):
        modules.append((source, current))
    return modules


def _report(inputs: Sequence[str],
            modules: Sequence[tuple[str, dict[str, str | None]]],
            globals_: dict[str, str | None]) -> dict:
    return {
        "schema": SCHEMA,
        "inputs": list(inputs),
        "modules": [
            {"source": source,
             **{name: values[name] for name in LOCAL_VARIABLES}}
            for source, values in modules
        ],
        "globals": {name: globals_[name] for name in APP_VARIABLES},
    }


def parse_text(text: str, source: str = "<memory>") -> dict:
    """Parse makefile *text* and return the report object for one input."""
    globals_ = {name: None for name in APP_VARIABLES}
    modules = _parse_source(text, source, globals_)
    return _report([source], modules, globals_)


def parse_paths(paths: Sequence[str | Path]) -> dict:
    """Parse each path in order and return one report object."""
    globals_ = {name: None for name in APP_VARIABLES}
    modules: list[tuple[str, dict[str, str | None]]] = []
    inputs: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        inputs.append(str(path))
        text = path.read_text(encoding="utf-8")
        modules.extend(_parse_source(text, str(path), globals_))
    return _report(inputs, modules, globals_)


def parse_file(path: str | Path) -> dict:
    """Parse a single makefile and return its report object."""
    return parse_paths([path])


def render(report: dict) -> str:
    """Serialise *report* deterministically (fixed key order, trailing newline)."""
    return json.dumps(report, indent=2, ensure_ascii=True) + "\n"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.android_mk_build_vars",
        description=(
            "Read-only extraction of Android.mk / Application.mk build "
            "variables. No $(...) expansion, no shell, no include following."
        ),
        epilog=(
            "Values are emitted raw: '$(LOCAL_PATH)' remains unexpanded. "
            "A module is emitted per non-empty include $(CLEAR_VARS) block."
        ),
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="MAKEFILE",
        help="Android.mk or Application.mk file to read (one or more)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="write JSON here instead of standard output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        report = parse_paths(args.files)
    except (OSError, UnicodeError) as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    payload = render(report)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
