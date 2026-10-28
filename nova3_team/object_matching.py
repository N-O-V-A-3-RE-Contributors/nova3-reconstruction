"""Offline ARM ELF matching research. Never make original-code objects production inputs.

The target .o files below are SYNTHETIC snapshots assembled from linked code
bytes. The linked ELF does not contain its original .o boundaries or full
pre-link relocation information. A synthetic .o match is not a verified decomp.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from .indexer import LedgerError, build_ledger

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nova3.object-matching/1"
MAX_FUNCTION_BYTES = 16384
MAX_SELECTED = 256


class MatchingError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fingerprint(data: bytes, binary: Path, readelf: str = "readelf") -> dict:
    """Observed evidence only. No inferred GCC/NDK release or optimization level."""
    ledger = build_ledger(data)
    probes = {}
    for name, flags in (
        ("arm_attributes", ["-A"]),
        ("compiler_comment", ["-p", ".comment"]),
        ("notes", ["-n"]),
        ("dynamic", ["-d"]),
    ):
        try:
            proc = subprocess.run([readelf, *flags, str(binary)], check=False,
                                  capture_output=True, text=True, timeout=20)
            probes[name] = dict(exit_code=proc.returncode,
                                stdout=proc.stdout[:16000],
                                stderr=proc.stderr[:2000],
                                truncated=len(proc.stdout) > 16000)
        except (OSError, subprocess.TimeoutExpired) as exc:
            probes[name] = dict(error=type(exc).__name__)
    return dict(schema=SCHEMA, binary_sha256=digest(data),
                architecture=ledger["binary"], symbol_coverage=ledger["coverage"],
                probes=probes, original_compiler="UNKNOWN",
                original_flags="UNKNOWN",
                warning="ELF metadata can narrow candidates but does not prove the original compiler")


def group_hint(name: str | None) -> str:
    """Conservative C++ scope hint, NOT proof of an original translation unit."""
    if name and name.startswith("_ZN"):
        match = re.match(r"_ZN([0-9]{1,3})", name)
        if match:
            n = int(match.group(1))
            part = name[match.end():match.end() + n]
            if len(part) == n and part.isascii():
                candidate = re.sub(r"[^A-Za-z0-9_]", "_", part)[:36]
                if candidate:
                    return "scope_" + candidate
    return "unclassified"


def select_functions(data: bytes, addresses: set[int] | None = None,
                     limit: int = 16) -> tuple[list[dict], list[dict]]:
    """Use only exact, non-conflicting ELF STT_FUNC sections and symbol bounds."""
    if not 1 <= limit <= MAX_SELECTED:
        raise MatchingError("limit must be between 1 and 256")
    ledger = build_ledger(data)
    if ledger["binary"]["endianness"] != "little":
        raise MatchingError("the ARM assembly emitter currently requires little-endian ELF")
    selected, exclusions = [], []
    previous_end = 0
    for fn in ledger["functions"]:
        address, size = fn["address"], fn["size"]
        if addresses is not None and address not in addresses:
            continue
        reason = None
        offsets = {s["file_offset"] for s in fn["symbols"] if s["size"] == size}
        if fn["size_conflict"]:
            reason = "conflicting_symbol_sizes"
        elif not 0 < size <= MAX_FUNCTION_BYTES:
            reason = "unknown_or_oversized_symbol"
        elif len(offsets) != 1:
            reason = "ambiguous_file_offset"
        elif size % (2 if fn["thumb"] else 4):
            reason = "unaligned_instruction_extent"
        elif address < previous_end:
            reason = "overlapping_symbol_extents"
        elif next(iter(offsets)) + size > len(data):
            reason = "symbol_outside_file"
        if reason:
            exclusions.append(dict(address=address, reason=reason))
            continue
        offset = next(iter(offsets))
        row = dict(address=address, size=size, thumb=fn["thumb"],
                   name=fn["name"], group=group_hint(fn["name"]),
                   file_offset=offset, sha256=digest(data[offset:offset + size]),
                   symbol_id=fn["id"], evidence="ELF STT_FUNC section+size")
        selected.append(row)
        previous_end = address + size
        if len(selected) >= limit:
            break
    if addresses is not None:
        found = {row["address"] for row in selected}
        missing = addresses - found
        if missing:
            raise MatchingError("requested addresses are absent, ambiguous, or excluded: " +
                                ",".join(hex(x) for x in sorted(missing)))
    if not selected:
        raise MatchingError("no safe functions selected; stripped functions require validated Ghidra/IDA bounds")
    return selected, exclusions


def function_assembly(row: dict, code: bytes) -> str:
    """Byte-preserving linked instruction snapshot, NOT reconstructed relocations."""
    name = "nova3_fn_%08x_%s" % (row["address"], "thumb" if row["thumb"] else "arm")
    lines = [
        "/* RESEARCH ONLY: linked bytes, synthetic original; NOT source matching. */",
        ".syntax unified",
        '.section .text.' + name + ', "ax", %progbits',
        ".thumb" if row["thumb"] else ".arm",
        ".balign 2" if row["thumb"] else ".balign 4",
        ".global " + name,
        ".type " + name + ", %function",
    ]
    if row["thumb"]:
        lines.append(".thumb_func")
    lines.append(name + ":")
    for pos in range(0, len(code), 12):
        lines.append("  .byte " + ", ".join("0x%02x" % b for b in code[pos:pos + 12]))
    lines.extend([".size " + name + ", .-" + name, ""])
    return "\n".join(lines)


def _asm_include(path: Path) -> str:
    raw = path.resolve().as_posix()
    if any(c in raw for c in '"\\\n\r'):
        raise MatchingError("unsafe path for assembler .include")
    return '.include "' + raw + '"'


def _donor_group_hints(plan: dict, binary_sha256: str) -> dict[int, dict]:
    """Optional DWARF-guided *hypotheses*, never proof of source TU boundaries."""
    if (not isinstance(plan, dict) or plan.get("schema") != "nova3.donor-plan/1"
            or plan.get("retail_sha256") != binary_sha256):
        raise MatchingError("donor plan schema or reference SHA mismatch")
    rows = plan.get("candidates")
    if not isinstance(rows, list) or len(rows) > 512:
        raise MatchingError("invalid or oversized donor plan")
    hints = {}
    for row in rows:
        if not isinstance(row, dict):
            raise MatchingError("malformed donor candidate")
        addr, size, hint = (row.get(x) for x in
                            ("retail_address", "retail_size", "group_hint"))
        if (type(addr) is not int or type(size) is not int
                or not isinstance(hint, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", hint)
                or row.get("translation_unit_proven") is not False):
            raise MatchingError("unsafe or overstated donor translation-unit hint")
        if addr in hints:
            raise MatchingError("ambiguous donor address hint")
        hints[addr] = dict(size=size, hint=hint, name=row.get("name"))
    return hints


def stage(data: bytes, binary: Path, out: Path, addresses: set[int] | None = None,
          limit: int = 16, donor_plan: dict | None = None) -> dict:
    """Generate ignored, local-only ASM, global-asm C++ and synthetic ref recipes."""
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise MatchingError("output directory must be new or empty to avoid overwriting work")
    selected, excluded = select_functions(data, addresses, limit)
    if donor_plan is not None:
        hints = _donor_group_hints(donor_plan, digest(data))
        for row in selected:
            donor = hints.get(row["address"])
            if donor is not None:
                if donor["size"] != row["size"] or donor["name"] != row["name"]:
                    raise MatchingError("donor name/size disagrees with reference ELF")
                row["group"] = donor["hint"]
                row["group_evidence"] = "INFERRED_DONOR_DWARF_OR_SYMBOL_SCOPE"
    groups = defaultdict(list)
    for row in selected:
        groups[row["group"]].append(row)
    units = []
    for row in selected:
        path = out / "asm" / ("%08x_%s.s" % (row["address"], "thumb" if row["thumb"] else "arm"))
        path.parent.mkdir(parents=True, exist_ok=True)
        code = data[row["file_offset"]:row["file_offset"] + row["size"]]
        path.write_text(function_assembly(row, code), encoding="ascii")
    for group, rows in sorted(groups.items()):
        paths = [out / "asm" / ("%08x_%s.s" % (r["address"], "thumb" if r["thumb"] else "arm"))
                 for r in rows]
        includes = "\n".join(_asm_include(p) for p in paths)
        source = out / "source" / (group + ".cpp")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "// RESEARCH-ONLY global asm scaffolding; replace functions with actual C++.\n"
            "// Do not include this file in production CMake targets.\n"
            "#if !defined(__arm__)\n#error ARMv7 cross-compiler required\n#endif\n"
            'asm(R"nova3(' + "\n" + includes + '\n)nova3");\n',
            encoding="utf-8")
        ref = out / "reference" / (group + ".s")
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text("/* Synthetic reference assembled from linked bytes. */\n" +
                       includes + "\n", encoding="ascii")
        units.append(dict(name=group, classification="INFERRED_SCOPE_NOT_ORIGINAL_TU",
                          functions=[r["symbol_id"] for r in rows],
                          reference_source=str(ref.relative_to(out)),
                          cpp_source=str(source.relative_to(out)),
                          target_path="objects/reference/" + group + ".o",
                          base_path="objects/build/" + group + ".o"))
    manifest = dict(schema=SCHEMA, kind="synthetic-linked-byte-research",
                    binary_path=str(binary.resolve()), binary_sha256=digest(data),
                    translation_units_verified=False,
                    grouping_source=("donor-plan-hypotheses" if donor_plan else "symbol-scope-hypotheses"),
                    original_object_files_recovered=False,
                    original_relocations_recovered=False,
                    original_compiler_verified=False, source_matching_verified=False,
                    functions=selected, exclusions=excluded, units=units)
    _json(out / "manifest.json", manifest)
    _json(out / "objdiff.json", dict(
        **{"$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json"},
        build_target=False, build_base=False,
        units=[dict(name=u["name"], target_path=u["target_path"],
                    base_path=u["base_path"], metadata={"complete": False})
               for u in units]))
    return manifest


def compiler_profile(compiler: Path, flags: list[str]) -> dict:
    compiler = compiler.expanduser().resolve(strict=True)
    proc = subprocess.run([str(compiler), "--version"], capture_output=True,
                          text=True, timeout=20, check=True)
    return dict(schema=SCHEMA, compiler=str(compiler), flags=flags,
                version_sha256=digest(proc.stdout.encode()), version=proc.stdout[:1000],
                match_to_original="UNVERIFIED")


def build(out: Path, profile_path: Path) -> dict:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or profile.get("schema") != SCHEMA:
        raise MatchingError("unsupported manifest or profile")
    binary = Path(manifest["binary_path"])
    if digest(binary.read_bytes()) != manifest["binary_sha256"]:
        raise MatchingError("reference ELF changed since staging")
    current = compiler_profile(Path(profile["compiler"]), profile["flags"])
    if current["version_sha256"] != profile["version_sha256"]:
        raise MatchingError("compiler version changed; create a new profile")
    commands = []
    for unit in manifest["units"]:
        for mode, source, dest in (
            ("assembler", unit["reference_source"], unit["target_path"]),
            ("c++", unit["cpp_source"], unit["base_path"]),
        ):
            outfile = out / dest
            outfile.parent.mkdir(parents=True, exist_ok=True)
            cmd = [current["compiler"], *current["flags"], "-c", "-x", mode,
                   str(out / source), "-o", str(outfile)]
            subprocess.run(cmd, check=True, timeout=120)
            commands.append(dict(input=source, output=dest,
                                 sha256=digest(outfile.read_bytes())))
    record = dict(schema=SCHEMA, kind="synthetic-comparison-only",
                  compiler_profile_sha256=digest(profile_path.read_bytes()),
                  objects=commands, source_matching_verified=False,
                  original_so_hash_matching_verified=False)
    _json(out / "build_record.json", record)
    return record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("fingerprint", help="Record ELF/ARM/compiler evidence, not a compiler guess")
    fp.add_argument("binary", type=Path)
    fp.add_argument("--out", type=Path)
    fp.add_argument("--readelf", default="readelf")
    sp = sub.add_parser("stage", help="Generate offline synthetic per-function object project")
    sp.add_argument("binary", type=Path)
    sp.add_argument("--out", type=Path, default=ROOT / ".cache" / "objmatch")
    sp.add_argument("--limit", type=int, default=16)
    sp.add_argument("--address", action="append", type=lambda s: int(s, 0))
    sp.add_argument("--donor-plan", type=Path,
                    help="Optional local Cyrus/Premium donor hypotheses JSON")
    cp = sub.add_parser("profile", help="Record exact candidate cross-compiler identity")
    cp.add_argument("compiler", type=Path)
    cp.add_argument("--flag", action="append", default=[])
    cp.add_argument("--out", type=Path, required=True)
    bp = sub.add_parser("build", help="Compile synthetic reference and global-asm objects")
    bp.add_argument("--out", type=Path, default=ROOT / ".cache" / "objmatch")
    bp.add_argument("--profile", required=True, type=Path)
    args = p.parse_args(argv)
    try:
        if args.command == "fingerprint":
            result = fingerprint(args.binary.read_bytes(), args.binary, args.readelf)
            if args.out:
                _json(args.out, result)
            else:
                print(json.dumps(result, indent=2))
        elif args.command == "stage":
            donor = (json.loads(args.donor_plan.read_text(encoding="utf-8"))
                     if args.donor_plan else None)
            result = stage(args.binary.read_bytes(), args.binary, args.out,
                           set(args.address) if args.address else None, args.limit,
                           donor_plan=donor)
            print("staged %d SYNTHETIC functions in %d inferred groups" %
                  (len(result["functions"]), len(result["units"])))
        elif args.command == "profile":
            result = compiler_profile(args.compiler, args.flag)
            _json(args.out, result)
            print("recorded candidate compiler; ORIGINAL MATCH UNVERIFIED")
        else:
            result = build(args.out.resolve(), args.profile)
            print("built %d synthetic objects; SOURCE MATCH UNVERIFIED" % len(result["objects"]))
    except (MatchingError, LedgerError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("object matching: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
