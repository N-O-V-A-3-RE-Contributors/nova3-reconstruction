#!/usr/bin/env python3
"""Read-only BRES structural research inspector; never relocates asset bytes."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def inspect(data):
    if len(data) < 60 or data[:4] != b"BRES":
        raise ValueError("requires at least 60 bytes and BRES magic")
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    count, table = u32(0x10), u32(0x18)
    if table > len(data) or count > (len(data) - table) // 4:
        raise ValueError("relocation table outside specimen")
    slots = [u32(table + 4 * i) for i in range(count)]
    if any(slot > len(data) - 4 for slot in slots):
        raise ValueError("relocation slot outside specimen")
    strings = []
    cursor, end = u32(0x1c), u32(0x20)
    if cursor <= end <= len(data):
        while cursor < end:
            if cursor + 4 > end:
                raise ValueError("truncated string length")
            n = u32(cursor)
            if n > end - cursor - 4:
                raise ValueError("string bytes outside region")
            raw = data[cursor + 4:cursor + 4 + n]
            strings.append({"length_offset": cursor, "data_offset": cursor + 4,
                            "length": n, "utf8": raw.decode("utf-8", "backslashreplace")})
            cursor = (cursor + 4 + n + 3) & ~3
        if cursor != end:
            raise ValueError("string alignment exceeds region")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "header_u32": {f"0x{i:02x}": u32(i) for i in range(0, 60, 4)},
            "relocation_count": count, "relocation_table_offset": table,
            "unique_slot_count": len(set(slots)),
            "relocations": [{"index": i, "slot_offset": slot, "raw_target": u32(slot)}
                            for i, slot in enumerate(slots)],
            "string_region_inference": strings,
            "scope": "structural observations; no full Collada object semantics recovered"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("specimen", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.specimen.read_bytes()), indent=2))
