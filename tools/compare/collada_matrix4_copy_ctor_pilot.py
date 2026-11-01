#!/usr/bin/env python3
"""Re-measure the recovered glitch::core::CMatrix4<float>::CMatrix4(
CMatrix4<float> const&, CMatrix4<float>::eConstructor).

Reference: Premium ARMv7 engine ELF,
`_ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE`,
ELF VA 0x00108c8c, 240 bytes, A32, STT_FUNC (exact `.symtab` bounds).

  STRUCTURE  decode the reference window and re-derive from the bytes, the
             symbol tables, the `.plt`/`.rel.plt`/`.dynsym` chain and a
             whole-library call-site sweep what the recovered source claims,
             instead of trusting the source comment:
               * r0 = `this`, r1 = the source matrix, r2 = the enumerator, and
                 `this` is kept in a callee-saved register and returned in r0;
               * the dispatcher is `sub r2,r2,#1 ; cmp r2,#4 ;
                 addls pc,pc,r2,lsl #2` -- and ARM's PC reads as insn+8, so the
                 five table entries start at addls+8, NOT addls+4.  The
                 instruction right after `addls` is the default arm and must
                 branch to the epilogue (values 0 and >= 6 write nothing);
               * each table entry is an unconditional branch; the target block
                 is walked (following one unconditional branch) and its call
                 list is resolved by address:
                   value 1 -> no call, a 64-byte `ldm`/`stm` copy of the source
                   value 2 -> `memset(this,0,0x40)` + four diagonal `vstr`
                   value 3 -> `getTransposed(source, this)`
                   value 4 -> `getInverse(source, this)`, zero result -> memset
                   value 5 -> `getInverse(source, this)`, then the plain
                              `CMatrix4(eConstructor)` with r1 = 0 on a stack
                              local, `getTransposed(this, local)`, and the same
                              64-byte copy tail
               * the epilogue is `mov r0,r4 ; add sp,sp,#0x44 ; pop {r4,r5,pc}`;
               * every in-binary direct call of this constructor is enumerated
                 and the enumerator argument visible as `mov r2,#imm` is counted,
                 so the value/meaning reading is stated against measured call
                 sites rather than assumed.

  WINDOW     compile reconstruction/recovered/libNOVA3_neon/
             collada_matrix4_copy_ctor.cpp with NDK r8e GCC 4.4.3 at
             -O2/-O3/-Os with -fvisibility=hidden, -O2 default visibility as a
             control, and -O2 -fno-pic as a REJECTED hypothesis (it changes the
             dispatcher from `addls pc,pc,r2,lsl #2` to a `ldrls pc,[pc,...]`
             offset table, which the reference does not use), then diff the
             0x00108c8c window with tools/compare/window_diff.py and record how
             much of the residue is branch displacement (block order) versus
             non-branch instruction content.

The report carries `source_matching_verified=False`.  A relocation-masked or
block-permuted window agreement is not a byte match of the shipped library, not
compiler identification and not a whole-.so SHA-256 match.  No reference
instruction bytes or disassembly text are emitted -- addresses, offsets,
mnemonics and counts only.

Example:
    .venv/bin/python tools/compare/collada_matrix4_copy_ctor_pilot.py \\
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import bisect
import struct
import subprocess
import sys
import tempfile
from collections import Counter
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

TARGET = ("_ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE", 0x00108C8C, 240)

PLAIN_CTOR = "_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE"
GET_TRANSPOSED = "_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_"
GET_INVERSE = "_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_"

# Expected call sequence per enumerator value, resolved names.
EXPECTED_CALLS = {
    1: [],
    2: ["memset"],
    3: [GET_TRANSPOSED],
    4: [GET_INVERSE, "memset"],
    5: [GET_INVERSE, PLAIN_CTOR, GET_TRANSPOSED],
}
IDENTITY_OFFSETS = [0x3C, 0x00, 0x14, 0x28]

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/"
              "collada_matrix4_copy_ctor.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                      "/home/jason/.local/opt/android-ndk-r8e"))

BASE_FLAGS = [
    "-c", "-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16",
    "-fno-exceptions", "-fno-rtti",
]
CONFIGS = [
    ("-O2", "-fvisibility=hidden", "matrix"),
    ("-O3", "-fvisibility=hidden", "matrix"),
    ("-Os", "-fvisibility=hidden", "matrix"),
    ("-O2", "-fvisibility=default", "matrix"),
    ("-O2", "-fvisibility=hidden", "rejected-fno-pic"),
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
        self.plt_base = None
        self.plt_data = b""
        plt = self.elf.get_section_by_name(".plt")
        if plt is not None:
            self.plt_base = plt.header.sh_addr
            self.plt_data = plt.data()
        # `.symtab` has ~39k entries here; pyelftools' Symbol objects cost
        # ~0.4 ms each, so the table is unpacked directly once and indexed.
        self.symbols = self._load_symbols()
        self._by_name: dict[str, list[dict]] = {}
        self._by_addr: dict[int, str] = {}
        for s in self.symbols:
            self._by_name.setdefault(s["name"], []).append(s)
            if s["type"] == "STT_FUNC":
                self._by_addr.setdefault(s["addr"], s["name"])

    def _load_symbols(self) -> list[dict]:
        symtab = self.elf.get_section_by_name(".symtab")
        strtab = self.elf.get_section(symtab["sh_link"]).data()
        data = symtab.data()
        types = {0: "STT_NOTYPE", 1: "STT_OBJECT", 2: "STT_FUNC",
                 3: "STT_SECTION", 4: "STT_FILE", 6: "STT_TLS"}
        binds = {0: "STB_LOCAL", 1: "STB_GLOBAL", 2: "STB_WEAK"}
        out = []
        for off in range(0, len(data), 16):
            st_name, st_value, st_size, st_info, _other, _shndx = \
                struct.unpack_from("<IIIBBH", data, off)
            end = strtab.find(b"\0", st_name)
            name = strtab[st_name:end].decode("latin-1")
            out.append({"name": name, "addr": st_value, "size": st_size,
                        "type": types.get(st_info & 0xF, "STT_OTHER"),
                        "bind": binds.get(st_info >> 4, "STB_OTHER")})
        return out

    def window(self, va: int, size: int) -> bytes:
        off = va - self.text_base
        if off < 0 or off + size > len(self.text_data):
            raise SystemExit(f"FAIL: 0x{va:08x} is outside .text")
        return self.text_data[off:off + size]

    def symtab_function(self, name: str):
        hits = [s for s in self._by_name.get(name, [])
                if s["type"] == "STT_FUNC"]
        if len(hits) != 1:
            raise SystemExit(f"FAIL: {name} is not a unique STT_FUNC")
        return hits[0]

    def name_at(self, va: int) -> str | None:
        """Symbol starting exactly at `va`, else the PLT import name."""
        name = self._by_addr.get(va)
        return name if name is not None else self.plt_symbol(va)

    def plt_symbol(self, stub_va: int) -> str | None:
        """Resolve an ARM PLT stub address to its imported symbol name.

        This ELF's `.plt` has a 20-byte PLT0 header followed by 12-byte stubs
        (`add ip,pc,#12 ; add ip,ip,#off ; ldr pc,[ip,#off]`), verified by
        decoding rather than assumed: the candidate stub index is accepted only
        when all three instructions decode as that pair.
        """
        if not self.plt_base or not self.plt_data:
            return None
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        rel = self.elf.get_section_by_name(".rel.plt")
        dyn = self.elf.get_section_by_name(".dynsym")
        if rel is None or dyn is None:
            return None
        for index, r in enumerate(rel.iter_relocations()):
            stub = self.plt_base + 0x14 + 12 * index
            if stub != stub_va:
                continue
            off = stub - self.plt_base
            insns = list(md.disasm(self.plt_data[off:off + 12], stub))
            if (len(insns) != 3
                    or insns[0].mnemonic != "add"
                    or insns[1].mnemonic != "add"
                    or insns[2].mnemonic != "ldr"
                    or not insns[2].op_str.startswith("pc,")):
                return None
            return dyn.get_symbol(r["r_info_sym"]).name
        return None

    def call_sites(self, target_va: int) -> list[dict]:
        """Enumerate direct calls to a VA and classify the r2 argument.

        Scans `.text` word by word for the A32 BL/B encoding and resolves the
        displacement, so the whole library need not be disassembled for a sweep
        that will find a handful of hits; only a 32-byte span ending at each hit
        is decoded, plus the containing function is resolved from the symbol
        intervals (a call outside every bounded STT_FUNC is ignored).
        """
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        md.detail = True
        intervals = sorted((s["addr"], s["addr"] + s["size"], s["name"])
                           for s in self.symbols
                           if s["type"] == "STT_FUNC" and s["size"])
        starts = [i[0] for i in intervals]
        hits = []
        data, base = self.text_data, self.text_base
        for index in range(len(data) // 4):
            word = struct.unpack_from("<I", data, index * 4)[0]
            if word & 0x0F000000 not in (0x0A000000, 0x0B000000):
                continue
            va = base + index * 4
            displacement = word & 0x00FFFFFF
            if displacement & 0x800000:
                displacement -= 0x1000000
            if va + 8 + displacement * 4 != target_va:
                continue
            slot = bisect.bisect_right(starts, va) - 1
            if slot < 0 or not (intervals[slot][0] <= va < intervals[slot][1]):
                continue
            start = max(intervals[slot][0], va - 0x20)
            span = data[start - base:va - base + 4]
            insns = list(md.disasm(span, start))
            arg = None
            for prev in reversed(insns[:-1]):
                if prev.mnemonic != "mov" or len(prev.operands) != 2:
                    continue
                if (prev.operands[0].type == ARM_OP_REG
                        and prev.reg_name(prev.operands[0].reg) == "r2"
                        and prev.operands[1].type == ARM_OP_IMM):
                    arg = prev.operands[1].imm
                    break
            hits.append({"site": f"0x{va:08x}",
                         "caller": intervals[slot][2],
                         "caller_va": f"0x{intervals[slot][0]:08x}",
                         "r2": arg})
        return hits


def reg(insn, index):
    if len(insn.operands) > index and insn.operands[index].type == ARM_OP_REG:
        return insn.reg_name(insn.operands[index].reg)
    return None


def imm(insn, index=0):
    if len(insn.operands) > index and insn.operands[index].type == ARM_OP_IMM:
        return insn.operands[index].imm
    return None


def mem(insn, index):
    if len(insn.operands) > index and insn.operands[index].type == ARM_OP_MEM:
        return insn.operands[index].mem
    return None


class Window:
    """Instruction view of one function window with a linear block walker."""

    def __init__(self, insns: list, epilogue: int):
        self.insns = insns
        self.by_addr = {i.address: i for i in insns}
        self.epilogue = epilogue

    def walk(self, start: int) -> list:
        """Collect instructions from `start`, following one unconditional branch.

        Stops at the epilogue, at an address outside the window, or on a
        revisited address.  Conditional branches are not followed, so the
        fall-through path is what is collected (the reference's shared blocks all
        live on the fall-through side).
        """
        out, seen, cur = [], set(), start
        while cur in self.by_addr and cur != self.epilogue and cur not in seen:
            seen.add(cur)
            insn = self.by_addr[cur]
            out.append(insn)
            if (insn.mnemonic in ("b", "bx") and len(insn.operands) == 1
                    and insn.operands[0].type == ARM_OP_IMM):
                cur = imm(insn)
                continue
            cur += len(insn.bytes)
        return out


def structure_report(ref: Reference) -> dict:
    name, va, size = TARGET
    sym = ref.symtab_function(name)
    if sym["addr"] != va or sym["size"] != size:
        raise SystemExit(f"FAIL: bounds changed (0x{sym['addr']:08x}/"
                         f"{sym['size']})")
    if sym["addr"] & 1:
        raise SystemExit("FAIL: constructor symbol is Thumb, expected A32")

    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(ref.window(va, size), va))
    facts: dict[str, object] = {
        "instructions_decoded": len(insns),
        "bytes_decoded": sum(len(i.bytes) for i in insns),
    }
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        facts[label] = bool(ok)
        if not ok:
            failures.append(label)

    check("window_is_fully_decoded", facts["bytes_decoded"] == size)

    # --- prologue: arguments and the frame --------------------------------
    first = insns[0]
    check("starts_by_normalising_the_enumerator",
          first.mnemonic == "sub" and reg(first, 0) == "r2" and reg(first, 1) == "r2"
          and imm(first, 2) == 1)
    push = next((i for i in insns[:4] if i.mnemonic == "push"), None)
    check("callee_saved_registers_are_pushed", push is not None and "lr" in push.op_str)
    this_reg = None
    for i in insns[:6]:
        if i.mnemonic == "mov" and reg(i, 1) == "r0":
            this_reg = reg(i, 0)
            break
    facts["this_register"] = this_reg
    check("this_is_saved_from_r0", this_reg is not None)
    frame = next((i for i in insns[:6] if i.mnemonic == "sub" and reg(i, 0) == "sp"), None)
    facts["frame_size"] = imm(frame, 2) if frame else None
    check("frame_holds_one_64_byte_local", imm(frame, 2) == 0x44 if frame else False)

    # --- dispatcher -------------------------------------------------------
    cmp_insn = next((i for i in insns if i.mnemonic == "cmp" and reg(i, 0) == "r2"), None)
    facts["switch_compare"] = imm(cmp_insn, 1) if cmp_insn else None
    check("switch_covers_five_labels",
          cmp_insn is not None and imm(cmp_insn, 1) == 4)
    addls = next((i for i in insns
                  if i.mnemonic == "addls" and reg(i, 0) == "pc"), None)
    facts["dispatcher"] = addls.mnemonic if addls else None
    check("dispatcher_is_the_pc_relative_jump_table",
          addls is not None and reg(addls, 1) == "pc" and reg(addls, 2) == "r2")

    # ARM reads PC as instruction + 8, so the entries begin two words later.
    table_base = addls.address + 8
    default_arm = insns[insns.index(addls) + 1]
    facts["default_arm"] = default_arm.mnemonic
    check("instruction_after_the_table_is_the_default_branch",
          default_arm.mnemonic == "b" and imm(default_arm) is not None)

    epilogue = imm(default_arm) if default_arm.mnemonic == "b" else None
    facts["epilogue_va"] = f"0x{epilogue:08x}" if epilogue else None
    epi = [i for i in insns if epilogue is not None
           and epilogue <= i.address < epilogue + 12]
    check("epilogue_returns_this",
          len(epi) == 3 and epi[0].mnemonic == "mov" and reg(epi[0], 0) == "r0"
          and reg(epi[0], 1) == this_reg
          and epi[1].mnemonic == "add" and reg(epi[1], 0) == "sp"
          and epi[2].mnemonic == "pop" and "pc" in epi[2].op_str)
    check("default_arm_writes_nothing",
          epi and epi[0].address == epilogue)

    window = Window(insns, epilogue)

    entries = []
    for index in range(5):
        addr = table_base + 4 * index
        insn = window.by_addr.get(addr)
        entries.append({
            "value": index + 1,
            "table_word_va": f"0x{addr:08x}",
            "target_va": (f"0x{imm(insn):08x}"
                          if insn is not None and insn.mnemonic == "b"
                          and imm(insn) is not None else None),
        })
    facts["jump_table"] = entries
    check("jump_table_has_five_branch_entries",
          all(e["target_va"] for e in entries))

    # --- per-value behaviour ---------------------------------------------
    blocks: dict[int, list] = {}
    for entry in entries:
        if not entry["target_va"]:
            continue
        blocks[entry["value"]] = window.walk(int(entry["target_va"], 16))

    summary: dict[str, dict] = {}
    for value in range(1, 6):
        block = blocks.get(value, [])
        calls = [ref.name_at(imm(i)) for i in block
                 if i.mnemonic in ("bl", "blx") and imm(i) is not None]
        summary[str(value)] = {
            "block_va": f"0x{block[0].address:08x}" if block else None,
            "instructions": len(block),
            "calls": calls,
            "vstr_offsets": [f"0x{p:02x}" for p in
                             (mem(i, 1).disp for i in block
                              if i.mnemonic == "vstr" and mem(i, 1) is not None)],
            "ldm_count": sum(1 for i in block if i.mnemonic.startswith("ldm")),
            "stm_count": sum(1 for i in block if i.mnemonic.startswith("stm")),
        }
    facts["value_blocks"] = summary

    for value, expected in EXPECTED_CALLS.items():
        check(f"value_{value}_calls_are_expected",
              summary[str(value)]["calls"] == expected)
    check("value_2_writes_the_row_major_identity",
          sorted(int(o, 16) for o in summary["2"]["vstr_offsets"])
          == sorted(IDENTITY_OFFSETS))
    check("value_1_and_5_share_a_64_byte_copy_tail",
          summary["1"]["ldm_count"] == 4 and summary["1"]["stm_count"] == 4
          and summary["5"]["ldm_count"] == 4 and summary["5"]["stm_count"] == 4)
    check("values_1_and_5_copy_the_same_number_of_bytes",
          summary["1"]["ldm_count"] * 16 == 64
          and summary["5"]["ldm_count"] * 16 == 64)

    # The stack local is introduced by the plain constructor with r1 = 0.
    block5 = blocks.get(5, [])
    ctor_call = next((i for i in block5
                      if i.mnemonic in ("bl", "blx")
                      and ref.name_at(imm(i)) == PLAIN_CTOR), None)
    ctor_arg = None
    ctor_dest = None
    if ctor_call is not None:
        before = block5[:block5.index(ctor_call)]
        for i in reversed(before[-3:]):
            if i.mnemonic == "mov" and reg(i, 0) == "r1":
                ctor_arg = imm(i, 1)
            if i.mnemonic == "mov" and reg(i, 0) == "r0":
                ctor_dest = reg(i, 1)
    facts["stack_local_ctor_argument"] = ctor_arg
    facts["stack_local_ctor_destination"] = ctor_dest
    check("stack_local_is_constructed_with_value_0", ctor_arg == 0)
    check("stack_local_lives_in_the_frame", ctor_dest == "sp")

    # The memset blocks: zero fill of the whole 64-byte object.
    memset_args = []
    for value in (2, 4, 5):
        block = blocks.get(value, [])
        for i in block:
            if i.mnemonic in ("bl", "blx") and ref.name_at(imm(i)) == "memset":
                before = block[:block.index(i)]
                args = {}
                destination = None
                for prev in before[-4:]:
                    if prev.mnemonic != "mov":
                        continue
                    if reg(prev, 0) == "r0":
                        destination = reg(prev, 1)
                    elif reg(prev, 0) in ("r1", "r2"):
                        args[reg(prev, 0)] = imm(prev, 1)
                priors = [p for p in before if p.mnemonic in ("bl", "blx")]
                memset_args.append({
                    "value": value,
                    "r0": destination,
                    "r1": args.get("r1"),
                    "r2": args.get("r2"),
                    # value 4 reaches the memset on the zero-inverse path, so
                    # r1 comes from `subs r1,r0,#0` rather than from a `mov`.
                    "r1_zeroed_by_subs": any(
                        p.mnemonic == "subs" and reg(p, 0) == "r1"
                        and reg(p, 1) == "r0" for p in before),
                    "calls_before_this_one": len(priors),
                })
    facts["memset_calls"] = memset_args
    check("every_memset_zeroes_the_whole_object",
          bool(memset_args) and all(m["r2"] == 0x40 for m in memset_args))
    check("every_memset_fill_value_is_zero",
          bool(memset_args) and all(m["r1"] == 0 or m["r1_zeroed_by_subs"]
                                    for m in memset_args))
    check("every_memset_targets_this",
          bool(memset_args) and all(
              m["r0"] == this_reg
              or (m["r0"] is None and m["calls_before_this_one"] == 0)
              for m in memset_args))

    # Value 5's zero-result path must land in the same memset block as value 4.
    def zero_path_target(block):
        for i in block:
            if (i.mnemonic in ("beq", "bne", "cbz") and imm(i) is not None):
                return imm(i)
        return None

    t5 = zero_path_target(block5)
    v4_addrs = {i.address for i in blocks.get(4, [])}
    facts["value_5_zero_path_va"] = f"0x{t5:08x}" if t5 else None
    check("a_failed_inverse_zeroes_the_object",
          t5 is not None and t5 in v4_addrs)

    # getInverse is tested for a zero result with `subs r1,r0,#0`.
    block4 = blocks.get(4, [])
    subs = next((i for i in block4 if i.mnemonic == "subs" and reg(i, 0) == "r1"), None)
    check("inverse_result_is_tested_for_zero", subs is not None)
    check("getTransposed_clobbers_nothing_so_it_returns_void",
          all(i.mnemonic != "subs" for i in blocks.get(3, [])))

    # --- call-site distribution ------------------------------------------
    sites = ref.call_sites(va)
    distribution: dict[str, int] = {}
    for hit in sites:
        key = str(hit["r2"]) if hit["r2"] is not None else "unresolved"
        distribution[key] = distribution.get(key, 0) + 1
    facts["call_site_count"] = len(sites)
    facts["call_site_argument_distribution"] = dict(sorted(distribution.items()))
    facts["call_site_examples"] = sites[:4]
    check("constructor_is_called_from_more_than_one_place", len(sites) > 1)
    check("every_visible_call_site_passes_value_1_or_4",
          set(distribution) <= {"1", "4", "unresolved"})

    facts["failures"] = failures
    facts["structure_agrees_with_source"] = not failures
    return facts


def block_content(ref: "Reference", va: int, obj_bytes: bytes, symbol: str,
                  size: int) -> dict:
    """Compare the non-branch instruction content of the two windows.

    Branch words are excluded because their displacements encode block
    placement; relocation words are excluded because an ET_REL object cannot
    hold them.  What remains is the instruction multiset that a block-order
    difference does not change.
    """

    def words(data: bytes, base: int) -> Counter:
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        counts: Counter = Counter()
        for insn in md.disasm(data, base):
            if insn.mnemonic.startswith("b"):
                continue
            counts[bytes(insn.bytes)] += 1
        return counts

    ref_win = ref.window(va, size)
    cand_win = symbol_bytes_from_bytes(obj_bytes, symbol, size)
    rf, cf = words(ref_win, va), words(cand_win, 0)
    common = sum((rf & cf).values())
    return {
        "reference_non_branch_words": sum(rf.values()),
        "candidate_non_branch_words": sum(cf.values()),
        "multiset_common_words": common,
        "non_branch_multiset_equal": rf == cf,
    }


def symbol_bytes_from_bytes(obj_bytes: bytes, symbol: str, size: int) -> bytes:
    import io
    elf = ELFFile(io.BytesIO(obj_bytes))
    symtab = elf.get_section_by_name(".symtab")
    for sym in symtab.iter_symbols():
        if sym.name == symbol and sym["st_info"]["type"] == "STT_FUNC":
            sec = elf.get_section(sym["st_shndx"])
            off = sym["st_value"] - sec.header.sh_addr
            return sec.data()[off:off + sym["st_size"]]
    raise SystemExit(f"FAIL: {symbol} missing from the candidate object")


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
    with tempfile.TemporaryDirectory(prefix="collada-copy-ctor-pilot-") as tmp:
        for opt, vis, label in CONFIGS:
            tag = "_".join([opt.strip("-"), vis.split("=")[1], label])
            obj = Path(tmp) / f"candidate_{tag}.o"
            cfg = [opt, vis] + (["-fno-pic"] if label != "matrix" else [])
            cmd = [str(compiler), *BASE_FLAGS, *cfg, f"-I{INCLUDE}",
                   str(SOURCE), "-o", str(obj)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            row: dict = {"config": " ".join(cfg), "hypothesis": label,
                         "compile_rc": proc.returncode}
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
            row.update(block_content(ref, va, obj.read_bytes(), name, size))
            rows.append(row)

    matched = any(r.get("code_window_equal") for r in rows)
    report = {
        "tool": "collada_matrix4_copy_ctor_pilot",
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
        "scope": ("one 240-byte window; a relocation-masked or block-permuted "
                  "agreement is not a byte match of the shipped library, not "
                  "compiler identification and not a whole-.so SHA-256 match"),
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
