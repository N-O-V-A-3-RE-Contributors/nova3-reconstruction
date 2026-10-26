"""Synthetic ELF/JSON fixtures only; no original game data or match claims.

Reproduce: python -m unittest nova3_team.tests.test_indexer -v
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import indexer
from nova3_team.autofeed import generate_from_ledger
from nova3_team.indexer import LedgerError, SCHEMA, build_ledger


def symbol(name="entry", value=0x1001, size=8, section=1, info=0x12):
    return dict(name=name, value=value, size=size, section=section, info=info)


def elf_fixture(symbols=None, dynamic=(), endian="<", kind=3, extended=False):
    """Construct ELF32 sections independently of the production reader.

    .text contains zero bytes, not a compiled program. Addresses are synthetic.
    Fixed sections: NULL, text, strtab, symtab, dynstr, dynsym, shstrtab, data.
    """
    if symbols is None:
        symbols = [symbol()]

    def table(entries):
        strings = bytearray(b"\0")
        packed = bytearray(16)  # STN_UNDEF
        for item in entries:
            name = item["name"]
            encoded = name if isinstance(name, bytes) else name.encode("utf-8")
            name_offset = len(strings)
            strings.extend(encoded + b"\0")
            packed.extend(struct.pack(endian + "IIIBBH", name_offset,
                                      item["value"], item["size"], item["info"],
                                      0, item["section"]))
        return bytes(strings), bytes(packed)

    strings, symtab = table(symbols)
    dynstrings, dynsym = table(dynamic)
    names = b"\0.text\0.strtab\0.symtab\0.dynstr\0.dynsym\0.shstrtab\0.data\0"
    # name, type, flags, VA, payload, link, alignment, entry size
    descriptions = [
        (".text", 1, 6, 0x1000, bytes(96), 0, 4, 0),
        (".strtab", 3, 0, 0, strings, 0, 1, 0),
        (".symtab", 2, 0, 0, symtab, 2, 4, 16),
        (".dynstr", 3, 0, 0, dynstrings, 0, 1, 0),
        (".dynsym", 11, 0, 0, dynsym, 4, 4, 16),
        (".shstrtab", 3, 0, 0, names, 0, 1, 0),
        (".data", 1, 3, 0x2000, bytes(16), 0, 4, 0),
    ]
    result = bytearray(52)
    sections = [(0, 0, 0, 0, 0, 8 if extended else 0, 6 if extended else 0, 0, 0, 0)]
    for name, kind_id, flags, address, payload, link, align, entsize in descriptions:
        result.extend(bytes((-len(result)) % align))
        offset = len(result)
        result.extend(payload)
        sections.append((names.index(name.encode() + b"\0"), kind_id, flags,
                         address, offset, len(payload), link,
                         1 if kind_id in (2, 11) else 0, align, entsize))
    result.extend(bytes((-len(result)) % 4))
    shoff = len(result)
    for section in sections:
        result.extend(struct.pack(endian + "10I", *section))
    ident = b"\x7fELF\x01" + (b"\x01" if endian == "<" else b"\x02") + b"\x01" + bytes(9)
    result[:52] = ident + struct.pack(endian + "HHIIIIIHHHHHH", kind, 40, 1,
                                     0x1001, 0, shoff, 0x05000000, 52, 0, 0, 40,
                                     0 if extended else 8, 0xffff if extended else 6)
    return bytes(result)


def patch_section(data, section, field, value):
    result = bytearray(data)
    shoff = struct.unpack_from("<I", result, 32)[0]
    struct.pack_into("<I", result, shoff + section * 40 + field * 4, value)
    return bytes(result)


def ghidra_fixture(data, **changes):
    record = dict(binary_sha256=hashlib.sha256(data).hexdigest(),
                  architecture="ARM:LE:32:v7", address=0x1000, raw_value=0x1001,
                  thumb=1, symbol="entry", symbol_size=8,
                  ghidra_image_base=0x10000, ghidra_address=0x11000,
                  ghidra_version="synthetic", byte_mapping_validated=True,
                  analysis_scope="synthetic selected-function fixture",
                  calls=[{"from": "00011002", "to": "00011020", "type": "UNCONDITIONAL_CALL"}],
                  # These fields deliberately must not be forwarded.
                  c="SYNTHETIC_PSEUDOCODE_SENTINEL", instructions=[{"bytes": "SENTINEL_BYTES"}],
                  incoming=[{"from": "00011040", "type": "DATA"}])
    record.update(changes)
    return record


class IndexerTests(unittest.TestCase):
    def test_stable_schema_ids_aliases_and_provenance(self):
        data = elf_fixture([symbol("z_alias"), symbol("entry"),
                            symbol("arm_entry", 0x1020, 12)],
                           dynamic=[symbol("entry")])
        ledger = build_ledger(data)
        self.assertEqual(set(ledger), {"schema", "exporter_version", "binary", "coverage", "functions"})
        self.assertEqual(ledger["schema"], SCHEMA)
        self.assertEqual(ledger["binary"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(ledger["binary"]["elf_type"], "ET_DYN")
        self.assertEqual(ledger["coverage"]["defined_function_symbols"], 4)
        self.assertEqual(ledger["coverage"]["function_count"], 2)
        self.assertFalse(ledger["coverage"]["inventory_complete"])
        thumb, arm = ledger["functions"]
        self.assertEqual(set(thumb), {"id", "address", "thumb", "size", "size_conflict",
                                     "name", "aliases", "symbols", "calls", "call_sources", "state"})
        self.assertEqual(thumb["id"], f'{hashlib.sha256(data).hexdigest()}:00001000:thumb')
        self.assertEqual(thumb["name"], "entry")
        self.assertEqual(thumb["aliases"], ["z_alias"])
        self.assertTrue(thumb["thumb"])
        self.assertIsNone(thumb["calls"])
        self.assertEqual(thumb["call_sources"], [])
        self.assertEqual(thumb["state"], "INVENTORIED")
        self.assertEqual({s["table"] for s in thumb["symbols"]}, {".symtab", ".dynsym"})
        self.assertEqual({s["raw_value"] for s in thumb["symbols"]}, {0x1001})
        self.assertEqual({s["file_offset"] for s in thumb["symbols"]}, {52})
        self.assertFalse(arm["thumb"])
        self.assertEqual((arm["address"], arm["size"]), (0x1020, 12))
        self.assertEqual(arm["id"], f'{hashlib.sha256(data).hexdigest()}:00001020:arm')
        self.assertEqual(ledger, build_ledger(data))
        self.assertEqual(ledger, json.loads(json.dumps(ledger)))

    def test_mode_conflicts_are_not_silently_merged(self):
        functions = build_ledger(elf_fixture([symbol(), symbol("arm", 0x1000)]))["functions"]
        self.assertEqual(len(functions), 2)
        self.assertEqual([f["thumb"] for f in functions], [False, True])
        self.assertEqual(len({f["id"] for f in functions}), 2)

    def test_sizes_zero_unnamed_non_utf8(self):
        data = elf_fixture([symbol(size=4), symbol("alias", size=12),
                            symbol("", 0x1021, 0), symbol(b"bad\xff", 0x1041, 0)])
        functions = build_ledger(data)["functions"]
        self.assertEqual(functions[0]["size"], 12)
        self.assertTrue(functions[0]["size_conflict"])
        self.assertEqual({s["size"] for s in functions[0]["symbols"]}, {4, 12})
        self.assertIsNone(functions[1]["name"])
        self.assertEqual(functions[1]["size"], 0)
        self.assertFalse(functions[1]["size_conflict"])
        self.assertEqual(functions[2]["name"].encode("utf-8", "surrogateescape"), b"bad\xff")
        self.assertEqual(functions, json.loads(json.dumps(functions)))

    def test_filter_undefined_reserved_noncode_and_nonfunctions(self):
        data = elf_fixture([symbol(), symbol("import", section=0),
                            symbol("absolute", section=0xfff1),
                            symbol("data_function", 0x2000, section=7),
                            symbol("object", info=0x11), symbol("$t", info=0),
                            symbol("$a", info=0), symbol("$d", info=0)])
        ledger = build_ledger(data)
        self.assertEqual(len(ledger["functions"]), 1)
        self.assertEqual(ledger["coverage"]["undefined_function_symbols"], 1)
        self.assertEqual(ledger["coverage"]["excluded_function_symbols"], 2)

    def test_endianness_extended_numbering_and_executable_kind(self):
        for endian in ("<", ">"):
            for extended in (False, True):
                with self.subTest(endian=endian, extended=extended):
                    ledger = build_ledger(elf_fixture(endian=endian, kind=2, extended=extended))
                    self.assertEqual(ledger["binary"]["elf_type"], "ET_EXEC")
                    self.assertEqual(ledger["binary"]["endianness"], "little" if endian == "<" else "big")
                    self.assertEqual(ledger["functions"][0]["address"], 0x1000)

    @unittest.skipUnless(shutil.which("readelf"), "optional independent binutils fixture check")
    def test_synthetic_fixture_agrees_with_readelf(self):
        for endian in ("<", ">"):
            data = elf_fixture([symbol(), symbol("alias"), symbol("arm_entry", 0x1020, 12)],
                               dynamic=[symbol()], endian=endian, extended=True)
            with self.subTest(endian=endian), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "synthetic.elf"
                path.write_bytes(data)
                result = subprocess.run(["readelf", "--file-header", "--symbols", str(path)],
                                        capture_output=True, text=True,
                                        env=dict(os.environ, LC_ALL="C"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn("ARM", result.stdout)
                reported = set()
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) == 8 and parts[3] == "FUNC":
                        reported.add((int(parts[1], 16), int(parts[2]), parts[7]))
                ledger = build_ledger(data)
                exported = {(s["raw_value"], s["size"], s["name"])
                            for f in ledger["functions"] for s in f["symbols"]}
                self.assertEqual(reported, exported)

    def test_sectionless_or_no_function_symbols_is_explicitly_incomplete(self):
        data = bytearray(elf_fixture())
        struct.pack_into("<I", data, 32, 0)
        struct.pack_into("<HHH", data, 46, 0, 0, 0)
        for fixture in (bytes(data[:52]), elf_fixture([])):
            ledger = build_ledger(fixture)
            self.assertEqual(ledger["functions"], [])
            self.assertFalse(ledger["coverage"]["inventory_complete"])

    def test_ghidra_rebase_calls_and_ignore_content_fields(self):
        data = elf_fixture([symbol(), symbol("callee", 0x1020, 8)])
        record = ghidra_fixture(data)
        record["calls"].extend([
            {"from": "ram:00011004", "to": "EXTERNAL:00000001", "type": "COMPUTED_CALL"},
            record["calls"][0]])
        ledger = build_ledger(data, [record, record])
        function = ledger["functions"][0]
        self.assertEqual(function["calls"], [
            {"from_address": 0x1002, "target_address": 0x1020,
             "target_location": "00011020", "type": "UNCONDITIONAL_CALL"},
            {"from_address": 0x1004, "target_address": None,
             "target_location": "EXTERNAL:00000001", "type": "COMPUTED_CALL"}])
        self.assertEqual(len(function["call_sources"]), 1)
        self.assertTrue(function["call_sources"][0]["byte_mapping_reported_validated"])
        self.assertEqual(ledger["coverage"]["functions_with_call_exports"], 1)
        self.assertIsNone(ledger["functions"][1]["calls"])
        serialized = json.dumps(ledger)
        for excluded in ("SYNTHETIC_PSEUDOCODE_SENTINEL", "SENTINEL_BYTES", "incoming"):
            self.assertNotIn(excluded, serialized)
        self.assertEqual(function["state"], "INVENTORIED")
        self.assertEqual(record["calls"][0]["from"], "00011002")  # Not mutated.

    def test_export_order_and_no_reported_calls(self):
        data = elf_fixture()
        first = ghidra_fixture(data, calls=[], byte_mapping_validated=False)
        second = ghidra_fixture(data)
        self.assertEqual(build_ledger(data, [first, second]), build_ledger(data, [second, first]))
        self.assertEqual(build_ledger(data, [first])["functions"][0]["calls"], [])

    def test_reject_mismatched_or_malformed_exports(self):
        data = elf_fixture()
        variants = [
            {"binary_sha256": "0" * 64}, {"architecture": "AARCH64:LE:64:v8A"},
            {"architecture": "ARM:BE:32:v7"}, {"address": 0x1020},
            {"address": "0x1000"}, {"address": True}, {"address": -1},
            {"address": 1 << 32}, {"raw_value": 0x1000}, {"thumb": 0},
            {"thumb": 2}, {"thumb": "1"}, {"thumb": 1.0},
            {"symbol": "wrong"}, {"symbol_size": 1}, {"symbol_size": True},
            {"ghidra_image_base": -1}, {"ghidra_address": 0x1000},
            {"calls": None}, {"calls": {}}, {"calls": [None]},
            {"calls": [{"from": "00011008", "to": "00011020", "type": "CALL"}]},
            {"calls": [{"from": "00010ffe", "to": "00011020", "type": "CALL"}]},
            {"calls": [{"from": "EXTERNAL:01", "to": "00011020", "type": "CALL"}]},
            {"calls": [{"from": "00011000", "to": "00000020", "type": "CALL"}]},
            {"calls": [{"from": "00011000", "to": "ram:bad!", "type": "CALL"}]},
            {"calls": [{"from": "00011000", "to": "arbitrary", "type": "CALL"}]},
            {"calls": [{"from": 0x11000, "to": "00011020", "type": "CALL"}]},
            {"calls": [{"from": "100000000", "to": "00011020", "type": "CALL"}]},
            {"calls": [{"from": "00011000", "to": "00011020"}]},
            {"ghidra_version": []}, {"analysis_scope": 1}, {"byte_mapping_validated": "true"},
        ]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(LedgerError):
                build_ledger(data, [ghidra_fixture(data, **changes)])
        with self.assertRaises(LedgerError):
            build_ledger(data, [[]])

    def test_truncations_rejected(self):
        data = elf_fixture()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(LedgerError):
                build_ledger(data[:length])

    def test_bad_headers_and_sections_rejected(self):
        data = elf_fixture()
        variants = []
        for offset, fmt, value in ((0, "I", 0), (4, "B", 2), (5, "B", 3),
                                   (6, "B", 2), (16, "H", 1), (18, "H", 62),
                                   (20, "I", 2), (32, "I", 0xffffffff),
                                   (40, "H", 51), (46, "H", 39), (50, "H", 99)):
            corrupt = bytearray(data)
            struct.pack_into("<" + fmt, corrupt, offset, value)
            variants.append(bytes(corrupt))
        # Section header fields: type, flags, address, offset, size, link, entsize.
        for section, field, value in ((0, 1, 1), (3, 9, 0), (3, 9, 17),
                                      (3, 6, 99), (3, 6, 1), (3, 4, 0xffffffff),
                                      (2, 4, 0xffffffff), (6, 1, 1),
                                      (1, 3, 0x2000), (1, 5, 2), (1, 4, 0xffffffff)):
            variants.append(patch_section(data, section, field, value))
        for index, corrupt in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(LedgerError):
                build_ledger(corrupt)

    def test_bad_symbols_strings_and_extended_symbol_indices(self):
        for item in (symbol(section=90), symbol(section=0xffff),
                     symbol(value=0x0fff), symbol(size=97), symbol(value=0x1061)):
            with self.subTest(symbol=item), self.assertRaises(LedgerError):
                build_ledger(elf_fixture([item]))
        data = elf_fixture()
        shoff = struct.unpack_from("<I", data, 32)[0]
        string_offset, string_size = struct.unpack_from("<II", data, shoff + 2 * 40 + 16)
        symbol_offset = struct.unpack_from("<I", data, shoff + 3 * 40 + 16)[0]
        bad_offset = bytearray(data)
        struct.pack_into("<I", bad_offset, symbol_offset + 16, string_size)
        unterminated = bytearray(data)
        unterminated[string_offset + string_size - 1] = 65
        for corrupt in (bad_offset, unterminated):
            with self.assertRaises(LedgerError):
                build_ledger(bytes(corrupt))

    def test_cli_deterministic_read_only_and_failure_without_partial_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary, export = Path(tmp) / "synthetic.elf", Path(tmp) / "synthetic.json"
            data = elf_fixture()
            binary.write_bytes(data)
            record = ghidra_fixture(data)
            export.write_text(json.dumps(record), encoding="utf-8")
            command = [sys.executable, "-m", "nova3_team.indexer", str(binary),
                       "--ghidra-export", str(export)]
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            runs = [subprocess.run(command, capture_output=True, env=env) for _ in range(2)]
            for run in runs:
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout), build_ledger(data, [record]))
            self.assertEqual(runs[0].stdout, runs[1].stdout)
            self.assertEqual(binary.read_bytes(), data)
            self.assertEqual(json.loads(export.read_text(encoding="utf-8")), record)
            record["binary_sha256"] = "wrong"
            export.write_text(json.dumps(record), encoding="utf-8")
            failed = subprocess.run(command, capture_output=True, env=env)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, b"")
            self.assertIn(b"hash mismatch", failed.stderr)
            export.write_text("{", encoding="utf-8")
            malformed = subprocess.run(command, capture_output=True, env=env)
            self.assertEqual(malformed.returncode, 2)
            self.assertEqual(malformed.stdout, b"")


class CoordinationLedgerTests(unittest.TestCase):
    def export(self, data, **changes):
        return ghidra_fixture(data, **{"ghidra_version": "12.1.2", **changes})

    def public(self, data, *records):
        return indexer.build_coordination_ledger(
            data, library="libSynthetic.so", ghidra_exports=records)

    def test_allowlisted_provenance_and_feeder_integration(self):
        data = elf_fixture([symbol(), symbol("callee", 0x1020, 8)])
        record = self.export(data, analysis_scope="PRIVATE_SCOPE_SENTINEL")
        public = self.public(data, record)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(public["references"], [{
            "library": "libSynthetic.so", "sha256": digest, "architecture": "ARM",
            "elf_class": 32, "endianness": "little", "elf_type": "ET_DYN"}])
        self.assertEqual(public["functions"], [{
            "reference_sha256": digest, "address": 0x1000, "size": 8, "thumb": True,
            "boundary_status": "tool_identified",
            "boundary_confidence": "elf_symbol_with_ghidra_mapping_report",
            "boundary_evidence": {"elf_symbol_type": "STT_FUNC",
                                  "ghidra_versions": ["12.1.2"],
                                  "byte_mapping": "reported_validated"},
            "state": "INVENTORIED", "status": "pending", "matching": "NOT MATCHED"}])
        self.assertEqual(public["statistics"]["excluded"], {"missing_ghidra": 1})
        self.assertFalse(public["statistics"]["inventory_complete"])
        tasks = generate_from_ledger(public)
        self.assertEqual(len(tasks), 1)
        self.assertIn(digest, tasks[0]["description"])
        self.assertIn("00001000:8", tasks[0]["description"])
        serialized = json.dumps((public, tasks))
        for private in ("PRIVATE_SCOPE_SENTINEL", "SYNTHETIC_PSEUDOCODE_SENTINEL",
                        "SENTINEL_BYTES", "incoming", "callee", "entry", "00011020"):
            self.assertNotIn(private, serialized)

    def test_no_evidence_does_not_manufacture_boundaries(self):
        for data, reason in ((elf_fixture(), "missing_ghidra"),
                             (elf_fixture([symbol(size=0)]), "unknown_size")):
            with self.subTest(reason=reason):
                public = self.public(data)
                self.assertEqual(public["functions"], [])
                self.assertEqual(public["statistics"]["excluded"], {reason: 1})
                self.assertEqual(generate_from_ledger(public), [])

    def test_ambiguous_extents_and_modes_are_excluded(self):
        cases = [([symbol(), symbol("alias", size=4)], "conflicting_sizes", 1),
                 ([symbol(), symbol("arm", value=0x1000)], "conflicting_modes", 2),
                 ([symbol(size=16), symbol("inside", value=0x1005, size=4),
                   symbol("tail", value=0x100b, size=8)], "overlapping_extents", 3),
                 ([symbol(size=7)], "unaligned_extent", 1),
                 ([symbol(value=0x1002, size=4)], "unaligned_extent", 1)]
        for symbols, reason, count in cases:
            with self.subTest(reason=reason):
                public = self.public(elf_fixture(symbols))
                self.assertEqual(public["functions"], [])
                self.assertEqual(public["statistics"]["excluded"], {reason: count})

    def test_adjacent_extents_are_not_overlaps_and_aliases_deduplicate(self):
        data = elf_fixture([symbol(), symbol("alias"), symbol("next", 0x1009, 8)],
                           dynamic=[symbol()])
        records = [self.export(data), self.export(data, address=0x1008, raw_value=0x1009,
                   symbol="next", ghidra_address=0x11008, calls=[])]
        first = self.public(data, *records, records[0])
        self.assertEqual(first, self.public(data, *reversed(records)))
        self.assertEqual([f["address"] for f in first["functions"]], [0x1000, 0x1008])
        self.assertEqual(first["statistics"]["excluded"], {})

    def test_reported_mapping_and_version_required_without_claiming_verification(self):
        data = elf_fixture()
        cases = [({"byte_mapping_validated": False}, "unconfirmed_mapping"),
                 ({"byte_mapping_validated": None}, "unconfirmed_mapping"),
                 ({"ghidra_version": None}, "missing_tool_version"),
                 ({"ghidra_version": "PRIVATE_VERSION_SENTINEL"}, "missing_tool_version")]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                public = self.public(data, self.export(data, **changes))
                self.assertEqual(public["functions"], [])
                self.assertEqual(public["statistics"]["excluded"], {reason: 1})
                self.assertNotIn("PRIVATE_VERSION_SENTINEL", json.dumps(public))
        # A later positive report must not silently override contradictory evidence.
        public = self.public(data, self.export(data, byte_mapping_validated=False), self.export(data))
        self.assertEqual(public["functions"], [])
        with mock.patch.object(indexer, "MAX_PUBLISHED_FUNCTION_SIZE", 4):
            self.assertEqual(self.public(data)["statistics"]["excluded"], {"oversized": 1})

    def test_all_hash_mode_size_and_rebase_checks_apply_to_public_output(self):
        data = elf_fixture()
        for change in ({"binary_sha256": "0" * 64}, {"raw_value": 0x1000},
                       {"symbol_size": 4}, {"ghidra_address": 0x1000},
                       {"architecture": "AARCH64:LE:64:v8A"}):
            with self.subTest(change=change), self.assertRaises(LedgerError):
                self.public(data, self.export(data, **change))
        for library in (None, "../libSynthetic.so", "/tmp/libSynthetic.so", "libX.so\n",
                        "libX.so/PRIVATE_PATH", "PRIVATE_SENTINEL"):
            with self.subTest(library=library), self.assertRaises(LedgerError):
                indexer.build_coordination_ledger(data, library=library)

    def test_big_endian_arm_provenance(self):
        data = elf_fixture([symbol(value=0x1000)], endian=">", kind=2)
        public = self.public(data, self.export(data, architecture="ARM:BE:32:v7",
                             thumb=False, raw_value=0x1000))
        self.assertEqual(public["references"][0]["endianness"], "big")
        self.assertEqual(public["references"][0]["elf_type"], "ET_EXEC")
        self.assertFalse(public["functions"][0]["thumb"])

    def test_cli_directory_is_read_only_deterministic_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "libSynthetic.so"
            exports = Path(tmp) / "exports"
            exports.mkdir()
            data = elf_fixture()
            binary.write_bytes(data)
            export = exports / "00001000.json"
            raw = json.dumps(self.export(data)).encode()
            export.write_bytes(raw)
            (exports / "ignored.txt").write_text("not JSON", encoding="utf-8")
            command = [sys.executable, "-m", "nova3_team.indexer", str(binary),
                       "--format", "coordination", "--ghidra-directory", str(exports)]
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            runs = [subprocess.run(command, capture_output=True, env=env) for _ in range(2)]
            for run in runs:
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout), self.public(data, self.export(data)))
                self.assertNotIn(tmp.encode(), run.stdout)
                self.assertNotIn(b"SYNTHETIC_PSEUDOCODE_SENTINEL", run.stdout)
            self.assertEqual(runs[0].stdout, runs[1].stdout)
            self.assertEqual(binary.read_bytes(), data)
            self.assertEqual(export.read_bytes(), raw)
            export.write_text('{"address": 4096, "address": 4098}', encoding="utf-8")
            failed = subprocess.run(command, capture_output=True, env=env)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, b"")
            export.unlink()
            failed = subprocess.run(command, capture_output=True, env=env)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, b"")
            self.assertIn(b"BLOCKED", failed.stderr)

    def test_bounded_export_reads_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(json.dumps(self.export(elf_fixture())), encoding="utf-8")
            for limit in ("MAX_EXPORT_BYTES", "MAX_TOTAL_EXPORT_BYTES", "MAX_EXPORTS"):
                with self.subTest(limit=limit), mock.patch.object(indexer, limit, 0):
                    with self.assertRaises(LedgerError):
                        list(indexer._read_exports([path]))
            for raw in (b'{"address":1,"address":2}', b'{', b'\xff'):
                path.write_bytes(raw)
                with self.subTest(raw=raw), self.assertRaises(LedgerError):
                    list(indexer._read_exports([path]))


if __name__ == "__main__":
    unittest.main()
