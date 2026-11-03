#!/usr/bin/env python3
"""Read-only, counts-only worksheet for four settled Premium residuals.

This never compares a candidate, compiles code, or establishes a match. ``ok``
means only that the named .symtab STT_FUNC has the cited A32 address and size.
The title of task #173 says five residuals; its objective and NEXT.md name four
functions, recorded once each below. Cited sizes are original sizes, not those
of the independently compiled candidates.

``literal_subset_count`` counts unique, complete in-window targets returned by
window_diff._pc_relative_ldr_targets whose unsigned little-endian value is
strictly below 0x10000. This is a syntactic subset, not a complete literal-pool
or control-flow analysis. ``directive_word_count`` counts A32 encoding patterns
for SVC, BKPT, PLD (immediate/register) and WFI (hint), excluding those literal
targets. Neither count identifies instructions actually executed. No words,
mnemonics, target lists or byte excerpts are emitted. Unavailable counts and
sizes are -1; changed bounds/mode are stale and never scanned as A32.

Only the requested JSON under this checkout's .cache is written. If the local
reference is absent the report says NO NEW EVIDENCE, with four missing rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

# Suppress import-cache writes outside .cache during standalone execution.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nova3_team.indexer import LedgerError, _symbols  # noqa: E402
from nova3_team.object_compare import CompareError, _read, _span  # noqa: E402
from tools.compare.window_diff import _pc_relative_ldr_targets  # noqa: E402

SCHEMA = "nova3.a32-window-mask/1"
REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
REFERENCE_RELATIVE = Path("analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so")
# (ELF virtual address, exact .symtab STT_FUNC name, cited original size).
# Sources: NEXT.md's settled residuals; the four corresponding compare pilots.
TARGETS = (
    (0x006E7E60,
     "_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_", 564),
    (0x0073F0A4,
     "_ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv", 228),
    (0x000D2F98, "android_cpuInit", 2632),
    (0x00108C8C, "_ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE", 240),
)


def default_reference() -> Path:
    local = ROOT / REFERENCE_RELATIVE
    if local.exists():
        return local
    return Path.home() / "Projects/nova3_re" / REFERENCE_RELATIVE


def _censored_encoding(value: int) -> bool:
    """Encoding patterns only; no disassembler or original bytes in output."""
    conditional = value >> 28 != 0xF
    return (
        (conditional and value & 0x0F000000 == 0x0F000000)  # SVC
        or value & 0xFFF000F0 == 0xE1200070                # BKPT
        or value & 0xFF70F000 == 0xF550F000                # PLD immediate
        or value & 0xFF70F010 == 0xF750F000                # PLD register
        or (conditional and value & 0x0FFFFFFF == 0x0320F003)  # WFI hint
    )


def count_window(window: bytes, address: int) -> tuple[int, int]:
    if address % 4 or len(window) % 4:
        raise ValueError("A32 window must be four-byte aligned")
    literals = _pc_relative_ldr_targets(window, address, 4)
    # Keep the completeness bound explicit even if the shared helper changes.
    literals = {i for i in literals if 0 <= i * 4 <= len(window) - 4}
    subset = sum(int.from_bytes(window[i * 4:i * 4 + 4], "little") < 0x10000
                 for i in literals)
    directives = sum(
        _censored_encoding(int.from_bytes(window[i:i + 4], "little"))
        for i in range(0, len(window), 4) if i // 4 not in literals
    )
    return subset, directives


def _row(data: bytes | None, observations: list[dict], target: tuple) -> dict:
    address, name, cited_size = target
    row = dict(address=f"0x{address:08x}", symbol=name, symtab_size=-1,
               cited_size=cited_size, size_agreement="unknown", mode="UNKNOWN",
               literal_subset_count=-1, directive_word_count=-1,
               verdict="missing", reason="reference_absent", declaration="NO MATCH")
    if data is None:
        return row
    # The ledger parser retains table identity and validates executable section
    # extents. A dynsym alias or neighbour never substitutes for this exact name.
    matches = [s for s in observations
               if s["table"] == ".symtab" and s["name"] == name]
    if not matches:
        row["reason"] = "symbol_absent"
        return row
    if len(matches) != 1:
        row.update(verdict="stale", reason="ambiguous_symbol")
        return row
    symbol = matches[0]
    row.update(symtab_size=symbol["size"],
               size_agreement="yes" if symbol["size"] == cited_size else "no",
               mode="Thumb" if symbol["thumb"] else "ARM")
    if symbol["address"] != address:
        row.update(verdict="stale", reason="address_disagrees")
    elif symbol["size"] != cited_size or cited_size <= 0 or cited_size % 4:
        row.update(verdict="stale", reason="size_disagrees")
    elif symbol["thumb"] or address % 4:
        row.update(verdict="stale", reason="not_a32")
    elif any(s["address"] == address and
             (s["size"] != cited_size or s["thumb"] != symbol["thumb"] or
              s["file_offset"] != symbol["file_offset"]) for s in observations):
        row.update(verdict="stale", reason="conflicting_bounds_or_mode")
    else:
        window = _span(data, symbol["file_offset"], cited_size)
        subset, directives = count_window(window, address)
        row.update(literal_subset_count=subset, directive_word_count=directives,
                   verdict="ok", reason="cited_bounds_agree")
    return row


def worksheet(data: bytes | None) -> dict:
    observations = []
    sha256 = "UNAVAILABLE"
    if data is not None:
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != REFERENCE_SHA256:
            raise ValueError("Premium reference SHA-256 mismatch")
        metadata, observations, _ = _symbols(data)
        if metadata["endianness"] != "little":
            raise ValueError("reference must be little-endian ARM32")
    rows = [_row(data, observations, target) for target in TARGETS]
    return dict(schema=SCHEMA, reference_sha256=sha256, declaration="NO MATCH",
                evidence_status=("COUNTS_RECORDED" if any(r["verdict"] == "ok" for r in rows)
                                 else "NO NEW EVIDENCE"), rows=rows)


def _output_path(path: Path) -> Path:
    # Do not resolve the cache root to an arbitrary symlink destination.
    cache = ROOT / ".cache"
    target = path.expanduser().resolve()
    if (cache.resolve() != cache or not target.is_relative_to(cache)
            or target == cache or target.suffix != ".json"):
        raise ValueError("output must be JSON under this checkout's .cache")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="local Premium ELF (read only)")
    parser.add_argument("--out", type=Path, required=True, help="JSON under .cache")
    args = parser.parse_args(argv)
    try:
        target = _output_path(args.out)
        reference = args.reference if args.reference is not None else default_reference()
        # A caller may name a reference through a .json path or symlink inside
        # .cache. Atomic replacement must never replace that reference itself.
        if reference.resolve() == target:
            raise ValueError("reference and output must be distinct paths")
        try:
            data = _read(reference)
        except FileNotFoundError:
            data = None
        report = worksheet(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Replacement avoids modifying another file through an existing hardlink.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=".a32-mask-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        for row in report["rows"]:
            print(f"{row['address']} {row['verdict']}: NOT A MATCH")
        print(report["evidence_status"])
    except (OSError, ValueError, CompareError, LedgerError):
        # Do not echo untrusted ELF contents or paths to public logs.
        print("a32 worksheet failed: check reference identity and .cache output", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
