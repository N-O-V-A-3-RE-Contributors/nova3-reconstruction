"""IDA Pro optional local function-boundary exporter for NOVA3 research.

Run inside an already loaded IDA database with File > Script file, or via the
installed IDA's supported -S switch. Output is metadata only, never code bytes.
IDA's rebasing/segment mapping MUST be checked against the actual ELF before
feeding these boundaries into the object matching pipeline.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

import ida_funcs
import ida_name
import ida_nalt
import idautils


def main():
    local_out = os.environ.get("NOVA3_IDA_INDEX_OUT")
    if not local_out:
        raise RuntimeError("Set NOVA3_IDA_INDEX_OUT to an offline output path")
    out = Path(local_out).expanduser().resolve()
    # IDA automation is optional. Reject paths outside ignored/private research.
    if not any(part in (".cache", "generated", "research") for part in out.parts):
        raise RuntimeError("IDA metadata output must be in private research/.cache/generated")
    input_path = Path(ida_nalt.get_input_file_path())
    binary_hash = None
    if input_path.is_file():
        binary_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    image_base = int(ida_nalt.get_imagebase())
    functions = []
    for address in idautils.Functions():
        func = ida_funcs.get_func(address)
        if func is None or int(func.start_ea) != int(address):
            continue
        chunks = [[int(start), int(end)] for start, end in idautils.Chunks(address)]
        # The function extent may be non-contiguous. Never merge chunks into
        # a purported original .o section or assume Thumb state from naming.
        functions.append(dict(
            ida_entry=int(address),
            image_relative_entry=int(address) - image_base,
            name=ida_name.get_name(address),
            chunks=chunks,
            thumb=None,
            original_translation_unit=None,
            mapping_verified=False,
        ))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(
        schema="nova3.ida-boundaries/1", binary_sha256=binary_hash,
        image_base=image_base, address_space="IDA database",
        confidence="TOOL_IDENTIFIED_NOT_BYTE_VERIFIED",
        functions=functions), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("NOVA3 IDA metadata exported:", len(functions), str(out))


if __name__ == "__main__":
    main()
