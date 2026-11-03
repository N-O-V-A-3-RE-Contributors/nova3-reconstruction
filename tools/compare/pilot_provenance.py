#!/usr/bin/env python3
"""Read-only provenance records for the three collada pilots.

The collada pilots (`collada_matrix4_inverse_pilot.py`,
`collada_matrix_multiply_pilot.py`, `collada_skin_prepare_cache_pilot.py`)
rebuild a candidate object and compare it with the Premium ELF, but their JSON
reports did not record *which* source content, *which* compiler and *which*
flags produced the candidate, so a stale or uncommitted build (the
`collada_matrix_multiply.cpp` experiment warned about in
`docs/progress/NEXT.md`) looked the same as a clean one.

This module is a small record format plus pure parser/comparator functions.
It deliberately does not read the original ELF or any game binary, does not
invoke a compiler, does not gate publication, and makes no match claim. A
"clean" verdict only says the recorded build identity equals the expected one.

Record (a JSON object):

    schema           "nova3.pilot-provenance/1"
    pilot            e.g. "collada_matrix_multiply"
    source_path      compiled source path, as reported by the pilot
    source_sha256    SHA-256 of the source bytes, 64 hex characters
    compiler_version version banner reported by the compiler binary
    argv             flag list: list of lists of str, one inner list per build
                     step; order and duplicates are kept verbatim (steps are
                     compared as one NUL-joined sequence)
    object_sha256    SHA-256 of the produced object, 64 hex characters

Optional background keys, type-checked when present and compared only when
the expected record also states them:

    source_dirty     bool; True = built from a tree with uncommitted edits
    git_head         str; commit the pilot believed it built
    compiler_path    str; binary the version string came from
    object_path      str; object file hashed into object_sha256

``compare_records(recorded, expected)`` returns one of:

    "clean"    every compared field present on both sides is well formed and
               equal, and no expected field differs
    "stale"    everything compared is well formed but a field differs; the
               differing fields are named in ``differing_fields``
    "unknown"  a field that differs is incomplete/malformed on either side,
               or the expected record lacks a required field or is not JSON

The expected record may give an empty/absent object hash (the object is not
built yet): then a complete recorded object is "stale" against it and an empty
one is "unknown"; an empty field never makes equal values "clean".

Known limitations (unfixed, deliberate): two records that both carry the same
blank or malformed field compare "clean" (no usable identity on either side
can be told apart from equality), and a NUL byte inside a flag aliases the
two-flag sequence when flags are flattened.

Example:
    record = build_record(
        pilot="collada_matrix_multiply",
        source_path="reconstruction/recovered/libNOVA3_neon/"
                    "collada_matrix_multiply.cpp",
        source_bytes=compiled_bytes,
        compiler_version=gcc_banner,
        argv=[["-O2", "-fvisibility=hidden"]],
        object_bytes=object_bytes)
    compare_records(record, expected_spec)

This is a record format only; nothing here establishes a byte match.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Sequence

__all__ = [
    "SCHEMA",
    "REQUIRED_FIELDS",
    "FIELDS",
    "EXTRA_FIELDS",
    "COMPARED_FIELDS",
    "CLEAN",
    "STALE",
    "UNKNOWN",
    "VERDICTS",
    "SCOPE",
    "sha256_hex",
    "build_record",
    "parse_record",
    "record_to_json",
    "record_problems",
    "compare_records",
    "source_state",
]

SCHEMA = "nova3.pilot-provenance/1"

REQUIRED_FIELDS = (
    "schema",
    "pilot",
    "source_path",
    "source_sha256",
    "compiler_version",
    "argv",
    "object_sha256",
)

EXTRA_FIELDS = {
    "source_dirty": bool,
    "git_head": str,
    "compiler_path": str,
    "object_path": str,
}

FIELDS = REQUIRED_FIELDS

# Keys the expected record must carry (values may be blank when not known yet).
ANCHOR_FIELDS = ("schema", "pilot", "source_path", "compiler_version")

COMPARED_FIELDS = REQUIRED_FIELDS + tuple(EXTRA_FIELDS)

CLEAN = "clean"
STALE = "stale"
UNKNOWN = "unknown"
VERDICTS = (CLEAN, STALE, UNKNOWN)

SCOPE = (
    "provenance record only; a 'clean' verdict means the recorded build "
    "identity equals the expected identity, not a byte match, not compiler "
    "identification and not a whole-.so SHA-256 match"
)

_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
# Flattened flags are joined with NUL, which cannot occur inside an argv
# entry, so ["a", "b"] can never alias the single flag "a\x00b".
_SEP = "\x00"


def sha256_hex(data: bytes | bytearray | memoryview | str) -> str:
    """Lowercase hex SHA-256 of in-memory bytes (str is UTF-8 encoded)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif isinstance(data, bytearray):
        data = bytes(data)
    elif not isinstance(data, (bytes, memoryview)):
        raise TypeError("sha256_hex expects bytes, bytearray, memoryview or str")
    return hashlib.sha256(data).hexdigest()


def _normalise_argv(argv: Iterable[Sequence[str]]) -> list[list[str]]:
    """Copy flag steps, keeping order and duplicates; reject non-strings."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise TypeError("argv must be a list of flag-list steps")
    steps = []
    for step in argv:
        if isinstance(step, (str, bytes)) or not isinstance(step, Sequence):
            raise TypeError("argv must be a list of flag-list steps")
        flags = []
        for flag in step:
            if not isinstance(flag, str):
                raise TypeError("every flag must be a str")
            flags.append(flag)
        steps.append(flags)
    return steps


def build_record(
    *,
    pilot: str,
    source_path: str,
    source_bytes: bytes | bytearray | memoryview | str,
    compiler_version: str,
    argv: Iterable[Sequence[str]],
    object_bytes: bytes | bytearray | memoryview | str | None,
    source_dirty: bool | None = None,
    git_head: str | None = None,
    compiler_path: str | None = None,
    object_path: str | None = None,
) -> dict[str, Any]:
    """Build one provenance record from values the pilot already holds.

    Pure: hashes the given bytes, copies the flag lists and returns a new
    JSON-ready dict. Empty compiler version and empty/absent object bytes are
    recorded as found (empty strings), never guessed; they make
    ``compare_records`` answer "unknown".
    """
    for name, value in (("pilot", pilot), ("source_path", source_path),
                        ("compiler_version", compiler_version)):
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a str")
    if object_bytes is None:
        object_sha256 = ""
    else:
        object_sha256 = sha256_hex(object_bytes)
    record = {
        "schema": SCHEMA,
        "pilot": pilot,
        "source_path": source_path,
        "source_sha256": sha256_hex(source_bytes),
        "compiler_version": compiler_version,
        "argv": _normalise_argv(argv),
        "object_sha256": object_sha256,
    }
    background = {"source_dirty": source_dirty, "git_head": git_head,
                  "compiler_path": compiler_path, "object_path": object_path}
    for name, value in background.items():
        if isinstance(value, (bool, str)):
            record[name] = value
    return record


def _check_field(field: str, value: Any) -> str | None:
    """Reason the field is incomplete/malformed, or None when well formed."""
    if field == "argv":
        if not isinstance(value, list):
            return "argv is not a list of flag-list steps"
        for index, step in enumerate(value):
            if not isinstance(step, list):
                return f"argv[{index}] is not a list of flags"
            for flag in step:
                if not isinstance(flag, str):
                    return f"argv[{index}] has a non-string flag"
        return None
    if not isinstance(value, str):
        return f"{field} is not a str"
    if field == "schema":
        if value != SCHEMA:
            return f"schema is {value!r}, not {SCHEMA!r}"
        return None
    if field in ("source_sha256", "object_sha256"):
        if value == "":
            return f"{field} is empty"
        if not _HEX64.match(value):
            return f"{field} is not 64 hex characters"
        return None
    if field == "compiler_version":
        if value.strip() == "":
            return "compiler_version is empty"
        return None
    return None


def record_problems(record: Any) -> dict[str, str]:
    """Map each missing, incomplete or malformed field to a reason.

    An empty dict means the record is complete and well formed. ``"record"`` is
    used for a non-object. Optional background keys are type-checked when
    present; unknown keys are tolerated and never compared.
    """
    if not isinstance(record, dict):
        return {"record": "record is not a JSON object"}
    problems = {}
    for field in REQUIRED_FIELDS:
        if field not in record:
            problems[field] = f"{field} is missing"
            continue
        reason = _check_field(field, record[field])
        if reason is not None:
            problems[field] = reason
    for field, kind in EXTRA_FIELDS.items():
        value = record.get(field)
        if value is None:
            continue
        if not isinstance(value, kind):
            problems[field] = f"{field} is not a {kind.__name__}"
    return problems


def source_state(record: Any) -> str:
    """``"dirty"``, ``"pristine"`` or ``"unknown"`` for the build tree."""
    if not isinstance(record, dict):
        return UNKNOWN
    marker = record.get("source_dirty")
    if marker is True:
        return "dirty"
    if marker is False:
        return "pristine"
    return UNKNOWN


def parse_record(
    text: str | bytes | bytearray | dict[str, Any],
) -> dict[str, Any]:
    """Parse a record (JSON text/bytes or dict) into required-field order.

    Not validated: incomplete records still parse, so the comparator can say
    "unknown" and name the fields. Raises ValueError for unparseable JSON or a
    non-object top level.
    """
    loaded: Any = text
    if isinstance(text, (str, bytes, bytearray)):
        try:
            loaded = json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"record is not valid JSON: {exc}") from None
    if not isinstance(loaded, dict):
        raise ValueError("record JSON must be an object")
    canonical: dict[str, Any] = {}
    for field in REQUIRED_FIELDS:
        value = loaded.get(field)
        if field not in loaded:
            continue
        if field == "argv" and isinstance(value, list):
            copied = []
            for step in value:
                copied.append(list(step) if isinstance(step, list) else step)
            value = copied
        canonical[field] = value
    for field, kind in EXTRA_FIELDS.items():
        if field in loaded and isinstance(loaded[field], kind):
            canonical[field] = loaded[field]
    return canonical


def record_to_json(record: dict[str, Any]) -> str:
    """Stable key-sorted JSON text for a record (round-trips via parse)."""
    ordered = {f: record[f] for f in FIELDS + tuple(EXTRA_FIELDS) if f in record}
    return json.dumps(ordered, sort_keys=True, indent=2) + "\n"


def _flatten_argv(argv) -> str:
    if not isinstance(argv, list):
        return ""
    flags = []
    for step in argv:
        if not isinstance(step, list):
            return ""
        for flag in step:
            if not isinstance(flag, str):
                return ""
            flags.append(flag)
    return _SEP.join(flags)


def _flat_value(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if field in EXTRA_FIELDS and value.__class__ is bool:
        return "true" if value else "false"
    if field == "argv":
        return _flatten_argv(value)
    if isinstance(value, str):
        return value
    return ""


def _flatten(record: dict[str, Any]) -> dict[str, str]:
    """Exact string view of each compared field (argv NUL-joined)."""
    flat = {}
    for field in COMPARED_FIELDS:
        flat[field] = _flat_value(record, field)
    return flat


def _out(verdict, differing, unknown, reason="", differing_args=None,
         source=None):
    """Canonical comparison report (JSON-ready, no shared state)."""
    report = {
        "verdict": verdict,
        "differing_fields": list(differing),
        "unknown_fields": list(unknown),
        "source_state": source_state(source),
        "scope": SCOPE,
    }
    if reason:
        report["reason"] = reason
    if differing_args:
        current = []
        wanted = []
        for step in differing_args[0]:
            if isinstance(step, list):
                current.extend(step)
        for step in differing_args[1]:
            if isinstance(step, list):
                wanted.extend(step)
        report["differing_args"] = {"recorded": current, "expected": wanted}
    return report


def _field_equal(flat_actual, flat_expected, field):
    same = flat_actual[field] == flat_expected[field]
    return same


def compare_records(
    recorded: str | bytes | dict[str, Any],
    expected: str | bytes | dict[str, Any],
) -> dict[str, Any]:
    """Compare a recorded provenance record with an expected one.

    Returns a JSON-ready dict::

        {"verdict": "clean" | "stale" | "unknown",
         "differing_fields": [...],     # named fields, canonical order
         "unknown_fields": [...],       # why the verdict is "unknown"
         "source_state": "dirty" | "pristine" | "unknown",
         "differing_args": {"recorded": [...], "expected": [...]},
         "scope": SCOPE}

    The recorded record is the candidate's claim; the expected record is the
    specification it must meet. The expected record must carry the ``schema``,
    ``pilot``, ``source_path`` and ``compiler_version`` keys (values may be
    blank when not yet known: a blank expected value is "not built yet", so a
    non-blank recorded value differs from it). The ``schema`` value must be
    valid on both sides.

    A field whose values differ but which is blank or malformed on either
    side makes the verdict ``UNKNOWN`` instead of ``STALE``; the differing
    fields are then listed in ``unknown_fields``. So an empty/non-hex object
    hash or a missing compiler version is "unknown" whenever it is the reason
    the records are not equal, and identical-but-blank records are "clean"
    only when every other identity field is complete.

    Flags compare as one verbatim, NUL-joined sequence: order and duplicates
    are kept exactly (a step ending in -O2 against an expected step ending in
    -O3 names only ``argv``); only the cosmetic grouping of a step boundary is
    not part of the comparison. Optional background fields are compared only
    when both sides carry them.
    """
    try:
        left = parse_record(recorded)
        expected_map = parse_record(expected)
    except ValueError as exc:
        return _out(UNKNOWN, [], ["record"], str(exc))

    actual_bad = record_problems(left)
    wanted_bad = record_problems(expected_map)
    for field in ANCHOR_FIELDS:
        if field not in expected_map:
            actual_bad[field] = f"{field} not in expected record"

    flat_actual = _flatten(left)
    flat_expected = _flatten(expected_map)
    unusable = []
    for field, _ in actual_bad.items():
        if field in COMPARED_FIELDS:
            unusable.append(field)
    for field, _ in wanted_bad.items():
        already = field in unusable
        if field in COMPARED_FIELDS and not already:
            required = field in EXTRA_FIELDS
            if not required:
                unusable.append(field)

    if unusable:
        return _out(UNKNOWN, [], unusable,
                    "identity field incomplete or malformed")

    differences = []
    for field in COMPARED_FIELDS:
        pending = 1
        if field in EXTRA_FIELDS:
            if not isinstance(left.get(field), bool):
                pending = 0
            if not isinstance(expected_map.get(field), bool):
                pending = 0
        if pending:
            if flat_actual[field] != flat_expected[field]:
                differences.append(field)

    if differences:
        return _out(STALE, differences, [], differing_args=(
            left.get("argv"), expected_map.get("argv")))
    return _out(CLEAN, [], [], source=left)


def main(args: list[str] | None = None) -> int:
    """Compare two JSON record files (read-only CLI).

    Exit 0 clean, 1 stale, 3 unknown, 2 usage. The tests never call this;
    they exercise only the pure in-memory functions.
    """
    import sys
    from pathlib import Path

    paths = list(sys.argv[1:] if args is None else args)
    if len(paths) != 2:
        print("usage: pilot_provenance.py RECORDED.json EXPECTED.json",
              file=sys.stderr)
        return 2
    comparison = compare_records(Path(paths[0]).read_bytes(),
                                 Path(paths[1]).read_bytes())
    print(json.dumps(comparison, indent=2, sort_keys=True))
    return {CLEAN: 0, STALE: 1, UNKNOWN: 3}[comparison["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
