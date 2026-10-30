"""Public synthetic fixtures only; no credentials, inference or live GitHub."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

if sys.version_info < (3, 11):
    raise unittest.SkipTest("optional manual subagent pilot requires Python 3.11+ tomllib")


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "nova3_subagent_preflight", ROOT / "tools/agents/subagent_preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class ProfileTests(unittest.TestCase):
    def test_actual_profiles_and_document_pass_offline_without_subprocess(self):
        with mock.patch.object(preflight.shutil, "which", return_value=None), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process")):
            result = preflight.inspect(ROOT)
        self.assertEqual(result["validation"], "PASS", result["errors"])
        self.assertFalse(result["enabled"])
        self.assertFalse(result["automatic_dispatch"])
        self.assertEqual((result["max_children"], result["max_spawn_depth"]), (2, 1))
        self.assertEqual(result["paid_calls"], 0)
        self.assertEqual(result["codex"]["status"], "UNAVAILABLE")
        self.assertEqual(result["hermes"]["status"], "UNAVAILABLE")

    def test_exact_six_path_scope(self):
        self.assertEqual(set(preflight.ALLOWED_PATHS), {
            ".codex/agents/nova3_tu_mapper.toml",
            ".codex/agents/nova3_match_reviewer.toml",
            ".codex/agents/nova3_test_reviewer.toml",
            "tools/agents/subagent_preflight.py",
            "nova3_team/tests/test_subagent_profiles.py",
            "docs/progress/SUBAGENT_PILOT.md",
        })

    def test_profile_schema_rejects_permission_and_vendor_overrides(self):
        name, relative = next(iter(preflight.PROFILES.items()))
        source = (ROOT / relative).read_text()
        # Insert before [features], so these are top-level unknown fields.
        for setting in ('sandbox_mode = "read-only"', 'approval_policy = "never"',
                        'model_provider = "openrouter"', 'model = "other"',
                        'mcp_servers = {}', 'budget = 20', 'model_reasoning_effort = "high"'):
            with self.subTest(setting=setting):
                altered = source.replace("[features]", setting + "\n[features]")
                self.assertTrue(preflight.validate_profile(name, altered))

    def test_invalid_and_wrongly_named_profiles_fail(self):
        name, relative = next(iter(preflight.PROFILES.items()))
        source = (ROOT / relative).read_text()
        for bad in ("[invalid", source.replace(name, "worker"),
                    source.replace('description = "', 'description = 1 # "', 1),
                    source.replace('name = "', 'name = false # "', 1)):
            with self.subTest(source=bad[:60]):
                self.assertTrue(preflight.validate_profile(name, bad))

    def test_each_profile_requires_all_read_only_budget_and_evidence_clauses(self):
        for name, relative in preflight.PROFILES.items():
            source = (ROOT / relative).read_text()
            for term in preflight.REQUIRED_INSTRUCTIONS:
                with self.subTest(name=name, term=term):
                    self.assertTrue(preflight.validate_profile(
                        name, source.replace(term, "removed")))

    def test_optional_capabilities_cannot_be_reenabled_or_added(self):
        name, relative = next(iter(preflight.PROFILES.items()))
        source = (ROOT / relative).read_text()
        for bad in (source.replace("apps = false", "apps = true"),
                    source.replace("apps = false", "apps = 0"),
                    source.replace("apps = false", ""),
                    source + "shell_tool = false\n"):
            with self.subTest(source=bad[-100:]):
                self.assertTrue(preflight.validate_profile(name, bad))

    def test_native_hermes_settings_are_required_in_documentation(self):
        source = (ROOT / "docs/progress/SUBAGENT_PILOT.md").read_text()
        for term in ("delegate_task", "max_concurrent_children: 2", "max_spawn_depth: 1",
                     "max_iterations: 20", "oneshot_max_children: 2",
                     preflight.PINNED_HERMES_MODEL):
            with self.subTest(term=term):
                self.assertTrue(preflight.validate_document(source.replace(term, "removed")))

    def test_default_never_probes_present_clis_or_reports_them_as_ready(self):
        with mock.patch.object(preflight.shutil, "which", return_value="/synthetic/bin"), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process")):
            result = preflight.inspect(ROOT)
        self.assertEqual(result["codex"]["status"], "NOT_PROBED")
        self.assertEqual(result["codex"]["profile_loading"], "UNKNOWN")
        self.assertEqual(result["hermes"]["status"], "PRESENT_UNVERIFIED")
        self.assertEqual(result["hermes"]["cli_probe"], "NOT_RUN")
        self.assertEqual(result["hermes"]["config_schema"], "UNKNOWN")
        self.assertEqual(result["hermes"]["endpoint_tool_calling"], "UNKNOWN")

    def test_absent_inputs_fail_without_exposing_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(preflight.shutil, "which", return_value=None):
            result = preflight.inspect(Path(temp))
        self.assertEqual(result["validation"], "FAIL")
        self.assertEqual(len(result["errors"]), 4)
        self.assertNotIn(temp, json.dumps(result))

    def test_source_path_guards_and_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                preflight.read_source(root, "../credentials")
            relative = next(iter(preflight.PROFILES.values()))
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x" * (preflight.MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError):
                preflight.read_source(root, relative)
            path.unlink()
            path.mkdir()
            with self.assertRaises(ValueError):
                preflight.read_source(root, relative)

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlinks")
    def test_symlinked_profile_or_parent_is_not_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            (root / ".codex").mkdir()
            try:
                (root / ".codex/agents").symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation not permitted")
            relative = next(iter(preflight.PROFILES.values()))
            with self.assertRaises(ValueError):
                preflight.read_source(root, relative)
            (root / ".codex/agents").unlink()
            (root / ".codex/agents").mkdir()
            (root / relative).symlink_to(target / "missing")
            with self.assertRaises(ValueError):
                preflight.read_source(root, relative)


class ProbeTests(unittest.TestCase):
    @staticmethod
    def fake_run(argv, **kwargs):
        data = (b"codex-cli 0.163.0\n" if argv[-1] == "--version" else
                b"Usage: codex exec --ephemeral --config --sandbox\n")
        kwargs["stdout"].write(data)
        return subprocess.CompletedProcess(argv, 0)

    def test_only_help_version_run_with_timeout_no_shell_and_no_credentials(self):
        secret = "synthetic-do-not-forward"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": secret,
                                         "OPENROUTER_API_KEY": secret,
                                         "CODEX_HOME": "/private/real-home",
                                         "HERMES_HOME": "/private/real-hermes",
                                         "NODE_OPTIONS": "--require /private/hook.js"}), \
                mock.patch.object(subprocess, "run", side_effect=self.fake_run) as run:
            result = preflight.probe_codex("/safe/codex")
        self.assertEqual(result["status"], "CLI_FLAGS_VERIFIED")
        self.assertEqual(result["version"], "0.163.0")
        self.assertEqual(result["profile_loading"], "UNKNOWN")
        self.assertFalse(result["inference_run"])
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["/safe/codex", "--version"], ["/safe/codex", "exec", "--help"]])
        for call in run.call_args_list:
            kwargs = call.kwargs
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["timeout"], 10)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn(secret, str(kwargs["env"]))
            self.assertNotIn("NODE_OPTIONS", kwargs["env"])
            self.assertNotIn("HERMES_HOME", kwargs["env"])
            self.assertNotEqual(kwargs["env"]["CODEX_HOME"], "/private/real-home")
            self.assertEqual(kwargs["cwd"], Path(kwargs["env"]["CODEX_HOME"]))

    def test_timeout_failure_and_raw_diagnostics_do_not_escape(self):
        failures = [OSError("synthetic-secret"),
                    subprocess.TimeoutExpired(["secret"], 10, output=b"secret"),
                    subprocess.CompletedProcess(["secret"], 7)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(subprocess, "run", side_effect=(
                        failure if isinstance(failure, Exception) else None),
                        return_value=failure):
                with self.assertRaises(ValueError) as caught:
                    preflight.probe_codex("/safe/codex")
                self.assertNotIn("secret", str(caught.exception))

    def test_oversized_or_noncontractual_cli_output_fails(self):
        for data in (b"x" * (preflight.MAX_PROBE_BYTES + 1), b"secret-provider-output"):
            def fake(argv, **kwargs):
                kwargs["stdout"].write(data)
                return subprocess.CompletedProcess(argv, 0)
            with self.subTest(size=len(data)), mock.patch.object(subprocess, "run", side_effect=fake):
                with self.assertRaises(ValueError):
                    preflight.probe_codex("/safe/codex")

    def test_missing_ephemeral_help_fails(self):
        def fake(argv, **kwargs):
            kwargs["stdout"].write(b"codex-cli 0.163.0\n" if argv[-1] == "--version"
                                    else b"Usage: codex exec --config --sandbox")
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(subprocess, "run", side_effect=fake), self.assertRaises(ValueError):
            preflight.probe_codex("/safe/codex")

    def test_opt_in_never_executes_hermes_wrapper(self):
        with mock.patch.object(preflight.shutil, "which", side_effect=["/safe/codex", "/unsafe/hermes"]), \
                mock.patch.object(subprocess, "run", side_effect=self.fake_run) as run:
            result = preflight.inspect(ROOT, probe=True)
        self.assertEqual(len(run.call_args_list), 2)
        self.assertTrue(all(c.args[0][0] == "/safe/codex" for c in run.call_args_list))
        self.assertEqual(result["hermes"]["cli_probe"], "NOT_RUN")

    def test_cli_exit_codes_and_sanitized_json(self):
        base = {"validation": "PASS", "codex": {"status": "NOT_PROBED"}}
        for flags, validation, state, expected in (
            ([], "PASS", "NOT_PROBED", 0),
            ([], "FAIL", "NOT_PROBED", 1),
            (["--probe-codex"], "PASS", "UNAVAILABLE", 1),
            (["--probe-codex"], "PASS", "PROBE_FAILED", 1),
            (["--probe-codex"], "PASS", "CLI_FLAGS_VERIFIED", 0),
        ):
            result = copy.deepcopy(base)
            result["validation"] = validation
            result["codex"]["status"] = state
            with self.subTest(flags=flags, validation=validation, state=state), \
                    mock.patch.object(preflight, "inspect", return_value=result) as inspect, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(preflight.main(["--root", str(ROOT), *flags]), expected)
                self.assertEqual(json.loads(output.getvalue()), result)
                inspect.assert_called_once_with(ROOT.resolve(), probe=bool(flags), loader=False)

    def test_loader_is_explicit_and_default_never_binds_loopback(self):
        with mock.patch.object(preflight, "ThreadingHTTPServer", side_effect=AssertionError("socket")), \
                mock.patch.object(preflight.shutil, "which", return_value="/safe/codex"), \
                mock.patch.object(preflight, "probe_profile_loading", side_effect=AssertionError("loader")):
            self.assertEqual(preflight.inspect(ROOT)["codex"]["status"], "NOT_PROBED")

    def test_loader_call_is_separate_from_flag_only_probe(self):
        flags = {"status": "CLI_FLAGS_VERIFIED", "profile_loading": "UNKNOWN", "inference_run": False}
        loaded = {"profile_loading": "VERIFIED_LOCAL_FIXTURE", "verified_profiles": list(preflight.PROFILES),
                  "children_closed": True, "model_responses": "SIMULATED_LOOPBACK_ONLY"}
        with mock.patch.object(preflight.shutil, "which", return_value="/safe/codex"), \
                mock.patch.object(preflight, "probe_codex", side_effect=lambda _: dict(flags)), \
                mock.patch.object(preflight, "probe_profile_loading", return_value=loaded) as loader:
            result = preflight.inspect(ROOT, probe=True)
            loader.assert_not_called()
            self.assertEqual(result["codex"]["profile_loading"], "UNKNOWN")
            result = preflight.inspect(ROOT, loader=True)
            loader.assert_called_once_with("/safe/codex", ROOT)
            self.assertEqual(result["codex"]["profile_loading"], "VERIFIED_LOCAL_FIXTURE")
            self.assertTrue(result["codex"]["children_closed"])
            self.assertEqual(result["paid_calls"], 0)

    def test_loader_socket_denial_fails_without_launching_a_session(self):
        with mock.patch.object(preflight, "ThreadingHTTPServer", side_effect=PermissionError("secret")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process")):
            with self.assertRaisesRegex(ValueError, "loopback fixture unavailable") as caught:
                preflight.probe_profile_loading("/safe/codex", ROOT)
            self.assertNotIn("secret", str(caught.exception))

    def test_loader_failure_or_absence_never_claims_loaded(self):
        with mock.patch.object(preflight.shutil, "which", return_value="/safe/codex"), \
                mock.patch.object(preflight, "probe_codex", return_value={
                    "status": "CLI_FLAGS_VERIFIED", "profile_loading": "UNKNOWN"}), \
                mock.patch.object(preflight, "probe_profile_loading", side_effect=ValueError("fixture failed")):
            result = preflight.inspect(ROOT, loader=True)
        self.assertEqual(result["codex"]["status"], "PROBE_FAILED")
        self.assertEqual(result["codex"]["profile_loading"], "UNKNOWN")
        for state in ("UNAVAILABLE", "CLI_FLAGS_VERIFIED", "PROBE_FAILED"):
            result = {"validation": "PASS", "codex": {"status": state, "profile_loading": "UNKNOWN"}}
            with self.subTest(state=state), mock.patch.object(preflight, "inspect", return_value=result), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(["--probe-codex-loader"]), 1)

    def test_mock_sse_has_no_real_inference_or_extra_tool_calls(self):
        item = {"id": "msg_fixture", "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "OFFLINE"}]}
        encoded = preflight._fixture_events(item).decode()
        events = [json.loads(line[6:]) for line in encoded.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(events[-1]["response"]["output"], [item])
        self.assertNotIn("function_call", encoded)


if __name__ == "__main__":
    unittest.main()
