"""Source-only fixtures; no original APK/ELF or compiler is required."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nova3_team.legacy_validation import _safe_repo_path, inventory, main


class LegacyValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nova-legacy-validation-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = self.root / "reconstruction/recovered/libNOVA3_neon"
        self.evidence = self.root / "coordination/evidence"
        self.sources.mkdir(parents=True)
        self.evidence.mkdir(parents=True)

    def source(self, address: str, content: bytes = b"// source-owned, NOT MATCHED\n") -> Path:
        path = self.sources / f"block_{address}.cpp"
        path.write_bytes(content)
        return path

    def record(self, name: str, document: dict) -> None:
        (self.evidence / name).write_text(json.dumps(document), encoding="utf-8")

    def test_unverified_is_default_even_with_behavioral_evidence(self):
        source = self.source("000d2f10")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.record("pilot.json", {
            "source": {"path": source.relative_to(self.root).as_posix(), "sha256": digest},
            "functions": [
                {"elf_va": "0xd2f10", "verification_grade": "behavioural",
                 "assembly_comparison": "NOT MATCHED"},
            ],
        })
        report = inventory(self.root)
        self.assertEqual(report["summary"]["with_explicit_evidence"], 1)
        self.assertEqual(report["summary"]["verified_target_machine_matches"], 0)
        block = report["blocks"][0]
        self.assertEqual(block["current_target_machine_match"], "NOT_VERIFIED")
        self.assertEqual(block["revalidation_status"], "REVALIDATION_REQUIRED")
        self.assertEqual(block["evidence"][0]["source_sha256_check"], "MATCH")
        self.assertEqual(block["reported_function_evidence"][0]["reported_grade"], "behavioural")

    def test_hash_change_is_detected_without_changing_source(self):
        source = self.source("000d39f0")
        self.record("older.json", {
            "build_provenance": {
                "source_path": source.relative_to(self.root).as_posix(),
                "source_sha256": "0" * 64,
            },
            "functions": [{"address": "0x000d39f0", "verification_grade": "tested"}],
        })
        block = inventory(self.root)["blocks"][0]
        self.assertEqual(block["revalidation_status"], "SOURCE_CHANGED_SINCE_EVIDENCE")
        self.assertEqual(block["evidence"][0]["source_sha256_check"], "MISMATCH")

    def test_unsupported_match_claim_never_advances_verification(self):
        source = self.source("000d52fc")
        self.record("claimed.json", {
            "source": {"path": source.relative_to(self.root).as_posix()},
            "matching_claim": "MATCHED",
            "functions": [{"address": "0xd52fc", "verification_grade": "matching"}],
        })
        block = inventory(self.root)["blocks"][0]
        self.assertEqual(block["historical_machine_match"], "REVIEW_REQUIRED")
        self.assertEqual(block["current_target_machine_match"], "NOT_VERIFIED")
        self.assertEqual(inventory(self.root)["summary"]["verified_target_machine_matches"], 0)

    def test_older_evidence_can_only_be_associated_tentatively(self):
        self.source("002ad444")
        self.record("older.json", {
            "matching_claim": "NOT MATCHED",
            "functions": [{"address": "0x002ad444", "verification_grade": "hypothesis"}],
        })
        block = inventory(self.root)["blocks"][0]
        self.assertEqual(block["evidence_association"], "INFERRED_FROM_BLOCK_ADDRESS")
        self.assertEqual(block["current_target_machine_match"], "NOT_VERIFIED")

    def test_traversal_claim_cannot_attach_to_source(self):
        self.source("000d2f10")
        self.record("bad.json", {
            "source": {"path": "reconstruction/recovered/../../block_000d2f10.cpp"},
            "functions": [],
        })
        block = inventory(self.root)["blocks"][0]
        self.assertEqual(block["evidence_association"], "NO_EVIDENCE")
        self.assertIsNone(_safe_repo_path("/tmp/malicious.cpp"))
        self.assertIsNone(_safe_repo_path("../malicious.cpp"))
        self.assertIsNone(_safe_repo_path("reconstruction\\recovered\\x.cpp"))

    def test_bad_json_is_an_issue_not_a_crash(self):
        self.source("000d2f10")
        (self.evidence / "broken.json").write_text("{", encoding="utf-8")
        report = inventory(self.root)
        self.assertEqual(report["evidence_read_warnings"][0]["reason"], "invalid_json")

    def test_cli_requires_private_cache_for_written_reports(self):
        self.source("000d2f10")
        destination = self.root / ".cache/audit.json"
        self.assertEqual(main(["--repo", str(self.root), "--out", str(destination)]), 0)
        data = json.loads(destination.read_text())
        self.assertEqual(data["schema"], "nova3.legacy-revalidation/1")
        self.assertEqual(main(["--repo", str(self.root), "--require-verified"]), 2)

    def test_absent_source_dir_is_not_reported_as_success(self):
        (self.root / "reconstruction/recovered").rename(self.root / "reconstruction/removed")
        with self.assertRaises(ValueError):
            inventory(self.root)


if __name__ == "__main__":
    unittest.main()
