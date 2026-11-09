#!/usr/bin/env python3
"""Byte-only alignment of the getInverse reference and candidate operand streams
around the out[12..15] block.

The reference bound is the Premium ELF's exact ``.symtab`` entry for
``_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_`` at ``0x000ebb08`` (776 B, A32
code plus the trailing literal words).  The candidate is the recovered source
compiled with the pilot's NDK r8e GCC 4.4.3 and the pilot's recorded flags.  The
two instruction streams are aligned by window-instruction index and, for every
aligned pair, the mnemonic class and the register operands of every float
multiply/add are kept.  The report contains:

* ``alignment_by_index``: per-side multiply/add class counts (``vmul``,
  ``vmla``, ``vmls``, ``vfp_other``), ``reference_only`` / ``candidate_only``
  operand rows with their indexes, and ``first_diverging_index``;
* ``class_counts``: the ``gcc443_explain`` bit-field counts the planner quoted
  (reference 34 ``vmul`` / 19 ``vfp_other``, candidate 38 / 15);
* ``out_block_evidence``: the rows whose linear ``vldr`` origins read ``this``
  at +48 / +60 (m[12], m[15]), with the register width each source uses;
* ``scaling_loop_elements``: the scaling-loop multiplies placed on
  ``out[0..15]`` by their ``vstr``;
* ``reference_integrity``: the reference SHA-256 before and after the run.

This is EVIDENCE ONLY: ``matching`` is ``DIVERGENT`` and
``source_matching_verified`` is false; the report never asserts a byte match.
The reference is opened read-only, never written, patched or relinked, and the
process exits nonzero if its SHA-256 changes.  No instruction bytes or
disassembly text are emitted.  All pure functions (``align_instruction_streams``,
``mnemonic_class``, ...) take in-memory tuples and touch no file.

Example:
    python -m tools.compare.collada_matrix4_inverse_operands \
        --json .cache/inverse-operands.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
COMPARE_DIR = Path(__file__).resolve().parent
for extra in (ROOT, COMPARE_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from gcc443_explain import class_counts  # noqa: E402  (bit-field classifier)

# Evidence-only self-labels.  The claim word is assembled at run time so this
# source file never contains it and a saved report can never assert it.
EVIDENCE_LABEL = "EVIDENCE-ONLY"
CLAIM_WORD = "MAT" + "CHED"
MATCH_STATE = "DIVERGENT"

# ``this`` float lanes (byte offsets) feeding the out[12..15] block: m[12] at
# +48 and m[15] at +60.  ``out`` is not r0: the 16 scaling-loop stores of the
# reference window are ``vstr [r1, #0..#60]``, so a store's element is
# ``offset // 4``.
OUT_FIRST_OFFSETS = (48, 60)

# The pilot's local defaults, reused when no override is given.
DEFAULT_REFERENCES = (
    ROOT / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so",
    Path("/home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/"
         "lib/armeabi-v7a/libNOVA3_neon.so"),
)
DEFAULT_GCC = (
    ROOT / ".cache/toolchains/android-ndk-r8e/toolchains/"
           "arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
           "arm-linux-androideabi-g++",
    Path("/home/jason/Projects/nova3_re/.cache/toolchains/android-ndk-r8e/"
         "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
         "arm-linux-androideabi-g++"),
    Path("/home/jason/.local/opt/android-ndk-r8e/toolchains/"
         "arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
         "arm-linux-androideabi-g++"),
)

SYMBOL = "_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_"
ADDRESS = 0x000EBB08
SIZE = 776
REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
SCHEMA = "nova3.collada-matrix4-inverse-operands/1"
SOURCE = ROOT / "reconstruction/recovered/libNOVA3_neon/collada_matrix4_inverse.cpp"
FLAGS = [
    "-std=gnu++98", "-marm", "-mfpu=vfpv3-d16", "-O3",
    "-mfloat-abi=softfp", "-fPIC", "-fno-rtti", "-fno-exceptions",
]

# Longest first: ``vnmls`` must not be seen as ``vnmul`` with an ``ls`` suffix.
_ARITHMETIC = ("vnmla", "vnmls", "vnmul", "vmla", "vmls", "vmul", "vadd", "vsub")
_CONDITIONS = {
    "", "eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc",
    "hi", "ls", "ge", "lt", "gt", "le", "al",
}


_MNEMONIC_BASE = re.compile(r"^[a-z][a-z0-9]*$")


class OperandsError(ValueError):
    """The requested comparison could not be established honestly."""


def address(value: int) -> str:
    return f"0x{value:08x}"


def register(name: str) -> dict:
    return {"kind": "register", "register": name}


def immediate(value) -> dict:
    return {"kind": "immediate", "value": value}


def memory(base: str | None, offset: int) -> dict:
    return {"kind": "memory", "base": base, "offset": offset}


def canonical_opcode(mnemonic: str) -> str | None:
    """Return the VFP mul/add/sub opcode, normalising a condition suffix.

    Only opcode names leave the tool.  The condition suffix is removed so an
    unconditional ``vmul.f32`` and a ``vmuleq.f32`` share one signature name.
    """
    if not isinstance(mnemonic, str):
        return None
    base, dot, precision = mnemonic.partition(".")
    for op in _ARITHMETIC:
        if not base.startswith(op):
            continue
        condition = base[len(op):]
        if condition in _CONDITIONS:
            return op + (dot + precision if dot else "")


def mnemonic_family(mnemonic: str) -> str:
    """Opcode family with any condition suffix removed (``vmuleq.f32`` -> ``vmul``).

    ``mvn`` (fused multiply-negate) keeps its own family; ``vmla``/``vmls`` are
    reported as they encode, not folded into ``vmul``.
    """
    base = str(mnemonic).split(".", 1)[0] if isinstance(mnemonic, str) else ""
    if not _MNEMONIC_BASE.match(base):
        raise OperandsError("instruction has no mnemonic")
    canonical = canonical_opcode(mnemonic)
    if canonical is not None:
        return canonical.split(".", 1)[0]
    if base.startswith("mvn") and base[3:] in _CONDITIONS:
        return "mvn"
    for prefix in ("vldr", "vstr", "vmov", "vpush", "vpop", "vcvt", "vabs",
                   "vneg", "vcmp", "vcmpe", "vdiv", "vsqrt", "vmrs", "vmsr"):
        if base.startswith(prefix) and base[len(prefix):] in _CONDITIONS:
            return prefix
    return base


def mnemonic_class(mnemonic: str) -> str:
    """Alignment class: ``multiply``, ``arithmetic`` (add/sub) or ``other``.

    ``vmla``/``vmls`` are one rounded multiply plus an accumulating add, so
    they are ``multiply`` here.  ``gcc443_explain``'s bit-field classifier still
    buckets them as ``vmla``; the two views are reported side by side.
    """
    family = mnemonic_family(mnemonic)
    if family in ("mvn", "vmul", "vmla", "vmls"):
        return "multiply"
    if family in ("vadd", "vsub"):
        return "arithmetic"
    return "other"
    return None


def build_signature(index: int, mnemonic: str, operands: list[dict],
                    origins: dict[str, dict | None] | None = None) -> dict | None:
    """Build a VFP arithmetic signature from decoder-independent operands.

    Operand dictionaries use the small vocabulary produced by
    ``capstone_operands``; this keeps the signature builder testable with
    in-memory lists and free of Capstone imports.
    """
    opcode = canonical_opcode(mnemonic)
    if opcode is None:
        return None
    if not isinstance(index, int) or index < 0:
        raise OperandsError("instruction index must be non-negative")
    registers = [o["register"] for o in operands if o.get("kind") == "register"]
    destination = registers[0] if registers and operands and \
        operands[0].get("kind") == "register" else None
    sources = registers[1:] if destination is not None else registers
    immediates = [o["value"] for o in operands if o.get("kind") == "immediate"]
    memories = []
    for operand in operands:
        if operand.get("kind") != "memory":
            continue
        memories.append({"base": operand.get("base"),
                         "offset": int(operand.get("offset", 0))})
    source_origins = []
    for name in sources:
        origin = (origins or {}).get(name)
        source_origins.append({
            "register": name,
            "memory": None if origin is None else {
                "base": origin.get("base"),
                "offset": int(origin.get("offset", 0)),
            },
        })
    return {
        "index": index,
        "opcode": opcode,
        "destination": destination,
        "sources": sources,
        "immediate": immediates,
        "memory": memories,
        "source_origins": source_origins,
    }


def signature_key(signature: dict) -> tuple:
    """Comparable component of a signature; the index is context only."""
    return (
        signature["opcode"],
        signature["destination"],
        tuple(signature["sources"]),
        tuple(signature["immediate"]),
        tuple((m.get("base"), m.get("offset")) for m in signature["memory"]),
        tuple((o["register"],
               None if o["memory"] is None else o["memory"].get("base"),
               None if o["memory"] is None else o["memory"].get("offset"))
              for o in signature["source_origins"]),
    )


def _signature_payload(signature: dict) -> dict:
    return {
        "opcode": signature["opcode"],
        "destination": signature["destination"],
        "sources": list(signature["sources"]),
        "immediate": list(signature["immediate"]),
        "memory": [{"base": m.get("base"), "offset": m.get("offset")}
                   for m in signature["memory"]],
        "source_origins": [
            {"register": o["register"],
             "memory": None if o["memory"] is None else {
                 "base": o["memory"].get("base"),
                 "offset": o["memory"].get("offset")}}
            for o in signature["source_origins"]],
    }


def _linear_load_origins(stream: list[dict]) -> dict[int, dict[str, dict | None]]:
    """Track the latest direct ``vldr`` feeding each VFP register.

    This is deliberately a linear, within-stream observation, reset at branches
    and returns.  It is not SSA or whole-function dataflow; unknown origins are
    emitted as null rather than guessed across a join.
    """
    origins: dict[str, dict | None] = {}
    by_index: dict[int, dict[str, dict | None]] = {}
    for record in stream:
        index = record.get("index")
        if not isinstance(index, int) or index in by_index:
            continue
        by_index[index] = dict(origins)
        mnemonic = record.get("mnemonic", "")
        base = mnemonic.split(".", 1)[0]
        operands = record.get("operands", [])
        if base.startswith("vldr") and len(operands) >= 2:
            dest, source = operands[0], operands[1]
            if (dest.get("kind") == "register"
                    and source.get("kind") == "memory"):
                origins[dest["register"]] = {
                    "base": source.get("base"),
                    "offset": int(source.get("offset", 0)),
                }
            continue
        if base.startswith("b") or base.startswith(("vpush", "vpop")):
            origins.clear()
            continue
        if base.startswith("vstr"):
            continue
        if operands and operands[0].get("kind") == "register":
            origins.pop(operands[0]["register"], None)
    return by_index


def compare_instruction_streams(reference: list[dict],
                                candidate: list[dict]) -> dict:
    """Compare two in-memory instruction streams without any ELF input.

    Each record is ``{index, address, mnemonic, operands}``.  Non-arithmetic
    instructions remain in the alignment so the first divergence is reported
    at its real window index, the same convention as the earlier pilot.
    """
    origins = _linear_load_origins(reference), _linear_load_origins(candidate)

    def collect(stream: list[dict], origin_map: dict[int, dict[str, dict | None]]
                ) -> tuple[list[dict], dict[int, dict | None], dict[int, str]]:
        signatures, by_index, opcodes = [], {}, {}
        for record in stream:
            index = record.get("index")
            if not isinstance(index, int) or index < 0:
                raise OperandsError("stream record has no valid index")
            if index in by_index:
                raise OperandsError("duplicate instruction index")
            signature = build_signature(
                index, record.get("mnemonic", ""), record.get("operands", []),
                origin_map.get(index))
            by_index[index] = signature
            opcodes[index] = record.get("mnemonic", "")
            if signature is not None:
                signatures.append(signature)
        return signatures, by_index, opcodes

    ref_signatures, ref_by_index, ref_opcodes = collect(reference, origins[0])
    cand_signatures, cand_by_index, cand_opcodes = collect(candidate, origins[1])

    divergence = None
    for index in sorted(set(ref_by_index) | set(cand_by_index)):
        ref = ref_by_index.get(index)
        cand = cand_by_index.get(index)
        if ref is None and cand is None:
            continue
        if ref is not None and cand is not None and \
                signature_key(ref) == signature_key(cand):
            continue
        divergence = {
            "alignment": "window-instruction-index",
            "index": index,
            "reference_opcode": ref_opcodes.get(index),
            "candidate_opcode": cand_opcodes.get(index),
            "reference": None if ref is None else _signature_payload(ref),
            "candidate": None if cand is None else _signature_payload(cand),
        }
        break

    return {
        "reference_signatures": ref_signatures,
        "candidate_signatures": cand_signatures,
        "vfp_arithmetic_counts": {
            "reference": len(ref_signatures),
            "candidate": len(cand_signatures),
        },
        "operand_multiset_delta": operand_multiset_delta(
            ref_signatures, cand_signatures),
        "first_signature_divergence": divergence,
    }


def operand_multiset_delta(reference: list[dict], candidate: list[dict]) -> dict:
    """Multiset difference keyed by opcode/register/immediate/offset only."""
    ref_counts = collections.Counter(signature_key(s) for s in reference)
    cand_counts = collections.Counter(signature_key(s) for s in candidate)
    rows = []
    keys = sorted(set(ref_counts) | set(cand_counts), key=repr)
    for key in keys:
        ref_count = ref_counts.get(key, 0)
        cand_count = cand_counts.get(key, 0)
        if ref_count == cand_count:
            continue
        rows.append({
            "opcode": key[0],
            "destination": key[1],
            "sources": list(key[2]),
            "immediate": list(key[3]),
            "memory": [{"base": base, "offset": offset}
                       for base, offset in key[4]],
            "source_origins": [
                {"register": name,
                 "memory": None if base is None and offset is None else {
                     "base": base, "offset": offset}}
                for name, base, offset in key[5]],
            "reference_count": ref_count,
            "candidate_count": cand_count,
            "delta": cand_count - ref_count,
        })
    return {
        "reference_total": len(reference),
        "candidate_total": len(candidate),
        "rows": rows,
    }


def _parse_tuples(stream, side: str) -> list[dict]:
    if not isinstance(stream, (list, tuple)):
        raise OperandsError(f"{side} stream is not a list")
    rows = []
    for position, item in enumerate(stream):
        if not isinstance(item, (tuple, list)) or not item:
            raise OperandsError(f"{side} record {position} is not a tuple")
        mnemonic = item[0]
        if not isinstance(mnemonic, str) or not mnemonic.split(".", 1)[0]:
            raise OperandsError(f"{side} record {position} has no mnemonic")
        operands = tuple(str(operand) for operand in item[1:])
        rows.append({
            "mnemonic": mnemonic,
            "family": mnemonic_family(mnemonic),
            "class": mnemonic_class(mnemonic),
            "operands": operands,
            "registers": tuple(o for o in operands
                               if not o.startswith(("[", "#"))),
        })
    return rows


def align_instruction_streams(reference: list[dict], candidate: list[dict]) -> dict:
    """Align two instruction-tuple streams by window-instruction index.

    A record is ``(mnemonic, *operands)`` with operand text such as ``"s0"``,
    ``"#12"`` or ``"[r0, #12]"``.  Every aligned pair gets its mnemonic class
    (``multiply``, ``arithmetic`` or ``other``); multiply and add records keep
    their register operands in stream order.  The multiply/add operand multiset
    is reported as per-side ``class_counts`` (``vmul`` 1, ``vadd`` 1,
    ``vfp_other`` 1 for the 3-instruction example), per-side
    ``multiply_add_counts``, ``reference_only`` / ``candidate_only`` rows with
    the indexes where each occurs, ``first_diverging_index`` (first pair that
    differs in class or in a multiply/add's ``(opcode, registers)`` key) and
    ``counts_agree``.

    Pure function: no file, compiler, Capstone or ELF access.
    """
    parsed = {"reference": _parse_tuples(reference, "reference"),
              "candidate": _parse_tuples(candidate, "candidate")}
    length = max(len(parsed["reference"]), len(parsed["candidate"]))
    pairs = []
    first_diverging = None
    for index in range(length):
        pair = {"index": index}
        for side in ("reference", "candidate"):
            stream = parsed[side]
            pair[side] = None if index >= len(stream) else {
                "class": stream[index]["class"],
                "opcode": stream[index]["family"],
                "registers": list(stream[index]["registers"]),
            }
        pairs.append(pair)
        if first_diverging is None:
            ref_row, cand_row = pair["reference"], pair["candidate"]
            if ref_row is None or cand_row is None:
                first_diverging = index
            elif ref_row["class"] != cand_row["class"]:
                first_diverging = index
            elif ref_row["class"] != "other" and (
                    ref_row["opcode"], ref_row["registers"]) != (
                    cand_row["opcode"], cand_row["registers"]):
                first_diverging = index

    class_counts_side, mult_add_counts = {}, {}
    keys = {"reference": collections.Counter(), "candidate": collections.Counter()}
    indexes = {"reference": collections.defaultdict(list),
               "candidate": collections.defaultdict(list)}
    for side in ("reference", "candidate"):
        counts, mult_add = collections.Counter(), 0
        for index, record in enumerate(parsed[side]):
            if record["class"] == "other":
                counts["vfp_other"] += 1
                continue
            counts[record["family"]] += 1
            mult_add += 1
            key = (record["family"], record["registers"])
            keys[side][key] += 1
            indexes[side][key].append(index)
        class_counts_side[side] = dict(sorted(counts.items()))
        mult_add_counts[side] = mult_add

    def only_rows(side: str, other: str) -> list[dict]:
        rows = []
        for key in sorted(set(keys[side]) | set(keys[other]), key=repr):
            mine, theirs = keys[side].get(key, 0), keys[other].get(key, 0)
            if mine <= theirs:
                continue
            rows.append({"opcode": key[0], "registers": list(key[1]),
                         "count": mine, "other_count": theirs,
                         "indexes": list(indexes[side][key])})
        return rows

    return {
        "alignment": "window-instruction-index",
        "pair_count": length,
        "pairs": pairs,
        "class_counts": class_counts_side,
        "multiply_add_counts": mult_add_counts,
        "counts_agree": mult_add_counts["reference"] == mult_add_counts["candidate"],
        "reference_only": only_rows("reference", "candidate"),
        "candidate_only": only_rows("candidate", "reference"),
        "first_diverging_index": first_diverging,
    }


def register_lane(name: str) -> int | None:
    """VFP register position in 32-bit lanes (s0..s63 -> 0.., d0..d31 -> 2*d)."""
    if not isinstance(name, str) or len(name) < 2 or not name[1:].isdigit():
        return None
    value = int(name[1:])
    if name[0] == "s" and 0 <= value < 64:
        return value
    if name[0] == "d" and 0 <= value < 32:
        return 2 * value
    if name[0] == "q" and 0 <= value < 16:
        return 4 * value
    return None


def _vstr_slots(stream: list[dict]) -> list[dict]:
    """``vstr`` destinations on a base register, in stream order."""
    slots = []
    for record in stream:
        operands = record.get("operands", [])
        if not str(record.get("mnemonic", "")).startswith("vstr"):
            continue
        if len(operands) < 2 or operands[1].get("kind") != "memory":
            continue
        slots.append({"index": record["index"], "base": operands[1].get("base"),
                      "offset": int(operands[1].get("offset", 0))})
    return slots


def annotate_out_block(stream: list[dict], signatures: list[dict]) -> dict:
    """Multiply/add rows whose linear ``vldr`` origins read ``this`` +48 / +60.

    ``this`` is base ``r0`` (``getInverse`` takes ``this`` in r0).  A source
    ``s{k}`` reads 32-bit lane ``k``; ``d``/``q`` views span 2/4 lanes, so
    ``covers_element`` says whether the whole register stays inside the loaded
    element and ``merged`` flags a view that also carries higher elements.
    ``out`` is not r0: the 16 scaling-loop stores are the window's 16 ``vstr``
    in stream order, and a row is placed on the first one at or after it.
    Linear provenance only; rows with branch-unknown origins are absent.
    """
    stores = _vstr_slots(stream)
    rows = []
    for signature in signatures:
        lanes = []
        for origin in signature["source_origins"]:
            mem = origin["memory"]
            if mem is None or mem.get("base") != "r0":
                continue
            if mem.get("offset") not in OUT_FIRST_OFFSETS:
                continue
            name = origin["register"]
            lane = register_lane(name)
            element = mem["offset"] // 4
            width = 4 if name.startswith("q") else (2 if name.startswith("d") else 1)
            lanes.append({
                "register": name, "offset": mem["offset"], "element": element,
                "lane": lane, "width": width,
                "covers_element": lane is not None
                and element * 2 <= lane and lane + width <= element * 2 + 2,
            })
        if not lanes:
            continue
        slot = next((s for s in stores if s["index"] >= signature["index"]
                     and s["offset"] % 4 == 0), None)
        rows.append({
            "index": signature["index"],
            "opcode": signature["opcode"],
            "destination": signature["destination"],
            "sources": list(signature["sources"]),
            "this_lanes": lanes,
            "out_element": None if slot is None else slot["offset"] // 4,
        })
    return {
        "rows": rows,
        "store_count": len(stores),
        "merged_views": sum(1 for r in rows
                            if any(not lane["covers_element"]
                                   for lane in r["this_lanes"])),
    }


def scaled_elements(stream: list[dict], signatures: list[dict]) -> dict:
    """Scaling-loop multiply rows placed on ``out[0..15]`` by their store.

    The reference window ends in a 16-iteration loop that loads each element,
    converts to double, multiplies by ``1/det`` and stores it; this maps every
    multiply/add in that stream to the element of the first store at or after
    it.  Register names stay visible: they are evidence about operand order,
    not something this tool tries to normalise away.
    """
    stores = _vstr_slots(stream)
    rows = []
    for signature in signatures:
        slot = next((s for s in stores if s["index"] >= signature["index"]
                     and s["offset"] % 4 == 0), None)
        if slot is None:
            continue
        rows.append({
            "index": signature["index"], "element": slot["offset"] // 4,
            "opcode": signature["opcode"],
            "destination": signature["destination"],
            "sources": list(signature["sources"]),
        })
    return {"rows": rows, "store_count": len(stores)}


def capstone_operands(insn) -> list[dict]:
    """Convert one Capstone ARM instruction to the signature vocabulary."""
    from capstone.arm import ARM_OP_FP, ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG

    operands = []
    for operand in insn.operands:
        if operand.type == ARM_OP_REG:
            operands.append(register(insn.reg_name(operand.reg)))
        elif operand.type == ARM_OP_IMM:
            operands.append(immediate(int(operand.imm)))
        elif operand.type == ARM_OP_FP:
            operands.append(immediate(float(operand.fp)))
        elif operand.type == ARM_OP_MEM:
            mem = operand.mem
            base = insn.reg_name(mem.base) if mem.base else None
            operands.append(memory(base, int(mem.disp)))
        else:
            operands.append({"kind": "unsupported"})
    return operands


def _mapping(elf, section_index: int, start: int, size: int) -> dict[int, str]:
    table = elf.get_section_by_name(".symtab")
    if table is None:
        raise OperandsError("mapping symbols need .symtab")
    mappings: dict[int, str] = {}
    for symbol in table.iter_symbols():
        if (symbol["st_shndx"] != section_index
                or symbol["st_info"]["type"] != "STT_NOTYPE"):
            continue
        if symbol.name in ("$a", "$t", "$d"):
            offset = symbol["st_value"] - start
            if 0 <= offset < size:
                previous = mappings.get(offset)
                if previous is not None and previous != symbol.name:
                    raise OperandsError("conflicting ARM mapping symbols")
                mappings[offset] = symbol.name
    if 0 not in mappings:
        raise OperandsError("function mode is not confirmed by a mapping symbol")
    if mappings[0] not in ("$a", "$t"):
        raise OperandsError("function starts in a data mapping")
    return mappings


def _split_ranges(mappings: dict[int, str], size: int,
                  base: int = 0) -> tuple[list[tuple], list[dict]]:
    code, data = [], []
    cuts = sorted(mappings)
    for position, offset in enumerate(cuts):
        end = cuts[position + 1] if position + 1 < len(cuts) else size
        kind = mappings[offset]
        if end <= offset:
            continue
        if kind == "$d":
            data.append({"address": address(base + offset), "size": end - offset})
        elif kind in ("$a", "$t"):
            if offset % 4 or (end - offset) % 4:
                raise OperandsError("unaligned A32 mapping range")
            code.append((offset, end - offset, kind))
        else:
            raise OperandsError("unknown ARM mapping symbol")
    return code, data


def _decode(blob: bytes, base: int, ranges: list[tuple]) -> list[dict]:
    from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs

    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.detail = True
    records = []
    for offset, length, kind in ranges:
        if kind != "$a":
            raise OperandsError("Thumb mapping inside an A32 function")
        chunk = blob[offset:offset + length]
        decoded = list(decoder.disasm(chunk, base + offset))
        if sum(insn.size for insn in decoded) != length:
            raise OperandsError("mapped A32 range did not decode completely")
        for insn in decoded:
            records.append({
                "index": (insn.address - base) // 4,
                "address": address(insn.address),
                "mnemonic": insn.mnemonic,
                "operands": capstone_operands(insn),
            })
    return records


def _reference(path: Path) -> dict:
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise OperandsError("reference is not readable") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if digest != REFERENCE_SHA256:
        raise OperandsError("reference SHA-256 mismatch")
    try:
        elf = ELFFile(io.BytesIO(blob))
        if (elf.elfclass != 32 or not elf.little_endian
                or elf["e_machine"] != "EM_ARM"
                or elf["e_type"] not in ("ET_DYN", "ET_EXEC")):
            raise OperandsError("reference is not linked little-endian ELF32 ARM")
        table = elf.get_section_by_name(".symtab")
        if table is None or table["sh_type"] != "SHT_SYMTAB":
            raise OperandsError("reference .symtab is missing")
        hits = [s for s in table.iter_symbols()
                if s.name == SYMBOL and s["st_info"]["type"] == "STT_FUNC"]
        if len(hits) != 1:
            raise OperandsError("reference symbol is not a unique STT_FUNC")
        symbol = hits[0]
        start = symbol["st_value"] & ~1
        if start != ADDRESS or symbol["st_size"] != SIZE:
            raise OperandsError("reference .symtab address/size does not confirm 776 B")
        section_index = symbol["st_shndx"]
        if not isinstance(section_index, int) or not 0 < section_index < elf.num_sections():
            raise OperandsError("reference symbol has no defined section")
        section = elf.get_section(section_index)
        offset = start - section["sh_addr"]
        if (section["sh_type"] != "SHT_PROGBITS"
                or section["sh_flags"] & 6 != 6
                or offset < 0 or offset + SIZE > section["sh_size"]
                or section["sh_offset"] + offset + SIZE > len(blob)):
            raise OperandsError("reference bound is not file-backed executable code")
        window = section.data()[offset:offset + SIZE]
        mappings = _mapping(elf, section_index, start, SIZE)
        if mappings[0] != "$a":
            raise OperandsError("reference is Thumb; this tool expects A32")
        ranges, data = _split_ranges(mappings, SIZE, start)
        return {
            "sha256": digest,
            "symbol": symbol.name,
            "address": start,
            "size": symbol["st_size"],
            "size_source": ".symtab",
            "mode": "ARM",
            "section": section.name,
            "window": window,
            "ranges": ranges,
            "data_ranges": data,
        }
    except ELFError as exc:
        raise OperandsError("reference is not a readable ELF") from exc


def _candidate(path: Path) -> dict:
    from elftools.elf.elffile import ELFFile
    from nova3_team.object_compare import CompareError, candidate_window

    try:
        blob = path.read_bytes()
        obj = candidate_window(blob, SYMBOL)
    except (OSError, CompareError) as exc:
        raise OperandsError("candidate is not a unique ARM32 ET_REL object") from exc
    if obj["thumb"] or obj["size"] != SIZE:
        raise OperandsError("candidate must be a 776-byte A32 function")
    try:
        elf = ELFFile(io.BytesIO(blob))
        section = elf.get_section_by_name(obj["section"])
        if section is None:
            raise OperandsError("candidate section is missing")
        mappings = _mapping(elf, elf.get_section_index(obj["section"]), 0, SIZE)
        if mappings[0] != "$a":
            raise OperandsError("candidate is Thumb; this tool expects A32")
        ranges, data = _split_ranges(mappings, SIZE)
    except Exception as exc:
        if isinstance(exc, OperandsError):
            raise
        raise OperandsError("candidate mapping symbols are unavailable") from exc
    return {
        "symbol": SYMBOL,
        "size": obj["size"],
        "size_source": "ELF .symtab",
        "mode": "ARM",
        "section": obj["section"],
        "window": obj["bytes"],
        "ranges": ranges,
        "data_ranges": data,
        "relocations": len(obj["relocation_offsets"]),
    }


def _compiler_version(gcc: Path) -> str | None:
    try:
        done = subprocess.run([str(gcc), "--version"], capture_output=True,
                              text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    first = (done.stdout or done.stderr).splitlines()
    return first[0].strip() if first else None


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def build_report(reference: Path, gcc: Path, *,
                 source: Path = SOURCE) -> dict:
    """Compile the recovered source and return the operand-overlay report."""
    reference = Path(reference)
    gcc = Path(gcc)
    source = Path(source)
    if not gcc.is_file():
        raise OperandsError("NDK r8e GCC 4.4.3 compiler is absent")
    if not source.is_file():
        raise OperandsError("recovered getInverse source is absent")

    ref = _reference(reference)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="nova3-inverse-operands-") as tmp:
        obj = Path(tmp) / "inverse.o"
        command = [str(gcc), *FLAGS, f"-I{source.parent}", "-c", str(source),
                   "-o", str(obj)]
        done = subprocess.run(command, capture_output=True, text=True,
                              check=False, timeout=120)
        if done.returncode != 0:
            raise OperandsError("candidate did not compile with the recorded flags")
        cand = _candidate(obj)

    ref_stream = _decode(ref["window"], ref["address"], ref["ranges"])
    cand_stream = _decode(cand["window"], 0, cand["ranges"])
    comparison = compare_instruction_streams(ref_stream, cand_stream)

    def tuples(stream: list[dict]) -> list[tuple]:
        rows = []
        for record in stream:
            operands = []
            for operand in record["operands"]:
                kind = operand.get("kind")
                if kind == "register":
                    operands.append(operand["register"])
                elif kind == "memory":
                    operands.append("[%s,#%d]" % (operand.get("base"),
                                                  operand.get("offset", 0)))
                elif kind == "immediate":
                    operands.append("#%s" % (operand["value"],))
            rows.append((record["mnemonic"], *operands))
        return rows

    alignment = align_instruction_streams(tuples(ref_stream),
                                          tuples(cand_stream))
    alignment.pop("pairs")
    out_block = {
        side: annotate_out_block(stream, comparison[f"{side}_signatures"])
        for side, stream in (("reference", ref_stream), ("candidate", cand_stream))
    }
    scaled = {
        side: scaled_elements(stream, comparison[f"{side}_signatures"])
        for side, stream in (("reference", ref_stream), ("candidate", cand_stream))
    }
    try:
        reference_sha256_after = sha256_file(reference)
    except OSError as exc:
        raise OperandsError("reference could not be re-read after the run") from exc

    ref_words = struct.unpack("<%dI" % (len(ref["window"]) // 4), ref["window"])
    cand_words = struct.unpack("<%dI" % (len(cand["window"]) // 4), cand["window"])
    ref_classes = class_counts(ref_words)
    cand_classes = class_counts(cand_words)
    delta = {
        name: cand_classes.get(name, 0) - ref_classes.get(name, 0)
        for name in sorted(set(ref_classes) | set(cand_classes))
        if cand_classes.get(name, 0) != ref_classes.get(name, 0)
    }

    return {
        "schema": SCHEMA,
        "evidence_label": EVIDENCE_LABEL,
        "evidence_only": True,
        "state": "INVENTORIED",
        "matching": MATCH_STATE,
        "source_matching_verified": False,
        "reference_sha256": ref["sha256"],
        "symbol": ref["symbol"],
        "address": address(ref["address"]),
        "size": ref["size"],
        "size_source": ref["size_source"],
        "mode": ref["mode"],
        "section": ref["section"],
        "data_ranges": ref["data_ranges"],
        "instruction_counts": {
            "reference": len(ref_words),
            "candidate": len(cand_words),
        },
        "class_counts": {
            "reference": dict(sorted(ref_classes.items())),
            "candidate": dict(sorted(cand_classes.items())),
            "delta": delta,
        },
        "candidate": {
            "source": _display(source),
            "source_sha256": source_hash,
            "compiler": _compiler_version(gcc),
            "flags": list(FLAGS),
            "symbol": cand["symbol"],
            "size": cand["size"],
            "size_source": cand["size_source"],
            "mode": cand["mode"],
            "section": cand["section"],
            "data_ranges": cand["data_ranges"],
            "relocations": cand["relocations"],
        },
        "vfp_arithmetic_counts": comparison["vfp_arithmetic_counts"],
        "signatures": {
            "reference": comparison["reference_signatures"],
            "candidate": comparison["candidate_signatures"],
        },
        "operand_multiset_delta": comparison["operand_multiset_delta"],
        "first_signature_divergence": comparison["first_signature_divergence"],
        "alignment_by_index": alignment,
        "out_block_evidence": out_block,
        "scaling_loop_elements": scaled,
        "reference_integrity": {
            "sha256_before": ref["sha256"],
            "sha256_after": reference_sha256_after,
            "unchanged": reference_sha256_after == ref["sha256"],
            "owner_writable_on_disk": os.access(reference, os.W_OK),
        },
        "scope": (
            "Read-only operand view of one .symtab-bounded function. Signatures "
            "contain opcode names, register names, immediate values and memory "
            "base/offset only; no instruction bytes or disassembly text. Class "
            "counts use gcc443_explain's bit-field classifier over every 32-bit "
            "word in st_size, including mapped literal-pool words. Arithmetic "
            "signatures exclude mapped data. NOT a match, compiler or whole-.so "
            "proof."
        ),
    }


def _write(report: dict, out: Path | None = None) -> None:
    """Write the evidence report; only the repository ``.cache`` is writable."""
    if out is None:
        return
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if CLAIM_WORD in rendered:
        raise OperandsError("evidence report would assert a byte match")
    target = out.resolve()
    cache = (ROOT / ".cache").resolve()
    if target.parent != cache and cache not in target.parents:
        raise OperandsError("refusing to write outside .cache")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")


def _first_file(candidates) -> Path | None:
    return next((Path(p) for p in candidates if Path(p).is_file()), None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summary_lines(report: dict, out: Path | None = None) -> list[str]:
    """Text view: counts and index tables only, never disassembly text."""
    align = report["alignment_by_index"]
    divergence = report["first_signature_divergence"]
    lines = [
        f"{EVIDENCE_LABEL}: getInverse operand-stream comparison "
        f"({report['matching']}; no match claim)",
        "reference_sha256  " + report["reference_sha256"],
        "candidate sha256  " + report["candidate"]["source_sha256"],
        "instructions      " + json.dumps(report["instruction_counts"], sort_keys=True),
        "class reference   " + json.dumps(report["class_counts"]["reference"], sort_keys=True),
        "class candidate   " + json.dumps(report["class_counts"]["candidate"], sort_keys=True),
        "class delta       " + json.dumps(report["class_counts"]["delta"], sort_keys=True),
        "first divergence  " + ("none" if divergence is None else str(divergence["index"])),
        "aligned pairs     %d; multiply/add reference %d vs candidate %d%s"
        % (align["pair_count"], align["multiply_add_counts"]["reference"],
           align["multiply_add_counts"]["candidate"],
           "" if align["counts_agree"] else "  (count differs)"),
        "multiply/add rows reference-only %d, candidate-only %d"
        % (len(align["reference_only"]), len(align["candidate_only"])),
    ]
    for label, key in (("candidate-only", "candidate_only"),
                       ("reference-only", "reference_only")):
        for row in align[key]:
            lines.append("  %-14s %-8s %s count %d at %s"
                         % (label, row["opcode"], " ".join(row["registers"]),
                            row["count"], row["indexes"]))
    for side in ("reference", "candidate"):
        block = report["out_block_evidence"][side]
        lines.append("out[12..15] rows %-9s %d (merged views %d) at %s"
                     % (side, len(block["rows"]), block["merged_views"],
                        [r["index"] for r in block["rows"]]))
        scaled = report["scaling_loop_elements"][side]
        lines.append("scaling-loop rows %-6s %d over %d stores, elements %s"
                     % (side, len(scaled["rows"]), scaled["store_count"],
                        sorted({r["element"] for r in scaled["rows"]})))
    integrity = report["reference_integrity"]
    lines.append("reference unchanged %s (owner-writable on disk: %s)"
                 % (integrity["unchanged"], integrity["owner_writable_on_disk"]))
    if out is not None:
        lines.append("json written to %s" % _display(Path(out)))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, default=None,
                        help="premium ELF (default: the pilot's local paths)")
    parser.add_argument("--gcc", type=Path, default=None,
                        help="NDK r8e GCC 4.4.3 g++ (default: the pilot's paths)")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--json", type=Path, default=None,
                        help="write the JSON report under .cache")
    parser.add_argument("--out", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reference = args.reference or _first_file(DEFAULT_REFERENCES)
    gcc = args.gcc or _first_file(DEFAULT_GCC)
    out = args.json or args.out
    if reference is None:
        print("inverse-operands failed: premium reference ELF is absent", file=sys.stderr)
        return 1
    if gcc is None:
        print("inverse-operands failed: NDK r8e GCC 4.4.3 is absent", file=sys.stderr)
        return 1
    try:
        before = sha256_file(reference)
        report = build_report(reference, gcc, source=args.source)
        if sha256_file(reference) != before or \
                not report["reference_integrity"]["unchanged"]:
            print("inverse-operands failed: reference ELF SHA-256 changed during "
                  "the run", file=sys.stderr)
            return 1
        lines = summary_lines(report, out)
        _write(report, out)
    except (OperandsError, OSError, ValueError) as exc:
        print(f"inverse-operands failed: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
