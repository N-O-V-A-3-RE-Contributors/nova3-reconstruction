#!/usr/bin/env python3
"""Re-measure the recovered glitch::collada CSoftwareSkinTechnique::prepareCache.

Reference: Premium ARMv7 engine ELF, `_ZN6glitch7collada6detail22CSoftwareSkin\
Technique12prepareCacheEv`, ELF VA 0x0073f0a4, 228 bytes, A32, STT_FUNC.

  STRUCTURE  decode the reference window and re-derive from the bytes + .symtab
             what the recovered source claims:
               * bit 0 of `*(this+0x08)` gates the body, and nothing is called or
                 stored before that test;
               * the five `bl` targets resolve to the five symbols the source
                 declares (base pointer-cache fill, CMatrix4 ctor, matrix-vector
                 resize, pointer-vector size, matrix*SMatrix operator);
               * the constructor is called with r1 = 2 and resize receives the
                 *same register* the constructor wrote, i.e. the fill value is
                 built in place and no 64-byte copy is emitted;
               * the loop stride is 0x40 and the 64-byte product is moved with
                 four ldm/stm pairs;
               * the tail clears bit 0 of the same flag word.

  WINDOW     compile reconstruction/recovered/libNOVA3_neon/
             collada_skin_technique.cpp with NDK r8e GCC 4.4.3 for -O2/-O3/-Os at
             -fvisibility=hidden (and -fvisibility=default as a negative control),
             then diff the prepareCache window with tools/compare/window_diff.py.
             The same object is also checked against the three 8-byte leaves,
             which must stay byte-equal: a layout change that broke them would be
             a regression, not a match.

prepareCache is NOT expected to be byte-equal: its window contains five branch
relocations and its stack frame differs from the reference.  The report carries
`source_matching_verified=False` and states the measured counts instead of
dressing them up.  No reference bytes or disassembly text are written.

Example:
    .venv/bin/python tools/compare/collada_skin_prepare_cache_pilot.py \
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

PREPARE_CACHE = ("_ZN6glitch7collada6detail22CSoftwareSkinTechnique"
                 "12prepareCacheEv", 0x0073F0A4, 228)

# The three leaves that must stay byte-equal after the class layout change.
TRIVIAL_LEAVES = [
    ("_ZNK6glitch7collada6detail22CSoftwareSkinTechnique17checkAvailability"
     "ERKNS_5video10STechniqueEj", 0x00710B50, 8),
    ("_ZNK6glitch7collada6detail22CSoftwareSkinTechnique16needOutputBufferEv",
     0x00710B48, 8),
    ("_ZN6glitch7collada6detail22CSoftwareSkinTechnique6attach"
     "EPNS_5scene10ISceneNodeE", 0x00710B58, 8),
]

# Expected `bl` targets, in program order, as the source declares them.
EXPECTED_CALLS = [
    "_ZN6glitch7collada6detail14ISkinTechnique15preparePtrCacheEv",
    "_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE",
    "_ZNSt6vectorIN6glitch4core8CMatrix4IfEENS1_17SAlignedAllocatorIS3_Lh4EEEE"
    "6resizeEjS3_",
    "_ZNKSt6vectorIPKN6glitch4core8CMatrix4IfEENS1_10SAllocatorIS5_LNS0_6memory"
    "13E_MEMORY_HINTE0EEEE4sizeEv",
    "_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_",
]

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/collada_skin_technique.cpp")
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


def symbol_bytes(obj: Path, wanted: set[str]) -> dict[str, tuple[bytes, int]]:
    out: dict[str, tuple[bytes, int]] = {}
    with open(obj, "rb") as fh:
        elf = ELFFile(fh)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            raise SystemExit(f"FAIL: {obj} has no .symtab")
        for sym in symtab.iter_symbols():
            if sym.name not in wanted or sym["st_info"]["type"] != "STT_FUNC":
                continue
            sec = elf.get_section(sym["st_shndx"])
            off = sym["st_value"] - sec["sh_addr"]
            out[sym.name] = (sec.data()[off:off + sym["st_size"]], sym["st_size"])
    return out


def load_reference(reference: Path):
    """Return (elf bytes, window bytes, {symbol: (va, size)})."""
    blob = reference.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if sha != REFERENCE_SHA256:
        raise SystemExit("FAIL: reference SHA-256 mismatch")
    bounds: dict[str, tuple[int, int]] = {}
    with open(reference, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        symtab = elf.get_section_by_name(".symtab")
        if text is None or symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        for sym in symtab.iter_symbols():
            if sym["st_value"] and sym["st_size"]:
                bounds.setdefault(sym.name, (sym["st_value"], sym["st_size"]))
        hits = [s for s in symtab.iter_symbols() if s.name == PREPARE_CACHE[0]]
        if len(hits) != 1:
            raise SystemExit("FAIL: prepareCache symbol is not unique")
        sym = hits[0]
        if sym["st_value"] != PREPARE_CACHE[1] or sym["st_size"] != PREPARE_CACHE[2]:
            raise SystemExit(
                f"FAIL: prepareCache bounds changed "
                f"(0x{sym['st_value']:08x}/{sym['st_size']})")
        if sym["st_info"]["type"] != "STT_FUNC" or sym["st_value"] & 1:
            raise SystemExit("FAIL: prepareCache is not an A32 STT_FUNC")
        base = text.header.sh_addr
        data = text.data()
        window = data[PREPARE_CACHE[1] - base:
                      PREPARE_CACHE[1] - base + PREPARE_CACHE[2]]
    return blob, window, bounds


def enclosing(bounds: dict[str, tuple[int, int]], va: int):
    best = None
    for name, (addr, size) in bounds.items():
        if size and addr <= va < addr + size:
            if best is None or size > best[1]:
                best = (name, size)
    return best[0] if best else None


def structure_report(window: bytes, bounds: dict[str, tuple[int, int]]) -> dict:
    base = PREPARE_CACHE[1]
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(window, base))
    facts: dict[str, object] = {"instructions_decoded": len(insns)}
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        facts[name] = bool(ok)
        if not ok:
            failures.append(name)

    def dst(insn):
        if insn.operands and insn.operands[0].type == ARM_OP_REG:
            return insn.reg_name(insn.operands[0].reg)
        return None

    def src_reg(insn, index=1):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_REG:
            return insn.reg_name(insn.operands[index].reg)
        return None

    def imm(insn, index=1):
        if len(insn.operands) > index and insn.operands[index].type == ARM_OP_IMM:
            return insn.operands[index].imm
        return None

    def defs_before(index, span=6):
        """dest register -> instruction, for the instructions just before a call."""
        out = {}
        for insn in insns[max(0, index - span):index]:
            register = dst(insn)
            if register is not None:
                out[register] = insn
        return out

    # 1. The guard comes first: a load pair through this+0x08, a bit-0 test and a
    #    conditional branch, with no call and no store before it.
    first_call = next((i for i, x in enumerate(insns) if x.mnemonic in ("bl", "blx")),
                      len(insns))
    guard = insns[:first_call]
    loaded = [x for x in guard
              if x.mnemonic == "ldr" and len(x.operands) == 2
              and x.operands[1].type == ARM_OP_MEM
              and x.reg_name(x.operands[1].mem.base) == "r0"
              and x.operands[1].mem.disp == 8]
    reloaded = [x for x in guard if x.mnemonic == "ldr" and len(x.operands) == 2
                and x.operands[1].type == ARM_OP_MEM
                and x.reg_name(x.operands[1].mem.base) == dst(x)
                and x.operands[1].mem.disp == 0]
    check("guard_double_deref_this_plus_8", bool(loaded) and bool(reloaded))
    check("guard_tests_bit_0", any(
        x.mnemonic == "tst" and len(x.operands) == 2 and imm(x) == 1 for x in guard))
    check("guard_branch_is_conditional", any(
        x.mnemonic.startswith("b") and x.mnemonic not in ("b", "bl", "blx")
        for x in guard))
    check("no_call_or_store_before_guard", not any(
        x.mnemonic.startswith(("str", "stm", "bl")) for x in guard))

    # 2. Exactly the five expected call targets, in order.
    calls = []
    call_index = {}
    for i, insn in enumerate(insns):
        if insn.mnemonic in ("bl", "blx"):
            target = imm(insn, 0)
            name = enclosing(bounds, target) if target is not None else None
            calls.append(name)
            if name in EXPECTED_CALLS:
                call_index[name] = i
    facts["call_targets"] = calls
    check("five_calls_resolve_to_expected_symbols", calls == EXPECTED_CALLS)

    # 3. The fill matrix is constructed in place: the register the constructor
    #    writes through is the register handed to resize as its third argument,
    #    and no 64-byte copy sits between the two calls.
    ctor_i = call_index.get(EXPECTED_CALLS[1])
    resize_i = call_index.get(EXPECTED_CALLS[2])
    in_place = ctor_arg2 = None
    if ctor_i is not None and resize_i is not None:
        ctor_defs = defs_before(ctor_i)
        resize_defs = defs_before(resize_i)
        ctor_arg2 = imm(ctor_defs["r1"], 1) if "r1" in ctor_defs else None
        ctor_r0_src = src_reg(ctor_defs["r0"]) if "r0" in ctor_defs else None
        resize_r2_src = (src_reg(resize_defs["r2"])
                         if "r2" in resize_defs else None)
        in_place = (ctor_r0_src is not None and ctor_r0_src == resize_r2_src)
    check("constructor_argument_is_2", ctor_arg2 == 2)
    check("fill_value_constructed_in_place", bool(in_place))
    check("no_64_byte_copy_before_resize", not any(
        x.mnemonic.startswith(("ldm", "stm"))
        for i, x in enumerate(insns) if ctor_i is not None and resize_i is not None
        and ctor_i < i < resize_i))

    # 4. Loop shape: 0x40 stride and a 64-byte product move.
    check("loop_stride_0x40", any(
        (x.mnemonic == "lsl" and imm(x, 2) == 6)
        or (x.mnemonic == "add" and imm(x, 2) == 0x40)
        for x in insns))
    check("product_moved_as_16_words",
          sum(1 for x in insns if x.mnemonic.startswith("ldm")) >= 4 and
          sum(1 for x in insns if x.mnemonic.startswith("stm")) >= 4)

    # 5. Tail clears bit 0 of the same flag word.
    check("clears_bit_0_of_flag_word", any(
        x.mnemonic == "bic" and len(x.operands) == 3 and imm(x, 2) == 1
        for x in insns) and any(x.mnemonic == "str" for x in insns))

    facts["failures"] = failures
    facts["structure_agrees_with_source"] = not failures
    return facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--ndk", default=str(DEFAULT_NDK_R8E))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    ref = Path(args.reference)
    blob, window, bounds = load_reference(ref)
    compiler = gxx(Path(args.ndk))
    structure = structure_report(window, bounds)

    rows = []
    leaves_ok = True
    with tempfile.TemporaryDirectory(prefix="collada-cache-pilot-") as tmp:
        for cfg in CONFIGS:
            tag = "_".join(c.strip("-").replace("=", "") for c in cfg)
            obj = Path(tmp) / f"candidate_{tag}.o"
            cmd = [str(compiler), *BASE_FLAGS, *cfg, f"-I{INCLUDE}",
                   str(SOURCE), "-o", str(obj)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            row: dict = {"config": " ".join(cfg), "compile_rc": proc.returncode}
            if proc.returncode != 0:
                row["error"] = (proc.stderr.strip().splitlines() or ["failed"])[-1]
                leaves_ok = False
                rows.append(row)
                continue
            found = symbol_bytes(obj, {PREPARE_CACHE[0]} |
                                 {name for name, _, _ in TRIVIAL_LEAVES})
            diff = diff_window(blob, obj.read_bytes(), address=PREPARE_CACHE[1],
                               symbol=PREPARE_CACHE[0],
                               expected_reference_sha256=REFERENCE_SHA256)
            leaves = []
            for name, va, size in TRIVIAL_LEAVES:
                data, cand_size = found.get(name, (b"", 0))
                exact = cand_size == size and data == window_for(blob, va, size)
                leaves.append({"va": f"0x{va:08x}", "reference_size": size,
                               "candidate_size": cand_size,
                               "bytes_equal": exact})
                leaves_ok = leaves_ok and exact
            row.update({
                "status": diff["status"],
                "reference_size": diff["reference_size"],
                "candidate_size": diff["candidate_size"],
                "words_compared": diff["words_compared"],
                "counts": diff["counts"],
                "code_window_equal": diff["code_window_equal"],
                "instruction_stream_equal": diff["instruction_stream_equal"],
                "trivial_leaves": leaves,
            })
            rows.append(row)

    report = {
        "tool": "collada_skin_prepare_cache_pilot",
        "reference": str(ref),
        "reference_sha256": REFERENCE_SHA256,
        "symbol": PREPARE_CACHE[0],
        "va": f"0x{PREPARE_CACHE[1]:08x}",
        "reference_size": PREPARE_CACHE[2],
        "source": str(SOURCE),
        "compiler": str(compiler),
        "flags": BASE_FLAGS,
        "configs": [" ".join(c) for c in CONFIGS],
        "structure": structure,
        "rows": rows,
        "trivial_leaves_still_byte_equal": leaves_ok,
        "prepare_cache_byte_equal": any(r.get("code_window_equal") for r in rows),
        "scope": ("one 228-byte window plus a regression check on three 8-byte "
                  "leaves; relocation-masked agreement is not a byte match, not "
                  "compiler identification and not a whole-.so SHA-256 match"),
        "source_matching_verified": False,
    }

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0 if (structure["structure_agrees_with_source"] and leaves_ok) else 1


def window_for(blob: bytes, va: int, size: int) -> bytes:
    import io
    elf = ELFFile(io.BytesIO(blob))
    text = elf.get_section_by_name(".text")
    base = text.header.sh_addr
    return text.data()[va - base:va - base + size]


if __name__ == "__main__":
    sys.exit(main())
