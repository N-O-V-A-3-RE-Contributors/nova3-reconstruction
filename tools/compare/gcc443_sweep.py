#!/usr/bin/env python3
"""Find the GCC 4.4.3 flag set that reproduces the original engine's code.

Why this exists
---------------
Readelf on Premium ``libNOVA3_neon.so`` (SHA-256
ec3a8f88...(see research/elf/inventory.json)) proves the compiler version
(``.comment``: GCC 4.4.3), the ARM attributes (v7-A, VFPv3, NEONv1, wchar_t 4,
8-byte alignment, "Aggressive Speed"), and the ABI (EABI5, softfp: no
``Tag_ABI_VFP_args``).  It does not prove the *flags*.  Guessing them per
function produces "it compiled but the bytes differ" dead ends.

This tool closes that gap empirically.  It compiles one real source-owned
function with every flag configuration in a documented matrix using the
*actual* GCC 4.4.3 from Android NDK r8e, compares the resulting ``.text`` bytes
against the linked original window (via ``nova3_team.object_compare``), and
reports which configurations reproduce which functions byte-for-byte.

A configuration that matches several unrelated functions (C++ constructor,
NEON matrix multiply, integer leaf) is evidence for the *project* build flags;
one that matches a single function is evidence about that function only.

Scope and honesty rules
-----------------------
* Bytes compared are instruction bytes only.  Branch/call offsets and literal
  pools differ between a relocatable object and a linked image, so a
  "byte-equal" result is reported exactly as ``object_compare`` reports it
  (window equality with unresolved relocations is never called a source match).
* This never executes original code and never writes original instruction
  bytes to disk; only hashes, sizes and match counts are emitted.
* The toolchain is not downloaded here: pass ``--gcc`` explicitly.  NDK r8e
  is fetched by ``tools/setup/fetch_gcc443.sh`` into ignored ``.cache``.

Usage:
    gcc443_sweep.py --gcc .cache/toolchains/android-ndk-r8e/.../bin/arm-linux-androideabi-g++ \
                    --reference analysis/.../libNOVA3_neon.so \
                    --case src=reconstruction/recovered/.../x.cpp:0x7bf44:fn ... \
                    --out .cache/gcc443-sweep/sweep.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nova3_team.object_compare import CompareError, compare_window  # noqa: E402

# The ABI is not a free variable: EABI5 softfp and v7-A are read out of the
# ELF attributes above, so they are constants, not sweep dimensions.
BASE = ["-mfloat-abi=softfp", "-fPIC", "-fno-rtti", "-fno-exceptions"]
# Dimensions that change code generation materially and are plausible for a
# 2012 Gameloft NDK build.  Order matters only for reporting.
AXES = {
    "std": ["-std=gnu++98"],
    "mode": ["-marm", "-mthumb"],
    "fpu": ["-mfpu=vfpv3-d16", "-mfpu=neon"],
    "opt": ["-O2", "-O3", "-Os", "-O2 -funroll-loops", "-O3 -funroll-loops",
            "-O2 -fno-inline-functions", "-O2 -finline-functions"],
    "align": ["", "-malign-loops=2 -malign-jumps=2", "-falign-functions=4",
              "-falign-functions=16", "-falign-loops=4"],
    "fpcont": ["", "-ffast-math", "-fno-strict-aliasing -ffast-math"],
}


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_object(gcc: Path, source: Path, flags: list[str], out: Path, include: list[Path]) -> tuple[bool, str]:
    cmd = [str(gcc), *flags, *[f"-I{p}" for p in include], "-c", str(source), "-o", str(out)]
    cp = run(cmd, out.parent)
    if cp.returncode != 0:
        return False, (cp.stderr or cp.stdout).strip().splitlines()[-1] if (cp.stderr or cp.stdout) else "failed"
    return True, ""


def sweep_case(gcc: Path, reference: bytes, source: Path, address: int, symbol: str,
               include: list[Path], workdir: Path) -> dict:
    results = []
    keys = list(AXES)
    for combo in itertools.product(*(AXES[k] for k in keys)):
        extra = " ".join(part for part in combo if part)
        flags = [*extra.split(), *BASE]
        obj = workdir / f"{symbol.replace(':', '_').replace('/', '_')}.o"
        ok, err = compile_object(gcc, source, flags, obj, include)
        entry = {"flags": extra, "compiled": ok}
        if ok:
            try:
                report = compare_window(reference, obj.read_bytes(), address=address, symbol=symbol)
                entry.update(status=report["status"], size=report["candidate_size"],
                             equal=report["code_window_equal"])
            except CompareError as exc:
                entry.update(status="COMPARE_ERROR", error=str(exc))
        else:
            entry["error"] = err
        results.append(entry)
        try:
            obj.unlink()
        except OSError:
            pass
    matches = [r for r in results if r.get("equal")]
    return {
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "address": address,
        "symbol": symbol,
        "trials": len(results),
        "compiled": sum(1 for r in results if r["compiled"]),
        "matching_flags": [r["flags"] for r in matches],
        "matching_status": sorted({r["status"] for r in matches}),
        "results": results,
    }


def parse_case(text: str) -> tuple[Path, int, str, list[Path]]:
    # source.cpp:0xADDR:SYMBOL[;extra/include/dir,...]
    body, _, inc = text.partition(";")
    source, address, symbol = body.rsplit(":", 2)
    include = [ROOT / part for part in inc.split(",") if part]
    return (ROOT / source).resolve(), int(address, 0), symbol, include


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gcc", required=True, type=Path, help="arm-linux-androideabi-g++ 4.4.3")
    parser.add_argument("--reference", required=True, type=Path, help="original linked ARM ELF")
    parser.add_argument("--case", action="append", required=True,
                        help="source.cpp:0xADDR:mangled_symbol[;include/dir,...] (repeatable)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    target = args.out.resolve()
    if not target.is_relative_to(ROOT / ".cache"):
        print("refusing to write outside .cache", file=sys.stderr)
        return 2
    gcc = args.gcc.resolve()
    if not gcc.is_file():
        print(f"gcc not found: {gcc}", file=sys.stderr)
        return 2
    version = run([str(gcc), "--version"], ROOT).stdout.splitlines()[0]
    reference = args.reference.read_bytes()
    reference_sha = hashlib.sha256(reference).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": "nova3.gcc443-flag-sweep/1", "compiler": version,
              "reference": str(args.reference), "reference_sha256": reference_sha,
              "base_flags": BASE, "cases": []}
    for text in args.case:
        source, address, symbol, include = parse_case(text)
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            case = sweep_case(gcc, reference, source, address, symbol, include, Path(tmp))
        report["cases"].append(case)
        print(f"{case['source']} @0x{address:x} {symbol}: {case['compiled']}/{case['trials']} compiled, "
              f"{len(case['matching_flags'])} byte-equal flag set(s)")
        for flags in case["matching_flags"][:6]:
            print(f"    MATCH: {flags}")
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(f"report: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
