#!/usr/bin/env python3
"""One offline GCC 4.4.3 window check per recorded target, with no flag search.

Run: python3 tools/compare/gcc443_seven_window.py --out .cache/gcc443-window/seven.json

The seven names in NEXT are groups, not seven fully specified build recipes.
Only getTransposed has a per-function size-matching recipe in STATUS/NEXT.
Missing recipes remain ERROR rows: do not borrow another function's flags.
The accessor cluster needs a manually selected single function before it can
be a row in a seven-function experiment. Edit TARGETS by hand when evidence
is available; never generate substitutes for missing source files.

Outputs contain metadata only. Objects are temporary, source-compiled ET_REL
files; neither an APK nor an OBB is read, and no link step is performed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from elftools.elf.elffile import ELFFile  # noqa: E402
from elftools.common.exceptions import ELFError  # noqa: E402
from nova3_team.object_compare import candidate_window  # noqa: E402
from tools.compare.gcc443_sweep import compile_object  # noqa: E402
from tools.compare import gcc443_window_check as window_check  # noqa: E402

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
REFERENCE = Path("analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so")
NDK = Path(".cache/toolchains/android-ndk-r8e")
GXX = Path("toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++")
RECOVERED = Path("reconstruction/recovered/libNOVA3_neon")


@dataclass(frozen=True)
class Target:
    name: str
    address: int | None
    symbol: str | None
    source: Path | None
    candidate_symbol: str | None
    flags: tuple[str, ...] | None
    evidence: str


@dataclass(frozen=True)
class ReferenceWindow:
    address: int
    code: bytes | None = None
    error: str | None = None


# A null recipe is deliberately not a guessed -O2/-O3 profile. NEXT's group
# list does not establish per-function flags, nor select an accessor symbol.
TARGETS = (
    Target("computePitch", 0x0077D37C,
           "_ZN6glitch5video12pixel_format12computePitchENS0_14E_PIXEL_FORMATEj",
           RECOVERED / "block_0077d37c.cpp", "nova3_recovered_compute_pitch", None,
           "docs/progress/NEXT.md: seven-group list; no measured flags recorded"),
    Target("getTransposed", 0x000EBA84,
           "_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_",
           RECOVERED / "collada_matrix4_transpose.cpp",
           "_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_",
           ("-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16",
            "-fno-exceptions", "-fno-rtti", "-fno-pic", "-O3", "-fvisibility=hidden"),
           "docs/progress/STATUS.md: matrix transpose, Window measured; "
           "tools/compare/collada_matrix4_transpose_pilot.py: BASE_FLAGS, CONFIGS"),
    Target("accessor cluster", None, None, None, None, None,
           "docs/progress/NEXT.md: cluster has multiple functions; "
           "single address/symbol/source and measured flags not specified"),
    Target("getStream (const)", 0x0077BD7C,
           "_ZNK6glitch5video14CVertexStreams9getStreamENS0_18E_VERTEX_ATTRIBUTEEPKNS0_13SVertexStreamES5_",
           RECOVERED / "block_0077bd7c.cpp", "nova3_recovered_get_stream_const", None,
           "docs/progress/NEXT.md: seven-group list; no measured flags recorded"),
    Target("addOffset", 0x0077BE04,
           "_ZN6glitch5video14CVertexStreams9addOffsetEi",
           RECOVERED / "block_0077be04.cpp", "nova3_recovered_add_offset", None,
           "docs/progress/NEXT.md: seven-group list; no measured flags recorded"),
    Target("getPrimitiveCount", 0x0077BF84,
           "_ZN6glitch5video17getPrimitiveCountENS0_16E_PRIMITIVE_TYPEEj",
           RECOVERED / "block_0077bf84.cpp", "nova3_recovered_get_primitive_count", None,
           "docs/progress/NEXT.md: seven-group list; no measured flags recorded"),
    Target("CFPSCounter", 0x0077BB7C, "_ZN6glitch5video11CFPSCounterC2Ev",
           RECOVERED / "block_0077bb7c.cpp", "nova3_recovered_fps_counter_ctor", None,
           "docs/progress/NEXT.md: seven-group list; no measured flags recorded"),
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _symbols(reference: Path, targets: tuple[Target, ...]) -> dict[str, list[ReferenceWindow]]:
    wanted = {t.symbol for t in targets if t.symbol}
    result: dict[str, list[ReferenceWindow]] = {s: [] for s in wanted}
    with reference.open("rb") as handle:
        elf = ELFFile(handle)
        if elf.elfclass != 32 or not elf.little_endian or elf["e_machine"] != "EM_ARM":
            raise ValueError("reference must be little-endian ARM32")
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None or symtab["sh_type"] != "SHT_SYMTAB":
            # Missing symbols belong in ERROR rows, including a missing table.
            return result
        for sym in symtab.iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC" or sym.name not in wanted:
                continue
            address, size, index = sym["st_value"], sym["st_size"], sym["st_shndx"]
            try:
                if not isinstance(index, int) or not 0 < index < elf.num_sections():
                    raise ValueError("reference function has no section")
                section = elf.get_section(index)
                offset = address - section["sh_addr"]
                if (address % 4 or not size or size % 4 or section["sh_type"] != "SHT_PROGBITS"
                        or not section["sh_flags"] & 4 or offset < 0
                        or offset + size > section["sh_size"]):
                    raise ValueError("reference function has invalid A32 bounds")
                code = section.data()[offset:offset + size]
                if len(code) != size:
                    raise ValueError("reference function data truncated")
                window = ReferenceWindow(address, code=code)
            except (ValueError, ELFError) as exc:
                # Preserve the exact st_value even when this symbol is unsafe;
                # one invalid target must not discard the other six rows.
                error = "malformed reference ELF metadata" if isinstance(exc, ELFError) else str(exc)
                window = ReferenceWindow(address, error=error)
            result[sym.name].append(window)
    return result


def _only_call_displacements_differ(original: bytes, candidate: bytes) -> bool:
    """Guard the existing checker's broader branch mask: normalize A32 BL only."""
    if len(original) != len(candidate) or len(original) % 4:
        return False
    for a, b in zip(struct.iter_unpack("<I", original), struct.iter_unpack("<I", candidate)):
        x, y = a[0], b[0]
        if x == y:
            continue
        if (x >> 28 == 15 or x & 0x0F000000 != 0x0B000000
                or x & 0xFF000000 != y & 0xFF000000):
            return False
    return True


def run_report(reference: Path, compiler: Path, workdir: Path, *, root: Path = ROOT,
               targets: tuple[Target, ...] = TARGETS,
               expected_sha256: str = REFERENCE_SHA256) -> dict:
    """Build each complete table row once. Incomplete rows fail closed in place."""
    if len(targets) != 7:
        raise ValueError("exactly seven table rows required")
    reference_sha = _digest(reference.read_bytes())
    if reference_sha != expected_sha256:
        raise ValueError("reference SHA-256 mismatch")
    version_lines = subprocess.run([str(compiler), "--version"], capture_output=True,
                                   text=True, check=True, timeout=15).stdout.splitlines()
    # Reject empty output and lookalike versions such as 4.4.30 before building.
    version = version_lines[0] if version_lines else ""
    if re.search(r"\bGCC\)?\s+4\.4\.3(?:\s|$)", version) is None:
        raise ValueError("compiler is not GCC 4.4.3")
    symbols = _symbols(reference, targets)
    report = dict(schema="nova3.gcc443-seven-window/1", reference=str(reference),
                  reference_sha256=reference_sha, compiler=version,
                  compiler_sha256=_digest(compiler.read_bytes()), rows=[])
    for index, target in enumerate(targets):
        row = dict(name=target.name, address=None, recorded_address=target.address,
                   symbol=target.symbol, candidate_symbol=target.candidate_symbol,
                   source=None if target.source is None else str(target.source),
                   source_sha256=None, flags=None if target.flags is None else list(target.flags),
                   evidence=target.evidence, status="ERROR", original_bytes=None,
                   candidate_bytes=None, call_sites=[])
        report["rows"].append(row)
        try:
            if target.symbol is None or target.address is None:
                raise ValueError("single function not selected for group; edit table with evidence")
            hits = symbols[target.symbol]
            if len(hits) != 1:
                raise ValueError("reference symbol absent or ambiguous")
            window = hits[0]
            address = window.address
            row["address"] = address
            if window.error:
                raise ValueError(window.error)
            original = window.code
            row.update(original_bytes=len(original),
                       original_window_sha256=_digest(original))
            if address != target.address:
                raise ValueError("resolved symbol address differs from recorded address")
            if target.source is None or not (root / target.source).is_file():
                raise ValueError("source missing; edit table by hand, no substitute generated")
            source = root / target.source
            row["source_sha256"] = _digest(source.read_bytes())
            if target.flags is None:
                raise ValueError("measured size-matching flags absent from STATUS/NEXT")
            if not target.candidate_symbol:
                raise ValueError("candidate symbol absent from table")
            obj = workdir / f"{index:02d}.o"
            # Never consume an object left by an earlier failed compilation.
            obj.unlink(missing_ok=True)
            ok, _ = compile_object(compiler, source, list(target.flags), obj, [source.parent])
            if not ok:
                raise ValueError("compilation failed")
            candidate = candidate_window(obj.read_bytes(), target.candidate_symbol)
            if candidate["thumb"]:
                raise ValueError("candidate ARM/Thumb mode differs from A32 reference")
            measured = window_check.analyse(reference, obj, address, target.candidate_symbol)
            if (measured["reference_sha256"] != reference_sha
                    or measured["original_window_sha256"] != _digest(original)):
                raise ValueError("window checker resolved a different reference window")
            row.update({key: measured[key] for key in (
                "status", "original_bytes", "candidate_bytes", "original_window_sha256",
                "candidate_window_sha256", "call_sites")})
            original_calls = {hex(site["address"])
                              for site in window_check.call_sites(original, address)}
            for call in row["call_sites"]:
                if (call["linked_target"] is not None
                        and call["address"] not in original_calls):
                    # The checker decodes the original word at every candidate
                    # BL, even if that original word is not a BL. Its arithmetic
                    # identity must not certify an unrelated instruction as a call.
                    call["linked_target"] = None
                    call["arm_link_formula_reproduces_original_call"] = False
            row["candidate_object_sha256"] = _digest(obj.read_bytes())
            if row["status"] == "MATCHED_NORMALIZED_CALLS":
                if (not _only_call_displacements_differ(original, candidate["bytes"])
                        or not all(c["arm_link_formula_reproduces_original_call"]
                                   for c in row["call_sites"])):
                    row["status"] = "DIFFERENT"
        except (OSError, ValueError, ELFError, SystemExit, subprocess.SubprocessError) as exc:
            # Parser/compiler diagnostics may contain input or source excerpts.
            # Keep the JSON metadata-only even on exceptional paths.
            if isinstance(exc, ELFError):
                error = "malformed ELF metadata"
            elif isinstance(exc, subprocess.SubprocessError):
                error = "compiler/comparison subprocess failed"
            else:
                error = str(exc)
            row.update(status="ERROR", error=error)
    return report


def exit_code(report: dict) -> int:
    # DIFFERENT is a completed measurement, not a build/symbol failure.
    return 1 if any(row["status"] == "ERROR" for row in report["rows"]) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reference", type=Path, default=ROOT / REFERENCE)
    parser.add_argument("--ndk", type=Path, default=ROOT / NDK,
                        help="explicit existing NDK override; never downloads tools")
    args = parser.parse_args(argv)
    compiler = args.ndk.resolve() / GXX
    if not compiler.is_file():
        print("toolchain absent: expected existing NDK r8e GCC 4.4.3", file=sys.stderr)
        return 2
    target = args.out.resolve()
    if not target.is_relative_to(ROOT / ".cache") or target.suffix != ".json":
        print("output must be JSON inside repository .cache", file=sys.stderr)
        return 2
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as work:
            report = run_report(args.reference.resolve(), compiler, Path(work))
            pending = Path(work) / "report.json"
            pending.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            pending.replace(target)
        print(f"report: {target}; {sum(r['status'] == 'ERROR' for r in report['rows'])} error rows")
        return exit_code(report)
    except ELFError:
        print("seven-window: malformed reference ELF metadata", file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"seven-window: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
