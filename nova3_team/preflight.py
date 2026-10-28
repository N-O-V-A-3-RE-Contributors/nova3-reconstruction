"""Bounded, read-only local readiness check for the NOVA3 matching research track.

Reports tool visibility and four expected private references. It does not run
analysis, start an AI session, install packages, or upload anything to GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]

REFERENCES = {
    "premium_engine": "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so",
    "premium_jni": "analysis/N.O.V.A.3_premium/lib/armeabi/libnativeinterface.so",
    "freemium_generator": "analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libgenerator.so",
    "freemium_engine": "analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libGloftNAHM.so",
}


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def memory_kib() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        pass
    return None


def inspect(root: Path, ida_executable: Path) -> dict:
    references = {}
    for key, rel in REFERENCES.items():
        source = root / rel
        if source.is_file():
            references[key] = dict(present=True, size=source.stat().st_size,
                                   sha256=hash_file(source))
        else:
            references[key] = dict(present=False, size=None, sha256=None)
    detected = {
        name: shutil.which(name)
        for name in ("readelf", "llvm-objdump", "cmake", "ninja", "clang++",
                     "objdiff", "objdiff-cli", "gh")
    }
    try:
        idalib_module = importlib.util.find_spec("idapro") is not None
    except (ImportError, ValueError):
        idalib_module = False
    mem_kib = memory_kib()
    # One native analysis process protects the user's 4-core/8-GiB laptop.
    return dict(schema="nova3.preflight/1", references=references,
                tools=detected, ida_executable_exists=ida_executable.is_file(),
                ida_path=str(ida_executable),
                idalib_importable_unverified=idalib_module,
                logical_cpus=os.cpu_count(),
                ram_kib=mem_kib,
                recommended_native_analysis_processes=1,
                original_compiler="UNKNOWN",
                matching_pipeline_operational=False,
                note="Tool presence is not validation; no actual decompilation executed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ida", type=Path,
                        default=Path("/home/jason/ida-pro-9.4/ida"))
    parser.add_argument("--out", type=Path,
                        default=ROOT / ".cache" / "object-preflight.json")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error("repository directory not found")
    report = inspect(root, args.ida.expanduser())
    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    present = sum(int(x["present"]) for x in report["references"].values())
    print("NOVA3 local preflight: %d/4 references; IDA executable: %s; idalib: %s" %
          (present, report["ida_executable_exists"],
           report["idalib_importable_unverified"]))
    print("Research metadata saved locally; compiler and original object matching unverified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
