#!/usr/bin/env python3
"""Compile the reconstructed cpu-features TU and diff it against the reference.

This drives the whole translation unit, not a single function: the four
recovered symbols of the Premium engine's `cpu-features.c` group
(android_getCpuCount / android_getCpuFeatures / android_getCpuFamily /
android_cpuInit) are each bounded in the reference ELF and compared word by
word against the freshly compiled ET_REL candidate using
`tools/compare/window_diff.py`.

What this proves and what it does not:

  * A matching window shows the reconstructed source produces the same A32
    instruction stream for that symbol under the given compiler and flags.
  * It does NOT identify the original compiler, flags or linker, and it is not
    an original object-file recovery.  Every report carries
    `source_matching_verified=False`.
  * Reference bytes are read locally and never printed; only sizes, counts and
    per-word SHA-256 digests appear in the report.

Example:
    .venv/bin/python tools/compare/cpufeatures_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
        --workdir .cache/cpufeat/pilot
"""
from __future__ import annotations

import argparse
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

from elftools.elf.elffile import ELFFile  # noqa: E402

# Recovered .bss layout of the reference TU, as offsets from g_once. The
# getters address these directly, so a candidate whose compiler orders the
# two int-sized objects the other way cannot reproduce the offset immediates
# even when its source is equivalent.
REFERENCE_BSS_LAYOUT = {
    "g_once": 0,
    "g_cpuCount": 4,
    "g_cpuFeatures": 8,
    "g_cpuFamily": 16,
}


def candidate_bss_layout(obj_path: Path) -> dict:
    """Section-relative offsets of the TU's file-scope objects in an ET_REL."""
    layout: dict[str, int] = {}
    with open(obj_path, "rb") as fh:
        elf = ELFFile(fh)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            return layout
        for sym in symtab.iter_symbols():
            if sym.name in REFERENCE_BSS_LAYOUT and sym["st_size"]:
                layout[sym.name] = sym["st_value"]
    if "g_once" in layout:
        base = layout["g_once"]
        layout = {name: value - base for name, value in layout.items()}
    return layout

DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK", "/home/jason/.local/opt/android-ndk-r8e"))

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

# Recovered symbols of the reference TU: (symbol, ELF VA, reported size).
TU_SYMBOLS = (
    ("android_getCpuCount", 0x000D2F10, 44),
    ("android_getCpuFeatures", 0x000D2F3C, 48),
    ("android_getCpuFamily", 0x000D2F6C, 44),
    ("android_cpuInit", 0x000D2F98, 2632),
)

# Candidate toolchains. The r8e entry is the historical NDK GCC 4.4.3 that the
# reference `.comment` string suggests; the rest bound how much of any observed
# agreement is compiler-independent.
def toolchain_matrix(ndk: Path) -> list[dict]:
    r8e_bin = ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin"
    sysroot = ndk / "platforms/android-14/arch-arm/usr/include"
    arm_flags = ["-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16"]
    matrix: list[dict] = []
    if (r8e_bin / "arm-linux-androideabi-gcc").exists():
        for opt in ("-O2", "-O3", "-Os"):
            for prot in ("-fstack-protector", ""):
                # Base entry: toplevel reordering left on (the compiler's own
                # ordering, which is what the first pilot measured).
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt] + ([prot] if prot else []) + arm_flags + [f"-I{sysroot}"],
                })
                # Rejected-hypothesis bound, kept visible in every report:
                # -fno-toplevel-reorder does reproduce the reference .bss
                # offsets, but it removes the address caching the reference
                # uses, so the candidate getters grow to 48/52 bytes.  The
                # real explanation is getter definition order (see the note in
                # cpu_features.c); this row exists so that conclusion stays
                # measured rather than asserted.
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}-noToplevelReorder",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt, "-fno-toplevel-reorder"]
                             + ([prot] if prot else []) + arm_flags + [f"-I{sysroot}"],
                })
                # Char signedness.  The reference sign-extends every cpuinfo
                # byte comparison (`sxtb` in the inlined has_list_item, `ldrsb`
                # for the ':' / ' ' delimiters in extract_cpuinfo_field), while
                # arm-linux-androideabi-gcc defaults to unsigned plain char and
                # emits neither.  -fsigned-char is therefore a measured source
                # property of the original TU, not a tuning knob; these rows
                # keep its effect visible in every report and let the residual
                # android_cpuInit gap be read as code layout, not as an
                # unexplained instruction difference.
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}-signedChar",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt, "-fsigned-char"] + ([prot] if prot else [])
                             + arm_flags + [f"-I{sysroot}"],
                })
    return matrix


def compile_candidate(entry: dict, source: Path, include_dirs: list[Path], out: Path) -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [entry["cc"], "-c", *entry["flags"]]
    for inc in include_dirs:
        cmd += ["-I", str(inc)]
    cmd += [str(source), "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, ""


def run(reference: Path, source: Path, workdir: Path, include_dirs: list[Path],
        matrix: list[dict], keep_objects: bool) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    ref_bytes = reference.read_bytes()
    results = []
    for entry in matrix:
        obj_path = workdir / f"candidate_{entry['name']}.o"
        ok, err = compile_candidate(entry, source, include_dirs, obj_path)
        if not ok:
            results.append({"toolchain": entry["name"], "compiled": False, "error": err[:800]})
            continue
        obj_bytes = obj_path.read_bytes()
        windows = []
        for symbol, address, expected in TU_SYMBOLS:
            report = diff_window(ref_bytes, obj_bytes, address=address, symbol=symbol,
                                 expected_reference_sha256=REFERENCE_SHA256)
            windows.append({
                "symbol": symbol,
                "reference_address": f"0x{address:08x}",
                "expected_reference_size": expected,
                "reference_size": report["reference_size"],
                "candidate_size": report["candidate_size"],
                "status": report["status"],
                "counts": report.get("counts"),
                "words_compared": report.get("words_compared"),
                "instruction_stream_equal": report.get("instruction_stream_equal"),
                "code_window_equal": report.get("code_window_equal"),
                "divergence_offsets": [
                    w["offset"] for w in report.get("word_details", [])
                    if w["kind"] == "ENCODING_DIFFERENCE"
                ][:40],
            })
        all_equal = all(w["instruction_stream_equal"] for w in windows)
        exact = all(w["code_window_equal"] for w in windows)
        layout = candidate_bss_layout(obj_path)
        results.append({
            "toolchain": entry["name"],
            "compiled": True,
            "candidate_object": str(obj_path),
            "all_windows_instruction_equal": all_equal,
            "all_windows_code_equal": exact,
            "candidate_bss_layout": layout,
            "reference_bss_layout": REFERENCE_BSS_LAYOUT,
            "bss_layout_matches": layout == REFERENCE_BSS_LAYOUT,
            "windows": windows,
        })
        if not keep_objects:
            obj_path.unlink(missing_ok=True)
    return {
        "schema": "nova3.cpufeatures-pilot/1",
        "reference": str(reference),
        "reference_sha256": REFERENCE_SHA256,
        "source": str(source),
        "toolchains": results,
        "source_matching_verified": False,
        "original_compiler_verified": False,
        "evidence_scope": (
            "Per-window A32 comparison of an ET_REL candidate against the linked "
            "reference. Agreement, when present, bounds instruction-selection "
            "equivalence for the stated toolchain only; it is not proof of the "
            "original compiler, options or linker, and relocation-driven words "
            "cannot match by construction."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--source", type=Path,
                        default=SCRIPTS / "reconstruction/recovered/libNOVA3_neon/cpu_features.c")
    parser.add_argument("--workdir", type=Path, default=SCRIPTS / ".cache/cpufeat/pilot")
    parser.add_argument("--ndk", type=Path, default=DEFAULT_NDK_R8E)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--keep-objects", action="store_true")
    args = parser.parse_args(argv)

    include_dirs = [args.source.parent]
    matrix = toolchain_matrix(args.ndk)
    if not matrix:
        print(f"no usable compiler found under {args.ndk}", file=sys.stderr)
        return 2

    report = run(args.reference, args.source, args.workdir, include_dirs, matrix,
                 args.keep_objects)
    out = args.out or (args.workdir / "cpufeatures_pilot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for entry in report["toolchains"]:
        if not entry["compiled"]:
            print(f"{entry['toolchain']:<34} COMPILE_FAILED")
            continue
        flags = []
        for w in entry["windows"]:
            mark = "EQ" if w["instruction_stream_equal"] else (
                "SIZE" if w["reference_size"] != w["candidate_size"] else "DIFF")
            flags.append(f"{w['symbol'].replace('android_getCpu','get').replace('android_cpuInit','cpuInit')}"
                         f"[{w['candidate_size']}/{w['reference_size']}]{mark}")
        layout = entry.get("candidate_bss_layout", {})
        note = "bss=ref" if entry.get("bss_layout_matches") else \
               f"bss_offsets={layout} (ref {REFERENCE_BSS_LAYOUT})"
        print(f"{entry['toolchain']:<34} " + "  ".join(flags) + f"   [{note}]")
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
