#!/usr/bin/env python3
"""Group the getInverse VFP arithmetic stream into data-connected runs.

``tools/compare/collada_matrix4_inverse_operands.py`` reports signature deltas
but not whether the reference *groups* its arithmetic differently from the
candidate (fused multiply-accumulate versus a separate multiply then add, which
shows up as the reference/candidate ``vmul`` 34/38 and ``vfp_other`` 19/15
split).  This read-only module answers the grouping half of that question from
the operand records the operands pilot has already decoded.

A record uses the already-defined operand shape::

    {"index": int, "address": str, "mnemonic": str, "operands": [...]}

where ``operands`` is the vocabulary produced by ``capstone_operands``
(``{"kind": "register"|"immediate"|"memory", ...}``).  Only the mnemonic and
the *register* operands (first register = shared destination, remaining
registers = sources) are read, so plain-dict lists are enough to exercise it.

Definitions, deliberately simple and total:

* *arithmetic* records are VFP multiply/add/subtract mnemonics, matched with
  the operands pilot's ``canonical_opcode`` when that module is importable
  (one opcode table), else a kept-in-sync local copy;
* two consecutive arithmetic records are *data-connected* when the later one
  reads as a source a register the earlier one wrote as its destination (or
  rewrites that same register), and independent multiplies to distinct
  registers stay 1-instruction runs;
* a *run* is a maximal sequence of arithmetic records with every consecutive
  pair connected; ``run_length_histogram`` maps "<n> instruction(s)" -> count
  of runs, so independent multiplies give 1-instruction runs;
* a *multiply not followed by a dependent add/subtract* (MNFCONS) is a
  multiply record whose destination is not read as a source by the very next
  arithmetic record when that record is an add/subtract.

Any other record (loads, moves, conversions, stores, core instructions,
arithmetic records with a destination only, unknown opcodes, non-dicts) breaks
the current run and is counted under one ``ignored_reasons`` bucket, never
raised.  One linear pass over the stream is used: it is not SSA, not
control-flow aware, and no substitute for an instruction comparison.

No instruction bytes or disassembly text are emitted.  The module's top level
imports no pyelftools, capstone or compiler and never executes reference code
or edits recovered source.  It makes no match claim: ``matching`` is
``NOT MATCHED`` and ``source_matching_verified`` is false.

CLI (each side is a JSON list of operand records, for example the operands
report's ``signatures`` sections)::

    python tools/compare/collada_inverse_arith_runs.py \
        --reference reference-records.json --candidate candidate-records.json \
        --out .cache/gcc443-sweep/inverse-arith-runs.json
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPARE_DIR = Path(__file__).resolve().parent

SECTION = "arithmetic_runs"
SCHEMA = "nova3.collada-inverse-arith-runs/1"
SIDES = ("reference", "candidate")
PILOT = "collada_matrix4_inverse_operands.py"
PILOT_LOAD_ERROR = "operands pilot is not importable"

SECTION_KEYS = frozenset({
    "schema", "matching", "source_matching_verified", "reference",
    "candidate", "delta", "histogram_delta",
})
SIDE_KEYS = frozenset({
    "records", "arithmetic_records", "multiply_records", "accumulate_records",
    "add_subtract_records", "family_counts", "runs", "dependent_links",
    "run_length_histogram", "longest_run",
    "multiplies_not_followed_by_dependent_add_sub", "ignored_records",
    "ignored_reasons",
})
HISTOGRAM_KEYS = frozenset({"length", "reference", "candidate", "delta"})
DELTA_KEYS = frozenset({
    "runs", "dependent_links", "longest_run",
    "multiplies_not_followed_by_dependent_add_sub",
})
IGNORED_REASONS = (
    "not_a_record", "missing_mnemonic", "unsupported_mnemonic",
    "no_destination_register", "no_source_register",
)

# Copy of the operands pilot's canonical_opcode table, used only when that
# module cannot be imported.  Keep in sync when an opcode is added there.
_FALLBACK_ARITHMETIC = (
    "vnmla", "vnmls", "vnmul", "vmla", "vmls", "vmul", "vadd", "vsub")
_FALLBACK_CONDITIONS = frozenset({
    "", "eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc",
    "hi", "ls", "ge", "lt", "gt", "le", "al",
})
_FAMILY_KIND = {
    "vmul": "multiply", "vnmul": "multiply",
    "vmla": "accumulate", "vmls": "accumulate",
    "vnmla": "accumulate", "vnmls": "accumulate",
    "vadd": "add_subtract", "vsub": "add_subtract",
}


class ArithRunsError(ValueError):
    """The requested grouping could not be established honestly."""


def _load_pilot():
    """Import the operands pilot by file path; None if unavailable."""
    path = COMPARE_DIR / PILOT
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "_nova3_inverse_operands_pilot", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        for extra in (str(ROOT), str(COMPARE_DIR)):
            if extra not in sys.path:
                sys.path.insert(0, extra)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError):
        return None
    finally:
        sys.dont_write_bytecode = previous


_PILOT = _load_pilot()


def _fallback_opcode(mnemonic: str) -> str | None:
    base, dot, precision = mnemonic.partition(".")
    for op in _FALLBACK_ARITHMETIC:
        if base.startswith(op) and base[len(op):] in _FALLBACK_CONDITIONS:
            return op + (dot + precision if dot else "")
    return None


def canonical_opcode(mnemonic) -> str | None:
    """Canonical VFP mul/add/sub opcode, or None for every other record."""
    if not isinstance(mnemonic, str) or not mnemonic:
        return None
    fallback = _fallback_opcode(mnemonic)
    if _PILOT is None:
        return fallback
    pilot = _PILOT.canonical_opcode(mnemonic)
    assert pilot == fallback, "operands pilot opcode table drifted"
    return pilot


def _family(opcode: str | None) -> str | None:
    return None if opcode is None else opcode.split(".", 1)[0]


def is_multiply(opcode: str | None) -> bool:
    return _family(opcode) in ("vmul", "vnmul")


def is_accumulating_multiply(opcode: str | None) -> bool:
    return _family(opcode) in ("vmla", "vmls", "vnmla", "vnmls")


def is_add_subtract(opcode: str | None) -> bool:
    return _family(opcode) in ("vadd", "vsub")


def _registers(record: dict) -> list[str]:
    operands = record.get("operands")
    if not isinstance(operands, (list, tuple)):
        return []
    names = []
    for operand in operands:
        if isinstance(operand, dict) and operand.get("kind") == "register":
            name = operand.get("register")
            names.append(name if isinstance(name, str) else str(name))
    return names


def collect_arithmetic(records) -> tuple[list[dict], dict]:
    """Split a record stream into arithmetic steps and the ignored counts.

    Plain dict/list input is total: no record raises.  An uncounted reason
    (e.g. a non-list ``records``) raises ``ArithRunsError``.
    """
    if isinstance(records, (str, bytes, bytearray)) or not isinstance(
            records, (list, tuple)):
        raise ArithRunsError("records must be a list of operand records")
    steps: list[dict] = []
    ignored: collections.Counter = collections.Counter()
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            ignored["not_a_record"] += 1
            continue
        opcode = canonical_opcode(record.get("mnemonic"))
        registers = _registers(record)
        destination = registers[0] if registers else None
        sources = registers[1:]
        if not isinstance(record.get("mnemonic"), str) \
                or not record.get("mnemonic"):
            reason = "missing_mnemonic"
        elif opcode is None:
            reason = "unsupported_mnemonic"
        elif destination is None:
            reason = "no_destination_register"
        elif not sources:
            reason = "no_source_register"
        else:
            reason = None
        if reason is not None:
            ignored[reason] += 1
            continue
        index = record.get("index")
        steps.append({
            "position": position,
            "index": index if isinstance(index, int) else None,
            "opcode": opcode,
            "family": _family(opcode),
            "kind": _FAMILY_KIND[_family(opcode)],
            "destination": destination,
            "sources": sources,
        })
    return steps, {reason: ignored[reason] for reason in IGNORED_REASONS
                   if ignored[reason]}


def dependent(previous: dict, current: dict) -> bool:
    """True when ``current`` reads a register ``previous`` wrote.

    "Reads" covers the later record's own sources and the register it
    rewrites: ``vsub s11, s11, s10`` names s11 as its source, while a fused
    ``vadd s0, s0, s3`` or ``vmla s0, s5, s6`` reads its accumulator s0 even
    when s0 is not in its source list.  A forward-order-only link would call
    those independent.
    """
    return (previous["destination"] in current["sources"]
            or current["destination"] == previous["destination"])


def split_runs(steps: list[dict]) -> list[list[dict]]:
    """Maximal sequences where each step depends on the one before it."""
    runs: list[list[dict]] = []
    for step in steps:
        if runs and dependent(runs[-1][-1], step):
            runs[-1].append(step)
        else:
            runs.append([step])
    return runs


def dangling_multiplies(steps: list[dict]) -> int:
    """Multiply records that the next record does not fuse into an add/sub.

    A multiply is counted when the very next arithmetic record is neither an
    add/subtract nor data-connected to it: the multiply stands apart.  A
    dependent add/subtract (the separate multiply-then-add shape) is not
    counted, and neither is an accumulate that reads the same accumulator,
    because the product there *is* folded into the addition.
    """
    count = 0
    for position, step in enumerate(steps):
        if step["kind"] != "multiply":
            continue
        following = steps[position + 1] if position + 1 < len(steps) else None
        if following is None or not dependent(step, following):
            count += 1
    return count


def run_length_histogram(lengths: list[int]) -> dict[str, int]:
    counts = collections.Counter(lengths)
    return {f"{length} instruction{'' if length == 1 else 's'}"
            : counts[length] for length in sorted(counts)}


def summarize_side(records) -> dict:
    """Dependent-run summary for one side's operand-record sequence."""
    steps, ignored = collect_arithmetic(records)
    lengths = [len(run) for run in split_runs(steps)]
    families = collections.Counter(step["family"] for step in steps)
    kinds = collections.Counter(step["kind"] for step in steps)
    return {
        "records": len(records),
        "arithmetic_records": len(steps),
        "multiply_records": kinds["multiply"] + kinds["accumulate"],
        "accumulate_records": kinds["accumulate"],
        "add_subtract_records": kinds["add_subtract"],
        "family_counts": dict(sorted(families.items())),
        "runs": len(lengths),
        "dependent_links": sum(length - 1 for length in lengths),
        "run_length_histogram": run_length_histogram(lengths),
        "longest_run": max(lengths) if lengths else 0,
        "multiplies_not_followed_by_dependent_add_sub": dangling_multiplies(
            steps),
        "ignored_records": sum(ignored.values()),
        "ignored_reasons": ignored,
    }


def _delta(new: int, old: int) -> int:
    return int(new) - int(old)


def compare_histograms(reference, candidate) -> dict:
    """``{"reference": [...], "candidate": [...]}`` -> the JSON section."""
    if not isinstance(reference, (list, tuple)) or not isinstance(
            candidate, (list, tuple)):
        raise ArithRunsError("each side must be a list of operand records")
    sides = {"reference": summarize_side(reference),
             "candidate": summarize_side(candidate)}
    keys = sorted(set(sides["reference"]["run_length_histogram"])
                  | set(sides["candidate"]["run_length_histogram"]))
    histogram = []
    for key in keys:
        ref = sides["reference"]["run_length_histogram"].get(key, 0)
        cand = sides["candidate"]["run_length_histogram"].get(key, 0)
        histogram.append({"length": key, "reference": ref, "candidate": cand,
                          "delta": _delta(cand, ref)})
    return {
        "schema": SCHEMA,
        "matching": "NOT MATCHED",
        "source_matching_verified": False,
        "reference": sides["reference"],
        "candidate": sides["candidate"],
        "delta": {
            "runs": _delta(sides["candidate"]["runs"],
                           sides["reference"]["runs"]),
            "dependent_links": _delta(sides["candidate"]["dependent_links"],
                                      sides["reference"]["dependent_links"]),
            "longest_run": _delta(sides["candidate"]["longest_run"],
                                  sides["reference"]["longest_run"]),
            "multiplies_not_followed_by_dependent_add_sub": _delta(
                sides["candidate"][
                    "multiplies_not_followed_by_dependent_add_sub"],
                sides["reference"][
                    "multiplies_not_followed_by_dependent_add_sub"]),
        },
        "histogram_delta": {"run_length": histogram},
    }


def run(records_by_side: dict) -> dict:
    """Short, stable entry point used by the operands pilot.

    ``run({"reference": [...], "candidate": [...]})`` is pure: it reads only
    the given lists and returns a new dict.
    """
    if not isinstance(records_by_side, dict):
        raise ArithRunsError("expected a mapping with reference/candidate lists")
    missing = [side for side in SIDES if side not in records_by_side]
    if missing:
        raise ArithRunsError("missing side: " + ", ".join(missing))
    return compare_histograms(records_by_side["reference"],
                              records_by_side["candidate"])


def validate_section(section) -> dict:
    """Refuse anything that is not metadata under the exact section schema."""
    if not isinstance(section, dict) or set(section) != SECTION_KEYS:
        raise ArithRunsError("section keys do not match the schema")
    for side in SIDES:
        value = section[side]
        if not isinstance(value, dict) or set(value) != SIDE_KEYS:
            raise ArithRunsError(f"{side} keys do not match the schema")
        if not isinstance(value["family_counts"], dict) or not isinstance(
                value["run_length_histogram"], dict) or not isinstance(
                value["ignored_reasons"], dict):
            raise ArithRunsError(f"{side} mapping fields must be objects")
    if not isinstance(section["delta"], dict) or set(section["delta"]) != \
            DELTA_KEYS:
        raise ArithRunsError("delta keys do not match the schema")
    histogram = section["histogram_delta"]
    if not isinstance(histogram, dict) or set(histogram) != {"run_length"}:
        raise ArithRunsError("histogram_delta keys do not match the schema")
    if not isinstance(histogram["run_length"], list):
        raise ArithRunsError("histogram_delta.run_length must be a list")
    for row in histogram["run_length"]:
        if not isinstance(row, dict) or set(row) != HISTOGRAM_KEYS:
            raise ArithRunsError("histogram row keys do not match the schema")
    if section["matching"] != "NOT MATCHED" or \
            section["source_matching_verified"] is not False:
        raise ArithRunsError("the arithmetic-run section cannot claim a match")

    def visit(value, path: str) -> None:
        if value is None or isinstance(value, (bool, int, float, str)):
            return
        if isinstance(value, list):
            for child in value:
                visit(child, path)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ArithRunsError(f"non-string key under {path}")
                visit(child, path + "." + key)
            return
        raise ArithRunsError(f"non-metadata value under {path}")

    visit(section, SECTION)
    return section


def _write(section: dict, out: Path | None) -> None:
    if out is None:
        return
    target = out.resolve()
    cache = (ROOT / ".cache").resolve()
    if target != cache and cache not in target.parents:
        raise ArithRunsError("refusing to write outside .cache")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(section, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")


def _records(path: Path) -> list:
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, list):
        raise ArithRunsError(f"{path.name} must contain a JSON list")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, required=True,
                        help="reference operand-record JSON list")
    parser.add_argument("--candidate", type=Path, required=True,
                        help="candidate operand-record JSON list")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        section = validate_section(run({
            "reference": _records(args.reference),
            "candidate": _records(args.candidate),
        }))
        _write(section, args.out)
    except (ArithRunsError, OSError, ValueError) as exc:
        print(f"inverse-arith-runs failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(section, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
