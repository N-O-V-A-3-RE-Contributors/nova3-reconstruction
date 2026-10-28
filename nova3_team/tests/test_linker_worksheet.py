"""Linked ELF layout worksheet fixtures; original game never involved."""
import struct
import unittest

from nova3_team import linker_worksheet
from nova3_team.tests.test_object_compare import elf32


def linked_with_segment():
    data = bytearray(elf32())
    offset = len(data)
    data.extend(struct.pack("<8I", 1, 52, 0x1000, 0,
                            16, 32, 5, 4096))
    struct.pack_into("<I", data, 28, offset)
    struct.pack_into("<H", data, 42, 32)
    struct.pack_into("<H", data, 44, 1)
    return bytes(data)


def linked_with_rel():
    data = bytearray(elf32(relocatable=True, relocation=True))
    struct.pack_into("<H", data, 16, 3)  # ET_DYN; synthetic only
    return bytes(data)


class WorksheetTests(unittest.TestCase):
    def test_pt_load_and_section_layout_have_no_original_bytes(self):
        report = linker_worksheet.worksheet(linked_with_segment())
        self.assertEqual(report["schema"], linker_worksheet.SCHEMA)
        self.assertEqual(report["elf_type"], "ET_DYN")
        self.assertEqual(len(report["loaded_segments"]), 1)
        seg = report["loaded_segments"][0]
        self.assertEqual(seg["virtual_address"], 0x1000)
        self.assertEqual(seg["memory_size"], 32)
        self.assertIn(".text", seg["allocated_sections"])
        self.assertFalse(report["original_linker_script_recovered"])
        self.assertFalse(report["original_prelink_relocations_recovered"])
        self.assertNotIn("raw_bytes", repr(report))

    def test_linked_relocation_inventory_is_not_prelink_recovery(self):
        report = linker_worksheet.worksheet(linked_with_rel())
        self.assertEqual(report["total_linked_relocations"], 1)
        self.assertEqual(report["relocation_tables"][0]["type_counts"],
                         {"R_ARM_TYPE_1": 1})
        self.assertFalse(report["relocation_tables"][0]["original_object_relocations"])

    def test_optional_linker_hypothesis_never_masquerades_as_original(self):
        report = linker_worksheet.worksheet(linked_with_segment())
        script = linker_worksheet.experimental_ld(report)
        self.assertIn("EXPERIMENTAL LAYOUT ONLY", script)
        self.assertIn(".text 0x00001000", script)
        self.assertIn("DO NOT ship", script)
        self.assertNotIn("ORIGINAL MATCHED", script)

    def test_wrong_architecture_and_malformed_header_fail_closed(self):
        data = bytearray(linked_with_segment())
        struct.pack_into("<H", data, 18, 3)  # EM_386
        with self.assertRaisesRegex(linker_worksheet.WorksheetError, "EABI5"):
            linker_worksheet.worksheet(bytes(data))
        data = bytearray(linked_with_segment())
        struct.pack_into("<I", data, 28, 0xffffffff)  # program header outside file
        with self.assertRaisesRegex(linker_worksheet.WorksheetError, "span"):
            linker_worksheet.worksheet(bytes(data))


if __name__ == "__main__":
    unittest.main()
