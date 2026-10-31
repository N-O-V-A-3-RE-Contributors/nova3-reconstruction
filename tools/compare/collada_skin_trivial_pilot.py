#!/usr/bin/env python3
"""Byte-compare three recovered CSoftwareSkinTechnique methods with the reference.

The three recovered functions are 8-byte A32 leaves with no relocation:

    0x00710b50  checkAvailability(video::STechnique const&, unsigned) const
    0x00710b48  needOutputBuffer() const
    0x00710b58  attach(scene::ISceneNode*)

Unlike the earlier pilot slices (whose windows always contained at least one
link-time relocation word), an exact byte comparison is possible here: if the
candidate object's symbol bytes equal the reference bytes for every word, the
report says so and does NOT hide it behind `RELOCATION_FIELD`.  It also states
what such an equality does and does not prove:

  proves      our source, compiled with this toolchain and these flags, emits the
              same instruction bytes as the reference for these three symbols;
  does not    identify the original compiler, its flags or its translation-unit
  prove       layout, nor say anything about the other 39k functions, nor about
              whole-.so SHA-256 equality.

This is a per-function instruction-window comparison, not a whole-library match.
Output is metadata only: addresses, symbol names, sizes, counts and booleans; no
reference bytes or disassembly text are written.

Exit code 0 when every configuration reproduces all three windows exactly.

Example:
    .venv/bin/python tools/compare/collada_skin_trivial_pilot.py \
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

from elftools.elf.elffile import ELFFile

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

# (mangled name, ELF VA, size, human-readable role)
TARGETS = [
    ("_ZNK6glitch7collada6detail22CSoftwareSkinTechnique17checkAvailability"
     "ERKNS_5video10STechniqueEj", 0x00710B50, 8, "checkAvailability -> true"),
    ("_ZNK6glitch7collada6detail22CSoftwareSkinTechnique16needOutputBufferEv",
     0x00710B48, 8, "needOutputBuffer -> true"),
    ("_ZN6glitch7collada6detail22CSoftwareSkinTechnique6attach"
     "EPNS_5scene10ISceneNodeE", 0x00710B58, 8, "attach: store arg at +0x0c"),
]

SOURCE = Path("reconstruction/recovered/libNOVA3_neon/collada_skin_technique.cpp")
INCLUDE = Path("reconstruction/recovered/libNOVA3_neon")
DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK",
                                      "/home/jason/.local/opt/android-ndk-r8e"))

BASE_FLAGS = [
    "-c", "-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16",
    "-fno-exceptions", "-fno-rtti", "-fvisibility=hidden", "-fno-pic",
]
CONFIGS = [("-O2",), ("-O3",), ("-Os",)]


def gxx(ndk: Path) -> Path:
    p = (ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
         "arm-linux-androideabi-g++")
    if not p.exists():
        raise SystemExit(f"FAIL: NDK r8e g++ not found at {p}")
    return p


def symbol_bytes(obj: Path, wanted: set[str]):
    """Map mangled name -> (bytes, size, bind) for the wanted symbols."""
    out: dict[str, tuple[bytes, int, str]] = {}
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
            out[sym.name] = (sec.data()[off:off + sym["st_size"]],
                             sym["st_size"], sym["st_info"]["bind"])
    return out


def reference_windows(ref: Path):
    blob = ref.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    windows: dict[str, bytes] = {}
    with open(ref, "rb") as fh:
        elf = ELFFile(fh)
        text = elf.get_section_by_name(".text")
        symtab = elf.get_section_by_name(".symtab")
        if text is None or symtab is None:
            raise SystemExit("FAIL: reference lacks .text or .symtab")
        base = text.header.sh_addr
        data = text.data()
        for name, va, size, _ in TARGETS:
            hits = [s for s in symtab.iter_symbols() if s.name == name]
            if len(hits) != 1:
                raise SystemExit(f"FAIL: {name} is not a unique symbol")
            sym = hits[0]
            if sym["st_value"] != va or sym["st_size"] != size:
                raise SystemExit(
                    f"FAIL: {name} bounds changed "
                    f"(0x{sym['st_value']:08x}/{sym['st_size']})")
            if sym["st_info"]["type"] != "STT_FUNC" or sym["st_value"] & 1:
                raise SystemExit(f"FAIL: {name} is not an A32 STT_FUNC")
            windows[name] = data[va - base: va - base + size]
    return sha, windows


def first_difference(left: bytes, right: bytes) -> int | None:
    for i in range(min(len(left), len(right))):
        if left[i] != right[i]:
            return i
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--ndk", default=str(DEFAULT_NDK_R8E))
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-objects", action="store_true")
    args = ap.parse_args(argv)

    ref = Path(args.reference)
    sha, windows = reference_windows(ref)
    compiler = gxx(Path(args.ndk))

    rows = []
    all_exact = True
    with tempfile.TemporaryDirectory(prefix="collada-skin-pilot-") as tmp:
        tmpdir = Path(tmp)
        for cfg in CONFIGS:
            obj = tmpdir / ("candidate" + "_".join(cfg).replace("-", "") + ".o")
            cmd = [str(compiler), *BASE_FLAGS, *cfg, f"-I{INCLUDE}",
                   str(SOURCE), "-o", str(obj)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            row: dict = {"config": " ".join(cfg), "compile_rc": proc.returncode}
            if proc.returncode != 0:
                row["error"] = proc.stderr.strip().splitlines()[-1:] or ["failed"]
                all_exact = False
                rows.append(row)
                continue
            found = symbol_bytes(obj, {t[0] for t in TARGETS})
            per_symbol = []
            for name, va, size, role in TARGETS:
                if name not in found:
                    per_symbol.append({
                        "symbol": name.split("CSoftwareSkinTechnique")[-1],
                        "role": role, "va": f"0x{va:08x}",
                        "present": False,
                    })
                    all_exact = False
                    continue
                data, cand_size, bind = found[name]
                diff = first_difference(windows[name], data)
                exact = diff is None and cand_size == size
                all_exact = all_exact and exact
                per_symbol.append({
                    "symbol": name.split("CSoftwareSkinTechnique")[-1],
                    "role": role,
                    "va": f"0x{va:08x}",
                    "present": True,
                    "object_bind": bind,
                    "reference_size": size,
                    "candidate_size": cand_size,
                    "first_difference_offset": diff,
                    "bytes_equal": exact,
                    "relocations_in_window": 0 if exact else None,
                })
            row["symbols"] = per_symbol
            row["all_bytes_equal"] = all(s.get("bytes_equal") for s in per_symbol)
            rows.append(row)

    report = {
        "tool": "collada_skin_trivial_pilot",
        "reference": str(ref),
        "reference_sha256": sha,
        "reference_sha256_matches": sha == REFERENCE_SHA256,
        "source": str(SOURCE),
        "compiler": str(compiler),
        "flags": BASE_FLAGS,
        "configs": [" ".join(c) for c in CONFIGS],
        "rows": rows,
        "all_configs_exact": all_exact,
        "scope": ("per-symbol instruction windows, 8 bytes each, no relocations; "
                  "a per-function byte equality, not a whole-.so SHA-256 match and "
                  "not compiler or flag identification"),
        "source_matching_verified": all_exact,
    }

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0 if all_exact else 1


if __name__ == "__main__":
    sys.exit(main())
