"""Synthetic encodings only; these tests establish no original-game matches."""
import hashlib
import json
import unittest

from nova3_team.matching import (
    BuildEvidence, Classification, InstructionStream, compare_instruction_streams,
)


# Hand-encoded toy functions: mov r0, #1; bx lr (A32 and T16).
ARM = bytes.fromhex("0100a0e3 1eff2fe1")
THUMB = bytes.fromhex("0120 7047")
# Mixed T16/T32: movs r0, #1; movw r0, #1; bx lr.
MIXED_THUMB = bytes.fromhex("0120 40f20100 7047")
SOURCE = b"unsigned toy(void) { return 1; }\n"


def stream(data=ARM, mode="arm", **kwargs):
    return InstructionStream(data, mode, "armv7-a", **kwargs)


def provenance(**changes):
    # Intentionally synthetic metadata, not a claim of a real compiler run.
    fields = dict(compiler_version="synthetic-compiler 1.0 (test fixture)",
                  flags=("-O2", "-marm", "-march=armv7-a"),
                  source_sha256=hashlib.sha256(SOURCE).hexdigest())
    fields.update(changes)
    return BuildEvidence(**fields)


class MatchingTests(unittest.TestCase):
    def test_missing_streams_are_not_matched(self):
        for arguments in ({}, {"candidate": stream()}, {"reference": stream()}):
            with self.subTest(arguments=tuple(arguments)):
                result = compare_instruction_streams(build=provenance(), **arguments)
                self.assertEqual(result.classification, Classification.UNKNOWN)
                self.assertEqual(result.matching_claim, "NOT MATCHED")
                self.assertIsNone(result.comparison)

    def test_exact_arm_records_scoped_machine_code_evidence(self):
        result = compare_instruction_streams(candidate=stream(address=0x1000),
                                             reference=stream(address=0x8000),
                                             build=provenance())
        report = result.to_dict()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["classification"], "verified")
        self.assertEqual(report["matching_claim"], "MATCHED")
        self.assertEqual(report["scope"], "supplied_arm_instruction_streams")
        self.assertIs(report["complete_elf_match"], False)
        for name, address in (("candidate", 0x1000), ("reference", 0x8000)):
            self.assertEqual(report[name]["sha256"], hashlib.sha256(ARM).hexdigest())
            self.assertEqual(report[name]["instruction_count"], 2)
            self.assertEqual(report[name]["byte_count"], 8)
            self.assertEqual(report[name]["address"], address)
        self.assertEqual(report["build"], provenance().to_dict())
        self.assertEqual(report["missing_build_evidence"], [])
        self.assertEqual(report["comparison"], {
            "method": "positional_instruction_bytes_v1", "exact_bytes": True,
            "corresponding_instruction_count": 2, "identical_instruction_count": 2,
            "first_difference": None,
        })

    def test_missing_each_provenance_field_prevents_verified(self):
        for field in ("compiler_version", "flags", "source_sha256"):
            with self.subTest(field=field):
                result = compare_instruction_streams(candidate=stream(), reference=stream(),
                                                     build=provenance(**{field: None}))
                self.assertEqual(result.classification, Classification.APPROXIMATE)
                self.assertEqual(result.matching_claim, "NOT MATCHED")
                self.assertTrue(result.comparison.exact_bytes)
                self.assertEqual(result.to_dict()["missing_build_evidence"], [field])

    def test_no_provenance_is_explicit(self):
        result = compare_instruction_streams(candidate=stream(), reference=stream())
        self.assertEqual(result.classification, Classification.APPROXIMATE)
        self.assertEqual(result.matching_claim, "NOT MATCHED")
        self.assertEqual(result.to_dict()["missing_build_evidence"],
                         ["compiler_version", "flags", "source_sha256"])

    def test_explicit_empty_flags_are_distinct_from_unknown_flags(self):
        result = compare_instruction_streams(candidate=stream(), reference=stream(),
                                             build=provenance(flags=()))
        self.assertEqual(result.classification, Classification.VERIFIED)
        self.assertEqual(result.to_dict()["build"]["flags"], [])

    def test_operand_difference_is_approximate_not_matched(self):
        candidate = bytes.fromhex("0200a0e3 1eff2fe1")  # mov r0, #2; bx lr
        result = compare_instruction_streams(candidate=stream(candidate), reference=stream(),
                                             build=provenance())
        self.assertEqual(result.classification, Classification.APPROXIMATE)
        self.assertEqual(result.matching_claim, "NOT MATCHED")
        self.assertFalse(result.comparison.exact_bytes)
        self.assertEqual(result.comparison.identical_instruction_count, 1)
        self.assertEqual(result.comparison.first_difference.instruction_index, 0)
        self.assertEqual(result.comparison.first_difference.candidate_offset, 0)
        self.assertNotEqual(result.candidate.sha256, result.reference.sha256)

    def test_no_identical_instruction_is_unknown(self):
        result = compare_instruction_streams(candidate=stream(ARM[:4]),
                                             reference=stream(ARM[4:]), build=provenance())
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertEqual(result.matching_claim, "NOT MATCHED")
        self.assertEqual(result.comparison.identical_instruction_count, 0)
        self.assertFalse(result.comparison.exact_bytes)

    def test_matching_prefix_never_verifies_a_longer_stream(self):
        for shorter_candidate in (True, False):
            with self.subTest(shorter_candidate=shorter_candidate):
                left, right = (ARM[:4], ARM) if shorter_candidate else (ARM, ARM[:4])
                result = compare_instruction_streams(candidate=stream(left), reference=stream(right),
                                                     build=provenance())
                self.assertEqual(result.classification, Classification.APPROXIMATE)
                self.assertEqual(result.comparison.corresponding_instruction_count, 1)
                self.assertEqual(result.comparison.identical_instruction_count, 1)
                difference = result.comparison.first_difference
                self.assertEqual(difference.instruction_index, 1)
                self.assertEqual(difference.candidate_offset, None if shorter_candidate else 4)
                self.assertEqual(difference.reference_offset, 4 if shorter_candidate else None)

    def test_reordered_instructions_are_not_normalized(self):
        result = compare_instruction_streams(candidate=stream(ARM[4:] + ARM[:4]),
                                             reference=stream(), build=provenance())
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertEqual(result.comparison.identical_instruction_count, 0)

    def test_thumb_16_and_mixed_width_streams(self):
        for data, count in ((THUMB, 2), (MIXED_THUMB, 3)):
            with self.subTest(data=data.hex()):
                result = compare_instruction_streams(candidate=stream(data, "thumb"),
                                                     reference=stream(data, "thumb"),
                                                     build=provenance(flags=("-mthumb",)))
                self.assertEqual(result.classification, Classification.VERIFIED)
                self.assertEqual(result.candidate.instruction_count, count)
                self.assertEqual(result.comparison.identical_instruction_count, count)

    def test_thumb_32_second_halfword_difference_is_not_ignored(self):
        candidate = bytes.fromhex("0120 40f20200 7047")
        result = compare_instruction_streams(candidate=stream(candidate, "thumb"),
                                             reference=stream(MIXED_THUMB, "thumb"),
                                             build=provenance())
        self.assertEqual(result.classification, Classification.APPROXIMATE)
        self.assertEqual(result.comparison.identical_instruction_count, 2)
        self.assertEqual(result.comparison.first_difference.instruction_index, 1)
        self.assertEqual(result.comparison.first_difference.candidate_offset, 2)
        self.assertEqual(result.comparison.first_difference.reference_offset, 2)

    def test_all_thumb_32_prefixes_reject_missing_second_halfword(self):
        for prefix in (0b11101, 0b11110, 0b11111):
            with self.subTest(prefix=prefix):
                truncated = (prefix << 11).to_bytes(2, "little")
                result = compare_instruction_streams(candidate=stream(truncated, "thumb"),
                                                     reference=stream(truncated, "thumb"),
                                                     build=provenance())
                self.assertEqual(result.classification, Classification.UNKNOWN)
                self.assertEqual(result.matching_claim, "NOT MATCHED")
                self.assertIsNone(result.comparison)

    def test_empty_and_truncated_streams_never_verify(self):
        for mode, valid in (("arm", ARM), ("thumb", MIXED_THUMB)):
            malformed = (b"", valid[:1], valid[:-1])
            for data in malformed:
                for bad_candidate in (True, False):
                    with self.subTest(mode=mode, length=len(data), candidate=bad_candidate):
                        left, right = (data, valid) if bad_candidate else (valid, data)
                        result = compare_instruction_streams(candidate=stream(left, mode),
                                                             reference=stream(right, mode),
                                                             build=provenance())
                        self.assertEqual(result.classification, Classification.UNKNOWN)
                        self.assertIsNone(result.comparison)

    def test_incompatible_contexts_fail_closed_even_for_identical_bytes(self):
        contexts = [stream(ARM, "thumb"), InstructionStream(ARM, "arm", "armv6"),
                    stream(byte_order="big")]
        for reference in contexts:
            with self.subTest(reference=reference):
                result = compare_instruction_streams(candidate=stream(), reference=reference,
                                                     build=provenance())
                self.assertEqual(result.classification, Classification.UNKNOWN)
                self.assertIsNone(result.comparison)

    def test_big_endian_is_explicitly_unsupported(self):
        for mode in ("arm", "thumb"):
            result = compare_instruction_streams(candidate=stream(ARM, mode, byte_order="big"),
                                                 reference=stream(ARM, mode, byte_order="big"),
                                                 build=provenance())
            self.assertEqual(result.classification, Classification.UNKNOWN)
            self.assertIn("little-endian", result.reason)

    def test_report_is_json_ready_and_does_not_contain_code_bytes(self):
        result = compare_instruction_streams(candidate=stream(), reference=stream(ARM[:4]),
                                             build=provenance())
        report = result.to_dict()
        self.assertEqual(json.loads(json.dumps(report)), report)
        self.assertNotIn("data", report["candidate"])
        self.assertNotIn(ARM.hex(), json.dumps(report))
        report["build"]["flags"].append("changed")
        self.assertEqual(result.build.flags, provenance().flags)

    def test_invalid_provenance_is_rejected(self):
        cases = [{"compiler_version": " "}, {"compiler_version": 3},
                 {"flags": "-O2"}, {"flags": ["-O2"]}, {"flags": ("",)},
                 {"flags": (1,)}, {"source_sha256": "not a hash"},
                 {"source_sha256": "a" * 63}, {"source_sha256": 7}]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    provenance(**changes)
        digest = hashlib.sha256(SOURCE).hexdigest()
        self.assertEqual(provenance(source_sha256=digest.upper()).source_sha256, digest)

    def test_invalid_stream_metadata_is_rejected(self):
        for changes in ({"mode": "aarch64"}, {"architecture": ""},
                        {"byte_order": "native"}, {"address": -4}, {"address": 1},
                        {"address": True}, {"address": 2**32}, {"address": 2**32 - 4}):
            fields = dict(data=ARM, mode="arm", architecture="armv7-a")
            fields.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    InstructionStream(**fields)
        with self.assertRaises(TypeError):
            stream(bytearray(ARM))
        with self.assertRaises(ValueError):
            stream(THUMB, "thumb", address=0x1001)  # Thumb pointer bit is not an address.
        self.assertEqual(stream(THUMB, "thumb", address=0x1002).address, 0x1002)

    def test_raw_or_untyped_arguments_are_not_treated_as_evidence(self):
        for arguments in ({"candidate": ARM}, {"reference": ARM}, {"build": {}}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    compare_instruction_streams(**arguments)


if __name__ == "__main__":
    unittest.main()
