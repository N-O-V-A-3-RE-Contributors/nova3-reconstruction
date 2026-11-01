#!/usr/bin/env python3
"""Relocation-aware word diff for one bounded ARM function window.

`object_compare` answers yes/no on byte equality between a linked original
function window and a freshly compiled ET_REL candidate. That is the right
verdict, but "FUNCTION_BYTES_DIFFERENT" hides whether the divergence is a
compiler instruction-selection difference or purely the link-time values a
relocatable object cannot contain.

This tool reports the difference per instruction word while never claiming that
a masked or partial agreement is a match:

  EQUAL                 identical word
  RELOCATION_FIELD      word differs only where the object carries a relocation
                        (branch immediate, literal-pool addend) and the
                        instruction encoding around that field is identical
  LITERAL_POOL          differing data word that a PC-relative load reads as a
                        literal; its linked value cannot exist in an object
  ENCODING_DIFFERENCE   word differs outside any relocation site and outside the
                        literal pool: the two compilers emitted different
                        instructions here

A relocation-masked agreement is NOT a byte match, not an original `.o`
recovery, and not compiler identification. Reports go under the private
repository `.cache` cache path; no original code bytes are printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nova3_team.object_compare import (  # noqa: E402
    CompareError,
    _read,
    _span,
    candidate_window,
    _digest,
)
from nova3_team.object_matching import MatchingError, select_functions  # noqa: E402

SCHEMA = "nova3.window-diff/1"
MAX_WORDS = 16384
# A32 B/BL: cond in [31:28], bits [27:25] = 101, imm24 in [23:0]. Only the
# immediate is relocation-driven; the rest of the encoding must agree.
A32_BRANCH_MASKS = ((0x0F000000, 0x0F000000), (0x00FFFFFF, 0x00000000))


def _word(data: bytes, offset: int, unit: int) -> int:
    return int.from_bytes(_span(data, offset, unit), "little")


def _masked_equal(left: int, right: int, unit: int) -> bool:
    """Compare an A32 branch word ignoring only its relocation-driven imm24."""
    if unit != 4:
        return False
    for mask, want in A32_BRANCH_MASKS:
        if (left & mask) != want or (right & mask) != want:
            return False
    return (left & 0x0F000000) == (right & 0x0F000000)


def _pc_relative_ldr_targets(window: bytes, start_address: int, unit: int) -> set[int]:
    """Word indices inside the window that a PC-relative LDR reads as data.

    A literal-pool word is data, not an instruction. Without this the word diff
    reports a link-assigned literal addend as an instruction-encoding
    divergence. Only the A32 immediate-form `LDR Rd, [pc, #imm]` is recognized;
    anything else is treated as code.
    """
    if unit != 4:
        return set()
    targets = set()
    for index in range(len(window) // 4):
        word = int.from_bytes(window[index * 4:(index + 1) * 4], "little")
        condition = word >> 28
        if condition == 0xF:
            continue
        if (word & 0x0E000000) != 0x04000000:  # bits 27:26 must be 01
            continue
        if word & 0x02000000:  # I flag set: register offset, not a literal
            continue
        if not word & 0x00100000:  # L flag: loads only
            continue
        if (word >> 16) & 0xF != 0xF:  # Rn must be pc
            continue
        immediate = word & 0xFFF
        address = start_address + index * 4 + 8
        target = address + immediate if word & 0x00800000 else address - immediate
        offset = target - start_address
        if offset % 4 == 0 and 0 <= offset < len(window):
            targets.add(offset // 4)
    return targets


def diff_window(reference: bytes, candidate: bytes, *, address: int,
                symbol: str, expected_reference_sha256: str | None = None) -> dict:
    if expected_reference_sha256 is not None:
        actual = _digest(reference)
        if actual != expected_reference_sha256:
            raise CompareError("reference SHA-256 mismatch")
    try:
        selected, _ = select_functions(reference, addresses={address}, limit=1)
    except MatchingError as exc:
        raise CompareError("reference function cannot be independently bounded") from exc
    if len(selected) != 1:
        raise CompareError("reference address has no unique bounded function")
    row = selected[0]
    obj = candidate_window(candidate, symbol)
    unit = 2 if row["thumb"] else 4
    if obj["thumb"] != row["thumb"]:
        return dict(schema=SCHEMA, status="ARM_THUMB_MODE_MISMATCH",
                    reference_address=row["address"], reference_size=row["size"],
                    candidate_size=obj["size"], candidate_symbol=symbol,
                    source_matching_verified=False,
                    evidence_scope="Mode differs; no word comparison attempted")
    if row["size"] % unit or obj["size"] % unit:
        raise CompareError("function extent is not a whole number of instruction words")
    if row["size"] > MAX_WORDS * unit or obj["size"] > MAX_WORDS * unit:
        raise CompareError("function window exceeds the bounded diff size")

    original = _span(reference, row["file_offset"], row["size"])
    compiled = obj["bytes"]
    sites = {off for off in obj["relocation_offsets"] if 0 <= off < len(compiled)}
    literals = (_pc_relative_ldr_targets(original, row["address"], unit)
                | _pc_relative_ldr_targets(compiled, row["address"], unit))
    counts = {"EQUAL": 0, "RELOCATION_FIELD": 0, "LITERAL_POOL": 0,
              "ENCODING_DIFFERENCE": 0, "UNCLASSIFIED_TAIL": 0}
    details = []
    total = min(len(original), len(compiled)) // unit
    for index in range(total):
        offset = index * unit
        left = _word(original, offset, unit)
        right = _word(compiled, offset, unit)
        if left == right:
            counts["EQUAL"] += 1
            continue
        relocated = any(offset <= site < offset + unit for site in sites)
        if relocated and _masked_equal(left, right, unit):
            # Branch word: only the relocation-driven imm24 differs.
            counts["RELOCATION_FIELD"] += 1
            kind = "RELOCATION_FIELD"
        elif relocated:
            # A relocation site with no comparable encoding field left, e.g. a
            # relocated literal-pool addend.
            counts["RELOCATION_FIELD"] += 1
            kind = "RELOCATION_FIELD"
        elif index in literals:
            # Data word in the pool: the linked value cannot exist in an object.
            counts["LITERAL_POOL"] += 1
            kind = "LITERAL_POOL"
        else:
            counts["ENCODING_DIFFERENCE"] += 1
            kind = "ENCODING_DIFFERENCE"
        details.append(dict(index=index, offset=offset, kind=kind,
                            reference_word_sha256=_digest(left.to_bytes(unit, "little")),
                            candidate_word_sha256=_digest(right.to_bytes(unit, "little"))))
    tail = max(len(original), len(compiled)) - total * unit
    if tail:
        counts["UNCLASSIFIED_TAIL"] = tail

    same_size = len(original) == len(compiled)
    if not same_size:
        status = "FUNCTION_SIZE_MISMATCH"
    elif counts["ENCODING_DIFFERENCE"] == 0:
        status = "ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION"
    else:
        status = "ENCODING_DIVERGENCE"
    return dict(
        schema=SCHEMA, kind="relocation-aware-window-diff", status=status,
        reference_sha256=_digest(reference), reference_address=row["address"],
        reference_size=row["size"], candidate_symbol=symbol,
        candidate_size=obj["size"], candidate_object_sha256=_digest(candidate),
        instruction_unit=unit, words_compared=total, counts=counts,
        candidate_relocation_count=len(obj["relocation_offsets"]),
        relocation_offsets_used=len(sites),
        word_details=details[:512],
        word_details_truncated=len(details) > 512,
        literal_pool_caveat=("A literal-pool word is detected from A32 "
                             "PC-relative LDR targets only; such a word is "
                             "data, so its linked addend cannot exist in an "
                             "object file."),
        instruction_stream_equal=(same_size and counts["ENCODING_DIFFERENCE"] == 0),
        code_window_equal=(status == "ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION"
                           and counts["RELOCATION_FIELD"] == 0
                           and counts["LITERAL_POOL"] == 0),
        source_matching_verified=False,
        original_compiler_verified=False,
        evidence_scope=("Per-word comparison of one linked window against an ET_REL "
                        "candidate. Relocation-masked agreement is neither a byte "
                        "match nor proof of the original compiler, flags or linker."))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--address", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--object", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--expected-reference-sha256")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        cache = (Path(__file__).resolve().parents[2] / ".cache").resolve()
        target = args.out.expanduser().resolve()
        if not target.is_relative_to(cache) or target == cache or target.suffix != ".json":
            raise CompareError("output must be JSON under repository .cache")
        report = diff_window(_read(args.reference), _read(args.object),
                            address=args.address, symbol=args.symbol,
                            expected_reference_sha256=args.expected_reference_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(report["status"] + "; NOT A BYTE MATCH BY ITSELF")
    except (OSError, CompareError, ValueError) as exc:
        print("window diff: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
