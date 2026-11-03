"""Offline contract tests for ``nova3_team.relocation_probe.comparison`` only.

Every input here is a synthetic byte string supplied by this module; no file,
APK, shared library, compiler or subprocess is involved. The module's caller
supplies the ``equal_status`` / ``unequal_status`` labels, so these tests pin
the status label to the verdict computed by ``comparison`` itself rather than
trusting the label as evidence.

Scope note: this isolates byte-string accounting only. A passing test says
nothing about any original function, compiler, function size, instruction
window or relocation.
"""
import inspect
import unittest

from nova3_team.relocation_probe import comparison


EQUAL = "SYNTHETIC_EQUAL"
UNEQUAL = "SYNTHETIC_UNEQUAL"

# Two explicit zero bytes for the length/-padding tests.
ZERO_TAIL = b"\x00\x00"

# A two-character filler planted in synthetic inputs; it must not leak into
# any textual report field (status, offsets repr, counts).
SENTINEL = "B0"


def word4(text: bytes) -> bytes:
    """Four-byte words from a short ASCII pattern such as b"AAAABBBB"."""
    if len(text) % 4:
        raise ValueError("word4 needs whole words")
    return text


class IdenticalInputs(unittest.TestCase):
    def test_identical_word_strings_report_equal_status(self):
        data = word4(b"AAAABBBBCCCCDDDD")
        result = comparison(data, data, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], EQUAL)
        self.assertIs(result["equal"], True)
        self.assertEqual(result["differing_byte_count"], 0)
        self.assertEqual(result["differing_offsets"], [])
        self.assertEqual(result["compared_bytes"], len(data))
        self.assertEqual(result["expected_sha256"], result["actual_sha256"])

    def test_identical_two_byte_words_report_equal_status(self):
        data = b"AABBCCDD"
        result = comparison(data, data, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], EQUAL)
        self.assertEqual(result["differing_byte_count"], 0)
        self.assertEqual(result["compared_bytes"], 8)

    def test_empty_inputs_compare_equal_with_zero_bytes(self):
        result = comparison(b"", b"", EQUAL, UNEQUAL)
        self.assertEqual(result["status"], EQUAL)
        self.assertEqual(result["differing_byte_count"], 0)
        self.assertEqual(result["compared_bytes"], 0)

    def test_same_bytes_from_distinct_objects_compare_equal(self):
        a = bytes(word4(b"AAAABBBB"))
        b = bytearray(w for w in a)
        self.assertIsNot(a, b)
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], EQUAL)
        self.assertEqual(result["differing_byte_count"], 0)


class CountingContract(unittest.TestCase):
    def test_one_differing_word_reports_exact_count_and_offsets(self):
        a = word4(b"AAAABBBBCCCCDDDD")
        b = word4(b"AAAABBBBXXXXDDDD")
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], UNEQUAL)
        self.assertIs(result["equal"], False)
        # Whole-byte accounting: one differing word = its full word width.
        self.assertEqual(result["differing_byte_count"], 4)
        self.assertEqual(result["differing_offsets"], [8, 9, 10, 11])
        self.assertEqual(result["compared_bytes"], len(a))
        self.assertNotEqual(result["expected_sha256"], result["actual_sha256"])

    def test_one_byte_difference_inside_a_word_is_a_word_mismatch(self):
        a = word4(b"AAAABBBB")
        b = word4(b"AAAABBBB")
        b = b[:-1] + b"C"
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], UNEQUAL)
        self.assertEqual(result["differing_byte_count"], 1)
        self.assertEqual(result["differing_offsets"], [7])

    def test_all_words_differing_with_two_byte_words(self):
        a = b"AABBCCDD"
        b = b"11223344"
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], UNEQUAL)
        self.assertEqual(result["differing_byte_count"], 8)
        self.assertEqual(result["differing_offsets"], list(range(8)))
        # Every word differs; the report must not present this as a match.
        self.assertIs(result["equal"], False)
        self.assertNotEqual(result["status"], EQUAL)


class MismatchedLengths(unittest.TestCase):
    def test_shorter_side_is_not_zero_padded(self):
        # 10-byte reference vs an 8-byte candidate with a 4-byte word
        # mismatch in the overlap (offsets 4..7). The reference's bytes at
        # 8..9 sit past the candidate's end. If the short side were padded
        # with matching zeros they would not count; they do count, and so
        # does the same explicit zero tail when the candidate is shortened.
        a = word4(b"AAAABBBB") + ZERO_TAIL
        b = word4(b"AAAAXXXX")
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["compared_bytes"], len(a))
        # A zero tail is compared as absent, not as a matching zero fill.
        self.assertEqual(result["differing_byte_count"], 6)
        self.assertEqual(result["differing_offsets"], [4, 5, 6, 7, 8, 9])
        # Identical 8-byte overlap, candidate has no tail: both zero bytes
        # still differ, so equal_status cannot be reached by length alone.
        same = comparison(b"AAAABBBB\0\0", b"AAAABBBB", EQUAL, UNEQUAL)
        self.assertEqual(same["differing_byte_count"], 2)
        self.assertEqual(same["differing_offsets"], [8, 9])
        self.assertEqual(same["status"], UNEQUAL)

    def test_extra_tail_in_candidate_is_not_counted(self):
        a = word4(b"AAAABBBB")
        b = word4(b"AAAABBBB") + b"ZZZZ"
        result = comparison(a, b, EQUAL, UNEQUAL)
        # Bytes beyond the overlap are compared against nothing on the
        # short side, so each one is counted as differing, not zero-padded
        # into an equal overlap.
        self.assertEqual(result["compared_bytes"], len(b))
        self.assertEqual(result["differing_byte_count"], len(b) - len(a))
        self.assertEqual(result["differing_offsets"], [8, 9, 10, 11])
        # Full-input hashes differ, exposing the unequal length.
        self.assertNotEqual(result["expected_sha256"], result["actual_sha256"])

    def test_mismatch_within_overlap_of_unequal_lengths(self):
        a = word4(b"AAAABBBB") + b"CCCC"
        b = word4(b"AAAAXXXX")
        result = comparison(a, b, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], UNEQUAL)
        self.assertEqual(result["compared_bytes"], len(a))
        # Overlap mismatch (4 bytes) plus the non-overlapping tail (4 bytes).
        self.assertEqual(result["differing_byte_count"], 8)
        self.assertEqual(result["differing_offsets"], [4, 5, 6, 7, 8, 9, 10, 11])

    def test_empty_against_nonempty_is_not_a_silent_equal(self):
        data = word4(b"AAAABBBB")
        result = comparison(b"", data, EQUAL, UNEQUAL)
        self.assertEqual(result["status"], UNEQUAL)
        # Compared width follows the longer input: the empty side never
        # matches the non-empty tail and misreports an overlap.
        self.assertEqual(result["compared_bytes"], len(data))
        self.assertEqual(result["differing_byte_count"], len(data))
        self.assertEqual(result["differing_offsets"], list(range(len(data))))
        # The verdict, not the differing-byte tally alone, decides the label.
        self.assertIs(result["equal"], False)
        self.assertNotEqual(result["status"], EQUAL)

    def test_label_is_verdict_driven_even_when_inputs_are_swapped(self):
        short = word4(b"AAAA")
        long_ = word4(b"AAAA") + b"BBBB"
        forward = comparison(short, long_, EQUAL, UNEQUAL)
        reverse = comparison(long_, short, EQUAL, UNEQUAL)
        self.assertEqual(forward["status"], UNEQUAL)
        self.assertEqual(reverse["status"], UNEQUAL)
        self.assertEqual(forward["compared_bytes"], reverse["compared_bytes"])


class TruncationAndNoMatchNeverReportEqual(unittest.TestCase):
    """No-match inputs must carry unequal_status; truncation is never silent.

    ``comparison`` has no truncation mode of its own and no caller-chosen
    equal label can force a match: the status is selected from the verdict.
    Truncated inputs are covered through the empty versus non-empty and
    partial-prefix cases, where overlap counting alone could suggest a match.
    """

    def assert_truncation_not_equal(self, prefix: bytes, full: bytes):
        for equal_label, unequal_label in (
                (EQUAL, UNEQUAL),
                ("TRUNCATED", "UNTRUNCATED"),
                (UNEQUAL, EQUAL),  # labels swapped: verdict still governs.
        ):
            result = comparison(prefix, full, equal_label, unequal_label)
            self.assertIs(result["equal"], False)
            self.assertEqual(result["status"], unequal_label)

    def test_truncated_window_never_reports_equal(self):
        full = word4(b"AAAABBBB")
        for cut in (0, 1, 2, 3, 4, 5, 6, 7):
            self.assert_truncation_not_equal(full[:cut], full)

    def test_truncation_label_named_status_is_not_emitted_for_full_match(self):
        # Conversely, a full match must never carry a status whose text
        # names a truncation/mismatch result.
        full = word4(b"AAAABBBB")
        result = comparison(full, full, "COMPLETE", "TRUNCATED")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertNotIn("TRUNC", result["status"].upper())

    def test_single_byte_truncation_is_not_equal(self):
        data = word4(b"AAAABBBBCCCC")
        result = comparison(data[:-1], data, EQUAL, UNEQUAL)
        self.assertNotEqual(result["status"], EQUAL)
        self.assertEqual(result["differing_offsets"], [len(data) - 1])


class Determinism(unittest.TestCase):
    def test_repeated_calls_produce_identical_reports(self):
        a = word4(b"AAAABBBBCCCC")
        b = word4(b"AAAAXBXXCCCC")
        first = comparison(a, b, EQUAL, UNEQUAL)
        for _ in range(5):
            self.assertEqual(comparison(a, b, EQUAL, UNEQUAL), first)

    def test_caller_buffer_mutation_does_not_affect_previous_report(self):
        a = bytearray(word4(b"AAAABBBB"))
        b = word4(b"AAAABBBB")
        first = comparison(bytes(a), b, EQUAL, UNEQUAL)
        a[:] = b"XXXXXXXX"
        second = comparison(word4(b"AAAABBBB"), b, EQUAL, UNEQUAL)
        self.assertEqual(first, second)
        # Reports are plain value snapshots.
        self.assertEqual(first["status"], EQUAL)


class NoInputBytesInStatus(unittest.TestCase):
    def test_status_is_the_caller_label_exactly(self):
        a = word4(b"AAAABBBB")
        result = comparison(a, a, EQUAL, UNEQUAL)
        self.assertIs(result["status"], EQUAL)
        result = comparison(a, word4(b"CCCCDDDD"), EQUAL, UNEQUAL)
        self.assertIs(result["status"], UNEQUAL)

    def test_sentinel_bytes_are_not_embedded_in_status_or_report(self):
        for payload in (b"B0B0B0B0", b"AAAAB0B0", b"B0B0AAAA"):
            a = word4(b"AAAABBBB")
            result = comparison(a, word4(payload), EQUAL, UNEQUAL)
            blob = repr({k: v for k, v in result.items()
                         if k not in ("expected_sha256", "actual_sha256")})
            self.assertNotIn(SENTINEL, blob)

    def test_report_keys_are_stable(self):
        result = comparison(b"AABB", b"AABB", EQUAL, UNEQUAL)
        self.assertEqual(set(result), {
            "status", "equal", "compared_bytes", "differing_byte_count",
            "differing_offsets", "expected_sha256", "actual_sha256"})


class OfflineOnly(unittest.TestCase):
    def test_signature_is_pure_bytes_in_and_labels(self):
        params = inspect.signature(comparison).parameters
        self.assertEqual(list(params), ["a", "b", "equal_status", "unequal_status"])
        with self.assertRaises(TypeError):
            comparison("AABB", "AABB", EQUAL, UNEQUAL)
        with self.assertRaises(TypeError):
            comparison(None, b"AABB", EQUAL, UNEQUAL)


if __name__ == "__main__":
    unittest.main()
