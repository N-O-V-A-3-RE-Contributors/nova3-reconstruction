"""Synthetic metadata only. No network, auth files, real CLI or paid calls."""
from dataclasses import replace
from decimal import Decimal
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import model_gateway as g
from tools import model_gateway_probe as probe

NOW = 1_800_000_000


def config_data():
    return dict(schema="nova3.gateway-config/1", model=g.MODEL, provider="wafer",
                quantization="fp8", context_required=8192, input_tokens=2048,
                output_tokens=512, input_cap_per_million="0.04",
                output_cap_per_million="0.50", budget_usd="0.01", timeout_seconds=30)


def endpoint_data():
    return dict(schema="nova3.gateway-endpoint/1", source=g.ENDPOINTS, model=g.MODEL,
                provider="wafer", quantization="fp8", checked_at=NOW,
                reachable=True, zdr=True, retention_reviewed=True,
                account_privacy_allows=True, tools=True, context=8192,
                input_per_million="0.038", output_per_million="0.50", balance_usd="1")


def review(schema=None):
    # Intentionally synthetic version/schema, never a supported live version.
    raw = json.dumps(schema or {"type": "object", "properties": {}}).encode()
    data = dict(schema="nova3.gateway-review/1", version="0.0.0",
                schema_sha256=hashlib.sha256(raw).hexdigest(), checked_at=NOW,
                router_options=sorted(g.OPTIONS), native_auth_list_reviewed=True,
                semantics_reviewed=True)
    return g.Review.parse(data, raw)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.config = g.GatewayConfig.parse(config_data())
        self.endpoint = g.EndpointEvidence.parse(endpoint_data())
        self.review = review()
        self.native = g.NativeStatus("PASS", "0.0.0", "PASS", "PASS")

    def status(self, **kwargs):
        args = dict(config=self.config, endpoint=self.endpoint, review=self.review,
                    native=self.native, now=NOW)
        args.update(kwargs)
        return g.preflight(**args)

    def test_exact_model_only(self):
        for model in ("deepseek/deepseek-v4.1-flash", g.MODEL + ":floor",
                      "deepseek/latest", "deepseek/deepseek-v4.1-flash-1016", None):
            with self.subTest(model=model), self.assertRaises(g.GatewayError):
                g.GatewayConfig.parse(dict(config_data(), model=model))

    def test_direct_deepseek_and_price_fallback_not_implemented(self):
        for provider in ("deepseek", "auto", ["wafer", "deepseek"]):
            with self.subTest(provider=provider), self.assertRaises(g.GatewayError):
                g.GatewayConfig.parse(dict(config_data(), provider=provider))

    def test_strict_config_and_budget(self):
        for changes in ({"apiKey": "DO-NOT-LOG"}, {"timeout_seconds": True},
                        {"budget_usd": "NaN"}, {"budget_usd": "0"},
                        {"budget_usd": "0.251"}, {"budget_usd": 0.01},
                        {"budget_usd": "0.00001"}, {"input_tokens": 0},
                        {"output_tokens": 8193}, {"context_required": 100},
                        {"quantization": "auto"}, {"schema": "2"}):
            with self.subTest(changes=changes), self.assertRaises(g.GatewayError):
                g.GatewayConfig.parse(dict(config_data(), **changes))

    def test_pass_is_not_endpoint_selection_or_key_validity(self):
        result = self.status()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["endpoint_selection"], "UNKNOWN")
        self.assertEqual(result["credential_validity"], "UNKNOWN")
        self.assertEqual(result["source_matching"], "NOT MATCHED")
        self.assertEqual(result, self.status())

    def test_missing_metadata_and_offline_are_unknown(self):
        result = self.status(endpoint=None, review=None, native=g.NativeStatus())
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["context"])
        self.assertIsNone(result["input_per_million"])
        self.assertIsNone(result["quantization"])
        self.assertEqual(result["requested_quantization"], "fp8")

    def test_endpoint_false_unknown_and_drift(self):
        for key in ("reachable", "zdr", "retention_reviewed", "account_privacy_allows", "tools"):
            for value, state in ((False, "FAIL"), (None, "UNKNOWN")):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.status(endpoint=replace(self.endpoint, **{key: value}))["status"], state)
        for changes in ({"quantization": "bf16"}, {"context": 1024},
                        {"input_per_million": Decimal("0.041")},
                        {"output_per_million": Decimal("0.51")},
                        {"balance_usd": Decimal("0.009")},
                        {"provider": "deepseek"}, {"model": "alias"},
                        {"checked_at": NOW - 3601}, {"checked_at": NOW + 1}):
            with self.subTest(changes=changes):
                self.assertEqual(self.status(endpoint=replace(self.endpoint, **changes))["status"], "FAIL")

    def test_unknown_precision_prices_balance_not_success(self):
        for key in ("quantization", "context", "input_per_million", "output_per_million", "balance_usd"):
            with self.subTest(key=key):
                self.assertEqual(self.status(endpoint=replace(self.endpoint, **{key: None}))["status"], "UNKNOWN")

    def test_endpoint_parser_rejects_wrong_units_identity_and_types(self):
        for changes in ({"model": "alias"}, {"provider": "deepseek"},
                        {"source": "https://evil.invalid"}, {"tools": 1},
                        {"input_per_million": 0.038}, {"context": False},
                        {"prompt": "private-source"}):
            with self.subTest(changes=changes), self.assertRaises(g.GatewayError):
                g.EndpointEvidence.parse(dict(endpoint_data(), **changes))

    def test_stale_review_and_version_mismatch_fail(self):
        self.assertEqual(self.status(review=replace(self.review, checked_at=NOW - 3601))["status"], "FAIL")
        self.assertEqual(self.status(native=replace(self.native, version="9.9.9"))["status"], "FAIL")

    def test_schema_hash_or_semantics_not_accepted_blindly(self):
        raw = b'{"properties":{}}'
        d = dict(schema="nova3.gateway-review/1", version="0.0.0",
                 schema_sha256=hashlib.sha256(raw).hexdigest(), checked_at=NOW,
                 router_options=sorted(g.OPTIONS), native_auth_list_reviewed=True,
                 semantics_reviewed=True)
        for changes in ({"schema_sha256": "0" * 64}, {"version": "latest"},
                        {"semantics_reviewed": False}, {"router_options": ["order"]},
                        {"native_auth_list_reviewed": False}):
            with self.subTest(changes=changes), self.assertRaises(g.GatewayError):
                g.Review.parse(dict(d, **changes), raw)

    def test_router_model_specific_fallback_disabled_and_no_secret(self):
        settings = g.opencode_settings(self.config, self.endpoint, self.review, self.native, now=NOW)
        self.assertEqual(settings["model"], "openrouter/" + g.MODEL)
        self.assertEqual(settings["small_model"], settings["model"])
        self.assertEqual(settings["provider"]["openrouter"]["options"]["baseURL"], g.ROUTER)
        opts = settings["provider"]["openrouter"]["models"][g.MODEL]["options"]["provider"]
        self.assertEqual(opts["order"], ["wafer"])
        self.assertEqual(opts["only"], ["wafer"])
        self.assertFalse(opts["allow_fallbacks"])
        self.assertTrue(opts["zdr"])
        self.assertTrue(opts["require_parameters"])
        self.assertEqual(opts["quantizations"], ["fp8"])
        self.assertEqual(opts["max_price"], {"prompt": 0.04, "completion": 0.5})
        self.assertNotIn("apiKey", json.dumps(settings))

    def test_settings_blocked_if_privacy_or_version_unknown(self):
        for native in (g.NativeStatus(), replace(self.native, credential="FAIL")):
            with self.assertRaises(g.GatewayError):
                g.opencode_settings(self.config, self.endpoint, self.review, native, now=NOW)

    def test_actual_schema_checked_remote_or_unsupported_schema_denied(self):
        for schema in ({"type": "object", "properties": {}, "additionalProperties": False},
                       {"properties": {}, "$ref": "https://evil.invalid/schema"},
                       {"properties": {}, "patternProperties": {}},
                       {"properties": 42},
                       {"properties": {}, "anyOf": True},
                       {"properties": {"model": {"enum": ["wrong"]}}}):
            with self.subTest(schema=schema), self.assertRaises(g.GatewayError):
                g.opencode_settings(self.config, self.endpoint, review(schema), self.native, now=NOW)

    def test_native_offline_no_execution(self):
        with mock.patch.object(g.shutil, "which") as which, mock.patch.object(g, "_native_command") as command:
            self.assertEqual(g.native_status(self.review), g.NativeStatus())
            which.assert_not_called()
            command.assert_not_called()

    def test_native_missing_or_windows_shell_wrapper(self):
        for executable in (None, "C:/opencode.cmd", "C:/opencode.BAT"):
            with mock.patch.object(g.shutil, "which", return_value=executable):
                self.assertEqual(g.native_status(self.review, enabled=True).executable, "FAIL")

    def test_native_status_redacts_noisy_outputs_and_never_reads_credentials(self):
        noisy = b'PRIVATE-KEY-DO-NOT-LOG\n\x1b[32m' + '│ ● OpenRouter api'.encode() + b'\x1b[0m\n'
        with mock.patch.object(g.shutil, "which", return_value="/fake/opencode"), mock.patch.object(g, "_native_command", side_effect=[(0, b"0.0.0\n"), (0, noisy)]) as command:
            native = g.native_status(self.review, enabled=True)
        self.assertEqual(native.credential, "PASS")
        self.assertNotIn("PRIVATE-KEY", json.dumps(self.status(native=native)))
        self.assertEqual(command.call_args_list, [mock.call("/fake/opencode", ["--version"], 5), mock.call("/fake/opencode", ["auth", "list"], 5)])

    def test_bad_missing_or_ambiguous_native_credentials(self):
        for output, code, expected in ((b"0 credentials", 0, "FAIL"),
                                       (b"invalid key SECRET", 1, "FAIL"),
                                       (b"error OpenRouter api SECRET", 0, "UNKNOWN")):
            with self.subTest(output=output), mock.patch.object(g.shutil, "which", return_value="/fake/opencode"), mock.patch.object(g, "_native_command", side_effect=[(0, b"0.0.0"), (code, output)]):
                self.assertEqual(g.native_status(self.review, enabled=True).credential, expected)

    def test_unreviewed_version_does_not_inspect_auth(self):
        with mock.patch.object(g.shutil, "which", return_value="/fake/opencode"), mock.patch.object(g, "_native_command", return_value=(0, b"9.9.9")) as command:
            native = g.native_status(self.review, enabled=True)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(native.credential, "UNKNOWN")

    def test_cli_timeout_and_errors_never_echo_raw_text(self):
        for error in (subprocess.TimeoutExpired("SECRET", 1), OSError("SECRET"), g.GatewayError("SECRET")):
            with mock.patch.object(g.shutil, "which", return_value="/fake/opencode"), mock.patch.object(g, "_native_command", side_effect=error):
                native = g.native_status(self.review, enabled=True)
                self.assertEqual(native.command, "FAIL")
                self.assertNotIn("SECRET", repr(native))

    def test_real_local_subprocess_bounds_noisy_output_and_timeout(self):
        # Only Python printing synthetic data; never invoke a model CLI.
        self.assertEqual(g._native_command(sys.executable, ["-c", "print('ok')"], 3), (0, b"ok\n"))
        with self.assertRaises(g.GatewayError):
            g._native_command(sys.executable, ["-c", "print('x'*100000)"], 3)
        with self.assertRaises(subprocess.TimeoutExpired):
            g._native_command(sys.executable, ["-c", "import time; time.sleep(5)"], 1)

    def test_private_report_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / ".cache"
            g.write_private_report(cache, self.status())
            path = cache / "model-gateway/preflight.json"
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                g.write_private_report(cache, self.status())

    def test_report_writer_rejects_appended_prompts_or_noisy_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / ".cache"
            for changes in ({"prompt": "SECRET"}, {"provider": "SECRET"},
                            {"quantization": "SECRET"}, {"opencode_version": "SECRET"}):
                with self.subTest(changes=changes), self.assertRaises(g.GatewayError):
                    g.write_private_report(cache, dict(self.status(), **changes))
            self.assertFalse(cache.exists())

    def test_user_settings_never_overwrite_or_write_project_root(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / ".config/opencode/opencode.json"
            g.write_user_settings(path, {"model": "synthetic"}, home=home)
            with self.assertRaises(FileExistsError):
                g.write_user_settings(path, {}, home=home)
            with self.assertRaises(g.GatewayError):
                g.write_user_settings(home / "opencode.json", {}, home=home)

    @unittest.skipUnless(os.name == "posix", "symlink fixture")
    def test_symlink_destinations_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".cache").symlink_to(root, target_is_directory=True)
            with self.assertRaises(g.GatewayError):
                g.write_private_report(root / ".cache", self.status())

    def test_approval_required_single_attempt_no_retry(self):
        approval = g.PilotApproval(130, self.config.budget_usd, True)
        for invalid in (replace(approval, confirmed=False), replace(approval, budget_usd=Decimal("1")), replace(approval, max_attempts=2), replace(approval, retries=1)):
            with self.assertRaises(g.GatewayError):
                g.PilotSession(self.config, self.endpoint, self.review, self.native, invalid, now=NOW)
        session = g.PilotSession(self.config, self.endpoint, self.review, self.native, approval, now=NOW)
        with self.assertRaises(g.GatewayError):
            session.reserve(now=NOW + 3601)
        self.assertEqual(session.reserve(now=NOW)["execution"], "MANUAL_ONLY")
        with self.assertRaises(g.GatewayError):
            session.reserve(now=NOW)

    def test_metrics_tool_failure_timeout_cost_and_unknown(self):
        base = dict(candidate=g.MODEL, task="synthetic-sort-unique-v1", input_tokens=100,
                    output_tokens=50, cache_read_tokens=0, cost_usd="0.001", latency_ms=1000,
                    endpoint="wafer", tool_success=True, ci_passed=True,
                    attempt=1, retries=0, timed_out=False)
        self.assertEqual(g.pilot_metrics(base, self.config)["status"], "PASS")
        self.assertEqual(g.pilot_metrics(dict(base, candidate="codex", endpoint="codex-native"), self.config)["status"], "PASS")
        for changes, expected in (({"tool_success": False}, "FAIL"), ({"timed_out": True}, "FAIL"),
                                  ({"latency_ms": 30001}, "FAIL"),
                                  ({"ci_passed": False}, "FAIL"), ({"cost_usd": "0.02"}, "FAIL"),
                                  ({"cost_usd": None}, "UNKNOWN"), ({"endpoint": None}, "UNKNOWN"),
                                  ({"cache_read_tokens": None}, "UNKNOWN")):
            with self.subTest(changes=changes):
                self.assertEqual(g.pilot_metrics(dict(base, **changes), self.config)["status"], expected)
        for changes in ({"endpoint": "deepseek"}, {"retries": 1}, {"prompt": "SECRET"}, {"input_tokens": 999999}):
            with self.subTest(changes=changes), self.assertRaises(g.GatewayError):
                g.pilot_metrics(dict(base, **changes), self.config)


class ProbeTests(unittest.TestCase):
    def test_reviewed_cli_pipeline_writes_only_explicit_user_config_and_private_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = b'{"type":"object","properties":{}}'
            fixtures = {
                "budget.json": config_data(), "endpoint.json": endpoint_data(),
                "review.json": dict(schema="nova3.gateway-review/1", version="0.0.0",
                    schema_sha256=hashlib.sha256(raw).hexdigest(), checked_at=NOW,
                    router_options=sorted(g.OPTIONS), native_auth_list_reviewed=True,
                    semantics_reviewed=True)}
            for name, data in fixtures.items():
                (root / name).write_text(json.dumps(data))
            (root / "schema.json").write_bytes(raw)
            user_config = root / ".config/opencode/opencode.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(probe.time, "time", return_value=NOW), mock.patch.object(Path, "home", return_value=root), mock.patch.object(probe, "native_status", return_value=g.NativeStatus("PASS", "0.0.0", "PASS", "PASS")), mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
                code = probe.main(["--config", str(root / "budget.json"),
                    "--endpoint-evidence", str(root / "endpoint.json"),
                    "--review", str(root / "review.json"), "--opencode-schema", str(root / "schema.json"),
                    "--local-native", "--cache", str(root / ".cache"),
                    "--write-user-config", str(user_config)])
            self.assertEqual(code, 0)
            report = json.loads((root / ".cache/model-gateway/preflight.json").read_text())
            self.assertEqual(report, json.loads(output.getvalue()))
            self.assertEqual(report["paid_requests"], 0)
            self.assertEqual(json.loads(user_config.read_text())["model"], "openrouter/" + g.MODEL)

    def test_offline_ignores_metadata_never_runs_native_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps(config_data()))
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(g, "_native_command") as native, mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
                code = probe.main(["--config", str(config), "--offline", "--endpoint-evidence", str(Path(temp)/"absent"), "--stdout-only"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "UNKNOWN")
            native.assert_not_called()

    def test_malformed_inputs_redacted_and_no_native_offline_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SECRET.json"
            path.write_text('{"token":"DO-NOT-PRINT", invalid}')
            for extra in ([], ["--offline", "--local-native"]):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = probe.main(["--config", str(path), "--stdout-only", *extra])
                self.assertEqual(code, 1)
                self.assertNotIn("SECRET", output.getvalue())
                self.assertNotIn("DO-NOT-PRINT", output.getvalue())

    def test_bounded_regular_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.json"
            path.write_bytes(b"x" * 65537)
            with self.assertRaises(g.GatewayError):
                probe.read_metadata(path)
            with self.assertRaises(g.GatewayError):
                probe.read_metadata(Path(temp))


if __name__ == "__main__":
    unittest.main()
