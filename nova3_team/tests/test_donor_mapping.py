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

    def test_nontrivial_mixed_pilot_not_consumed_by_four_byte_stubs(self):
        data = fixture()
        original = data["comparison"]["functions"][:1]
        data["comparison"]["functions"] = original
        for i in range(80):
            data["comparison"]["functions"].append(dict(
                name=f"_ZN5Stub{i}E", status="exact_bytes",
                retail_raw_value=0x3000 + i * 16,
                debug_raw_value=0x5000 + i * 16,
                retail_size=4, debug_size=4,
                retail_sha256=SHA_A, debug_sha256=SHA_A))
        for i, size in enumerate((32, 48, 72, 136, 192, 224, 500, 700)):
            data["comparison"]["functions"].append(dict(
                name=f"real_{i}", status="exact_bytes",
                retail_raw_value=0x9000 + i * 0x1000,
                debug_raw_value=0x19000 + i * 0x1000,
                retail_size=size, debug_size=size,
                retail_sha256=SHA_A, debug_sha256=SHA_A))
        result = donor_mapping.plan_donors(data, limit=12)
        sizes = [x["retail_size"] for x in result["candidates"]]
        self.assertNotIn(4, sizes[:9])
        self.assertIn(32, sizes)
        self.assertIn(136, sizes)
        self.assertIn(500, sizes)
        self.assertEqual(result["verified_decompilation_count"], 0)

    def test_legacy_source_hints_are_unverified_and_sha_guarded(self):
        data = fixture()
        source = "reconstruction/recovered/libNOVA3_neon/block_00001000.cpp"
        legacy = {"schema": donor_mapping.LEGACY_SCHEMA, "tasks": [
            {"source_path": source, "source_sha256": SHA_B,
             "reference_sha256": SHA_A, "review_priority": 1,
             "known_function_ranges": [{"address": "0x1000", "size_bytes": 12}]}
        ]}
        result = donor_mapping.plan_donors(data, legacy=legacy)
        self.assertEqual(result["candidates"][0]["legacy_source_path"], source)
        self.assertEqual(result["selected_existing_source_candidates"], 1)
        self.assertFalse(result["candidates"][0]["source_matching_verified"])
        legacy["tasks"][0]["reference_sha256"] = SHA_B
        result = donor_mapping.plan_donors(data, legacy=legacy)
        self.assertNotIn("legacy_source_path", result["candidates"][0])
        legacy["schema"] = "unexpected"
        with self.assertRaisesRegex(donor_mapping.DonorError, "legacy"):
            donor_mapping.plan_donors(data, legacy=legacy)


if __name__ == "__main__":
    unittest.main()
