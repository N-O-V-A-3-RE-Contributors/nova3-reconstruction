"""Optional IDA 9.4+ idalib metadata-only exporter. Local use only.

Use a saved .i64 database or a COPY of a reference .so under ignored .cache.
Never run this against a source .so in analysis/ or originals/, and never
upload the output file. All address mapping remains unverified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import idapro  # Per Hex-Rays: import before ANY other ida_* module.
import ida_auto
import ida_funcs
import ida_name
import ida_nalt
import idautils


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path,
                        help="Existing .i64, or a disposable ELF copy under .cache")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=256)
    parser.add_argument("--analyze", action="store_true",
                        help="Explicitly run full IDA auto-analysis (can be slow on 8 GB)")
    args = parser.parse_args()
    source = args.database.expanduser().resolve(strict=True)
    target = args.out.expanduser().resolve()
    if not 1 <= args.limit <= 100000:
        parser.error("--limit must be 1..100000")
    if source.suffix.lower() in (".so", ".apk", ".elf") and ".cache" not in source.parts:
        parser.error("stage a COPY of the reference ELF in .cache before IDA import")
    if not any(part in (".cache", "generated", "research") for part in target.parts):
        parser.error("output must remain under private .cache/generated/research")
    if source == target:
        parser.error("refusing to overwrite input")
    idapro.open_database(str(source), run_auto_analysis=args.analyze)
    if args.analyze:
        ida_auto.auto_wait()
    base = int(ida_nalt.get_imagebase())
    rows = []
    for ea in idautils.Functions():
        if len(rows) >= args.limit:
            break
        func = ida_funcs.get_func(ea)
        if func is None or int(func.start_ea) != int(ea):
            continue
        chunks = [[int(lo), int(hi)] for lo, hi in idautils.Chunks(ea)]
        rows.append(dict(ida_entry=int(ea), image_relative=int(ea) - base,
                         name=ida_name.get_name(ea), chunks=chunks,
                         thumb=None, byte_mapping_verified=False,
                         inferred_translation_unit=None))
    reference = dict(
        schema="nova3.ida-headless-boundaries/1",
        input_name=source.name,
        input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        input_kind="database_or_disposable_binary",
        image_base=base, functions=rows,
        autoanalysis_requested=args.analyze,
        original_so_byte_match_verified=False,
        classification="TOOL_IDENTIFIED_NOT_RECONSTRUCTED")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print("IDA headless metadata: %d functions -> %s" % (len(rows), target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
