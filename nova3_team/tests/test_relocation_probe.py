"""Source-independent ELF fixtures; no proprietary instructions or tool requirement."""
import contextlib
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest

from nova3_team import relocation_probe as rp


ADDRESS = 0x4000
TARGETS = {"service": 0x3f00, "state": 0x8800, "initializer": 0x4210}
# Synthetic BL with implicit -8, followed by two synthetic REL32 addends.
RAW = struct.pack("<III", 0xebfffffe, 16, 0xfffffff4)
# Independent arithmetic: service-(P+8), state+16-P, initializer-12-P.
RESOLVED = struct.pack("<III", 0xebffffbe, 0x480c, 0x1fc)


def fixture(*, linked=False, text=RAW, thumb=False, relocations=None,
            prefix=0, ehabi=False):
    """Minimal EABI5 ELF with deliberately different VA/file mappings."""
    names = b"\0source_fn\0service\0state\0initializer\0"
    value = (ADDRESS if linked else prefix) | int(thumb)
    symbols = bytes(16) + struct.pack("<IIIBBH", 1, value, len(text), 0x12, 0, 1)
    for name in ("service", "state", "initializer"):
        symbols += struct.pack("<IIIBBH", names.index(name.encode()), 0, 0, 0x10, 0, 0)
    symbols += struct.pack("<IIIBBH", 0, 0, 0, 3, 0, 1)  # STT_SECTION .text
    desc = [
        [".text", 1, 6, ADDRESS - prefix if linked else 0,
         bytes(prefix) + text, 0, 0, 4, 0],
        [".strtab", 3, 0, 0, names, 0, 0, 1, 0],
        [".symtab", 2, 0, 0, symbols, 2, 1, 4, 16],
    ]
    if not linked:
        if relocations is None:
            relocations = [(prefix, 2, 28), (prefix + 4, 3, 3), (prefix + 8, 4, 3)]
        records = b"".join(struct.pack("<II", offset, sym << 8 | kind)
                           for offset, sym, kind in relocations)
        desc.append([".rel.text", 9, 0, 0, records, 3, 1, 4, 8])
        if ehabi:
            index = len(desc) + 1
            desc.append([".ARM.exidx", 0x70000001, 2, 0, bytes(8), 1, 0, 4, 0])
            desc.append([".rel.ARM.exidx", 9, 0, 0,
                         struct.pack("<II", 0, 5 << 8 | 42), 3, index, 4, 8])
    shnames = b"\0" + b"".join(d[0].encode() + b"\0" for d in desc) + b".shstrtab\0"
    desc.append([".shstrtab", 3, 0, 0, shnames, 0, 0, 1, 0])
    buf = bytearray(84 if linked else 52)
    sections = [tuple([0] * 10)]
    for name, kind, flags, va, content, link, info, align, entsize in desc:
        buf.extend(bytes(-len(buf) % align))
        offset = len(buf)
        buf.extend(content)
        sections.append((shnames.index(name.encode()), kind, flags, va, offset,
                         len(content), link, info, align, entsize))
    buf.extend(bytes(-len(buf) % 4))
    shoff = len(buf)
    for section in sections:
        buf.extend(struct.pack("<10I", *section))
    buf[:52] = b"\x7fELF\x01\x01\x01" + bytes(9) + struct.pack(
        "<HHIIIIIHHHHHH", 2 if linked else 1, 40, 1,
        ADDRESS if linked else 0, 52 if linked else 0, shoff, 0x05000000,
        52, 32 if linked else 0, int(linked), 40, len(sections), len(sections) - 1)
    if linked:
        offset, size = sections[1][4], sections[1][5]
        buf[52:84] = struct.pack("<8I", 1, offset, ADDRESS - prefix,
                                 ADDRESS - prefix, size, size, 5, 4)
    return bytes(buf)


def run_probe(*, obj=None, reference=None, linked=None, targets=None):
    ref = fixture(linked=True, text=RESOLVED) if reference is None else reference
    return rp.probe(ref, fixture() if obj is None else obj, address=ADDRESS,
                    symbol="source_fn", targets=TARGETS if targets is None else targets,
                    expected_reference_sha256=hashlib.sha256(ref).hexdigest(),
                    expected_window_sha256=hashlib.sha256(RESOLVED).hexdigest(), linked=linked)


class RelocationProbeTests(unittest.TestCase):
    def test_complete_unmasked_linked_comparison_has_independent_grades(self):
        r = run_probe(linked=fixture(linked=True, text=RESOLVED))
        self.assertEqual(r["raw"]["status"], "RAW_MISMATCH")
        self.assertEqual(r["relocated_linked_window"], "RELOCATED_LINKED_WINDOW_MATCH")
        self.assertEqual(r["raw"]["compared_bytes"], 12)
        self.assertEqual(r["linked_vs_reference"]["compared_bytes"], 12)
        self.assertEqual(r["linked_vs_reference"]["differing_byte_count"], 0)
        self.assertEqual(r["original_object_match"], "NOT_AVAILABLE")
        for flag in ("historical_compiler_verified", "whole_so_sha_match", "behavior_verified",
                     "original_prelink_relocations_recovered"):
            self.assertFalse(r[flag])
        def check_metadata(value):
            self.assertNotIsInstance(value, (bytes, bytearray))
            if isinstance(value, dict):
                self.assertTrue({"bytes", "code", "encoded", "word"}.isdisjoint(value))
                for item in value.values():
                    check_metadata(item)
            elif isinstance(value, list):
                for item in value:
                    check_metadata(item)
        check_metadata(r)

    def test_implicit_addends_and_nonzero_section_offset(self):
        obj = fixture(prefix=8)
        window, relocs, _ = rp.object_relocations(obj, "source_fn")
        code, rows = rp.relocate(window["bytes"], relocs, ADDRESS, TARGETS)
        self.assertEqual(code, RESOLVED)
        self.assertEqual([r["addend"] for r in rows], [-8, 16, -12])
        self.assertEqual([r["offset"] for r in rows], [0, 4, 8])
        self.assertEqual([r["section_offset"] for r in rows], [8, 12, 16])
        self.assertEqual(run_probe(obj=obj)["resolved"]["status"], "RESOLVED_WINDOW_EQUAL")

    def test_without_linked_elf_never_awards_linked_grade(self):
        r = run_probe()
        self.assertEqual(r["resolved"]["status"], "RESOLVED_WINDOW_EQUAL")
        self.assertEqual(r["relocated_linked_window"], "NOT_CHECKED")

    def test_wrong_target_and_linker_disagreement_are_not_hidden(self):
        wrong = dict(TARGETS, state=TARGETS["state"] + 4)
        r = run_probe(targets=wrong, linked=fixture(linked=True, text=RESOLVED))
        self.assertFalse(r["resolved"]["equal"])
        self.assertTrue(r["linked_vs_reference"]["equal"])
        self.assertEqual(r["relocated_linked_window"], "LINKER_DISAGREES")
        changed = bytes([RESOLVED[0] ^ 1]) + RESOLVED[1:]
        r = run_probe(linked=fixture(linked=True, text=changed))
        self.assertEqual(r["linked_vs_reference"]["differing_offsets"], [0])
        self.assertEqual(r["relocated_linked_window"], "LINKER_DISAGREES")

    def test_nonrelocation_difference_still_fails_the_complete_window(self):
        original = fixture(linked=True, text=RESOLVED + b"abce")
        linked = fixture(linked=True, text=RESOLVED + b"abcd")
        r = rp.probe(original, fixture(text=RAW + b"abcd"), address=ADDRESS,
                     symbol="source_fn", targets=TARGETS,
                     expected_reference_sha256=rp.sha256(original),
                     expected_window_sha256=rp.sha256(RESOLVED + b"abce"), linked=linked)
        self.assertTrue(r["linker_vs_resolved"]["equal"])
        self.assertEqual(r["linked_vs_reference"]["compared_bytes"], 16)
        self.assertEqual(r["linked_vs_reference"]["differing_offsets"], [15])
        self.assertEqual(r["relocated_linked_window"], "RELOCATED_LINKED_WINDOW_MISMATCH")

    def test_ehabi_is_inventory_only_and_names_its_actual_section_symbol(self):
        r = run_probe(obj=fixture(ehabi=True))
        self.assertEqual(r["outside_function_relocations"], [dict(
            section=".ARM.exidx", section_offset=0, type="R_ARM_PREL31", symbol=".text")])
        self.assertEqual(len(r["relocations"]), 3)

    def test_wrong_relocation_offsets_indices_and_duplicate_sites_rejected(self):
        cases = [([(12, 2, 28)], "offset"), ([(1, 2, 28)], "offset"),
                 ([(0, 99, 28)], "symbol index"),
                 ([(0, 2, 28), (0, 2, 28)], "overlapping"),
                 ([(0, 2, 2)], "unsupported"),
                 ([(0, 1, 28)], "external symbols"),
                 ([(4, 2, 28)], "A32 BL")]
        for relocs, message in cases:
            with self.subTest(relocs=relocs), self.assertRaisesRegex(ValueError, message):
                if message == "A32 BL":
                    run_probe(obj=fixture(relocations=relocs), targets={"service": 0x3f00})
                else:
                    rp.object_relocations(fixture(relocations=relocs), "source_fn")

    def test_unresolved_unused_and_out_of_range_symbol_maps_rejected(self):
        for targets in ({}, dict(TARGETS, typo=1), dict(TARGETS, state=-1),
                        dict(TARGETS, state=1 << 32), dict(TARGETS, state=True)):
            with self.subTest(targets=targets), self.assertRaises(rp.ProbeError):
                run_probe(targets=targets)

    def test_thumb_input_and_call_targets_rejected(self):
        with self.assertRaisesRegex(rp.ProbeError, "A32"):
            rp.object_relocations(fixture(thumb=True), "source_fn")
        with self.assertRaisesRegex(rp.ProbeError, "Thumb"):
            run_probe(reference=fixture(linked=True, text=RESOLVED, thumb=True))
        with self.assertRaisesRegex(rp.ProbeError, "Thumb"):
            run_probe(linked=fixture(linked=True, text=RESOLVED, thumb=True))
        with self.assertRaisesRegex(rp.ProbeError, "interworking"):
            run_probe(targets=dict(TARGETS, service=0x3f01))
        with self.assertRaisesRegex(rp.ProbeError, "unaligned"):
            run_probe(targets=dict(TARGETS, service=0x3f02))
        for word in (0xfa000000, 0xea000000, 0x00000000):
            with self.subTest(word=word), self.assertRaisesRegex(rp.ProbeError, "A32 BL"):
                run_probe(obj=fixture(text=struct.pack("<I", word) + RAW[4:]))

    def test_branch_range_limits_and_no_extra_pipeline_bias(self):
        reloc = [dict(offset=0, type="R_ARM_CALL", symbol="service")]
        place = 0x04000000
        for delta in (-(1 << 25), (1 << 25) - 4, 0):
            target = place + 8 + delta
            code, _ = rp.relocate(RAW[:4], reloc, place, {"service": target})
            encoded = struct.unpack("<I", code)[0] & 0xffffff
            self.assertEqual(rp.signed(encoded << 2, 26), delta)
        for delta in (-(1 << 25) - 4, 1 << 25):
            with self.assertRaisesRegex(rp.ProbeError, "overflow"):
                rp.relocate(RAW[:4], reloc, place, {"service": place + 8 + delta})

    def test_rel32_modulo_arithmetic_retains_odd_symbol_value(self):
        reloc = [dict(offset=0, type="R_ARM_REL32", symbol="data")]
        code, _ = rp.relocate(struct.pack("<I", 0xfffffff0), reloc, 0xfffffff0,
                              {"data": 1})
        self.assertEqual(struct.unpack("<I", code)[0], 1)
        # STT_FUNC differs from an odd data address: strip T before addition,
        # then OR it back, including when the implicit addend is odd.
        code, _ = rp.relocate(struct.pack("<I", 1),
                              [dict(reloc[0], symbol_type=2)], ADDRESS, {"data": 0x4201})
        self.assertEqual(struct.unpack("<I", code)[0], 0x201)

    def test_non_elf_wrong_abi_linked_object_and_truncation_rejected(self):
        for data in (b"not ELF", fixture()[:51], fixture()[:-1],
                     fixture(linked=True, text=RESOLVED)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                rp.object_relocations(data, "source_fn")
        for offset, fmt, value in ((4, "B", 2), (5, "B", 2), (18, "H", 3),
                                   (36, "I", 0x04000000), (46, "H", 0), (50, "H", 99)):
            bad = bytearray(fixture())
            struct.pack_into("<" + fmt, bad, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                rp.object_relocations(bytes(bad), "source_fn")

    def test_reference_and_window_hash_gates(self):
        ref, obj = fixture(linked=True, text=RESOLVED), fixture()
        for a, b in (("0" * 64, rp.sha256(RESOLVED)), (rp.sha256(ref), "0" * 64)):
            with self.assertRaisesRegex(rp.ProbeError, "SHA-256"):
                rp.probe(ref, obj, address=ADDRESS, symbol="source_fn", targets=TARGETS,
                         expected_reference_sha256=a, expected_window_sha256=b)

    def test_independent_segment_mapping_gate(self):
        bad = bytearray(fixture(linked=True, text=RESOLVED))
        struct.pack_into("<I", bad, 52 + 4, 80)  # disagrees with section's offset 84
        with self.assertRaisesRegex(rp.ProbeError, "mappings disagree"):
            run_probe(reference=bytes(bad))
        for offset, value in ((52, 0), (52 + 24, 4), (52 + 16, 0)):
            bad = bytearray(fixture(linked=True, text=RESOLVED))
            struct.pack_into("<I", bad, offset, value)
            with self.subTest(offset=offset), self.assertRaises(rp.ProbeError):
                run_probe(reference=bytes(bad))

    def test_direct_api_bounds_and_duplicate_gates(self):
        r = dict(offset=0, type="R_ARM_REL32", symbol="data")
        for address in (-4, 1, 1 << 32, True, 0xfffffffc):
            with self.subTest(address=address), self.assertRaises(rp.ProbeError):
                rp.relocate(bytes(8), [r], address, {"data": 1})
        for offset in (-4, 1, 8):
            with self.subTest(offset=offset), self.assertRaises(rp.ProbeError):
                rp.relocate(bytes(8), [dict(r, offset=offset)], ADDRESS, {"data": 1})
        with self.assertRaisesRegex(rp.ProbeError, "overlapping"):
            rp.relocate(bytes(8), [r, r], ADDRESS, {"data": 1})

    def test_cli_emits_metadata_and_rejects_duplicate_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.joinpath("ref").write_bytes(fixture(linked=True, text=RESOLVED))
            root.joinpath("obj").write_bytes(fixture())
            args = ["--reference", str(root / "ref"), "--object", str(root / "obj"),
                    "--address", hex(ADDRESS), "--symbol", "source_fn",
                    "--expected-reference-sha256", rp.sha256((root / "ref").read_bytes()),
                    "--expected-window-sha256", rp.sha256(RESOLVED)]
            for name, value in TARGETS.items():
                args += ["--target", f"{name}={value}"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(rp.main(args), 0)
            self.assertIn('"RAW_MISMATCH"', output.getvalue())
            for extra in (["--target", "service=0"], ["--target", "bad"],
                          ["--target", "=3"]):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(rp.main(args + extra), 2)


if __name__ == "__main__":
    unittest.main()
