"""Compiler candidate matrix with synthetic local fixture objects only."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from nova3_team import compiler_matrix
from nova3_team.object_matching import SCHEMA as PROFILE_SCHEMA
from nova3_team.tests.test_object_compare import elf32


class CompilerMatrixTests(unittest.TestCase):
    def test_bounded_sequential_matrix_records_window_but_not_source_verification(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            profile = base / "candidate.json"
            profile.write_text(json.dumps({"schema": PROFILE_SCHEMA, "compiler": "/fake/arm-g++",
                                           "flags": ["-O2", "-mthumb"],
                                           "version_sha256": "1" * 64}))
            source = base / "sample.cpp"
            source.write_text('extern "C" int source_fn() { return 7; }')
            objects = base / "objects"

            def fake_compile(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(elf32(relocatable=True))
                return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

            with mock.patch.object(compiler_matrix, "compiler_profile") as compiler:
                compiler.return_value = {"compiler": "/fake/arm-g++",
                                         "flags": ["-O2", "-mthumb"],
                                         "version_sha256": "1" * 64}
                with mock.patch.object(compiler_matrix.subprocess, "run", side_effect=fake_compile):
                    report = compiler_matrix.run_matrix(elf32(), source, [profile], objects,
                                                        address=0x1000, symbol="source_fn")
            self.assertEqual(report["experiments"][0]["status"], "LINKED_FUNCTION_WINDOW_EQUAL")
            self.assertFalse(report["source_matching_verified"])
            self.assertFalse(report["original_compiler_verified"])
            self.assertTrue((objects / "candidate_01.o").exists())
            self.assertEqual(report, json.loads((objects / "matrix.json").read_text()))
            with self.assertRaisesRegex(compiler_matrix.MatrixError, "new or empty"):
                compiler_matrix.run_matrix(elf32(), source, [profile], objects,
                                           address=0x1000, symbol="source_fn")

    def test_profile_rejects_unsafe_flags_before_executing_compiler(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "unsafe.json"
            profile.write_text(json.dumps({"schema": PROFILE_SCHEMA,
                                           "compiler": "/fake/compiler", "flags": ["-fplugin=/tmp/lib.so"],
                                           "version_sha256": "1" * 64}))
            with mock.patch.object(compiler_matrix, "compiler_profile") as c:
                with self.assertRaisesRegex(compiler_matrix.MatrixError, "unsafe"):
                    compiler_matrix._profile(profile)
                c.assert_not_called()

    def test_compiler_timeout_is_recorded_without_false_matching_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            profile = base / "candidate.json"
            profile.write_text(json.dumps({"schema": PROFILE_SCHEMA, "compiler": "/fake/arm-g++",
                                           "flags": ["-Os"], "version_sha256": "1" * 64}))
            source = base / "sample.cpp"
            source.write_text("int source_fn() { return 7; }")
            with mock.patch.object(compiler_matrix, "compiler_profile") as c:
                c.return_value = {"compiler": "/fake/arm-g++", "flags": ["-Os"],
                                  "version_sha256": "1" * 64}
                with mock.patch.object(compiler_matrix.subprocess, "run",
                                       side_effect=subprocess.TimeoutExpired(["compiler"], 1)):
                    report = compiler_matrix.run_matrix(elf32(), source, [profile], base / "out",
                                                        address=0x1000, symbol="source_fn")
            self.assertEqual(report["experiments"][0]["status"], "COMPILER_TIMEOUT")
            self.assertFalse(report["source_matching_verified"])


if __name__ == "__main__":
    unittest.main()
