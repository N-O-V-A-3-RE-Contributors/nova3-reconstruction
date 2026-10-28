"""Synthetic Cyrus/Premium donor planning; no original game binaries needed."""
import unittest

from nova3_team import donor_mapping
from nova3_team.version_compare import SCHEMA as VERSION_SCHEMA


SHA_A = "a" * 64
SHA_B = "b" * 64


def fixture():
    return {
        "schema": VERSION_SCHEMA,
        "retail": {
            "sha256": SHA_A,
            "dwarf": {
                "compile_units": [{"offset": 12, "name_sha256": SHA_A}],
                "function_dies": [{"low_pc": 0x1000, "cu_offset": 12,
                                   "declaration": False}],
            },
        },
        "debug": {
            "sha256": SHA_B,
            "dwarf": {
                "compile_units": [{"offset": 13, "name_sha256": SHA_B}],
                "function_dies": [{"low_pc": 0x2000, "cu_offset": 13,
                                   "declaration": False}],
            },
        },
        "comparison": {
            "functions": [
                {"name": "_ZN8Renderer4drawEv", "status": "exact_bytes",
                 "retail_raw_value": 0x1000, "debug_raw_value": 0x2000,
                 "retail_size": 12, "debug_size": 12,
                 "retail_sha256": SHA_A, "debug_sha256": SHA_A},
                {"name": "another", "status": "different_bytes",
                 "retail_raw_value": 0x100d, "debug_raw_value": 0x201d,
                 "retail_size": 16, "debug_size": 20,
                 "retail_sha256": SHA_A, "debug_sha256": SHA_B},
                {"name": "ambiguous", "status": "ambiguous_alias_mapping"},
            ]
        },
    }


class DonorMapTests(unittest.TestCase):
    def test_selects_unique_pairs_with_conservative_dwarf_hypotheses(self):
        result = donor_mapping.plan_donors(fixture(), limit=10)
        self.assertEqual(result["selected_pairs"], 2)
        self.assertEqual(result["skipped_pairs"], 1)
        self.assertEqual(result["verified_decompilation_count"], 0)
        first, second = result["candidates"]
        self.assertEqual(first["group_hint"], "retail_cu_0000000c")
        self.assertEqual(first["retail_cu_status"], "INFERRED_DWARF_CU")
        self.assertEqual(first["debug_cu_status"], "INFERRED_DWARF_CU")
        self.assertEqual(first["ida_status"], "NO_IDA_RECORD")
        self.assertFalse(first["source_matching_verified"])
        self.assertFalse(first["translation_unit_proven"])
        self.assertEqual(second["group_hint"], "unclassified")

    def test_ida_metadata_is_not_automatically_byte_verified(self):
        ida = {"schema": "nova3.ida-headless-boundaries/1", "input_sha256": SHA_A,
               "functions": [{"image_relative": 0x1000, "name": "Renderer::draw",
                              "byte_mapping_verified": False}]}
        result = donor_mapping.plan_donors(fixture(), ida=ida)
        self.assertEqual(result["candidates"][0]["ida_status"], "IDA_METADATA_ONLY")
        ida["input_sha256"] = SHA_B
        with self.assertRaisesRegex(donor_mapping.DonorError, "IDA input"):
            donor_mapping.plan_donors(fixture(), ida=ida)

    def test_schema_malformed_rows_and_bounds_fail_closed(self):
        data = fixture()
        data["schema"] = "invented"
        with self.assertRaisesRegex(donor_mapping.DonorError, "schema"):
            donor_mapping.plan_donors(data)
        with self.assertRaisesRegex(donor_mapping.DonorError, "limit"):
            donor_mapping.plan_donors(fixture(), limit=0)
        data = fixture()
        data["comparison"]["functions"][0]["retail_size"] = -1
        with self.assertRaisesRegex(donor_mapping.DonorError, "unsafe"):
            donor_mapping.plan_donors(data)
        data = fixture()
        data["retail"]["sha256"] = "wrong"
        with self.assertRaisesRegex(donor_mapping.DonorError, "SHA-256"):
            donor_mapping.plan_donors(data)

    def test_ambiguous_cu_does_not_justify_translation_unit(self):
        data = fixture()
        data["retail"]["dwarf"]["compile_units"].append({"offset": 14})
        data["retail"]["dwarf"]["function_dies"].append(
            {"low_pc": 0x1000, "cu_offset": 14, "declaration": False})
        result = donor_mapping.plan_donors(data)
        first = result["candidates"][0]
        self.assertEqual(first["retail_cu_status"], "AMBIGUOUS_DWARF_CU")
        self.assertIsNone(first["retail_cu"])
        self.assertEqual(first["group_hint"], "scope_Renderer")

    def test_alias_names_are_not_counted_as_independent_function_candidates(self):
        data = fixture()
        alias = dict(data["comparison"]["functions"][0], name="_ZN8Renderer9drawAliasEv")
        data["comparison"]["functions"].append(alias)
        result = donor_mapping.plan_donors(data)
        self.assertEqual(result["total_pairs"], 4)
        self.assertEqual(result["qualifying_pairs"], 2)
        self.assertEqual(result["selected_pairs"], 2)


if __name__ == "__main__":
    unittest.main()
