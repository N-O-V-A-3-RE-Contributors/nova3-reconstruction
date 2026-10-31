#!/usr/bin/env python3
"""Re-measure the recovered glitch::core::CMatrix4<float>::CMatrix4(eConstructor).

Reference: Premium ARMv7 engine ELF,
`_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE`,
ELF VA 0x00108c54, 56 bytes, A32, STT_FUNC (exact `.symtab` bounds).

  STRUCTURE  decode the reference window and re-derive from the bytes, `.symtab`,
             `.rel.plt` and `.dynsym` what the recovered source claims, instead
             of trusting the source comment:
               * the enumerator argument in r1 is compared against 1 with an
                 UNSIGNED `bls` to the epilogue, so values 0 and 1 skip every
                 store and everything >= 2 runs the body;
               * `this` (r0) is kept in a callee-saved register and returned in
                 r0 at the end (C1 complete-object constructor);
               * the body's single call targets a `.plt` stub, which is resolved
                 through `.rel.plt` -> `.dynsym` and must be `memset`;
               * the memset arguments are literally 0 and 0x40, i.e. the whole
                 object;
               * after the call, exactly four stores of one `vmov.f32`-loaded
                 constant land at byte offsets 0x3c, 0x00, 0x14 and 0x28 -- the
                 diagonal of a row-major 4x4 -- and no other memory is written;
               * every in-binary call site of this constructor is enumerated and
                 the argument values that are visible as `mov r1,#imm` are
                 counted, so the "value 2 = identity" reading is stated against
                 the measured call-site distribution rather than assumed.

  WINDOW     compile reconstruction/recovered/libNOVA3_neon/
             collada_matrix4_ctor.cpp with NDK r8e GCC 4.4.3 at -O2/-O3/-Os with
             -fvisibility=hidden (and -O2 default visibility as a negative
             control), then diff the 0x00108c54 window with
             tools/compare/window_diff.py.

The report carries `source_matching_verified=False`.  A relocation-masked window
agreement is not a byte match of the shipped library, not compiler
identification, and not a whole-.so SHA-256 match.  No reference instruction
bytes or disassembly text are emitted -- addresses, offsets, mnemonics and
counts only.

Example:
    .venv/bin/python tools/compare/collada_matrix4_ctor_pilot.py \
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

TARGET = ("_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE", 0x00108C54, 56)

# The four diagonal slots of a row-major 4x4, in the reference's store order.
DIAGONAL_OFFSETS = [0x3C, 0x00, 0x14, 0x28]

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/collada_matrix4_ctor.cpp")
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
        self.bounds: dict[str, tuple[int, int]] = {}
        self.plt_base = None
        self.plt_data = b""
        plt = self.elf.get_section_by_name(".plt")
        if plt is not None:
            self.plt_base = plt.header.sh_addr
            self.plt_data = plt.data()

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

    def plt_symbol(self, stub_va: int) -> str | None:
        """Resolve an ARM PLT stub address to its imported symbol name.

        This ELF's `.plt` has a 20-byte PLT0 header followed by 12-byte stubs
        (`add ip,pc,#12 ; add ip,ip,#off ; ldr pc,[ip,#off]`), which is verified
        by decoding rather than assumed: the candidate stub index is accepted
        only when both of its instructions decode as that pair.
        """
        if not self.plt_base or not self.plt_data:
            return None
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        rel = self.elf.get_section_by_name(".rel.plt")
        dyn = self.elf.get_section_by_name(".dynsym")
        if rel is None or dyn is None:
            return None
        rels = list(rel.iter_relocations())
        for index, r in enumerate(rels):
            stub = self.plt_base + 0x14 + 12 * index
            if stub != stub_va:
                continue
            off = stub - self.plt_base
            insns = list(md.disasm(self.plt_data[off:off + 12], stub))
            # `add ip,pc,#12 ; add ip,ip,#<got> ; ldr pc,[ip,#<slot>]`
            if (len(insns) != 3
                    or insns[0].mnemonic != "add"
                    or insns[1].mnemonic != "add"
                    or insns[2].mnemonic != "ldr"
                    or not insns[2].op_str.startswith("pc,")):
                return None
            return dyn.get_symbol(r["r_info_sym"]).name
        return None

    def enclosing(self, va: int) -> str | None:
        best = None
        for sym in self.symtab.iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC":
                continue
            addr, size = sym["st_value"], sym["st_size"]
            if size and addr <= va < addr + size:
                if best is None or size > best[1]:
                    best = (sym.name, size, addr)
        return best[0] if best else None

    def call_sites(self, target_va: int) -> list[dict]:
        """Enumerate direct calls/branches to a VA and classify the r1 argument."""
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        md.detail = True
        hits = []
        for sym in self.symtab.iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC":
                continue
            va, size = sym["st_value"], sym["st_size"]
            if not size or va & 1:
                continue
            off = va - self.text_base
            if off < 0 or off + size > len(self.text_data):
                continue
            insns = list(md.disasm(self.text_data[off:off + size], va))
            for i, insn in enumerate(insns):
                if insn.mnemonic not in ("bl", "blx", "b"):
                    continue
                if not insn.operands or insn.operands[0].type != ARM_OP_IMM:
                    continue
                if insn.operands[0].imm != target_va:
                    continue
                arg = None
                for prev in reversed(insns[max(0, i - 6):i]):
                    if prev.mnemonic != "mov" or len(prev.operands) != 2:
                        continue
                    if (prev.operands[0].type == ARM_OP_REG
                            and prev.reg_name(prev.operands[0].reg) == "r1"
                            and prev.operands[1].type == ARM_OP_IMM):
                        arg = prev.operands[1].imm
                        break
                hits.append({"site": f"0x{insn.address:08x}",
                             "caller": sym.name,
                             "caller_va": f"0x{va:08x}",
                             "r1": arg})
        return hits


def structure_report(ref: Reference) -> dict:
    name, va, size = TARGET
    sym = ref.symtab_function(name)
    if sym["st_value"] != va or sym["st_size"] != size:
        raise SystemExit(f"FAIL: bounds changed (0x{sym['st_value']:08x}/"
                         f"{sym['st_size']})")
    if sym["st_value"] & 1:
        raise SystemExit("FAIL: constructor symbol is Thumb, expected A32")

    window = ref.window(va, size)
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(window, va))
    facts: dict[str, object] = {
        "instructions_decoded": len(insns),
        "bytes_decoded": sum(len(i.bytes) for i in insns),
    }
    failures: list[str] = []

    def check(name_: str, ok: bool) -> None:
        facts[name_] = bool(ok)
        if not ok:
            failures.append(name_)

    def reg(insn, index):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_REG:
            return insn.reg_name(insn.operands[index].reg)
        return None

    def imm(insn, index=1):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_IMM:
            return insn.operands[index].imm
        return None

    def mem(insn, index):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_MEM:
            return insn.operands[index].mem
        return None

    check("window_is_fully_decoded", facts["bytes_decoded"] == size)

    # 1. The predicate: `cmp r1,#1` then an unsigned conditional branch past
    #    every store, to the epilogue.
    first = insns[0] if insns else None
    facts["first_instruction"] = (first.mnemonic if first else None)
    check("predicate_compares_r1_with_1",
          first is not None and first.mnemonic == "cmp"
          and reg(first, 0) == "r1" and imm(first) == 1)

    branch = next((i for i in insns[1:4]
                   if i.mnemonic.startswith("b")
                   and i.mnemonic not in ("bl", "blx")), None)
    facts["guard_branch"] = (branch.mnemonic if branch else None)
    facts["guard_branch_target"] = (f"0x{imm(branch, 0):08x}"
                                    if branch and imm(branch, 0) is not None
                                    else None)
    check("guard_branch_is_unsigned_less_or_same", 
          branch is not None and branch.mnemonic == "bls")
    check("guard_branch_skips_to_the_epilogue",
          branch is not None and imm(branch, 0) is not None
          and imm(branch, 0) >= va + size - 8)

    # 2. `this` is kept and returned.
    saved = next((i for i in insns[:6] if i.mnemonic == "mov"
                  and reg(i, 0) and reg(i, 0).startswith("r")
                  and reg(i, 0) not in ("r0", "r1") and reg(i, 1) == "r0"), None)
    facts["this_register"] = reg(saved, 0) if saved else None
    check("this_is_saved_before_the_call", saved is not None)
    this_reg = reg(saved, 0) if saved else None
    returns = [i for i in insns if i.mnemonic == "mov" and reg(i, 0) == "r0"
               and reg(i, 1) == this_reg]
    check("this_is_returned_in_r0", len(returns) == 1)
    check("returns_through_pop_of_the_link_register",
          any(i.mnemonic == "pop" and "pc" in i.op_str for i in insns))

    # 3. The single call resolves through .rel.plt/.dynsym.
    calls = [i for i in insns if i.mnemonic in ("bl", "blx")]
    facts["call_count"] = len(calls)
    check("exactly_one_call", len(calls) == 1)
    callee_va = imm(calls[0], 0) if calls else None
    callee = ref.plt_symbol(callee_va) if callee_va is not None else None
    facts["call_target_va"] = (f"0x{callee_va:08x}"
                              if callee_va is not None else None)
    facts["call_target_symbol"] = callee
    check("call_is_memset", callee == "memset")

    # 4. The memset arguments: value 0, length 0x40.
    before = insns[:insns.index(calls[0])] if calls else []
    memset_args = {}
    for i in before[-4:]:
        if i.mnemonic == "mov" and reg(i, 0) in ("r1", "r2"):
            memset_args[reg(i, 0)] = imm(i)
    facts["memset_arguments"] = memset_args
    check("memset_value_is_0", memset_args.get("r1") == 0)
    check("memset_length_is_the_whole_object", memset_args.get("r2") == 0x40)

    # 5. The four constant stores, after the call, all from one VFP register.
    call_index = insns.index(calls[0]) if calls else 0
    const_reg = None
    for i in insns:
        if i.mnemonic.startswith("vmov") and "#1" in i.op_str:
            const_reg = i.op_str.split(",")[0].strip()
            break
    facts["constant_source_register"] = const_reg
    check("one_is_loaded_from_an_immediate", const_reg is not None)

    stores = [i for i in insns[call_index:] if i.mnemonic == "vstr"
              and mem(i, 1) is not None
              and this_reg and i.reg_name(mem(i, 1).base) == this_reg]
    offsets = [mem(i, 1).disp for i in stores]
    facts["constant_store_offsets"] = [f"0x{x:02x}" for x in offsets]
    check("four_constant_stores", len(stores) == 4)
    check("stores_are_the_row_major_diagonal",
          sorted(offsets) == sorted(DIAGONAL_OFFSETS))
    check("every_store_writes_the_same_constant",
          all(i.op_str.split(",")[0].strip() == const_reg for i in stores))

    # 6. No other memory access in the window.
    other_stores = [i for i in insns
                    if i.mnemonic in ("str", "strb", "strh", "vstr", "stm")
                    and i not in stores]
    facts["other_stores"] = len(other_stores)
    check("no_store_outside_the_four_diagonal_writes", not other_stores)

    # 7. Call-site distribution across the whole library.
    sites = ref.call_sites(va)
    distribution: dict[str, int] = {}
    for hit in sites:
        key = str(hit["r1"]) if hit["r1"] is not None else "unresolved"
        distribution[key] = distribution.get(key, 0) + 1
    facts["call_site_count"] = len(sites)
    facts["call_site_argument_distribution"] = dict(sorted(distribution.items()))
    facts["call_site_examples"] = sites[:6]
    check("constructor_is_called_from_more_than_one_place", len(sites) > 1)

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
    with tempfile.TemporaryDirectory(prefix="collada-ctor-pilot-") as tmp:
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

    matched = any(r.get("code_window_equal") for r in rows)
    report = {
        "tool": "collada_matrix4_ctor_pilot",
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
        "byte_equal_in_any_config": matched,
        "scope": ("one 56-byte window; a relocation-masked agreement is not a "
                  "byte match of the shipped library, not compiler "
                  "identification and not a whole-.so SHA-256 match"),
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
