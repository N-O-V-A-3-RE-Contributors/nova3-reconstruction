"""No proprietary fixtures: local readiness report tests."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from nova3_team import preflight


class PreflightTests(unittest.TestCase):
    def test_missing_inputs_are_reported_without_guesstimates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(preflight, "memory_kib", return_value=8_000_000):
                result = preflight.inspect(root, root / "ida")
            self.assertEqual(len(result["references"]), 4)
            self.assertFalse(any(x["present"] for x in result["references"].values()))
            self.assertEqual(result["recommended_native_analysis_processes"], 1)
            self.assertEqual(result["original_compiler"], "UNKNOWN")
            self.assertFalse(result["matching_pipeline_operational"])

    def test_hashes_existing_reference_but_does_not_copy_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            relative = preflight.REFERENCES["premium_engine"]
            source = root / relative
            source.parent.mkdir(parents=True)
            source.write_bytes(b"not-a-real-elf-synthetic")
            output = root / ".cache" / "report.json"
            self.assertEqual(preflight.main(["--root", str(root), "--ida", str(root/"ida"),
                                              "--out", str(output)]), 0)
            self.assertTrue(output.is_file())
            self.assertEqual(source.read_bytes(), b"not-a-real-elf-synthetic")
            result = preflight.inspect(root, root / "ida")
            self.assertTrue(result["references"]["premium_engine"]["present"])
            self.assertEqual(result["references"]["premium_engine"]["size"], 24)


if __name__ == "__main__":
    unittest.main()
