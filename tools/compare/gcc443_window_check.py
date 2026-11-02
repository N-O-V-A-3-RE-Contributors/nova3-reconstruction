#!/usr/bin/env python3
"""Decide whether a GCC 4.4.3 build reproduces an original function window.

``nova3_team.object_compare`` compares raw bytes, so a function containing a
call always reports ``FUNCTION_BYTES_DIFFERENT``: a relocatable object's ``bl``
still carries its placeholder displacement while the linked original carries the
final one.  That hides real results -- a 56-byte constructor whose *only*
difference is the call target is a match in every part the compiler controls.

This tool reports three levels, most to least strict:

* ``MATCHED_RAW`` -- byte equality including call displacements;
* ``MATCHED_NORMALIZED_CALLS`` -- identical after blanking each ``BL``'s 24-bit
  displacement (the part the linker/relocation decides);
* ``DIFFERENT`` -- an instruction the compiler controls differs.

For every call site it also reports the relocatable placeholder target, the
linked target decoded from the original, and whether the ARM link formula
``link((S + A) | 1 - P) >> 2`` -- with S+A taken as the linked target -- would
reproduce the original word, i.e. whether the call is fully explained by a
relocation rather than by a code difference.

Only sizes, hashes, addresses and booleans are emitted (no instruction bytes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from elftools.elf import elffile  # noqa: E402

from nova3_team.object_compare import candidate_window  # noqa: E402


def original_window(reference: Path, address: int) -> bytes:
    with reference.open("rb") as handle:
        elf = elffile.ELFFile(handle)
        size = None
        symtab = elf.get_section_by_name(".symtab")
        for entry in symtab.iter_symbols():
            if entry["st_info"]["type"] == "STT_FUNC" and entry["st_value"] == address:
                size = entry["st_size"]
                break
        if not size:
            raise SystemExit(f"0x{address:x} has no exact .symtab bound")
        for section in elf.iter_sections():
            start = section["sh_addr"]
            if start <= address < start + section["sh_size"] and section["sh_flags"] & 0x4:
                return section.data()[address - start:address - start + size]
    raise SystemExit(f"0x{address:x} is not inside an executable section")


def call_sites(code: bytes, base: int) -> list[dict]:
    """Every ARM BL site (A32; condition field 0b1111 is not a branch)."""
    sites = []
    for offset in range(0, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if (word >> 28) == 0xF:
            continue
        if (word & 0x0F000000) == 0x0B000000:
            sites.append({"offset": offset, "word": word, "address": base + offset})
    return sites


def branch_target(site_address: int, word: int) -> int:
    disp = word & 0x00FFFFFF
    if disp & 0x00800000:
        disp -= 0x01000000
    return site_address + 8 + (disp << 2)


def analyse(reference: Path, obj: Path, address: int, symbol: str) -> dict:
    original = original_window(reference, address)
    candidate = candidate_window(obj.read_bytes(), symbol)["bytes"]
    same_len = len(original) == len(candidate)
    raw_equal = same_len and original == candidate
    normalized = False
    if same_len:
        normalized = all(
            ((struct.unpack_from("<I", original, o)[0] & 0xFF000000)
             if (struct.unpack_from("<I", original, o)[0] & 0x0E000000) == 0x0A000000
             else struct.unpack_from("<I", original, o)[0])
            == ((struct.unpack_from("<I", candidate, o)[0] & 0xFF000000)
                if (struct.unpack_from("<I", candidate, o)[0] & 0x0E000000) == 0x0A000000
                else struct.unpack_from("<I", candidate, o)[0])
            for o in range(0, len(original) - 3, 4)
        )
    calls = []
    for site in call_sites(candidate, address):
        linked = None
        formula_ok = None
        if same_len:
            original_word = struct.unpack_from("<I", original, site["offset"])[0]
            linked = branch_target(site["address"], original_word)
            shifted = (linked | 1) - (site["address"] + 8)
            reproduced = (original_word & 0xFF000000) | ((shifted >> 2) & 0x00FFFFFF)
            formula_ok = reproduced == original_word
        calls.append({
            "address": hex(site["address"]),
            "relocatable_placeholder_target": hex(branch_target(site["address"], site["word"])),
            "linked_target": None if linked is None else hex(linked),
            "arm_link_formula_reproduces_original_call": formula_ok,
            "caller_aligned_4": site["address"] % 4 == 0,
        })
    status = ("MATCHED_RAW" if raw_equal else
              "MATCHED_NORMALIZED_CALLS" if normalized else "DIFFERENT")
    return {
        "schema": "nova3.gcc443-window-check/1",
        "reference": str(reference),
        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "object": str(obj), "symbol": symbol, "address": address,
        "original_bytes": len(original), "candidate_bytes": len(candidate),
        "original_window_sha256": hashlib.sha256(original).hexdigest(),
        "candidate_window_sha256": hashlib.sha256(candidate).hexdigest(),
        "status": status,
        "call_sites": calls,
        "scope": ("Instruction window only. MATCHED_NORMALIZED_CALLS means every "
                  "instruction the compiler controls is identical; each call's "
                  "displacement is decided by the linker and reported with the "
                  "formula check. Not a whole-.so or whole-source proof."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--object", required=True, type=Path)
    parser.add_argument("--address", required=True, type=lambda s: int(s, 0))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    report = analyse(args.reference, args.object, args.address, args.symbol)
    text = json.dumps(report, indent=2)
    if args.out:
        target = args.out.resolve()
        if not target.is_relative_to(ROOT / ".cache"):
            print("refusing to write outside .cache", file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
