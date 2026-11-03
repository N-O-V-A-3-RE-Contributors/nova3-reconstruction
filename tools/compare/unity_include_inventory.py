#!/usr/bin/env python3
"""Read-only lexical inventory of explicitly supplied UB_*.cpp wrappers.

Usage: python tools/compare/unity_include_inventory.py /local/path/UB_demo.cpp
JSON goes to stdout; keep inventories of private wrappers private. No source
bodies, conditions or macro definitions are emitted. Include operands and the
supplied wrapper paths are metadata, not safe-to-publish provenance by default.

Only literal, case-sensitive .c/.cpp operands and unresolved nonliteral operands
are reported. Operands retain their quotes/angle brackets; comments are replaced
with whitespace and backslash-newline splices are removed. occurrence_order is
one-based among reported records, reset for each supplied wrapper. Wrapper order
is argument order (including repeated paths), and include order is source order.
line is the physical line containing the directive's '#', ignoring #line remaps.
conditional_depth counts enclosing #if/#ifdef/#ifndef blocks, including inactive
branches; #elif/#else do not change it. No expression or macro is evaluated.

This is not a preprocessor: no include expansion, search paths, project/makefile
resolution, compiler invocation or included-file reads. Standard comments,
strings, character literals, raw strings and line splices are recognized; legacy
trigraphs and alternative directive tokens (%:) are outside this lexical scope.
Malformed comments/literals/directives or unbalanced conditionals are refused
rather than producing a misleading partial inventory. All grouping remains a
HYPOTHESIS, not evidence that Premium used these wrappers or original TU groups.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Iterator


class InventoryError(ValueError):
    """An explicit input could not be inventoried without ambiguity."""


_DIRECTIVE = re.compile(r"^[ \t\v\f]*#[ \t\v\f]*([A-Za-z_][A-Za-z_0-9]*)")
_INCLUDE_PREFIX = re.compile(r"^[ \t\v\f]*#[ \t\v\f]*include[ \t\v\f]*$")
_RAW = re.compile(r'(?:u8|u|U|L)?R"([^ ()\\\t\v\f\r\n]{0,16})\(')
_LITERAL = re.compile(r'(?:"([^"\n]+)"|<([^>\n]+)>)[ \t\v\f]*$')


def _logical_lines(text: str) -> Iterator[tuple[str, list[int]]]:
    """Splice before recognizing comments, retaining physical line provenance."""
    pieces: list[str] = []
    numbers: list[int] = []
    lines = text.split("\n")
    for number, line in enumerate(lines, 1):
        continued = number < len(lines) and line.endswith("\\")
        piece = line[:-1] if continued else line
        pieces.append(piece)
        numbers.extend([number] * len(piece))
        if not continued:
            yield "".join(pieces), numbers
            pieces, numbers = [], []


def _lex_line(line: str, state: str) -> tuple[str, str, str]:
    """Return comment-cleaned text and a literal-masked directive projection.

    state is empty, '*/' (block comment), or a raw string closing delimiter.
    Keeping two projections prevents directives inside multiline raw strings
    from being treated as code while retaining literal include operands.
    """
    clean: list[str] = []
    code: list[str] = []
    i = 0
    while i < len(line):
        if state:
            end = line.find(state, i)
            stop = len(line) if end < 0 else end + len(state)
            segment = line[i:stop]
            clean.append(" " * len(segment) if state == "*/" else segment)
            # A non-whitespace sentinel prevents a following '#' on the same
            # raw-string closing line from masquerading as a directive.
            code.append(" " * len(segment) if state == "*/" else "@" * len(segment))
            i = stop
            if end >= 0:
                state = ""
        elif line.startswith("//", i):
            clean.append(" " * (len(line) - i))
            code.append(" " * (len(line) - i))
            break
        elif line.startswith("/*", i):
            clean.append("  ")
            code.append("  ")
            i += 2
            state = "*/"
        else:
            raw = _RAW.match(line, i)
            if raw and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")):
                segment = raw.group()
                clean.append(segment)
                code.append("@" * len(segment))
                i = raw.end()
                state = ")" + raw.group(1) + '"'
            elif line[i] in "\"'" or (
                line[i] == "<" and _INCLUDE_PREFIX.fullmatch("".join(clean))
            ):
                quote = ">" if line[i] == "<" else line[i]
                end = i + 1
                while end < len(line):
                    if line[end] == quote:
                        break
                    # Header names do not use C string escape semantics.
                    if line[end] == "\\" and line[i] != "<":
                        end += 1
                    end += 1
                if end >= len(line):
                    raise InventoryError("unterminated literal or include operand")
                segment = line[i:end + 1]
                clean.append(segment)
                code.append("@" * len(segment))
                i = end + 1
            else:
                clean.append(line[i])
                code.append(line[i])
                i += 1
    return "".join(clean), "".join(code), state


def _scan(text: str, wrapper: str) -> list[dict]:
    records: list[dict] = []
    conditionals: list[bool] = []  # whether each block has seen #else
    state = ""
    for logical, physical_lines in _logical_lines(text):
        clean, code, state = _lex_line(logical, state)
        directive = _DIRECTIVE.match(code)
        if not directive:
            continue
        line = physical_lines[code.index("#")]
        name = directive.group(1)
        if name in ("if", "ifdef", "ifndef"):
            conditionals.append(False)
        elif name == "endif":
            if not conditionals:
                raise InventoryError(f"line {line}: unmatched #endif")
            conditionals.pop()
        elif name in ("else", "elif"):
            if not conditionals or conditionals[-1]:
                raise InventoryError(f"line {line}: misplaced #{name}")
            if name == "else":
                conditionals[-1] = True
        elif name == "include":
            operand = clean[directive.end():].strip()
            if not operand:
                raise InventoryError(f"line {line}: empty #include")
            literal = _LITERAL.fullmatch(operand)
            if literal:
                target = literal.group(1) or literal.group(2)
                if not target.endswith((".c", ".cpp")):
                    continue
                kind = "literal"
            else:
                if operand.startswith(('"', '<')):
                    raise InventoryError(f"line {line}: malformed literal #include")
                kind = "unresolved"
            records.append({
                "wrapper": wrapper,
                "line": line,
                "include_operand": operand,
                "occurrence_order": len(records) + 1,
                "conditional_depth": len(conditionals),
                "kind": kind,
            })
    if state:
        raise InventoryError("unterminated block comment or raw string")
    if conditionals:
        raise InventoryError("unterminated conditional block")
    return records


def inventory(wrappers: Iterable[str | Path]) -> dict:
    """Open only explicitly supplied wrappers, read-only; never follow includes."""
    paths = [Path(path) for path in wrappers]
    if not paths:
        raise InventoryError("supply at least one explicit UB_*.cpp wrapper path")
    # Validate every name before opening any input; no globbing/discovery.
    for path in paths:
        if not (path.name.startswith("UB_") and path.name.endswith(".cpp")):
            raise InventoryError("expected an explicit UB_*.cpp wrapper path")
    records: list[dict] = []
    for path in paths:
        # strict decoding avoids quietly changing operand spelling. Universal
        # newlines support LF/CRLF/CR; utf-8-sig accepts a leading UTF-8 BOM.
        with path.open("r", encoding="utf-8-sig") as source:
            text = source.read()
        records.extend(_scan(text, str(path)))
    return {
        "schema": "nova3.unity-include-inventory/1",
        "grouping": "HYPOTHESIS",
        "original_build": "UNKNOWN",
        "scope": "lexical-direct-includes-only",
        "wrappers": [str(path) for path in paths],
        "includes": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrappers", nargs="+", help="explicit UB_*.cpp paths; no discovery")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.wrappers)
    except (InventoryError, OSError, UnicodeError) as error:
        # Do not echo source snippets or OS messages containing private paths.
        message = str(error) if isinstance(error, InventoryError) else type(error).__name__
        parser.exit(2, f"unity include inventory: {message}\n")
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
