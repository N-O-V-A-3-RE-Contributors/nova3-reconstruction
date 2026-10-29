"""Offline synthetic ARM/DWARF fixtures. No game files or live analyzer needed."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import donor_tu_catalog as tu
from nova3_team import version_compare as vc
from nova3_team.tests.test_indexer import patch_section, symbol
from nova3_team.tests.test_version_compare import add_sections, fixture, sections


HAVE_DWARF = importlib.util.find_spec("elftools") is not None


def catalog(retail, donor=None, **kwargs):
    donor = retail if donor is None else donor
    return tu.build_catalog(retail, donor, retail_sha256=vc.digest(retail),
                            donor_sha256=vc.digest(donor), **kwargs)


def dwarf_fixture(data, units):
    """Real source-owned DWARF v4: named CUs, concrete/declared subprograms.

    units = [(name, [(low_pc, high_pc_or_size, form_is_offset, declaration), ...])]
    CU PC ranges deliberately aren't supplied, so readers must use function
    DIE ownership. Unit/producer names are synthetic privacy sentinels.
    """
    abbrev = (b"\x01\x11\x01\x03\x08\x25\x08\x00\x00"
              b"\x02\x2e\x00\x11\x01\x12\x01\x3c\x0c\x00\x00"
              b"\x03\x2e\x00\x11\x01\x12\x06\x3c\x0c\x00\x00\x00")
    info = bytearray()
    for name, functions in units:
        dies = bytearray(b"\x01" + name.encode() + b"\0PRIVATE_COMPILER_SENTINEL\0")
        for low, high, offset, declaration in functions:
            dies.extend(bytes([3 if offset else 2]) + struct.pack("<IIB", low, high, declaration))
        dies.extend(b"\0")
        body = struct.pack("<HIB", 4, 0, 4) + dies
        info.extend(struct.pack("<I", len(body)) + body)
    return add_sections(data, [(".debug_abbrev", abbrev), (".debug_info", bytes(info))])


def grouped_addresses(result):
    return [f["retail"]["address"] for f in result["functions"] if f["group_id"]]


class CatalogTests(unittest.TestCase):
    def test_full_scope_plus_order_groups_but_not_unrelated_adjacent(self):
        data = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1009),
                        symbol("_ZN3Bar3oneEv", 0x1011), symbol("plain", 0x1019)])
        result = catalog(data)
        self.assertEqual(grouped_addresses(result), [0x1000, 0x1008])
        self.assertEqual(result["confidence_bins"], {"low": 1})
        self.assertEqual(result["counts"]["unclassified_functions"], 2)
        self.assertEqual(result["compiler"]["identity"], "UNKNOWN")
        self.assertEqual(result["compiler"]["source_match"], "NOT MATCHED")
        self.assertEqual(result["counts"]["proven_original_tus"], 0)
        group = result["groups"][0]
        for key in ("translation_unit_proven", "original_object_layout_recovered", "relocations_recovered"):
            self.assertIs(group[key], False)
        self.assertFalse(result["staging_contract"]["automatic_staging_allowed"])
        self.assertEqual(result, catalog(data))

    def test_whole_namespace_prefix_is_not_a_shared_class_scope(self):
        data = fixture([symbol("_ZN2ns3Foo3oneEv"), symbol("_ZN2ns3Bar3twoEv", 0x1009)])
        self.assertEqual(catalog(data)["groups"], [])
        for name in ("_Z3foov", "_ZN3FooI3BarE3oneEv", "_ZN3FooS_3oneEv", "_ZThn4_N3Foo3oneEv", "_ZN999FooE"):
            self.assertIsNone(tu._scope(name), name)
        self.assertEqual(tu._scope("_ZNK2ns3Foo3oneEv"), ("ns", "Foo"))

    def test_scope_requires_consecutive_order_in_both_builds(self):
        a = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1009)])
        variants = [
            fixture([symbol("_ZN3Foo3oneEv", 0x1009), symbol("_ZN3Foo3twoEv")]),
            fixture([symbol("_ZN3Foo3oneEv"), symbol("unpaired", 0x1009),
                     symbol("_ZN3Foo3twoEv", 0x1011)]),
        ]
        for b in variants:
            self.assertEqual(catalog(a, b)["groups"], [])
        far = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1041)])
        self.assertEqual(catalog(far, max_neighbor_gap=8)["groups"], [])
        self.assertEqual(len(catalog(far, max_neighbor_gap=64)["groups"]), 1)

    def test_linked_section_transition_breaks_scope_run(self):
        data = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1061, section=7)])
        # Move a second executable fixture section immediately after .text.
        data = patch_section(data, 7, 2, 6)
        data = patch_section(data, 7, 3, 0x1060)
        # Independently map the second section with its own executable PT_LOAD.
        # Both function ranges must qualify; only their section transition
        # should prevent the otherwise-consecutive same-scope hypothesis.
        second = sections(data)[7]
        data = bytearray(data)
        struct.pack_into("<H", data, 44, 2)
        data.extend(struct.pack("<8I", 1, second[4], 0x1060, 0x1060, second[5], second[5], 5, 4))
        result = catalog(bytes(data))
        self.assertEqual(result["donor_window_counts"], {"exact_bytes": 2})
        self.assertTrue(all(not f["retail"]["exclusions"] for f in result["functions"]))
        self.assertEqual(result["groups"], [])

    def test_duplicates_aliases_overlaps_and_alias_split_fail_closed(self):
        duplicate = fixture([symbol("dup"), symbol("dup", 0x1011)])
        result = catalog(duplicate)
        self.assertEqual(result["groups"], [])
        self.assertTrue(all("PAIR_AMBIGUOUS_NAME" in f["reason_codes"] for f in result["functions"]))
        aliases = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Bar3oneEv"),
                           symbol("_ZN3Foo3twoEv", 0x1009)])
        self.assertEqual(catalog(aliases)["groups"], [])
        self.assertEqual(len(catalog(aliases)["functions"]), 2)
        self.assertEqual(catalog(aliases)["functions"][0]["symbol_alias_count"], 2)
        split = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Bar3oneEv", 0x1011)])
        self.assertEqual(catalog(aliases, split)["groups"], [])
        overlap = fixture([symbol("long", size=32), symbol("tail", 0x1019, size=16),
                           symbol("next", 0x1029)])
        result = catalog(overlap)
        self.assertTrue(all("UNSAFE_RETAIL_RANGE" in f["reason_codes"] for f in result["functions"][:2]))
        self.assertEqual(result["groups"], [])

    def test_repeated_identical_symbol_entries_and_order_are_deterministic(self):
        a = fixture([symbol("_ZN3Foo3twoEv", 0x1009), symbol("_ZN3Foo3oneEv"),
                     symbol("_ZN3Foo3oneEv")])
        b = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3oneEv"),
                     symbol("_ZN3Foo3twoEv", 0x1009)])
        # The ELF hash is part of identity; within a pin, symbol table iteration
        # order does not affect membership/IDs. Shuffle only inspected metadata.
        report = vc.compare(a, b, retail_sha256=vc.digest(a), debug_sha256=vc.digest(b))
        first = tu._catalog(report, tu.MAX_FUNCTIONS, 256, 256)
        report["comparison"]["functions"].reverse()
        report["retail"]["names"] = dict(reversed(list(report["retail"]["names"].items())))
        self.assertEqual(first, tu._catalog(report, tu.MAX_FUNCTIONS, 256, 256))
        self.assertEqual(len(first["functions"]), 2)

    def test_arm_thumb_explicit_modes_and_mode_conflicts(self):
        data = fixture([symbol("_ZN3Foo3oneEv", 0x1000), symbol("_ZN3Foo3twoEv", 0x1009)])
        result = catalog(data)
        self.assertEqual([f["retail"]["mode"] for f in result["functions"]], ["arm", "thumb"])
        self.assertEqual(result["functions"][1]["retail"]["raw_value"], 0x1009)
        other = fixture([symbol("_ZN3Foo3oneEv", 0x1001), symbol("_ZN3Foo3twoEv", 0x1009)])
        self.assertEqual(catalog(data, other)["groups"], [])
        conflict = fixture([symbol("_ZN3Foo3oneEv"), symbol("$a", 0x1000, 0, info=0)])
        self.assertIn("UNSAFE_RETAIL_RANGE", catalog(conflict)["functions"][0]["reason_codes"])

    def test_empty_or_plain_metadata_does_not_create_tus(self):
        for data in (fixture([]), fixture([symbol("plain")])):
            result = catalog(data)
            self.assertEqual(result["groups"], [])
            self.assertEqual(result["counts"]["proven_original_tus"], 0)
        other = fixture([symbol("different_name")])
        result = catalog(fixture([symbol("plain")]), other)
        self.assertEqual(result["functions"][0]["reason_codes"], ["NO_SHARED_SYMBOL"])

    def test_limits_and_hash_pins_are_enforced(self):
        data = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1009)])
        with self.assertRaisesRegex(tu.CatalogError, "function budget"):
            catalog(data, max_functions=1)
        result = catalog(data, max_group_members=1)
        self.assertEqual(result["groups"], [])
        self.assertTrue(all("GROUP_MEMBER_LIMIT" in f["reason_codes"] for f in result["functions"]))
        for changes in ({"max_functions": 0}, {"max_group_members": 257},
                        {"max_neighbor_gap": 65537}, {"max_neighbor_gap": True}):
            with self.assertRaises(tu.CatalogError):
                catalog(data, **changes)
        with self.assertRaisesRegex(vc.CompareError, "SHA-256 mismatch"):
            tu.build_catalog(data, data)
        with self.assertRaisesRegex(tu.CatalogError, "pin"):
            tu.build_catalog(data, data, retail_sha256="bad")
        with mock.patch.object(vc, "MAX_BYTES", 100):
            with self.assertRaises(vc.CompareError):
                catalog(data)
        for bad in (b"", data[:52], b"not ELF"):
            with self.assertRaises((vc.CompareError, ValueError)):
                catalog(bad)

    @unittest.skipUnless(HAVE_DWARF, "optional local pyelftools unavailable")
    def test_donor_cu_transfer_exact_and_changed_sizes_and_addresses(self):
        a = fixture([symbol("one"), symbol("two", 0x1011)])
        b = fixture([symbol("one", 0x3001), symbol("two", 0x3011, size=12)], base=0x3000)
        b = bytearray(b)
        b[sections(b)[1][4] + 16] = 23
        b = dwarf_fixture(bytes(b), [("PRIVATE_TU_SENTINEL.cpp", [(0x3000, 8, True, False),
                                                               (0x3010, 0x301c, False, False)])])
        result = catalog(a, b)
        self.assertEqual(result["confidence_bins"], {"medium": 1})
        self.assertEqual(result["donor_window_counts"], {"different_bytes": 1, "exact_bytes": 1})
        self.assertEqual(grouped_addresses(result), [0x1000, 0x1010])
        self.assertIn("DONOR_SIZE_CHANGED", result["functions"][1]["reason_codes"])
        self.assertTrue(all("DONOR_ADDRESS_CHANGED" in f["reason_codes"] for f in result["functions"]))
        self.assertEqual(result["groups"][0]["evidence"]["origin"], "donor")
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertEqual(result["counts"]["source_matched_functions"], 0)

    @unittest.skipUnless(HAVE_DWARF, "optional local pyelftools unavailable")
    def test_retail_direct_cu_clue_wins_but_names_and_ranges_conflict(self):
        base = fixture([symbol("one"), symbol("two", 0x1011)])
        good = [("PRIVATE_A.cpp", [(0x1000, 8, True, False), (0x1010, 8, True, False)])]
        a = dwarf_fixture(base, good)
        result = catalog(a)
        self.assertEqual(result["confidence_bins"], {"high": 1})
        self.assertFalse(result["groups"][0]["translation_unit_proven"])
        b = dwarf_fixture(base, [("PRIVATE_B.cpp", good[0][1])])
        self.assertEqual(catalog(a, b)["groups"], [])
        self.assertIn("RETAIL_DONOR_CU_NAME_CONFLICT", catalog(a, b)["functions"][0]["reason_codes"])
        bad = dwarf_fixture(base, [("PRIVATE_A.cpp", [(0x1000, 16, True, False)])])
        self.assertIn("DWARF_SYMBOL_EXTENT_CONFLICT", catalog(base, bad)["functions"][0]["reason_codes"])

    @unittest.skipUnless(HAVE_DWARF, "optional local pyelftools unavailable")
    def test_ambiguous_cus_declarations_and_unassociated_names_never_promote(self):
        base = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1009)])
        overlap = dwarf_fixture(base, [("PRIVATE_A.cpp", [(0x1000, 8, True, False)]),
                                      ("PRIVATE_B.cpp", [(0x1000, 8, True, False)])])
        result = catalog(base, overlap)
        self.assertEqual(result["groups"], [])
        self.assertIn("AMBIGUOUS_DWARF_CU", result["functions"][0]["reason_codes"])
        # Named CU and declaration alone cannot promote a scope hypothesis.
        declarations = dwarf_fixture(base, [("PRIVATE_A.cpp", [(0x1000, 8, True, True)])])
        self.assertEqual(catalog(base, declarations)["confidence_bins"], {"low": 1})
        unnamed_functions = dwarf_fixture(fixture([symbol("plain")]), [("PRIVATE_A.cpp", [])])
        self.assertEqual(catalog(unnamed_functions)["groups"], [])

    @unittest.skipUnless(HAVE_DWARF, "optional local pyelftools unavailable")
    def test_present_but_unreadable_dwarf_and_missing_backend_block(self):
        malformed = add_sections(fixture(), [(".debug_info", b"broken")])
        with self.assertRaisesRegex(vc.CompareError, "DWARF parse failed"):
            catalog(malformed)
        good = dwarf_fixture(fixture(), [("PRIVATE.cpp", [(0x1000, 8, True, False)])])
        with mock.patch.dict(sys.modules, {"elftools": None}):
            with self.assertRaisesRegex(vc.CompareError, "pyelftools"):
                catalog(good)
        wrong_tag = bytearray(good)
        # Replace only the synthetic top-DIE abbrev tag with DW_TAG_namespace.
        wrong_tag[sections(good)[8][4] + 1] = 0x39
        with self.assertRaisesRegex(tu.CatalogError, "top DIE"):
            catalog(bytes(wrong_tag))

    def test_public_summary_fixed_allowlist_and_size(self):
        data = fixture([symbol("PRIVATE_SYMBOL_SENTINEL")])
        result = catalog(data)
        result["unexpected_private_path"] = "/PRIVATE/owner"
        result["counts"]["PRIVATE_COUNT_NAME"] = 23
        result["references"]["premium"]["path"] = "/PRIVATE/owner"
        result["references"]["premium"]["edition_pin"] = "PRIVATE_EDITION"
        result["tool_source_hashes"]["path"] = "PRIVATE"
        result["confidence_bins"]["PRIVATE"] = 3
        summary = tu.public_summary(result)
        serialized = json.dumps(summary)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("functions", summary)
        self.assertNotIn("groups", summary)
        self.assertNotIn("address", serialized)
        self.assertLess(len(serialized), tu.MAX_SUMMARY_BYTES)
        self.assertEqual(summary["references"]["premium"]["edition_pin"], "explicit_unrecognized_reference")
        result["counts"]["candidate_groups"] = "PRIVATE"
        with self.assertRaises(tu.CatalogError):
            tu.public_summary(result)

    def test_compiler_comment_is_only_a_clue(self):
        data = add_sections(fixture(), [(".comment", b"GCC: (GNU) 4.4.3\0")])
        result = catalog(data)
        self.assertEqual(result["compiler_comment_version_clues"]["premium"], ["4.4.3"])
        self.assertEqual(result["compiler"], {"identity": "UNKNOWN", "flags": "UNKNOWN", "source_match": "NOT MATCHED"})
        self.assertEqual(result["groups"], [])

    @unittest.skipUnless(HAVE_DWARF, "optional local pyelftools unavailable")
    def test_unsupported_range_lists_and_conflicting_linkage_stay_unclassified(self):
        base = fixture([symbol("_ZN3Foo3oneEv"), symbol("_ZN3Foo3twoEv", 0x1009)])
        donor = dwarf_fixture(base, [("PRIVATE.cpp", [(0x1000, 8, True, False)])])
        report = vc.compare(base, donor, retail_sha256=vc.digest(base), debug_sha256=vc.digest(donor))
        die = report["debug"]["dwarf"]["function_dies"][0]
        for key, value, reason in (("has_ranges", True, "DWARF_RANGE_UNSUPPORTED"),
                                   ("linkage_name_sha256", "f" * 64, "DWARF_LINKAGE_CONFLICT")):
            die[key] = value
            result = tu._catalog(report, 100, 256, 256)
            self.assertEqual(result["groups"], [])
            self.assertIn(reason, result["functions"][0]["reason_codes"])
            del die[key]

    def test_many_aliases_emit_digests_not_a_name_catalog(self):
        data = fixture([symbol(f"PRIVATE_ALIAS_{i}") for i in range(2000)])
        result = catalog(data)
        self.assertEqual(len(result["functions"]), 1)
        self.assertEqual(result["functions"][0]["symbol_alias_count"], 2000)
        self.assertNotIn("PRIVATE_ALIAS", json.dumps(result))
        self.assertLess(len(json.dumps(result)), 8192)

    def test_streaming_output_cap_preserves_existing_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "catalog.json"
            out.write_text("previous")
            with self.assertRaisesRegex(tu.CatalogError, "byte budget"):
                tu._write_json(out, {"synthetic": list(range(100))}, 32)
            self.assertEqual(out.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [out])
            tu._write_json(out, {"synthetic": 1}, 100)
            self.assertEqual(json.loads(out.read_text()), {"synthetic": 1})
            if os.name == "posix":
                self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_output_paths_reject_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(tu, "ROOT", Path(directory)):
            root = Path(directory)
            with self.assertRaises(tu.CatalogError):
                tu._output_path(root / "public.json")
            cache = root / ".cache"
            cache.mkdir()
            self.assertEqual(tu._output_path(cache / "a.json"), cache / "a.json")
            with self.assertRaises(tu.CatalogError):
                tu._output_path(cache / ".." / "public.json")
            if os.name == "posix":
                (cache / "escape").symlink_to(root, target_is_directory=True)
                with self.assertRaises(tu.CatalogError):
                    tu._output_path(cache / "escape" / "a.json")

    def test_cli_spawned_worker_synthetic_success_and_scrubbed_failure(self):
        cache = tu.ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            root = Path(directory)
            data = fixture([symbol("PRIVATE_SENTINEL")])
            source = root / "PRIVATE_INPUT.elf"
            source.write_bytes(data)
            out, summary = root / "catalog.json", root / "summary.json"
            command = [sys.executable, str(tu.ROOT / "tools/build_donor_tu_catalog.py"),
                       "--retail", str(source), "--donor", str(source),
                       "--retail-sha256", vc.digest(data), "--donor-sha256", vc.digest(data),
                       "--out", str(out), "--summary-out", str(summary)]
            process = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), json.loads(summary.read_text()))
            self.assertEqual(json.loads(out.read_text())["schema"], tu.SCHEMA)
            self.assertNotIn("PRIVATE", process.stdout + process.stderr)
            before = source.read_bytes()
            process = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 2)
            self.assertNotIn("PRIVATE", process.stdout + process.stderr)
            self.assertEqual(source.read_bytes(), before)

    def test_cross_platform_timeout_terminates_child(self):
        process = mock.Mock(exitcode=-1)
        process.is_alive.side_effect = [True, False]
        context = mock.Mock()
        context.Process.return_value = process
        with mock.patch.object(tu.multiprocessing, "get_context", return_value=context), contextlib.redirect_stderr(io.StringIO()):
            status = tu.main(["--retail", "private", "--donor", "private", "--out", ".cache/test.json"])
        self.assertEqual(status, 2)
        process.join.assert_any_call(tu.TIMEOUT_SECONDS)
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
