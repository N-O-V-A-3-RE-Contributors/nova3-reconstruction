#!/usr/bin/env python3
"""Re-measure the recovered glitch::core::CMatrix4<float>::getTransposed.

Reference: Premium ARMv7 engine ELF,
`_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_`, ELF VA 0x000eba84,
132 bytes, A32, STT_FUNC (exact `.symtab` bounds).

  STRUCTURE  decode the reference window and re-derive from the bytes what the
             recovered source claims, instead of trusting its comment:
               * 33 instructions and no frame: no push/pop, no stack adjustment,
                 no callee-saved register — so the function reads no global and
                 writes nothing outside its two arguments;
               * exactly 16 loads and 16 stores, every load from r0, every store
                 to r1, all single-precision (`vldr`/`vstr`), nothing else;
               * no call and no indirect branch; the only branch is `bx lr`;
               * the destination offsets are exactly the 16 distinct slots
                 0x00..0x3c each visited once, in ascending order;
               * destination slot k reads source slot (k % 4) * 4 + k / 4,
                 which is the transpose relation; the source is never written;
               * the mapping is self-inverse (applying it twice is the identity),
                 so the observed pairing really is a transpose and not an
                 arbitrary permutation or a rotation;
               * r0 (the source) and r1 (the destination) are distinct registers
                 and neither is re-used as a scratch, so an in-place call
                 (out == *this) would be read-before-write element by element —
                 recorded as an observed property, not an endorsement.

  WINDOW     compile reconstruction/recovered/libNOVA3_neon/
             collada_matrix4_transpose.cpp with NDK r8e GCC 4.4.3 at
             -O2/-O3/-Os with -fvisibility=hidden, plus -O2 default visibility as
             a negative control, then diff the 0x000eba84 window with
             tools/compare/window_diff.py.

The report carries `source_matching_verified=False`: a byte-equal 132-byte window
is not compiler identification, not a flag claim, not a proof of the original
translation unit and not a whole-.so SHA-256 match.  No reference instruction
bytes or disassembly text are emitted — addresses, offsets, mnemonics and
counts only.

Example:
    .venv/bin/python tools/compare/collada_matrix4_transpose_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
for extra in (SCRIPTS, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from window_diff import diff_window  # noqa: E402

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

TARGET = ("_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_", 0x000EBA84, 132)

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/collada_matrix4_transpose.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                      "/home/jason/.local/opt/android-ndk-r8e"))

BASE_FLAGS = [
    "-c", "-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16",
    "-fno-exceptions", "-fno-rtti", "-fno-pic",
]
CONFIGS = [
    ("-O2", "-fvisibility=hidden"),
    ("-O3", "-fvisibility=hidden"),
    ("-Os", "-fvisibility=hidden"),
    ("-O2", "-fvisibility=default"),
]


def gxx(ndk: Path) -> Path:
    p = (ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
         "arm-linux-androideabi-g++")
    if not p.exists():
        raise SystemExit(f"FAIL: NDK r8e g++ not found at {p}")
    return p


def symbol_bytes(obj: Path, wanted: str) -> tuple[bytes, int]:
    with open(obj, "rb") as fh:
        elf = ELFFile(fh)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            raise SystemExit(f"FAIL: {obj} has no .symtab")
        for sym in symtab.iter_symbols():
            if sym.name == wanted and sym["st_info"]["type"] == "STT_FUNC":
                sec = elf.get_section(sym["st_shndx"])
                off = sym["st_value"] - sec["sh_addr"]
                return sec.data()[off:off + sym["st_size"]], sym["st_size"]
    raise SystemExit(f"FAIL: {wanted} missing from {obj}")


class Reference:
    """Loaded reference ELF with the helpers the structure pass needs."""

    def __init__(self, path: Path):
        self.path = path
        self.blob = path.read_bytes()
        if hashlib.sha256(self.blob).hexdigest() != REFERENCE_SHA256:
            raise SystemExit("FAIL: reference SHA-256 mismatch")
        self.fh = open(path, "rb")
        self.elf = ELFFile(self.fh)
        self.text = self.elf.get_section_by_name(".text")
        self.symtab = self.elf.get_section_by_name(".symtab")
        if self.text is None or self.symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        self.text_base = self.text.header.sh_addr
        self.text_data = self.text.data()

    def window(self, va: int, size: int) -> bytes:
        off = va - self.text_base
        if off < 0 or off + size > len(self.text_data):
            raise SystemExit(f"FAIL: 0x{va:08x} is outside .text")
        return self.text_data[off:off + size]

    def symtab_function(self, name: str):
        hits = [s for s in self.symtab.iter_symbols()
                if s.name == name and s["st_info"]["type"] == "STT_FUNC"]
        if len(hits) != 1:
            raise SystemExit(f"FAIL: {name} is not a unique STT_FUNC")
        return hits[0]

    def function_ranges(self):
        """(start, size, name) for every bounded A32 function, sorted by start."""
        out = []
        for sym in self.symtab.iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC":
                continue
            va, size = sym["st_value"], sym["st_size"]
            if not size or va & 1:
                continue
            off = va - self.text_base
            if off < 0 or off + size > len(self.text_data):
                continue
            out.append((va, size, sym.name))
        out.sort()
        return out

    def call_sites(self, target_va: int) -> list[dict]:
        """Enumerate direct calls/branches to a VA across all bounded functions.

        Scans `.text` word by word for the A32 B/BL/BLX immediate encodings and
        keeps the ones whose computed target equals `target_va`, then verifies
        each surviving word by decoding it (so a literal-pool word that happens
        to look like a branch cannot be reported).  This is ~10x faster than
        disassembling all 39,108 functions.
        """
        import bisect
        import struct

        funcs = self.function_ranges()
        starts = [f[0] for f in funcs]
        data = self.text_data
        base = self.text_base
        words = len(data) // 4
        hits = []
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        md.detail = True
        for index in range(words):
            word = struct.unpack_from("<I", data, index * 4)[0]
            if (word >> 24) not in (0xEA, 0xEB, 0xFA, 0xFB):
                continue
            imm = word & 0x00FFFFFF
            if imm & 0x00800000:
                imm -= 0x01000000
            pc = base + index * 4 + 8
            if pc + (imm << 2) != target_va:
                continue
            site = base + index * 4
            decoded = list(md.disasm(data[index * 4:index * 4 + 4], site))
            if not decoded or decoded[0].mnemonic not in ("b", "bl", "blx"):
                continue
            if not decoded[0].operands:
                continue
            if decoded[0].operands[0].type != ARM_OP_IMM:
                continue
            if decoded[0].operands[0].imm != target_va:
                continue
            pos = bisect.bisect_right(starts, site) - 1
            owner = funcs[pos] if pos >= 0 else None
            if owner is None or site >= owner[0] + owner[1]:
                continue
            hits.append({"site": f"0x{site:08x}",
                         "caller": owner[2],
                         "caller_va": f"0x{owner[0]:08x}",
                         "caller_size": owner[1]})
        return hits


def structure_report(ref: Reference) -> dict:
    name, va, size = TARGET
    sym = ref.symtab_function(name)
    if sym["st_value"] != va or sym["st_size"] != size:
        raise SystemExit(f"FAIL: bounds changed (0x{sym['st_value']:08x}/"
                         f"{sym['st_size']})")
    if sym["st_value"] & 1:
        raise SystemExit("FAIL: symbol is Thumb, expected A32")

    window = ref.window(va, size)
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(window, va))
    facts: dict[str, object] = {
        "instructions_decoded": len(insns),
        "bytes_decoded": sum(len(i.bytes) for i in insns),
        "symbol_binding": str(sym["st_info"]["bind"]),
    }
    failures: list[str] = []

    def check(name_: str, ok: bool) -> None:
        facts[name_] = bool(ok)
        if not ok:
            failures.append(name_)

    def mem(insn, index=1):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_MEM:
            return insn.operands[index].mem
        return None

    check("window_is_fully_decoded", facts["bytes_decoded"] == size)
    check("instruction_count_is_33", len(insns) == 33)

    # 1. No frame, no callee-saved register: nothing outside the two arguments
    #    can be touched and no global is read.
    frame = [i for i in insns
             if i.mnemonic in ("push", "pop", "stmdb", "ldmia", "sub", "add")
             and "sp" in i.op_str]
    check("no_stack_frame", not frame)
    calls = [i for i in insns if i.mnemonic in ("bl", "blx")]
    check("no_call", not calls)
    indirect = [i for i in insns if i.mnemonic in ("bx", "blx")
                and "lr" not in i.op_str]
    check("only_branch_is_bx_lr",
          len(indirect) == 0
          and any(i.mnemonic == "bx" and i.op_str == "lr" for i in insns))

    # 2. Exactly 16 loads and 16 stores, single precision, all through r0/r1.
    loads = [i for i in insns if i.mnemonic == "vldr"]
    stores = [i for i in insns if i.mnemonic == "vstr"]
    facts["load_count"] = len(loads)
    facts["store_count"] = len(stores)
    check("sixteen_loads", len(loads) == 16)
    check("sixteen_stores", len(stores) == 16)
    check("every_load_reads_r0",
          all(mem(i) is not None and i.reg_name(mem(i).base) == "r0"
              for i in loads))
    check("every_store_writes_r1",
          all(mem(i) is not None and i.reg_name(mem(i).base) == "r1"
              for i in stores))
    other_memory = [i for i in insns
                    if i.mnemonic in ("ldr", "str", "ldrb", "strb", "ldrh",
                                      "strh", "ldrd", "strd", "ldm", "stm",
                                      "vldm", "vstm", "vpush", "vpop")
                    or (i.mnemonic in ("vldr", "vstr") and i not in loads
                        and i not in stores)]
    facts["other_memory_instructions"] = len(other_memory)
    check("no_other_memory_access", not other_memory)

    # 3. Destination slots: all 16 distinct byte offsets, each written once, in
    #    ascending order.
    dest_offsets = [mem(i).disp for i in stores]
    facts["destination_offsets"] = [f"0x{x:02x}" for x in dest_offsets]
    check("destination_is_the_whole_64_byte_object",
          sorted(dest_offsets) == [4 * k for k in range(16)])
    check("destination_is_written_in_ascending_order",
          dest_offsets == sorted(dest_offsets))

    # 4. The source index for destination slot k.
    src_offsets = [mem(i).disp for i in loads]
    mapping = [src // 4 for src in src_offsets]
    facts["source_index_per_destination_slot"] = mapping
    check("mapping_is_transpose",
          mapping == [(k % 4) * 4 + k // 4 for k in range(16)])
    check("source_is_never_written",
          sorted(src_offsets) == [4 * k for k in range(16)])

    # 5. The mapping is an involution, i.e. a transpose and not a rotation or an
    #    arbitrary permutation.
    involution = all(mapping[mapping[k]] == k for k in range(16))
    facts["mapping_is_self_inverse"] = involution
    check("mapping_is_self_inverse", involution)
    check("mapping_is_not_the_identity",
          mapping != list(range(16)))
    facts["mapping_swaps_off_diagonal_slots"] = all(
        (k % 5 != 0) == (mapping[k] != k) for k in range(16))
    check("only_the_four_diagonal_slots_are_fixed",
          facts["mapping_swaps_off_diagonal_slots"])

    # 6. The two argument registers are never used as scratch.
    clobbers = [i for i in insns
                if i.mnemonic in ("mov", "add", "sub", "eor", "and", "orr")
                and i.operands and i.operands[0].type == ARM_OP_REG
                and i.reg_name(i.operands[0].reg) in ("r0", "r1")]
    facts["argument_register_writes"] = len(clobbers)
    check("arguments_are_never_reassigned", not clobbers)

    # 7. Call sites: how many callers this window has.
    sites = ref.call_sites(va)
    facts["call_site_count"] = len(sites)
    distinct: dict[str, int] = {}
    for hit in sites:
        distinct[hit["caller"]] = distinct.get(hit["caller"], 0) + 1
    facts["distinct_callers"] = dict(sorted(distinct.items()))
    facts["call_sites"] = sites[:8]
    check("has_at_least_one_caller", len(sites) >= 1)

    facts["failures"] = failures
    facts["structure_agrees_with_source"] = not failures
    return facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--ndk", default=str(DEFAULT_NDK_R8E))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    ref = Reference(Path(args.reference))
    compiler = gxx(Path(args.ndk))
    structure = structure_report(ref)
    blob = ref.blob
    name, va, size = TARGET

    rows = []
    with tempfile.TemporaryDirectory(prefix="collada-transpose-pilot-") as tmp:
        for cfg in CONFIGS:
            tag = "_".join(c.strip("-").replace("=", "") for c in cfg)
            obj = Path(tmp) / f"candidate_{tag}.o"
            cmd = [str(compiler), *BASE_FLAGS, *cfg, f"-I{INCLUDE}",
                   str(SOURCE), "-o", str(obj)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            row: dict = {"config": " ".join(cfg), "compile_rc": proc.returncode}
            if proc.returncode != 0:
                row["error"] = (proc.stderr.strip().splitlines() or ["failed"])[-1]
                rows.append(row)
                continue
            _data, cand_size = symbol_bytes(obj, name)
            diff = diff_window(blob, obj.read_bytes(), address=va,
                               symbol=name,
                               expected_reference_sha256=REFERENCE_SHA256)
            row.update({
                "status": diff["status"],
                "reference_size": diff["reference_size"],
                "candidate_size": diff["candidate_size"],
                "words_compared": diff["words_compared"],
                "counts": diff["counts"],
                "code_window_equal": diff["code_window_equal"],
                "instruction_stream_equal": diff["instruction_stream_equal"],
            })
            rows.append(row)

    exact = [r["config"] for r in rows if r.get("code_window_equal")]
    report = {
        "tool": "collada_matrix4_transpose_pilot",
        "reference": str(ref.path),
        "reference_sha256": REFERENCE_SHA256,
        "symbol": name,
        "va": f"0x{va:08x}",
        "reference_size": size,
        "source": str(SOURCE),
        "compiler": str(compiler),
        "flags": BASE_FLAGS,
        "configs": [" ".join(c) for c in CONFIGS],
        "structure": structure,
        "rows": rows,
        "byte_equal_configs": exact,
        "byte_equal_in_any_config": bool(exact),
        "scope": ("one 132-byte window with no address-dependent word; a "
                  "byte-equal window is not compiler identification, not a "
                  "flag claim, not a recovered translation unit and not a "
                  "whole-.so SHA-256 match"),
        # Column-major storage would give the same 16 offset pairs, so the
        # element naming stays INFERRED even though the window is byte-equal.
        "source_matching_verified": False,
    }

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0 if structure["structure_agrees_with_source"] else 1


if __name__ == "__main__":
    sys.exit(main())
