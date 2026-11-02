#!/usr/bin/env python3
"""Explain *why* a GCC 4.4.3 build differs from the original function.

``gcc443_sweep.py`` answers "does any flag set match".  When none does, the
useful next question is "*which* instruction classes differ", because that
names the mistaken source construct or compiler assumption instead of leaving
a size delta.

This tool compiles one source-owned translation unit with one explicit flag
set using the real NDK r8e GCC 4.4.3, disassembles the chosen function with
Capstone, disassembles the linked original window, and compares the two at the
level of *instruction classes* (opcode sequence, register-independent): which
classes the original uses that the candidate never emits and vice versa, and
where the aligned sequences first diverge.

Output contains only mnemonic counts and the first divergence offset -- no
original instruction bytes -- so the report is safe to keep in ``.cache`` and
to quote in progress notes.
"""

from __future__ import annotations

import argparse
import collections
import struct
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from elftools.elf.elffile import ELFFile  # noqa: E402
import capstone  # noqa: E402

from nova3_team.object_compare import candidate_window  # noqa: E402


def original_window(path: Path, address: int, size: int) -> bytes:
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        for section in elf.iter_sections():
            start = section["sh_addr"]
            if start <= address < start + section["sh_size"] and section["sh_flags"] & 0x4:
                offset = address - start
                return section.data()[offset:offset + size]
    raise SystemExit(f"0x{address:x} is not inside an executable section")


def disasm(code: bytes, address: int, thumb: bool):
    mode = capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM
    md = capstone.Cs(capstone.CS_ARCH_ARM, mode | capstone.CS_MODE_LITTLE_ENDIAN)
    return list(md.disasm(code, address))


# Capstone names some VFP encodings differently from GNU objdump for the same
# opcode (e.g. `fconsts s15,#112` is `vmov.f32 s15,#1.0`, word 0xeef77a00).
# Counting disassembler text therefore invents differences that do not exist;
# class signatures are built from ARM opcode fields instead.
# ARM A32 opcode classes from opcode fields.  Capstone and GNU objdump print
# different mnemonics for some VFP encodings (0xeef77a00 is `vmov.f32 s15,#1.0`
# to one and `fconsts s15,#112` to the other), so disassembler text cannot be
# compared; the fields below are the ARM ARM encodings.
def opcode_class(word: int) -> str:
    """ARM A32 instruction class from opcode fields, not disassembler text.

    Capstone and GNU objdump print different mnemonics for the same VFP
    encoding (``0xeef77a00`` is ``vmov.f32 s15,#1.0`` to one and
    ``fconsts s15,#112`` to the other), so only the encoded fields are
    comparable.  The test word's structure, which the unit test in
    ``tools/dsh_loop/tests/test_opcode_class.py`` pins:

        bits 27-24 = 1110  arithmetic (0=MLA, 2=MUL, 3=ADD/SUB)
        bits 23-20 = source operand: 6 = float scalar from a core register,
                     0/1/4/5 = float scalar from VFP, A/B/C/D = vector
        bit 6 of the encoding, i.e. 0x4 of the low nibble: 0 = MUL/ADD/MLA,
                     1 = MLA/SUB variant
    """
    if ((word >> 28) & 0xF) == 0xF:
        return "uncond"
    if (word & 0x0F000000) == 0x0B000000:
        return "bl"
    if (word & 0x0F000000) == 0x0A000000:
        return "b"
    if (word & 0x0E000000) in (0x0C000000, 0x0D000000, 0x0E000000):
        if (word & 0x0F300F00) == 0x0D100A00:
            return "vldr"
        if (word & 0x0F300F00) == 0x0D000A00:
            return "vstr"
        if (word & 0x0F800F00) == 0x0E800A00:
            return "vmov_imm"
        if (word & 0x0F000000) == 0x0D000000:
            return "vfp_mem_other"
        top = (word >> 20) & 0xF
        if top < 0x4 or top == 0x6:
            # Operand form, read off the annotated words:
            #   top = 6 (VFP-scalar from a core register, 0xee042aa3 / 0xee676a26)
            #       -> bit 0 (0x1) is set for the accumulating variant,
            #   top = 2/3 (fp register/constant forms, 0xee202a82 / 0xee311a80)
            #       -> bit 7 (0x80) is set for the accumulating variant.
            variant = bool(word & 0x1) if top == 0x6 else bool(word & 0x80)
            if top == 0x3:
                # ADD/SUB: 0xee310a80 (ADD-family) / 0xee210a80 (MUL-family).
                return "vadd" if word & 0x100000 else "vsub"
            return "vmla" if variant else "vmul"
        return "vfp_other"
    return "arm32"


def class_counts(words: list[int]) -> collections.Counter:
    return collections.Counter(opcode_class(word) for word in words)


def first_divergence(original, candidate) -> tuple[int, str, str] | None:
    for index, (a, b) in enumerate(zip(original, candidate)):
        if a.mnemonic.split(".")[0] != b.mnemonic.split(".")[0]:
            return index, a.mnemonic, b.mnemonic
    if len(original) != len(candidate):
        return min(len(original), len(candidate)), "(end)", "(end)"
    return None


def compile_one(gcc: Path, source: Path, flags: list[str], out: Path) -> tuple[bool, str]:
    cmd = [str(gcc), *flags, "-I", str(ROOT / "reconstruction/recovered/libNOVA3_neon"),
           "-c", str(source), "-o", str(out)]
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return cp.returncode == 0, (cp.stderr or cp.stdout)[-400:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gcc", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--address", required=True, type=lambda s: int(s, 0))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--flags", required=True, help="flag string, e.g. '-std=gnu++98 -marm -mfpu=neon'")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        obj = Path(tmp) / "candidate.o"
        ok, err = compile_one(args.gcc, args.source, args.flags.split(), obj)
        if not ok:
            print("compile failed: " + err, file=sys.stderr)
            return 2
        window = candidate_window(obj.read_bytes(), args.symbol)
        # The original's exact bound comes from its own symbol table; the
        # candidate is disassembled alone (it is a relocatable object).
        original_code = original_window(args.reference, args.address,
                                        local_size(args.reference, args.address, obj.read_bytes(), args.symbol))
        original = disasm(original_code, args.address, thumb=False)
    candidate_words = list(struct.unpack_from("<%dI" % (len(window["bytes"]) // 4), window["bytes"]))
    original_words = list(struct.unpack_from("<%dI" % (len(original_code) // 4), original_code))
    candidate = disasm(window["bytes"], 0, thumb=window["thumb"])
    original_classes = class_counts(original_words)
    candidate_classes = class_counts(candidate_words)
    divergence = first_divergence(original, candidate)
    report = {
        "schema": "nova3.gcc443-explain/1",
        "source": str(args.source.relative_to(ROOT)) if args.source.is_relative_to(ROOT) else str(args.source),
        "symbol": args.symbol,
        "address": args.address,
        "flags": args.flags,
        "original_instructions": len(original),
        "candidate_instructions": len(candidate),
        "original_classes": dict(original_classes.most_common()),
        "candidate_classes": dict(candidate_classes.most_common()),
        "classes_only_in_original": {k: v for k, v in original_classes.items() if k not in candidate_classes},
        "classes_only_in_candidate": {k: v for k, v in candidate_classes.items() if k not in original_classes},
        "count_deltas": {k: candidate_classes[k] - original_classes.get(k, 0)
                         for k in sorted(set(candidate_classes) | set(original_classes))
                         if candidate_classes[k] != original_classes.get(k, 0)},
        "first_divergence": None if divergence is None else {
            "index": divergence[0], "original": divergence[1], "candidate": divergence[2]},
    }
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


def local_size(reference: Path, address: int, obj: bytes, symbol: str) -> int:
    """Size of the original function from its .symtab (authoritative bound)."""
    with reference.open("rb") as handle:
        elf = ELFFile(handle)
        symtab = elf.get_section_by_name(".symtab")
        for entry in symtab.iter_symbols():
            if entry["st_info"]["type"] == "STT_FUNC" and entry["st_value"] == address:
                return entry["st_size"]
    raise SystemExit("address has no exact .symtab function bound")


if __name__ == "__main__":
    raise SystemExit(main())
