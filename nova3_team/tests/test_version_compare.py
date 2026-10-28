"""Offline synthetic ELF fixtures only; no original code or assets.

Run: python -m unittest nova3_team.tests.test_version_compare -v
"""
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

from nova3_team import version_compare as vc
from nova3_team.tests.test_indexer import elf_fixture, patch_section, symbol


def sections(data):
    start = struct.unpack_from("<I", data, 32)[0]
    count = struct.unpack_from("<H", data, 48)[0]
    return [struct.unpack_from("<10I", data, start + 40 * i) for i in range(count)]


def add_sections(data, extra):
    """Append source-owned fixture payloads and rebuild only the section table."""
    result = bytearray(data)
    rows = list(sections(data))
    names = bytearray(data[rows[6][4]:rows[6][4] + rows[6][5]])
    for name, payload in extra:
        n = len(names)
        names.extend(name.encode() + b"\0")
        rows.append((n, 1, 0, 0, len(result), len(payload), 0, 0, 1, 0))
        result.extend(payload)
    rows[6] = (*rows[6][:4], len(result), len(names), *rows[6][6:])
    result.extend(names)
    result.extend(bytes(-len(result) % 4))
    struct.pack_into("<I", result, 32, len(result))
    struct.pack_into("<H", result, 48, len(rows))
    for row in rows:
        result.extend(struct.pack("<10I", *row))
    return bytes(result)


def fixture(entries=None, dynamic=(), *, base=0x1000, fill=0, dwarf=False):
    data = bytearray(elf_fixture(entries, dynamic))
    text = sections(data)[1]
    data[text[4]:text[4] + text[5]] = bytes([fill]) * text[5]
    data = bytearray(patch_section(data, 1, 3, base))
    # An executable PT_LOAD proves VA-to-file mapping independently of sh_offset.
    struct.pack_into("<I", data, 28, len(data))
    struct.pack_into("<HH", data, 42, 32, 1)
    data.extend(struct.pack("<8I", 1, text[4], base, base, text[5], text[5], 5, 4))
    if dwarf:
        # DWARF v2, one CU, a named base type/typedef and one concrete subprogram.
        # All strings are synthetic privacy sentinels, never reference content.
        abbrev = (b"\x01\x11\x01\x03\x08\x10\x06\x00\x00"
                  b"\x02\x24\x00\x03\x08\x00\x00"
                  b"\x03\x2e\x00\x03\x08\x11\x01\x12\x01\x00\x00"
                  b"\x04\x16\x00\x03\x08\x00\x00\x00")
        dies = (b"\x01PRIVATE_SOURCE_PATH_SENTINEL\0" + struct.pack("<I", 0)
                + b"\x02PRIVATE_TYPE_SENTINEL\0\x04PRIVATE_TYPEDEF_SENTINEL\0\x03PRIVATE_FUNCTION_SENTINEL\0"
                + struct.pack("<II", base, base + 8) + b"\0")
        body = struct.pack("<HI", 2, 0) + b"\x04" + dies
        info = struct.pack("<I", len(body)) + body
        header = (b"\x01\x01\xfb\x0e\x0d" + bytes([0, 1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 1])
                  + b"\0PRIVATE_FILE_SENTINEL\0\0\0\0\0")
        program = b"\0\x05\x02" + struct.pack("<I", base) + b"\x01\x02\x08\0\x01\x01"
        line_body = struct.pack("<HI", 2, len(header)) + header + program
        data = bytearray(add_sections(data, [(".debug_abbrev", abbrev), (".debug_info", info),
                                             (".debug_line", struct.pack("<I", len(line_body)) + line_body)]))
    return bytes(data)


def compare(a, b):
    return vc.compare(a, b, retail_sha256=vc.digest(a), debug_sha256=vc.digest(b))


class VersionCompareTests(unittest.TestCase):
    def test_relocated_named_function_and_aliases_deduplicate(self):
        a = fixture([symbol("_Z3foov"), symbol("alias")], dynamic=[symbol("_Z3foov")])
        b = fixture([symbol("_Z3foov", 0x3001), symbol("alias", 0x3001)], base=0x3000)
        report = compare(a, b)
        summary = report["comparison"]["summary"]
        self.assertEqual(summary["alias_deduplicated_pair_counts"], {"exact_bytes": 1})
        self.assertEqual(summary["name_status_counts"], {"exact_bytes": 2})
        self.assertEqual(summary["shared_mangled_names"], 1)
        self.assertEqual([t["summary"]["defined_func_entries"] for t in report["retail"]["tables"]], [2, 1])
        self.assertEqual(report["retail"]["candidates"][0x1000]["file_offset"], 52)
        self.assertEqual(report["debug"]["candidates"][0x3000]["file_offset"], 52)
        self.assertEqual(report, compare(a, b))

    def test_offset_equality_is_never_used_for_pairing(self):
        a = fixture([symbol("foo")])
        b = fixture([symbol("foo", 0x1021)])
        # Equal corresponding code at distinct offsets is accepted.
        self.assertEqual(compare(a, b)["comparison"]["summary"]["name_status_counts"], {"exact_bytes": 1})
        b = bytearray(b)
        b[52 + 32] = 7
        self.assertEqual(compare(a, bytes(b))["comparison"]["summary"]["name_status_counts"], {"different_bytes": 1})

    def test_divergent_bytes_sizes_and_overloads(self):
        a = fixture([symbol("_Z3fooi"), symbol("_Z3foov", 0x1021), symbol("old", 0x1041)])
        b = fixture([symbol("_Z3fooi", size=12), symbol("_Z3food", 0x1021)], fill=1)
        summary = compare(a, b)["comparison"]["summary"]
        self.assertEqual(summary["shared_mangled_names"], 1)
        self.assertEqual(summary["retail_only_mangled_names"], 1)
        self.assertEqual(summary["debug_only_mangled_names"], 1)
        self.assertEqual(summary["size_different_names"], 1)
        self.assertEqual(summary["name_status_counts"], {"different_bytes": 1})

    def test_duplicate_names_distinct_definitions_are_ambiguous(self):
        a = fixture([symbol("duplicate"), symbol("duplicate", 0x1021)])
        b = fixture([symbol("duplicate")])
        self.assertEqual(compare(a, b)["comparison"]["summary"]["name_status_counts"], {"ambiguous_name": 1})
        # Repeated identical entries are harmless evidence duplication.
        a = fixture([symbol("duplicate"), symbol("duplicate")])
        self.assertEqual(compare(a, b)["comparison"]["summary"]["name_status_counts"], {"exact_bytes": 1})

    def test_alias_split_and_merge_rejected_in_both_directions(self):
        a = fixture([symbol("foo"), symbol("bar")])
        b = fixture([symbol("foo"), symbol("bar", 0x1021)])
        for left, right in ((a, b), (b, a)):
            s = compare(left, right)["comparison"]["summary"]
            self.assertEqual(s["name_status_counts"], {"ambiguous_alias_mapping": 2})
            self.assertEqual(s["alias_deduplicated_pair_counts"], {})

    def test_arm_thumb_mode_mismatch_and_mapping_symbol_conflicts(self):
        a, b = fixture([symbol("foo")]), fixture([symbol("foo", 0x1000)])
        self.assertEqual(compare(a, b)["comparison"]["summary"]["name_status_counts"], {"mode_mismatch": 1})
        for marks in ([symbol("$a", 0x1000, 0, info=0)],
                      [symbol("$t", 0x1000, 0, info=0), symbol("$a", 0x1004, 0, info=0)]):
            data = fixture([symbol("foo"), *marks])
            self.assertTrue(vc.inspect(data, vc.digest(data))["candidates"][0x1000]["exclusions"])
        # Literal pools inside a symbol are byte-window data, not decoded instructions.
        data = fixture([symbol("foo"), symbol("$t", 0x1000, 0, info=0),
                        symbol("$d", 0x1004, 0, info=0)])
        self.assertFalse(vc.inspect(data, vc.digest(data))["candidates"][0x1000]["exclusions"])

    def test_all_overlapping_participants_excluded_but_adjacent_allowed(self):
        a = fixture([symbol("long", size=32), symbol("inside", 0x1005, 4),
                     symbol("tail", 0x1019, 16), symbol("adjacent", 0x1029, 8)])
        r = vc.inspect(a, vc.digest(a))
        self.assertEqual([p for p, c in r["candidates"].items() if not c["exclusions"]], [0x1028])
        for addr in (0x1000, 0x1004, 0x1018):
            self.assertIn("overlapping_function_ranges", r["candidates"][addr]["exclusions"])

    def test_conflicting_alias_size_mode_and_dynamic_evidence_reject(self):
        for other in (symbol("alias", size=4), symbol("alias", value=0x1000)):
            a = fixture([symbol("foo"), other])
            self.assertIn("conflicting_alias_bounds_or_modes",
                          vc.inspect(a, vc.digest(a))["candidates"][0x1000]["exclusions"])
        a = fixture([symbol("foo")], dynamic=[symbol("foo", size=4)])
        self.assertEqual(compare(a, a)["comparison"]["summary"]["name_status_counts"], {"excluded_range": 1})

    def test_reserved_or_nonexecuting_symbol_cannot_borrow_other_function_range(self):
        for shndx in (0xfff1, 7):
            a = fixture([symbol("foo", section=shndx)], dynamic=[symbol("other")])
            b = fixture([symbol("foo")])
            self.assertEqual(compare(a, b)["comparison"]["summary"]["name_status_counts"], {"unmapped_range": 1})

    def test_zero_unaligned_and_segment_mapping_exclusions(self):
        for item in (symbol(size=0), symbol(size=7), symbol(value=0x1002)):
            a = fixture([item])
            self.assertEqual(vc.inspect(a, vc.digest(a))["summary"]["safe_candidate_ranges"], 0)
        a = fixture()
        phoff = struct.unpack_from("<I", a, 28)[0]
        for field, value in ((1, 0), (6, 4)):
            bad = bytearray(a)
            struct.pack_into("<I", bad, phoff + field * 4, value)
            self.assertEqual(vc.inspect(bytes(bad), vc.digest(bad))["summary"]["safe_candidate_ranges"], 0)
        # Executable sections without load segments are not safe runtime ranges.
        raw = elf_fixture()
        self.assertEqual(vc.inspect(raw, vc.digest(raw))["summary"]["safe_candidate_ranges"], 0)

    def test_overlapping_executable_sections_block(self):
        original = fixture()
        for field, value in ((3, 0x1000), (4, sections(original)[1][4])):
            data = patch_section(original, 7, 2, 6)
            data = patch_section(data, 7, field, value)
            with self.assertRaisesRegex(vc.CompareError, "overlapping executable sections"):
                vc.inspect(data, vc.digest(data))

    def test_missing_dwarf_is_explicit(self):
        a = fixture()
        d = vc.inspect(a, vc.digest(a))["dwarf"]
        self.assertEqual(d["status"], "absent")
        self.assertEqual(d["counts"]["compile_units"], 0)
        self.assertEqual(d["counts"]["symbol_entries_with_line_coverage"], 0)

    def test_relocation_sites_counted_and_malformed_metadata_rejected(self):
        a = add_sections(fixture(), [(".rel.text", struct.pack("<II", 0x1004, (1 << 8) | 2))])
        for field, value in ((1, 9), (6, 3), (7, 1), (9, 8)):
            a = patch_section(a, 8, field, value)
        report = vc.inspect(a, vc.digest(a))
        self.assertEqual(report["summary"]["relocation_entries"], 1)
        self.assertEqual(report["candidates"][0x1000]["relocation_entries_in_range"], 1)
        for field, value in ((6, 99), (6, 1), (7, 99), (9, 7)):
            bad = patch_section(a, 8, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(vc.CompareError):
                vc.inspect(bad, vc.digest(bad))
        # A nonzero relocation symbol index must reference an existing entry.
        bad = bytearray(a)
        struct.pack_into("<I", bad, sections(a)[8][4] + 4, 99 << 8)
        with self.assertRaisesRegex(vc.CompareError, "relocation symbol index"):
            vc.inspect(bytes(bad), vc.digest(bad))

    @unittest.skipUnless(importlib.util.find_spec("elftools"), "optional local pyelftools unavailable")
    def test_actual_dwarf_counts_line_coverage_and_string_privacy(self):
        a = fixture(dwarf=True)
        report = compare(a, a)
        d = report["debug"]["dwarf"]
        for key, count in dict(compile_units=1, named_types=2, function_dies=1,
                               concrete_function_dies=1, line_rows=2, line_unique_addresses=1,
                               line_executable_bytes=8, symbol_entries_with_line_coverage=1,
                               function_die_low_pc_at_symbol_entry=1).items():
            self.assertEqual(d["counts"][key], count, key)
        self.assertEqual(d["counts"]["named_type_tag_counts"], {"DW_TAG_base_type": 1, "DW_TAG_typedef": 1})
        self.assertEqual(report["dwarf_comparison"]["shared_sections"], 3)
        self.assertEqual(len(report["dwarf_comparison"]["identical_sections"]), 3)
        self.assertNotIn("PRIVATE_", json.dumps(report))
        bad = add_sections(fixture(), [(".debug_info", b"broken")])
        with self.assertRaisesRegex(vc.CompareError, "DWARF parse failed"):
            vc.inspect(bad, vc.digest(bad))

    def test_dwarf_present_without_backend_blocks(self):
        a = fixture(dwarf=True)
        with mock.patch.dict(sys.modules, {"elftools": None}):
            with self.assertRaisesRegex(vc.CompareError, "pyelftools is required"):
                vc.inspect(a, vc.digest(a))

    def test_hash_mismatch_and_corrupt_or_wrong_architecture(self):
        a = fixture()
        with self.assertRaisesRegex(vc.CompareError, "SHA-256 mismatch"):
            vc.inspect(a, "0" * 64)
        variants = [b"", b"not an elf"]
        for offset, fmt, value in ((4, "B", 2), (5, "B", 2), (18, "H", 62),
                                   (36, "I", 0), (32, "I", 0xffffffff), (48, "H", 4097)):
            bad = bytearray(a)
            struct.pack_into("<" + fmt, bad, offset, value)
            variants.append(bytes(bad))
        variants.extend([patch_section(a, 3, 9, 17), patch_section(a, 3, 6, 99),
                         patch_section(a, 1, 5, 1), patch_section(a, 3, 1, 1)])
        for bad in variants:
            with self.subTest(length=len(bad)), self.assertRaises(vc.CompareError):
                vc.inspect(bad, vc.digest(bad))
        for length in range(52):
            with self.subTest(length=length), self.assertRaises(vc.CompareError):
                vc.inspect(a[:length], vc.digest(a[:length]))

    def test_limits_reject_before_expensive_work(self):
        a = fixture(dwarf=True)
        for limit in ("MAX_BYTES", "MAX_SECTIONS", "MAX_SYMBOLS", "MAX_LINE_BYTES", "MAX_DEBUG_BYTES",
                      "MAX_TOTAL_NAME_CHARS"):
            with self.subTest(limit=limit), mock.patch.object(vc, limit, 0):
                with self.assertRaises(vc.CompareError):
                    vc.inspect(a, vc.digest(a))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oversize.elf"
            path.write_bytes(a)
            with mock.patch.object(vc, "MAX_BYTES", 1), self.assertRaises(vc.CompareError):
                vc._read(path)

    def test_read_rejects_fifo_without_waiting(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fifo"
            os.mkfifo(path)
            with self.assertRaisesRegex(vc.CompareError, "nonregular"):
                vc._read(path)

    def test_cli_deterministic_private_atomic_and_no_input_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = fixture([symbol("PRIVATE_SYMBOL")]), fixture([symbol("PRIVATE_SYMBOL")], fill=1)
            retail, debug = root / "r.elf", root / "d.elf"
            retail.write_bytes(a)
            debug.write_bytes(b)
            out = root / ".cache/report.json"
            argv = ["--retail", str(retail), "--debug", str(debug), "--out", str(out)]
            # Production CLI has no alternate-hash override. Patch only its pure
            # comparison function for these synthetic acceptance runs.
            original = vc.compare
            def synthetic_compare(r, d):
                return original(r, d, retail_sha256=vc.digest(a), debug_sha256=vc.digest(b))
            outputs, reports = [], []
            with mock.patch.object(vc, "ROOT", root), mock.patch.object(vc, "compare", synthetic_compare):
                for _ in range(2):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(vc.main(argv), 0)
                    outputs.append(stdout.getvalue())
                    reports.append(out.read_bytes())
                self.assertEqual(list(out.parent.iterdir()), [out])
                self.assertEqual(reports[0], reports[1])
                self.assertEqual(outputs[0], outputs[1])
                self.assertNotIn("PRIVATE_SYMBOL", outputs[0])
                self.assertNotIn(tmp, outputs[0])
                self.assertEqual(retail.read_bytes(), a)
                self.assertEqual(debug.read_bytes(), b)
                debug.write_bytes(b"corrupt")
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(vc.main(argv), 2)
                self.assertIn("SHA-256 mismatch", stderr.getvalue())
                self.assertEqual(out.read_bytes(), reports[0])

    def test_output_escape_alias_and_real_cli_hash_enforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / ".cache"
            cache.mkdir()
            source = cache / "input.json"
            source.write_bytes(fixture())
            with mock.patch.object(vc, "ROOT", root):
                for path in (root / "public.json", cache, cache / "report.txt"):
                    with self.assertRaises(vc.CompareError):
                        vc._output_path(path)
                (cache / "link").symlink_to(root, target_is_directory=True)
                with self.assertRaises(vc.CompareError):
                    vc._output_path(cache / "link/escape.json")
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(vc.main(["--retail", str(source), "--debug", str(source),
                                              "--out", str(source)]), 2)
                self.assertIn("alias", stderr.getvalue())
            # Subprocess verifies actual entry point cannot accept fixture hashes.
            run = subprocess.run([sys.executable, "-m", "nova3_team.version_compare",
                                  "--retail", str(source), "--debug", str(source)],
                                 capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, b"")
            self.assertIn(b"SHA-256 mismatch", run.stderr)
            self.assertNotIn(tmp.encode(), run.stderr)


if __name__ == "__main__":
    unittest.main()
