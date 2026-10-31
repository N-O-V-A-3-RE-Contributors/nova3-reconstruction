#!/usr/bin/env python3
"""Re-measure the recovered DetectNeon() probe: structure and code window.

Two independent checks over the Premium ARMv7 engine ELF at ELF VA 0x009dad14
(`_ZN3vox29DriverCallbackSourceInterface10DetectNeonEv`, 32 bytes, A32,
STB_LOCAL):

  STRUCTURE  decode the reference window and re-derive the three facts the
             recovered source relies on, so the source comment is not the
             evidence: (a) the CPU-family comparison is against the literal 1,
             (b) the feature extract takes exactly one bit, bit 2, (c) the
             second call is skipped when the family test fails (the
             conditional return sits between the two calls).

  WINDOW     compile `reconstruction/recovered/libNOVA3_neon/
             driver_callback_detect_neon.cpp` with the historical NDK r8e GCC
             4.4.3 across an -O / stack-protector matrix and diff each ET_REL
             symbol window against the reference with
             `tools/compare/window_diff.py`.

Output is metadata only: addresses, sizes, counts, booleans and per-word
SHA-256 digests.  No reference instruction bytes or disassembly text are
written.  Every report carries `source_matching_verified=False` because a
relocation-masked agreement is not a byte match, not an original object
recovery, and not compiler identification.

Example:
    .venv/bin/python tools/compare/detect_neon_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
for extra in (SCRIPTS, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from window_diff import diff_window  # noqa: E402

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402
from capstone.arm import ARM_INS_BL, ARM_INS_BLX  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
DETECT_NEON = ("_ZN3vox29DriverCallbackSourceInterface10DetectNeonEv", 0x009DAD14, 32)
GETTER_FAMILY_VA = 0x000D2F6C
GETTER_FEATURES_VA = 0x000D2F3C
SOURCE = Path("reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                     "/home/jason/.local/opt/android-ndk-r8e"))


def load_reference(reference: Path) -> tuple[bytes, bytes]:
    """Return (whole ELF bytes, DetectNeon window bytes).

    `window_diff.diff_window` bounds the reference function itself, so it needs
    the complete ELF; the extracted window is used only for the structure pass.
    """
    blob = reference.read_bytes()
    if hashlib.sha256(blob).hexdigest() != REFERENCE_SHA256:
        raise SystemExit("FAIL: reference SHA-256 mismatch")
    with open(reference, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        symtab = elf.get_section_by_name(".symtab")
        if text is None or symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        base = text.header.sh_addr
        data = text.data()
        sym, = [s for s in symtab.iter_symbols() if s.name == DETECT_NEON[0]]
        if sym["st_value"] != DETECT_NEON[1] or sym["st_size"] != DETECT_NEON[2]:
            raise SystemExit("FAIL: DetectNeon symbol bounds changed")
        if sym["st_info"]["type"] != "STT_FUNC" or sym["st_value"] & 1:
            raise SystemExit("FAIL: DetectNeon is not an A32 STT_FUNC")
    return blob, data[DETECT_NEON[1] - base:DETECT_NEON[1] - base + DETECT_NEON[2]]


def structure(window: bytes) -> dict:
    """Re-derive the source's three behavioural claims from the reference."""
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(window, DETECT_NEON[1]))
    calls, cmp_imms, ubfx, r0_reads, shadowed = [], [], [], [], []
    for index, insn in enumerate(insns):
        if insn.id in (ARM_INS_BL, ARM_INS_BLX):
            calls.append(insn.operands[0].imm)
            shadowed.append(index)
        if insn.mnemonic.startswith("cmp") and insn.operands:
            if insn.reg_name(insn.operands[0].reg) == "r0":
                cmp_imms.append(insn.operands[1].imm)
        if insn.mnemonic.startswith("ubfx"):
            # Capstone ARM operand order for UBFX is Rd, Rm, lsb(imm), width(imm).
            ubfx.append((insn.operands[2].imm, insn.operands[3].imm))
        reads, _writes = insn.regs_access()
        if any(insn.reg_name(r) == "r0" for r in reads) and all(index < s for s in shadowed):
            # r0 read before the first call would be the incoming `this`.
            r0_reads.append(index)
    conditional_between = any(insn.mnemonic.startswith(("popne", "moveq", "poplt"))
                              for insn in insns[shadowed[0]:shadowed[-1] + 1]) \
        if len(shadowed) >= 2 else False
    return {
        "instruction_count": len(insns),
        "call_targets": [f"0x{a:08x}" for a in calls],
        "call_targets_as_recovered_getters": calls == [GETTER_FAMILY_VA,
                                                       GETTER_FEATURES_VA],
        "family_compare_immediates": cmp_imms,
        "family_compared_against_1": cmp_imms == [1],
        "ubfx_lsb_width": ubfx,
        "feature_extract_is_bit_2_width_1": ubfx == [(2, 1)],
        "conditional_return_between_calls": conditional_between,
        "incoming_this_read": bool(r0_reads),
    }


def toolchain(ndk: Path) -> list[dict]:
    bin_dir = ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin"
    sysroot = ndk / "platforms/android-14/arch-arm/usr/include"
    arm = ["-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16"]
    out = []
    if not (bin_dir / "arm-linux-androideabi-gcc").exists():
        return out
    for opt in ("-O2", "-O3", "-Os"):
        for prot in ("-fstack-protector", ""):
            out.append({"name": f"ndk-r8e-gcc-4.4.3{opt}{prot}",
                        "cc": str(bin_dir / "arm-linux-androideabi-gcc"),
                        "flags": [opt] + ([prot] if prot else []) + arm
                                 + [f"-I{sysroot}"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path(".cache/cpufeat/detectneon.json"))
    ap.add_argument("--workdir", type=Path, default=Path(".cache/cpufeat/detectneon"))
    args = ap.parse_args()

    blob, window = load_reference(args.reference)
    facts = structure(window)
    rows = []
    args.workdir.mkdir(parents=True, exist_ok=True)
    for entry in toolchain(DEFAULT_NDK_R8E):
        obj = args.workdir / f"{entry['name']}.o"
        cmd = [entry["cc"], "-c", *entry["flags"], "-I", str(INCLUDE),
               str(SOURCE), "-o", str(obj)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            rows.append({"config": entry["name"], "status": "COMPILE_FAILED",
                         "diagnostic": (proc.stderr or proc.stdout).strip()[:400]})
            continue
        report = diff_window(blob, obj.read_bytes(), address=DETECT_NEON[1],
                             symbol=DETECT_NEON[0],
                             expected_reference_sha256=REFERENCE_SHA256)
        report["config"] = entry["name"]
        rows.append(report)

    ok = [r for r in rows if r.get("status") == "ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION"]
    payload = {
        "recovered_source": str(SOURCE),
        "recovered_symbol": DETECT_NEON[0],
        "reference": str(args.reference),
        "reference_sha256": REFERENCE_SHA256,
        "reference_address": f"0x{DETECT_NEON[1]:08x}",
        "reference_size": DETECT_NEON[2],
        "mode": "A32",
        "structure": facts,
        "structure_agrees_with_source": bool(
            facts["call_targets_as_recovered_getters"]
            and facts["family_compared_against_1"]
            and facts["feature_extract_is_bit_2_width_1"]
            and facts["conditional_return_between_calls"]
            and not facts["incoming_this_read"]),
        "configs_compiled": len(rows),
        "configs_words_equal_modulo_relocation": len(ok),
        "code_window_equal": any(r.get("code_window_equal") for r in rows),
        "source_matching_verified": False,
        "original_compiler_verified": False,
        "disposition": "IMPLEMENTED + TESTED (behaviour); NOT MATCHED (no byte equality)",
        "evidence_scope": "One linked A32 window against ET_REL candidates; "
                          "relocation-masked agreement is not a byte match, an "
                          "original object recovery or compiler identification.",
        "matrix": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"structure agrees with recovered source: {payload['structure_agrees_with_source']}")
    print(f"  calls={facts['call_targets']} cmp={facts['family_compare_immediates']} "
          f"ubfx={facts['ubfx_lsb_width']}")
    print(f"  short-circuit return between calls: {facts['conditional_return_between_calls']}")
    print(f"  incoming `this` read: {facts['incoming_this_read']}")
    print(f"window: {len(ok)}/{len(rows)} configs "
          f"{'ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION'}; "
          f"code_window_equal={payload['code_window_equal']} -> {args.out}")
    for r in rows:
        counts = r.get("counts") or {}
        print(f"  {r['config']:34s} {r.get('status')} "
              f"{r.get('candidate_size')}B EQUAL={counts.get('EQUAL')} "
              f"RELOC={counts.get('RELOCATION_FIELD')} "
              f"ENC={counts.get('ENCODING_DIFFERENCE')}")
    return 0 if (payload["structure_agrees_with_source"] and ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
