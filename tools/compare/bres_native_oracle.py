#!/usr/bin/env python3
"""Offline research only: execute bounded original ARM BRES Init() branch.
No engine startup, no Android services, no production dependency or code hooks.
"""
import hashlib
import json
import pathlib
import struct
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

ROOT = pathlib.Path(__file__).resolve().parents[2]
BINARY = ROOT / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
SPECIMEN = ROOT / "research/assets/premium_01_room_a.bdae.specimen"
BINARY_HASH = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
SPECIMEN_HASH = "7443fa93cddec857399170024f1f7cfb47d2bcd89ab29baed10b7ec4496b0fc7"


def run(data, base):
    u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    with BINARY.open("rb") as f:
        for seg in ELFFile(f).iter_segments():
            if seg["p_type"] == "PT_LOAD":
                lo = seg["p_vaddr"] & ~4095
                hi = (seg["p_vaddr"] + seg["p_memsz"] + 4095) & ~4095
                u.mem_map(lo, hi - lo)
                u.mem_write(seg["p_vaddr"], seg.data())
    u.mem_map(base, 0x20000)
    u.mem_map(0x3000000, 0x10000)
    obj, stop = base + 0x10000, 0x300fffc
    u.mem_write(base, data)
    # Minimal res::File object: base pointer, no split relocation/string buffers.
    u.mem_write(obj, struct.pack("<I", base) + bytes(60))
    def invoke():
        u.reg_write(UC_ARM_REG_R0, obj)
        u.reg_write(UC_ARM_REG_SP, 0x3008000)
        u.reg_write(UC_ARM_REG_LR, stop)
        u.emu_start(0x69c6f0, stop, count=100000)
        assert u.reg_read(UC_ARM_REG_PC) == stop, "instruction limit exhausted"
        return u.reg_read(UC_ARM_REG_R0)
    result = invoke()
    actual = bytes(u.mem_read(base, len(data)))
    second = invoke()
    assert actual == bytes(u.mem_read(base, len(data))), "second call changed initialized asset"
    return result, second, actual


def main():
    assert hashlib.sha256(BINARY.read_bytes()).hexdigest() == BINARY_HASH
    data = SPECIMEN.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SPECIMEN_HASH
    count, table = struct.unpack_from("<I", data, 16)[0], struct.unpack_from("<I", data, 24)[0]
    slots = struct.unpack_from(f"<{count}I", data, table)
    results = []
    for base in (0x02000000, 0x02100000, 0x02200000, 0x82000000):
        rc, second_rc, actual = run(data, base)
        expected = bytearray(data)
        struct.pack_into("<H", expected, 6, struct.unpack_from("<H", data, 6)[0] | 0x8000)
        struct.pack_into("<I", expected, 24, base + table)
        for i, slot in enumerate(slots):
            struct.pack_into("<I", expected, table + 4 * i, base + slot)
            if i:
                struct.pack_into("<I", expected, slot, base + struct.unpack_from("<I", data, slot)[0])
        assert rc == second_rc == 0
        assert actual == expected, "original ARM disagrees with independently stated relocation rules"
        results.append({"base": base, "return": rc, "second_return": second_rc,
                        "output_sha256": hashlib.sha256(actual).hexdigest(),
                        "relocations": [{"index": i, "slot": s,
                                         "table_after": struct.unpack_from("<I", actual, table + 4 * i)[0],
                                         "slot_after": struct.unpack_from("<I", actual, s)[0]}
                                        for i, s in enumerate(slots)]})
    header_cases = []
    # Contiguous Init checks magic and flag bit15; it does not inspect +4/+8.
    for offset, width, value in [(4, "H", 0), (4, "H", 0xffff), (8, "I", 0),
                                 (8, "I", 0xffffffff), (6, "H", 0x7fff),
                                 (6, "H", 0x8000), (6, "H", 0xffff)]:
        changed = bytearray(data)
        struct.pack_into("<" + width, changed, offset, value)
        rc, second, actual = run(bytes(changed), 0x02000000)
        assert rc == second == 0
        if offset == 6 and value & 0x8000:
            assert actual == changed
        else:
            expected = bytearray(changed)
            struct.pack_into("<H", expected, 6, struct.unpack_from("<H", changed, 6)[0] | 0x8000)
            struct.pack_into("<I", expected, 24, 0x02000000 + table)
            for i, slot in enumerate(slots):
                struct.pack_into("<I", expected, table + 4*i, 0x02000000 + slot)
                if i:
                    struct.pack_into("<I", expected, slot, 0x02000000 + struct.unpack_from("<I", changed, slot)[0])
            assert actual == expected
        header_cases.append({"offset": offset, "value": value, "return": rc,
                             "output_sha256": hashlib.sha256(actual).hexdigest()})
    # A safe synthetic table repeats one non-table slot: original mutates in order.
    synthetic = bytearray(128)
    synthetic[:4] = b"BRES"
    for offset, value in [(12,128),(16,3),(24,60),(28,0),(60,24),(64,28),(68,28)]:
        struct.pack_into("<I", synthetic, offset, value)
    rc, second, actual = run(bytes(synthetic), 0x02000000)
    assert rc == second == 0
    assert struct.unpack_from("<I", actual, 28)[0] == 0x04000000
    alias_case = {"input": "synthetic128bytes; count3; table60; slots24,28,28; slot28initial0",
                  "return": rc, "slot28_after": 0x04000000,
                  "scope": "zero raw targets become base; duplicate slots accumulate sequential additions"}
    bad = b"FAIL" + data[4:]
    rc, second, actual = run(bad, 0x02000000)
    assert rc == second == 0xffffffff and actual == bad
    report = {"binary_sha256": BINARY_HASH, "input_sha256": SPECIMEN_HASH,
              "entry_elf_va": "0x69c6f0", "entry_file_offset": "0x69c6f0", "mode": "ARM",
              "oracle": "Unicorn 2.1.4", "code_hooks": [], "external_calls": [],
              "elf_relocations_applied": False,
              "scope": "res::File::Init() object+8=0 contiguous buffer branch; real specimen at three bases; repeated initialization; invalid magic. Full loader and split/string paths excluded.",
              "bad_magic_return": rc, "cases": results, "header_cases": header_cases, "alias_case": alias_case}
    path = ROOT / "research/assets/BRES_native_verification.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: original ARM Init():4 base addresses x253 relocations + idempotence;7 header cases;duplicate/zero target;bad magic=-1;no code hooks")


if __name__ == "__main__":
    main()
