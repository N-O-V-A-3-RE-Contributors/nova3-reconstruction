"""Synthetic-ledger contract tests for ``object_matching.select_functions``.

Every case replaces ``nova3_team.object_matching.build_ledger`` with a hand-built
ledger dict. No ELF is parsed and no file is read. The bytes in ``DATA`` are an
arbitrary deterministic payload used only for bounds checks and SHA-256
comparisons; these tests make no claim about any real binary.
"""
import hashlib
import unittest
from unittest import mock

from nova3_team import object_matching as matching

# Deterministic synthetic image standing in for the ELF bytes.  Selection only
# uses its length for bounds and its slice contents for the selected row hash.
DATA = bytes((index * 37 + 11) & 0xFF for index in range(0x1000))


def _symbol(file_offset, size, name="sym"):
    return {"file_offset": file_offset, "size": size, "name": name}


def _function(address, size, *, thumb=False, symbols=None,
              size_conflict=None, name=None):
    if symbols is None:
        symbols = [_symbol(0x100, size)]
    if size_conflict is None:
        size_conflict = len({item["size"] for item in symbols}) > 1
    label = name or "fn_%08x" % address
    return {
        "id": "synthetic:%08x:%s" % (address, "thumb" if thumb else "arm"),
        "address": address,
        "thumb": thumb,
        "size": size,
        "size_conflict": size_conflict,
        "name": label,
        "symbols": symbols,
    }


def _ledger(functions, endianness="little"):
    return {
        "schema": "nova3.object-matching/1",
        "binary": {"endianness": endianness},
        "functions": list(functions),
    }


class SelectFunctionsSyntheticLedgerTests(unittest.TestCase):
    def _good(self, address=0x1000, size=0x10, *, offset=0x100):
        return _function(address, size,
                         symbols=[_symbol(offset, size, "good")], name="good")

    def _select(self, functions, **kwargs):
        ledger = _ledger(functions)
        with mock.patch.object(matching, "build_ledger",
                               return_value=ledger) as build:
            result = matching.select_functions(DATA, **kwargs)
            build.assert_called_once_with(DATA)
        return result

    def _assert_single_exclusion(self, good, bad, reason):
        selected, exclusions = self._select([good, bad])
        self.assertEqual(
            exclusions, [{"address": bad["address"], "reason": reason}])
        self.assertEqual([row["address"] for row in selected],
                         [good["address"]])

    def test_limit_zero_and_above_maximum_are_rejected_before_ledger_build(self):
        for limit in (0, 257):
            with self.subTest(limit=limit):
                with mock.patch.object(matching, "build_ledger") as build:
                    with self.assertRaisesRegex(matching.MatchingError, "limit"):
                        matching.select_functions(DATA, limit=limit)
                    build.assert_not_called()

    def test_big_endian_ledger_is_rejected(self):
        ledger = _ledger([self._good()], endianness="big")
        with mock.patch.object(matching, "build_ledger",
                               return_value=ledger) as build:
            with self.assertRaisesRegex(matching.MatchingError,
                                        "little-endian"):
                matching.select_functions(DATA, limit=1)
            build.assert_called_once_with(DATA)

    def test_each_exclusion_reason_is_reported_once(self):
        good = self._good(0x1000, 0x40)
        oversized = matching.MAX_FUNCTION_BYTES + 1
        cases = [
            ("conflicting symbols",
             "conflicting_symbol_sizes",
             _function(0x2000, 8, symbols=[
                 _symbol(0x200, 8), _symbol(0x300, 4)])),
            ("oversized symbol",
             "unknown_or_oversized_symbol",
             _function(0x2000, oversized,
                       symbols=[_symbol(0x200, oversized)])),
            ("more than one exact-size offset",
             "ambiguous_file_offset",
             _function(0x2000, 8, symbols=[
                 _symbol(0x200, 8), _symbol(0x210, 8)])),
            ("unaligned ARM extent",
             "unaligned_instruction_extent",
             _function(0x2000, 6, symbols=[_symbol(0x200, 6)])),
            ("unaligned Thumb extent",
             "unaligned_instruction_extent",
             _function(0x2000, 3, thumb=True,
                       symbols=[_symbol(0x200, 3)])),
            ("overlapping previous selected extent",
             "overlapping_symbol_extents",
             _function(0x1020, 8, symbols=[_symbol(0x200, 8)])),
            ("outside file",
             "symbol_outside_file",
             _function(0x2000, 0x90,
                       symbols=[_symbol(len(DATA) - 0x10, 0x90)])),
        ]
        for label, reason, bad in cases:
            with self.subTest(case=label):
                self._assert_single_exclusion(good, bad, reason)

    def test_exclusion_reasons_are_evaluated_in_documented_priority_order(self):
        good = self._good(0x1000, 0x40)
        oversized = matching.MAX_FUNCTION_BYTES + 1
        cases = [
            # Conflicts first, even though this also has >1 exact-size offset,
            # an unaligned extent and an overlapping start.
            ("conflicting_symbol_sizes",
             _function(0x1020, 6, symbols=[
                 _symbol(0x200, 6), _symbol(0x300, 4), _symbol(0x210, 6)])),
            # Oversized before ambiguity/alignment/overlap.
            ("unknown_or_oversized_symbol",
             _function(0x1020, oversized, symbols=[
                 _symbol(0x200, oversized), _symbol(0x210, oversized)])),
            # Ambiguity before alignment/overlap/outside-file.
            ("ambiguous_file_offset",
             _function(0x1020, 8, symbols=[
                 _symbol(0xFF8, 8), _symbol(0xFFC, 8)])),
            # Alignment before overlap/outside-file.
            ("unaligned_instruction_extent",
             _function(0x1020, 6, symbols=[_symbol(0xFFC, 6)])),
            # Overlap before outside-file.
            ("overlapping_symbol_extents",
             _function(0x1020, 8, symbols=[_symbol(0xFFC, 8)])),
        ]
        for reason, bad in cases:
            with self.subTest(reason=reason):
                self._assert_single_exclusion(good, bad, reason)

    def test_overlap_uses_previous_selected_function_end(self):
        first = self._good(0x1000, 0x10)
        # If this excluded row advanced previous_end to 0x3000, the later row
        # at 0x2800 would be rejected as overlapping.
        excluded = _function(0x2000, 0x1000, symbols=[
            _symbol(0x200, 0x1000), _symbol(0x300, 0x1000)])
        later = _function(0x2800, 0x10,
                          symbols=[_symbol(0x400, 0x10)], name="later")
        selected, exclusions = self._select([first, excluded, later])
        self.assertEqual([row["address"] for row in selected],
                         [0x1000, 0x2800])
        self.assertEqual(exclusions, [{"address": 0x2000,
                                       "reason": "ambiguous_file_offset"}])

    def test_selected_row_carries_bounds_and_content_hash(self):
        function = _function(0x1000, 10, thumb=True,
                             symbols=[_symbol(0x234, 10, "thumb_fn")],
                             name="thumb_fn")
        selected, exclusions = self._select([function])
        self.assertEqual(exclusions, [])
        self.assertEqual(len(selected), 1)
        row = selected[0]
        self.assertEqual(row["address"], 0x1000)
        self.assertEqual(row["size"], 10)
        self.assertEqual(row["thumb"], True)
        self.assertEqual(row["file_offset"], 0x234)
        self.assertEqual(row["sha256"],
                         hashlib.sha256(DATA[0x234:0x23E]).hexdigest())

    def test_requested_addresses_filter_selection_only(self):
        first = self._good(0x1000, 0x10)
        unrequested_bad = _function(0x3000, 8, symbols=[
            _symbol(0x300, 8), _symbol(0x310, 8)])
        requested = self._good(0x2000, 0x10, offset=0x200)
        selected, exclusions = self._select(
            [first, unrequested_bad, requested], addresses={0x2000})
        self.assertEqual([row["address"] for row in selected], [0x2000])
        self.assertEqual(exclusions, [])

    def test_requested_excluded_or_absent_address_appears_in_error(self):
        good = self._good()
        excluded = _function(0x2000, 8, symbols=[
            _symbol(0x200, 8), _symbol(0x210, 8)])
        cases = [
            ("excluded alongside a selected row",
             {0x1000, 0x2000}, [good, excluded], 0x2000),
            ("excluded on its own", {0x2000}, [good, excluded], 0x2000),
            ("absent", {0xDEADBEEF}, [good], 0xDEADBEEF),
        ]
        for label, requested, functions, must_name in cases:
            with self.subTest(case=label):
                ledger = _ledger(functions)
                with mock.patch.object(matching, "build_ledger",
                                       return_value=ledger):
                    with self.assertRaises(matching.MatchingError) as raised:
                        matching.select_functions(DATA,
                                                  addresses=requested)
                self.assertIn(hex(must_name), str(raised.exception))

    def test_empty_selection_raises(self):
        excluded = _function(0x2000, 8, symbols=[
            _symbol(0x200, 8), _symbol(0x210, 8)])
        for label, functions in (("empty ledger", []),
                                 ("all rows excluded", [excluded])):
            with self.subTest(case=label):
                ledger = _ledger(functions)
                with mock.patch.object(matching, "build_ledger",
                                       return_value=ledger):
                    with self.assertRaisesRegex(
                            matching.MatchingError,
                            "no safe functions selected"):
                        matching.select_functions(DATA)


if __name__ == "__main__":
    unittest.main()
