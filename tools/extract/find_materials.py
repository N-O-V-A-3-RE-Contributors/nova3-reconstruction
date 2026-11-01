#!/usr/bin/env python3
"""Locate the game materials a contributor already owns.

The project never ships game files; every contributor points the tools at
their own copy.  This script finds them in the places the project supports and
prints the exact commands that consume them, so nobody has to read source code
to know where a file belongs.

Search order (first match wins, all hashes reported):

1. ``$NOVA3_MATERIALS`` (a directory, or a single APK/OBB file)
2. ``<checkout>/materials/{apk,obb}`` -- the documented drop point
3. ``<checkout>/originals`` + ``<checkout>/Archive`` -- the owner's layout
4. ``~/.local/share/nova3/materials`` -- optional shared location

Recognised files:

* APKs: ``N.O.V.A.3_premium.apk`` (primary target), ``N.O.V.A.3_freemium.apk``
  (comparative reference).  Any other ``*.apk`` is reported, never assumed.
* Expansions: ``*.obb`` directly, or ``*_OBB.rar`` archives (which have been
  seen to be ZIP containers in practice -- detected, not assumed).
* Optional pre-released source: a directory containing ``reconstruction/`` and
  ``tools/`` (reported so the owner can merge it into the tracked layout).

Read-only: nothing is extracted, moved or executed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KNOWN = {
    "N.O.V.A.3_premium.apk": "premium-apk",
    "N.O.V.A.3_freemium.apk": "freemium-apk",
    "Nova3_premium_only_OBB.rar": "premium-obb-archive",
    "Nova3_freemium_only_OBB.rar": "freemium-obb-archive",
    "premium-main.obb": "premium-main-obb",
    "premium-patch.obb": "premium-patch-obb",
    "freemium-main.obb": "freemium-main-obb",
    "freemium-patch.obb": "freemium-patch-obb",
}

EXPECTED_SHA256 = {
    # From AGENTS.md; recorded so a wrong edition is obvious instead of silent.
    "N.O.V.A.3_premium.apk": "e2a4e30546c5a82270c3f867e814d8759cc3ea96bf22ab4e5eae173083b163b2",
    "N.O.V.A.3_freemium.apk": "e3f5a609ce952bdf46ae78521c6ac31b35fcdea8ee94eb44c738b0431d21cdf8",
}

SUFFIXES = (".apk", ".obb", ".rar", ".zip", ".gla")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("NOVA3_MATERIALS")
    if env:
        dirs.append(Path(env).expanduser())
    dirs += [ROOT / "materials" / "apk", ROOT / "materials" / "obb",
             ROOT / "originals", ROOT / "Archive",
             Path.home() / ".local" / "share" / "nova3" / "materials"]
    return dirs


def scan(limit_hashes: int = 8) -> dict:
    found: list[dict] = []
    seen: set[Path] = set()
    for directory in candidate_dirs():
        if not directory.is_dir():
            continue
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        for entry in sorted(resolved.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in SUFFIXES:
                continue
            kind = KNOWN.get(entry.name, "unrecognised-" + entry.suffix.lstrip(".").lower())
            record = {"path": str(entry), "name": entry.name, "kind": kind,
                      "bytes": entry.stat().st_size}
            if len(found) < limit_hashes:
                record["sha256"] = sha256(entry)
                if entry.name in EXPECTED_SHA256:
                    record["sha256_matches_expected"] = record["sha256"] == EXPECTED_SHA256[entry.name]
            found.append(record)
    return {"materials": found, "search_paths": [str(d) for d in candidate_dirs()]}


def ps_source_tree() -> dict:
    """A pre-released source tree, if the owner has dropped one next to the checkout."""
    for name in ("source-release", "nova3-source", "prerelease", "materials/source"):
        candidate = ROOT / name
        if (candidate / "reconstruction").is_dir() and (candidate / "tools").is_dir():
            return {"path": str(candidate), "reconstruction_files":
                    sum(1 for _ in (candidate / "reconstruction").rglob("*") if _.is_file())}
    return {}


def guidance(report: dict) -> list[str]:
    kinds = {m["kind"]: m for m in report["materials"]}
    lines = []
    py = ".venv/bin/python" if (ROOT / ".venv/bin/python").exists() else "python3"
    if "premium-apk" in kinds:
        lines.append(f"{py} tools/extract/materialize_premium.py      # verified OBB working copies into .cache/assets")
    if "premium-main-obb" in kinds:
        lines.append("build/host/nova3_resource 01_room_a.bdae build/first.bres --bres .cache/assets/premium-main.obb")
        lines.append("build/host/nova3_mesh .cache/assets/premium-main.obb 01_room_a.bdae build/room_a.obj")
    if {"premium-obb-archive"} & set(kinds):
        lines.append("# premium OBB archive: verify it is a ZIP (file <archive>) then extract with 7z/bsdtar into materials/obb/")
    if not report["materials"]:
        lines.append("nothing found: see materials/README.md for the two files and two archives to place")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)
    report = scan()
    report["source_tree"] = ps_source_tree()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print("NOVA3 materials")
    if not report["materials"]:
        print("  none found")
    for item in report["materials"]:
        mark = ""
        if "sha256_matches_expected" in item:
            mark = "  (matches expected)" if item["sha256_matches_expected"] else "  (DIFFERENT from expected)"
        print(f"  {item['kind']:<24} {item['bytes'] / 2**20:9.1f} MiB  {item['path']}{mark}")
    if report["source_tree"]:
        print(f"  pre-released source at {report['source_tree']['path']} "
              f"({report['source_tree']['reconstruction_files']} files)")
    print("\nnext commands:")
    for line in guidance(report):
        print("  " + line)
    return 0 if report["materials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
