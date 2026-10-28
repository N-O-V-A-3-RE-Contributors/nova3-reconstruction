"""Synthetic-only ELF32 ARM object-window regression tests."""
import hashlib
import struct
import unittest

from nova3_team import object_compare


def elf32(*, relocatable=False, text=b"\x00" * 16, thumb=True, relocation=False,
          symbol_name="source_fn"):
    """Independent executable and relocatable ELF fixtures; no game instructions."""
    names = b"\0.text\0.strtab\0.symtab\0.rel.text\0.shstrtab\0"
    symbol_names = b"\0" + symbol_name.encode() + b"\0"
    start = (1 if thumb else 0) if relocatable else (0x1001 if thumb else 0x1000)
    symtab = bytes(16) + struct.pack("<IIIBBH", 1, start, 8, 0x12, 0, 1)
    desc = [
        (".text", 1, 6, 0 if relocatable else 0x1000, text, 0, 0, 4, 0),
        (".strtab", 3, 0, 0, symbol_names, 0, 0, 1, 0),
        (".symtab", 2, 0, 0, symtab, 2, 1, 4, 16),
    ]
    if relocation:
        desc.append((".rel.text", 9, 0, 0, struct.pack("<II", 4, 0x101), 3, 1, 4, 8))
    desc.append((".shstrtab", 3, 0, 0, names, 0, 0, 1, 0))
    buf = bytearray(52)
    sections = [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    for name, kind, flags, address, data, link, info, align, entsize in desc:
        buf.extend(bytes((-len(buf)) % align))
        offset = len(buf)
        buf.extend(data)
        sections.append((names.index(name.encode() + b"\0"), kind, flags,
                         address, offset, len(data), link, info, align, entsize))
    buf.extend(bytes((-len(buf)) % 4))
    shoff = len(buf)
    for section in sections:
        buf.extend(struct.pack("<10I", *section))
    shstrndx = len(sections) - 1
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    buf[:52] = ident + struct.pack("<HHIIIIIHHHHHH",
                                   1 if relocatable else 3, 40, 1,
                                   start, 0, shoff, 0x5000000, 52, 0, 0, 40,
                                   len(sections), shstrndx)
    return bytes(buf)


class ObjectCompareTests(unittest.TestCase):
    def test_matching_relocatable_function_reports_scoped_code_equality(self):
        original = elf32()
        obj = elf32(relocatable=True)
        result = object_compare.compare_window(
            original, obj, address=0x1000, symbol="source_fn",
            expected_reference_sha256=hashlib.sha256(original).hexdigest())
        self.assertTrue(result["code_window_equal"])
        self.assertEqual(result["status"], "LINKED_FUNCTION_WINDOW_EQUAL")
        self.assertFalse(result["source_matching_verified"])
        self.assertFalse(result["original_object_recovered"])
        self.assertFalse(result["original_compiler_verified"])

    def test_relocation_equality_is_not_accepted_as_complete_source_match(self):
        r = object_compare.compare_window(elf32(), elf32(relocatable=True, relocation=True),
                                          address=0x1000, symbol="source_fn")
        self.assertEqual(r["candidate_relocation_count"], 1)
        self.assertEqual(r["status"], "BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS")
        self.assertFalse(r["source_matching_verified"])

    def test_size_mode_and_code_mismatch(self):
        r = object_compare.compare_window(elf32(), elf32(relocatable=True, thumb=False),
                                          address=0x1000, symbol="source_fn")
        self.assertEqual(r["status"], "ARM_THUMB_MODE_MISMATCH")
        modified = bytearray(16)
        modified[0] = 0x9a
        r = object_compare.compare_window(elf32(), elf32(relocatable=True, text=bytes(modified)),
                                          address=0x1000, symbol="source_fn")
        self.assertEqual(r["status"], "FUNCTION_BYTES_DIFFERENT")

    def test_refuses_ambiguous_synthetic_or_wrong_architecture(self):
        with self.assertRaisesRegex(object_compare.CompareError, "synthetic"):
            object_compare.candidate_window(elf32(relocatable=True), "nova3_fn_001000_arm")
        with self.assertRaisesRegex(object_compare.CompareError, "absent"):
            object_compare.candidate_window(elf32(relocatable=True), "unknown")
        with self.assertRaisesRegex(object_compare.CompareError, "ARM32 ET_REL"):
            object_compare.candidate_window(elf32(), "source_fn")
        bad = bytearray(elf32(relocatable=True))
        struct.pack_into("<H", bad, 18, 3)
        with self.assertRaisesRegex(object_compare.CompareError, "ARM32 ET_REL"):
            object_compare.candidate_window(bytes(bad), "source_fn")

    def test_refuses_wrong_original_hash(self):
        with self.assertRaisesRegex(object_compare.CompareError, "reference SHA-256"):
            object_compare.compare_window(elf32(), elf32(relocatable=True),
                                          address=0x1000, symbol="source_fn",
                                          expected_reference_sha256="0" * 64)
        with self.assertRaisesRegex(object_compare.CompareError, "independently bounded"):
            object_compare.compare_window(elf32(), elf32(relocatable=True),
                                          address=0x2000, symbol="source_fn")


if __name__ == "__main__":
    unittest.main()
