#!/usr/bin/env python3
"""Run bounded structural checks against the real local BRES specimen."""
import struct
from pathlib import Path
from bres_inspect import inspect

path = Path("research/assets/premium_01_room_a.bdae.specimen")
data = path.read_bytes()
r = inspect(data)
assert r["sha256"] == "7443fa93cddec857399170024f1f7cfb47d2bcd89ab29baed10b7ec4496b0fc7"
assert r["size"] == r["header_u32"]["0x0c"] == 5204
assert r["relocation_count"] == r["unique_slot_count"] == 253
assert r["relocations"][0] == {"index": 0, "slot_offset": 24, "raw_target": 60}
assert all(x["slot_offset"] % 4 == 0 and x["raw_target"] < len(data) for x in r["relocations"])
assert len(r["string_region_inference"]) == 73
# Native FileReader Init reads count(+0x30)*8 bytes of descriptors at tail.
tail = len(data) - r["header_u32"]["0x2c"]
assert tail == 0x1268
size_a, ptr_a, size_b, ptr_b = struct.unpack_from("<4I", data, tail)
assert (size_a, ptr_a, size_b, ptr_b) == (436, 0x1278, 40, 0x142c)
assert ptr_a + size_a == ptr_b and ptr_b + size_b == len(data)
failures = [data[:59], b"FAIL" + data[4:]]
for offset, value in [(0x10, 0xffffffff), (0x18, len(data)), (60, len(data) - 3)]:
    bad = bytearray(data)
    struct.pack_into("<I", bad, offset, value)
    failures.append(bytes(bad))
for bad in failures:
    try:
        inspect(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed input accepted")
print("PASS: real BRES size/hash,253 relocation slots,73 string records,2 contiguous tail buffers;5 malformed inputs rejected")
