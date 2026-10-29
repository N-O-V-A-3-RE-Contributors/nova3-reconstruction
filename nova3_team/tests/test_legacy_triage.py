"""Offline synthetic metadata/C++ fixtures; no original binaries or tools."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import legacy_triage as triage
from nova3_team import legacy_validation as legacy
from nova3_team import verification_gate as gate
from nova3_team.compiler_matrix import SCHEMA as BUILD_SCHEMA
from nova3_team.object_compare import SCHEMA as COMPARE_SCHEMA

REF = "a" * 64
OTHER = "b" * 64


class LegacyTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nova-triage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / legacy.SOURCE_ROOT / "engine").mkdir(parents=True)
        (self.root / legacy.EVIDENCE_ROOT).mkdir(parents=True)
        (self.root / ".gitignore").write_text("/.cache/\n")

    def source(self, address="00001000", data=b"int candidate() { return 7; }\n", directory="engine"):
        path = self.root / legacy.SOURCE_ROOT / directory / f"block_{address}.cpp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.relative_to(self.root).as_posix()

    def write(self, path, data):
        (self.root / path).write_text(json.dumps(data))

    def record(self, path, *, explicit=True, digest=None, address="0x1000", reference=REF):
        data = {"reference": {"sha256": reference, "edition": "premium"},
                "functions": [{"address": address, "size_bytes": 4, "thumb": False,
                               "verification_grade": "tested", "matching_claim": "NOT MATCHED"}]}
        if explicit:
            data["source"] = {"path": path, "sha256": digest or
                              hashlib.sha256((self.root / path).read_bytes()).hexdigest()}
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        return data

    def run_triage(self, **kwargs):
        return triage.triage(self.root, expected_reference_sha256=REF, **kwargs)

    def cli(self, *extra):
        with redirect_stdout(io.StringIO()) as output:
            code = triage.main(["--repo", str(self.root), "--expected-reference-sha256", REF, *extra])
        return code, json.loads(output.getvalue())

    def test_explicit_source_is_hash_current_and_never_matched(self):
        path = self.source()
        self.record(path)
        report = self.run_triage()
        block = report["blocks"][0]
        self.assertEqual(block["category"], "COMPILER_ABI_GATE_NEEDED")
        self.assertEqual(block["source_hash_status"], "CURRENT")
        self.assertEqual(block["evidence_association"], "EXPLICIT_SOURCE_PATH")
        self.assertEqual(block["reported_reference_sha256"], [REF])
        self.assertEqual(block["edition_pin"], "premium")
        self.assertTrue(block["next_proof_step"])
        self.assertEqual(block["machine_match"], "NOT MATCHED")
        self.assertEqual(set(block["proof_grades"].values()), {"NOT_VERIFIED"})
        self.assertFalse(block["reference_binary_rechecked"])
        self.assertEqual(report["summary"]["independently_verified_original_machine_matches"], 0)

    def test_missing_evidence_and_inferred_mapping_remain_research(self):
        path = self.source()
        block = self.run_triage()["blocks"][0]
        self.assertEqual(block["evidence_association"], "NO_EVIDENCE")
        self.assertEqual(block["category"], "RESEARCH_ONLY")
        self.record(path, explicit=False)
        report = self.run_triage()
        self.assertEqual(report["blocks"][0]["evidence_association"], "INFERRED_FROM_BLOCK_ADDRESS")
        self.assertEqual(report["blocks"][0]["category"], "RESEARCH_ONLY")
        self.assertEqual(report["summary"]["unrecorded_source_hashes"], 1)
        # Share association semantics with the historical inventory.
        self.assertEqual(legacy.inventory(self.root)["blocks"][0]["evidence_association"],
                         report["blocks"][0]["evidence_association"])

    def test_unbound_legacy_hash_map_is_not_a_scalar_source_pin(self):
        path = self.source()
        data = self.record(path, explicit=False)
        data["build_provenance"] = {"source_sha256": {path: REF}}
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        block = self.run_triage()["blocks"][0]
        self.assertEqual(block["source_hash_status"], "UNRECORDED")
        self.assertEqual(block["evidence_association"], "INFERRED_FROM_BLOCK_ADDRESS")

    def test_unmeasured_sizes_remain_unknown_without_boundary_guesses(self):
        path = self.source()
        data = self.record(path)
        for value in ["0x20", "unknown", True, -1, 0]:
            data["functions"][0]["size_bytes"] = value
            self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
            self.assertIsNone(self.run_triage()["blocks"][0]["reported_function_details"][0]["size"])

    def test_stale_source_keeps_stable_id_and_both_hashes(self):
        path = self.source()
        data = self.record(path)
        old = self.run_triage()["blocks"][0]
        (self.root / path).write_text("int candidate() { return 9; }\n")
        block = self.run_triage()["blocks"][0]
        self.assertEqual(block["id"], old["id"])
        self.assertNotEqual(block["source_sha256"], old["source_sha256"])
        self.assertEqual(block["evidence"][0]["declared_source_sha256"], [data["source"]["sha256"]])
        self.assertEqual(block["source_hash_status"], "MISMATCH")
        self.assertEqual(block["category"], "CONFLICTING_EVIDENCE")

    def test_reference_aliases_are_preserved_and_conflicts_quarantined(self):
        path = self.source()
        data = self.record(path)
        data["reference"] = {"expected_sha256": REF, "observed_sha256": REF}
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        self.assertEqual(self.run_triage()["blocks"][0]["reported_reference_sha256"], [REF])
        data["reference"]["observed_sha256"] = OTHER
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        self.assertIn("REFERENCE_PIN_CONFLICT", self.run_triage()["blocks"][0]["conflicts"])
        data["reference"] = {"sha256": REF, "edition": "freemium"}
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        self.assertIn("EDITION_PIN_CONFLICT", self.run_triage()["blocks"][0]["conflicts"])

    def test_duplicate_block_and_conflicting_function_identities(self):
        path = self.source()
        self.source(directory="second")
        data = self.record(path)
        report = self.run_triage()
        self.assertEqual(len({b["id"] for b in report["blocks"]}), 2)
        self.assertTrue(all("DUPLICATE_BLOCK_IDENTITY" in b["conflicts"] for b in report["blocks"]))
        data["functions"].append({"address": "0x1000", "size_bytes": 8, "thumb": True})
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        self.assertIn("CONFLICTING_FUNCTION_METADATA", self.run_triage()["blocks"][0]["conflicts"])

    def test_false_historical_byte_claims_are_not_promoted(self):
        path = self.source()
        data = self.record(path)
        for field, value in [("matching_claim", "MATCHED"), ("verification_grade", "matching"),
                             ("exact_bytes", True), ("machine_code_match", "MATCHED"),
                             ("grade", "MATCHED_ARM"), ("status", "LINKED_FUNCTION_WINDOW_EQUAL"),
                             ("source_matching_verified", True), ("original_object_recovered", True),
                             ("machine_match", " MATCHED ")]:
            altered = deepcopy(data)
            altered["functions"][0][field] = value
            self.write(legacy.EVIDENCE_ROOT + "/evidence.json", altered)
            block = self.run_triage()["blocks"][0]
            self.assertIn("UNRECHECKED_POSITIVE_MATCH_CLAIM", block["conflicts"])
            self.assertEqual(block["machine_match"], "NOT MATCHED")

    def gate_fixture(self):
        path = self.source()
        data = self.record(path)
        claim = {"source": data["source"], "grade": "FUNCTION_BYTES_DIFFERENT",
                 "build": {"producer_schema": BUILD_SCHEMA, "source_sha256": data["source"]["sha256"],
                           "reference_sha256": REF, "compiler_version": "synthetic test compiler",
                           "compiler_version_sha256": REF, "flags": ["-O2"],
                           "compile_exit_code": 0, "test_exit_code": 0, "tests_passed": 6,
                           "log_sha256": REF, "object_sha256": REF},
                 "comparison": {"schema": COMPARE_SCHEMA, "kind": "source-candidate-to-linked-window",
                                "reference_sha256": REF, "reference_window_sha256": REF,
                                "candidate_object_sha256": REF, "candidate_symbol": "candidate",
                                "candidate_thumb": False, "reference_address": 0x1000,
                                "reference_size": 4, "candidate_code_sha256": OTHER,
                                "candidate_size": 4, "original_thumb": False,
                                "candidate_relocation_count": 0, "code_window_equal": False,
                                "status": "FUNCTION_BYTES_DIFFERENT",
                                **{k: False for k in ("original_object_recovered", "original_relocations_recovered",
                                                     "original_compiler_verified", "original_so_hash_matching_verified",
                                                     "source_matching_verified")}}}
        manifest = {"schema": gate.SCHEMA, "reference_sha256": REF, "claims": [claim]}
        self.write("gate.json", manifest)
        return path, manifest

    def test_gate_metadata_is_separate_from_independent_machine_proof(self):
        path, manifest = self.gate_fixture()
        claim = manifest["claims"][0]
        b = self.run_triage(gate_manifest="gate.json")["blocks"][0]
        self.assertTrue(b["gate_metadata_valid"])
        self.assertTrue(b["comparison_metadata_valid"])
        self.assertEqual(b["category"], "PRIVATE_ORIGINAL_COMPARISON_NEEDED")
        self.assertEqual(b["machine_match"], "NOT MATCHED")
        claim["grade"] = "ORIGINAL_OBJECT_MATCH"
        self.write("gate.json", manifest)
        self.assertEqual(self.cli("--gate-manifest", "gate.json")[0], 2)

    def test_gate_comparison_must_bind_to_legacy_function_metadata(self):
        path, manifest = self.gate_fixture()
        comparison = manifest["claims"][0]["comparison"]
        comparison["reference_address"] = 0x2000
        self.write("gate.json", manifest)
        report = self.run_triage(gate_manifest="gate.json")
        b = report["blocks"][0]
        self.assertTrue(b["gate_metadata_valid"])
        self.assertEqual(b["comparison_function_binding"], "CONFLICT")
        self.assertIn("COMPARISON_FUNCTION_IDENTITY_CONFLICT", b["conflicts"])
        self.assertEqual(b["category"], "CONFLICTING_EVIDENCE")
        self.assertEqual(report["gate_manifest"]["sha256"],
                         hashlib.sha256((self.root / "gate.json").read_bytes()).hexdigest())
        data = self.record(path)
        data["functions"] = []
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        b = self.run_triage(gate_manifest="gate.json")["blocks"][0]
        self.assertEqual(b["comparison_function_binding"], "UNKNOWN_NO_FUNCTION_METADATA")
        self.assertEqual(b["category"], "COMPILER_ABI_GATE_NEEDED")

    def test_gate_source_hash_drift_and_duplicate_claims_rejected(self):
        path, manifest = self.gate_fixture()
        manifest["claims"].append(deepcopy(manifest["claims"][0]))
        self.write("gate.json", manifest)
        self.assertEqual(self.cli("--gate-manifest", "gate.json")[0], 2)
        manifest["claims"].pop()
        self.write("gate.json", manifest)
        (self.root / path).write_text("int changed() { return 99; }\n")
        self.assertEqual(self.cli("--gate-manifest", "gate.json")[0], 2)

    def test_consistent_equal_window_and_six_host_tests_are_metadata_only(self):
        _, manifest = self.gate_fixture()
        claim = manifest["claims"][0]
        claim["grade"] = "HOST_TESTED_SOURCE"
        claim["comparison"].update(candidate_code_sha256=REF, code_window_equal=True,
                                   status="LINKED_FUNCTION_WINDOW_EQUAL")
        self.write("gate.json", manifest)
        report = self.run_triage(gate_manifest="gate.json")
        block = report["blocks"][0]
        self.assertEqual(claim["build"]["tests_passed"], 6)
        self.assertTrue(block["gate_metadata_valid"])
        self.assertEqual(block["reported_gate_grade"], "HOST_TESTED_SOURCE")
        self.assertEqual(block["machine_match"], "NOT MATCHED")
        self.assertEqual(set(block["proof_grades"].values()), {"NOT_VERIFIED"})
        self.assertEqual(report["summary"]["independently_verified_original_machine_matches"], 0)
        self.assertEqual(self.cli("--gate-manifest", "gate.json", "--require-verified")[0], 2)

    def test_identical_duplicate_function_rows_are_still_ambiguous(self):
        path = self.source()
        data = self.record(path)
        data["functions"].append(deepcopy(data["functions"][0]))
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        block = self.run_triage()["blocks"][0]
        self.assertIn("DUPLICATE_FUNCTION_IDENTITY", block["conflicts"])
        self.assertEqual(block["category"], "CONFLICTING_EVIDENCE")

    def test_orphan_explicit_evidence_is_retained_without_inferred_fallback(self):
        path = self.source()
        data = self.record(path)
        data["source"]["path"] = path.replace("engine/", "missing/")
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        report = self.run_triage()
        self.assertEqual(report["summary"]["orphan_explicit_mappings"], 1)
        self.assertEqual(report["orphan_explicit_evidence"][0]["source_path"], data["source"]["path"])
        self.assertEqual(report["blocks"][0]["evidence_association"], "NO_EVIDENCE")

    def test_invalid_identity_metadata_not_silently_treated_as_missing(self):
        path = self.source()
        data = self.record(path)
        for container, value in [("source", "PRIVATE_ASSEMBLY_LOG"), ("source", None),
                                 ("build_provenance", []), ("reference", [])]:
            altered = deepcopy(data)
            altered[container] = value
            self.write(legacy.EVIDENCE_ROOT + "/evidence.json", altered)
            self.assertEqual(self.cli()[0], 2)
        data["reference"]["edition"] = "unknown"
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        self.assertEqual(self.cli()[0], 2)

    def test_deterministic_order_and_bounded_future_inputs(self):
        large = self.source("00002000", b"int larger() { return 42; }\n" * 4)
        small = self.source()
        self.record(small)
        a = self.run_triage()
        self.assertEqual(a, self.run_triage())
        self.assertEqual(a["blocks"][0]["source_path"], small)
        self.assertEqual(len(a["future_task_inputs"]), 2)
        self.assertEqual([t["priority"] for t in a["future_task_inputs"]], [1, 2])
        self.assertTrue(all(t["requires_private_original"] for t in a["future_task_inputs"]))
        self.assertEqual({t["source_path"] for t in a["future_task_inputs"]}, {large, small})
        contents = {p: (self.root / p).read_bytes() for p in (small, large)}
        for p in contents:
            (self.root / p).unlink()
        for p, data in contents.items():
            (self.root / p).write_bytes(data)
        self.assertEqual(a, self.run_triage())  # Changed directory insertion order.

    def test_local_include_evidence_and_effort_are_bounded_not_a_build_graph(self):
        path = self.source(data=b'#include "local.hpp"\n#include "missing.hpp"\n#include "original.s"\n')
        header = self.root / legacy.SOURCE_ROOT / "engine/local.hpp"
        header.write_text("struct Owned { int value; };\n")
        self.record(path)
        block = self.run_triage()["blocks"][0]
        checks = block["local_include_checks"]
        self.assertEqual([r["status"] for r in checks],
                         ["PRESENT_HASHED", "MISSING_LOCAL_INCLUDE", "UNKNOWN_NOT_READ"])
        self.assertEqual(checks[0]["sha256"], hashlib.sha256(header.read_bytes()).hexdigest())
        self.assertEqual(block["dependency_resolution"], "OTHER_DEPENDENCIES_UNKNOWN")
        header.unlink()
        header.symlink_to(self.root / ".gitignore")
        self.assertEqual(self.cli()[0], 2)

    def test_parent_include_paths_are_unknown_without_normalizing_symlinks(self):
        path = self.source(data=b'#include "missing/../local.hpp"\n#include "../../private.hpp"\n')
        (self.root / path).with_name("local.hpp").write_text("struct Owned {};\n")
        block = self.run_triage()["blocks"][0]
        self.assertTrue(all(d["status"] == "UNKNOWN_NOT_READ" for d in block["local_include_checks"]))

    def test_donor_metadata_is_not_a_verified_priority_cue(self):
        path = self.source()
        self.record(path)
        donor = {"schema": "nova3.donor-plan/1", "retail_sha256": REF, "debug_sha256": OTHER,
                 "candidates": [{"retail_address": 0x1000, "donor_byte_status": "exact_bytes",
                                 "source_matching_verified": False, "original_compiler_verified": False,
                                 "original_object_recovered": False}]}
        self.write("donor.json", donor)
        b = self.run_triage(donor_plan="donor.json")["blocks"][0]
        self.assertEqual(b["donor_cue"], "REPORTED_EXACT_NOT_RECHECKED")
        self.assertFalse(b["verified_donor_cue"])
        donor["retail_sha256"] = OTHER
        self.write("donor.json", donor)
        self.assertEqual(self.cli("--donor-plan", "donor.json")[0], 2)
        donor["retail_sha256"] = REF
        donor["candidates"][0]["source_matching_verified"] = True
        self.write("donor.json", donor)
        self.assertEqual(self.cli("--donor-plan", "donor.json")[0], 2)
        donor["candidates"][0]["source_matching_verified"] = False
        donor["candidates"].append(deepcopy(donor["candidates"][0]))
        self.write("donor.json", donor)
        self.assertEqual(self.cli("--donor-plan", "donor.json")[0], 2)

    def test_invalid_paths_symlinks_and_malformed_metadata_fail_closed(self):
        path = self.source()
        data = self.record(path)
        for bad in ["../private.cpp", "/private.cpp", path + "/../block_00001000.cpp", "a\\b", ""]:
            data["source"]["path"] = bad
            self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
            self.assertEqual(self.cli()[0], 2)
        data["source"]["path"] = path
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        target = self.root / path
        target.unlink()
        target.symlink_to(self.root / ".gitignore")
        self.assertEqual(self.cli()[0], 2)
        target.unlink()
        target.parent.rmdir()
        target.parent.symlink_to(self.root / legacy.EVIDENCE_ROOT, target_is_directory=True)
        self.assertEqual(self.cli()[0], 2)

    def test_evidence_symlink_and_intermediate_parent_symlink_rejected(self):
        self.source()
        ev = self.root / legacy.EVIDENCE_ROOT
        (ev / "link.json").symlink_to(self.root / ".gitignore")
        self.assertEqual(self.cli()[0], 2)
        (ev / "link.json").unlink()
        ev.rmdir()
        ev.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.cli()[0], 2)

    def test_bounded_json_and_source_limits_and_sanitized_errors(self):
        self.source()
        evidence = self.root / legacy.EVIDENCE_ROOT / "bad.json"
        for raw in [b'{"private":"PRIVATE_ASSEMBLY_LOG",', b'{"functions":[],"functions":[]}',
                    b'{"functions":NaN}', b'null', b'\xff', b'[' * 100 + b'0' + b']' * 100,
                    json.dumps({"functions": [{"address": True}]}).encode()]:
            evidence.write_bytes(raw)
            code, output = self.cli()
            self.assertEqual(code, 2)
            self.assertNotIn("PRIVATE_ASSEMBLY_LOG", json.dumps(output))
            self.assertNotIn(str(self.root), json.dumps(output))
        evidence.unlink()
        with patch.object(triage, "MAX_BLOCKS", 0):
            self.assertEqual(self.cli()[0], 2)
        (self.root / legacy.SOURCE_ROOT / "engine/block_00001000.cpp").write_bytes(b"x" * (gate.MAX_FILE + 1))
        self.assertEqual(self.cli()[0], 2)

    def test_empty_inventory_and_require_verified_fail_closed(self):
        self.assertEqual(self.cli()[0], 2)
        self.assertTrue(self.run_triage()["summary"]["inventory_empty"])
        self.source()
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(self.cli("--require-verified")[0], 2)

    def test_argument_errors_do_not_echo_private_values(self):
        for args in [["--edition", "PRIVATE_ASSEMBLY_LOG"], ["--PRIVATE_ASSEMBLY_LOG"], ["--out"]]:
            with redirect_stdout(io.StringIO()) as output, \
                    patch("sys.stderr", new_callable=io.StringIO) as errors:
                with self.assertRaises(SystemExit) as raised:
                    triage.main(args)
            self.assertEqual(raised.exception.code, 2)
            self.assertNotIn("PRIVATE_ASSEMBLY_LOG", output.getvalue() + errors.getvalue())
            self.assertEqual(json.loads(output.getvalue())["error"], "legacy_triage_rejected")

    def test_unsupported_secure_file_operations_fail_closed(self):
        self.source()
        with patch.object(triage.os, "supports_dir_fd", set()):
            self.assertEqual(self.cli()[0], 2)

    def test_private_worksheet_and_public_stdout_exclude_arbitrary_payloads(self):
        path = self.source()
        data = self.record(path)
        data.update(assembly="PRIVATE_ASSEMBLY_LOG", log="PRIVATE_ASSEMBLY_LOG",
                    build_provenance={"compiler": "PRIVATE_ASSEMBLY_LOG"})
        self.write(legacy.EVIDENCE_ROOT + "/evidence.json", data)
        code, public = self.cli("--out", ".cache/triage.json")
        self.assertEqual(code, 0)
        self.assertNotIn("blocks", public)
        self.assertNotIn(path, json.dumps(public))
        worksheet = self.root / ".cache/triage.json"
        self.assertNotIn("PRIVATE_ASSEMBLY_LOG", worksheet.read_text())
        self.assertEqual(stat.S_IMODE(worksheet.stat().st_mode), 0o600)
        original = worksheet.read_bytes()
        self.assertEqual(self.cli("--out", ".cache/triage.json")[0], 2)
        self.assertEqual(worksheet.read_bytes(), original)

    def test_output_traversal_unignored_cache_and_symlinks_rejected(self):
        self.source()
        for name in ["public.json", ".cache/../public.json", "/tmp/report.json", ".cache/sub/a.json"]:
            self.assertEqual(self.cli("--out", name)[0], 2)
        (self.root / ".gitignore").write_text("# no cache ignore\n")
        self.assertEqual(self.cli("--out", ".cache/report.json")[0], 2)
        (self.root / ".gitignore").write_text("/.cache/\n")
        (self.root / ".cache").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.cli("--out", ".cache/report.json")[0], 2)
        self.assertFalse((self.root / "report.json").exists())

    def test_no_external_tools_network_or_input_mutation(self):
        path = self.source()
        self.record(path)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("tools forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("tools forbidden")), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(self.cli()[0], 0)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
