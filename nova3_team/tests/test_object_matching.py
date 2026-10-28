"""Synthetic-only regression tests for object matching scaffolding."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from nova3_team import object_matching as matching
from nova3_team.tests.test_indexer import elf_fixture, symbol


class MatchingTests(unittest.TestCase):
    def test_scope_hint_is_not_an_original_translation_unit(self):
        self.assertEqual(matching.group_hint("_ZN8Renderer4drawEv"), "scope_Renderer")
        self.assertEqual(matching.group_hint("JNI_OnLoad"), "unclassified")
        self.assertEqual(matching.group_hint("_ZN999oops"), "unclassified")

    def test_stage_generates_private_snapshot_and_cpp_include(self):
        data = elf_fixture([symbol("_ZN8Renderer4drawEv", 0x1001, 8),
                            symbol("JNI_OnLoad", 0x1020, 12)])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "objmatch"
            binary = Path(temp) / "synthetic.so"
            binary.write_bytes(data)
            result = matching.stage(data, binary, out, limit=2)
            self.assertEqual(len(result["functions"]), 2)
            self.assertEqual(len(result["units"]), 2)
            self.assertFalse(result["original_object_files_recovered"])
            self.assertFalse(result["source_matching_verified"])
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["binary_sha256"], hashlib.sha256(data).hexdigest())
            asm = (out / "asm/00001000_thumb.s").read_text()
            self.assertIn(".thumb_func", asm)
            self.assertIn(".byte 0x00", asm)
            cpp = (out / "source/scope_Renderer.cpp").read_text()
            self.assertIn('asm(R"nova3(', cpp)
            self.assertIn('.include "', cpp)
            config = json.loads((out / "objdiff.json").read_text())
            self.assertFalse(config["build_base"])
            self.assertEqual(config["units"][0]["metadata"]["complete"], False)
            with self.assertRaisesRegex(matching.MatchingError, "empty"):
                matching.stage(data, binary, out)

    def test_fail_closed_on_ambiguous_or_absent_symbol(self):
        data = elf_fixture([symbol("bad", 0x1001, 8),
                            symbol("same_address", 0x1001, 16)])
        with self.assertRaisesRegex(matching.MatchingError, "no safe"):
            matching.select_functions(data)
        good = elf_fixture([symbol()])
        with self.assertRaisesRegex(matching.MatchingError, "requested addresses"):
            matching.select_functions(good, addresses={0x2000})
        with self.assertRaisesRegex(matching.MatchingError, "limit"):
            matching.select_functions(good, limit=0)

    def test_symbol_extent_not_guessed_from_next_function(self):
        data = elf_fixture([symbol(size=0), symbol("next", 0x1020, 12)])
        rows, excluded = matching.select_functions(data, limit=2)
        self.assertEqual([r["address"] for r in rows], [0x1020])
        self.assertEqual(excluded[0]["reason"], "unknown_or_oversized_symbol")

    def test_fingerprint_never_guesses_compiler(self):
        data = elf_fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fake.so"
            path.write_bytes(data)
            with mock.patch.object(matching.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                result = matching.fingerprint(data, path)
        self.assertEqual(result["original_compiler"], "UNKNOWN")
        self.assertEqual(result["original_flags"], "UNKNOWN")
        self.assertEqual(set(result["probes"]), {
            "arm_attributes", "compiler_comment", "notes", "dynamic"})

    def test_build_requires_matching_elf_and_compiler_profile(self):
        data = elf_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "test.so"
            binary.write_bytes(data)
            out = root / "stage"
            matching.stage(data, binary, out, limit=1)
            profile = root / "profile.json"
            profile.write_text(json.dumps({"schema": matching.SCHEMA,
                "compiler": str(root / "fake-compiler"), "flags": [],
                "version_sha256": "00000000"}))
            with mock.patch.object(matching, "compiler_profile") as compiler:
                compiler.return_value = {"compiler": "fake",
                                         "flags": [], "version_sha256": "111"}
                with self.assertRaisesRegex(matching.MatchingError, "version changed"):
                    matching.build(out, profile)
            binary.write_bytes(data + b"changed")
            with self.assertRaisesRegex(matching.MatchingError, "changed"):
                matching.build(out, profile)


if __name__ == "__main__":
    unittest.main()
