"""Offline regression tests for the optional Hermes backend (#136).

Every subprocess is a fake. Nothing here logs in, calls a provider, publishes an
issue or applies a label.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from nova3_team import hermes_runtime as hr

HELP = (
    "usage: hermes chat [-h] [-q QUERY | --query-file PATH] [--oneshot]\n"
    "                   [--image IMAGE] [-m MODEL] [-t TOOLSETS]\n"
    "                   [--reasoning LEVEL] [-s SKILLS] [--provider PROVIDER]\n"
    "                   [--base-url URL] [--api-key KEY] [-v] [-Q]\n"
    "                   [--format {text,stream-json}] [--resume SESSION_ID]\n"
    "                   [--max-turns N] [--run-budget SECONDS]\n"
    "  -m, --model MODEL     Model to use\n"
    "  -t, --toolsets TOOLSETS\n"
)


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = default
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        for key, response in self.responses.items():
            if key in argv:
                if isinstance(response, BaseException):
                    raise response
                return response
        if self.default is not None:
            if isinstance(self.default, BaseException):
                raise self.default
            return self.default
        return FakeCompleted(0, HELP)


def available_status(**kwargs):
    runner = FakeRunner(default=FakeCompleted(0, HELP))
    return hr.probe("hermes", run=runner, which=lambda name: "/opt/hermes/bin/hermes", **kwargs)


class ProbeTests(unittest.TestCase):
    def test_missing_binary_is_actionable(self):
        status = hr.probe("hermes", which=lambda name: None)
        self.assertEqual(status.state, hr.RuntimeState.MISSING)
        self.assertIn("hermes", status.reason)
        self.assertEqual(status.install_url, hr.INSTALL_URL)

    def test_incompatible_build_fails_closed(self):
        runner = FakeRunner(default=FakeCompleted(0, "usage: hermes chat [-q QUERY]\n"))
        status = hr.probe("hermes", run=runner, which=lambda name: "/x/hermes")
        self.assertEqual(status.state, hr.RuntimeState.INCOMPATIBLE)
        self.assertNotIn("--run-budget", status.capabilities)

    def test_probe_timeout_is_not_a_crash(self):
        runner = FakeRunner(default=subprocess.TimeoutExpired(["hermes"], 1))
        status = hr.probe("hermes", run=runner, which=lambda name: "/x/hermes")
        self.assertEqual(status.state, hr.RuntimeState.PROBE_FAILED)

    def test_nonzero_help_exit_fails_closed(self):
        runner = FakeRunner(default=FakeCompleted(2, HELP))
        status = hr.probe("hermes", run=runner, which=lambda name: "/x/hermes")
        self.assertEqual(status.state, hr.RuntimeState.PROBE_FAILED)

    def test_available_status_reports_capabilities(self):
        status = available_status()
        self.assertTrue(status.available)
        self.assertEqual(set(status.capabilities), set(hr.REQUIRED_CAPABILITIES))
        self.assertEqual(status.executable_path, "/opt/hermes/bin/hermes")

    def test_version_parsed_when_advertised(self):
        runner = FakeRunner(responses={"--version": FakeCompleted(0, "hermes 0.9.12\n")},
                            default=FakeCompleted(0, HELP))
        status = hr.probe("hermes", run=runner, which=lambda name: "/x/hermes")
        self.assertEqual(status.version, "0.9.12")

    def test_version_absent_is_not_fatal(self):
        status = available_status()
        self.assertIsNone(status.version)


class InvocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        self.prompt = os.path.join(self.cwd, "prompt.md")
        with open(self.prompt, "w", encoding="utf-8") as handle:
            handle.write("plan something")
        self.status = available_status()

    def build(self, **overrides):
        kwargs = dict(role=hr.Role.PLANNER, prompt_path=self.prompt, cwd=self.cwd)
        kwargs.update(overrides)
        return hr.build_invocation(self.status, **kwargs)

    def test_planner_argv_is_exact_and_headless(self):
        invocation = self.build()
        argv = list(invocation.argv)
        self.assertEqual(argv[0], "/opt/hermes/bin/hermes")
        self.assertEqual(argv[1], "chat")
        self.assertIn("--query-file", argv)
        self.assertEqual(argv[argv.index("--query-file") + 1], self.prompt)
        self.assertIn("--oneshot", argv)
        self.assertIn("--quiet", argv)
        self.assertEqual(argv[argv.index("--format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--provider") + 1], hr.DEFAULT_PROVIDER)
        self.assertEqual(argv[argv.index("--model") + 1], hr.DEFAULT_MODEL)
        self.assertIn("--safe-mode", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("--source") + 1], "tool")

    def test_no_forbidden_flags_in_any_role(self):
        for role in hr.Role:
            with self.subTest(role=role):
                argv = list(self.build(role=role).argv)
                for flag in hr.FORBIDDEN_FLAGS:
                    self.assertNotIn(flag, argv)

    def test_planner_toolsets_exclude_execution(self):
        invocation = self.build()
        toolsets = invocation.argv[invocation.argv.index("--toolsets") + 1].split(",")
        self.assertEqual(set(toolsets), set(hr.PLANNER_TOOLSETS))
        self.assertFalse(hr.FORBIDDEN_FOR_PLANNER.intersection(toolsets))

    def test_planner_cannot_request_terminal_or_delegation(self):
        for name in ("terminal", "code_execution", "delegation", "cronjob", "browser"):
            with self.subTest(toolset=name):
                with self.assertRaises(hr.HermesError) as caught:
                    self.build(toolsets=[name])
                self.assertEqual(caught.exception.code, "toolsets")

    def test_unknown_toolset_rejected(self):
        with self.assertRaises(hr.HermesError):
            self.build(toolsets=["made_up_toolset"])

    def test_worker_role_is_not_forced_into_safe_mode(self):
        invocation = self.build(role=hr.Role.WORKER)
        self.assertNotIn("--safe-mode", invocation.argv)
        toolsets = invocation.argv[invocation.argv.index("--toolsets") + 1].split(",")
        self.assertIn("terminal", toolsets)

    def test_invalid_provider_and_model_rejected(self):
        for kwargs in ({"provider": "bad provider"}, {"provider": ""},
                       {"model": "../../etc"}, {"model": "a" * 200},
                       {"model": "${INJECTION}"}):
            with self.subTest(**kwargs):
                with self.assertRaises(hr.HermesError):
                    self.build(**kwargs)

    def test_prompt_path_is_a_single_argv_element(self):
        tricky = os.path.join(self.cwd, "prompt $(id) ; `whoami` 'quote'.md")
        with open(tricky, "w", encoding="utf-8") as handle:
            handle.write("x")
        invocation = self.build(prompt_path=tricky)
        argv = list(invocation.argv)
        self.assertIn(tricky, argv)
        self.assertEqual(argv.count(tricky), 1)

    def test_oversized_prompt_rejected(self):
        big = os.path.join(self.cwd, "big.md")
        with open(big, "wb") as handle:
            handle.write(b"x" * (hr.MAX_PROMPT_BYTES + 1))
        with self.assertRaises(hr.HermesError) as caught:
            self.build(prompt_path=big)
        self.assertEqual(caught.exception.code, "prompt")

    def test_missing_cwd_rejected(self):
        with self.assertRaises(hr.HermesError):
            self.build(cwd=os.path.join(self.cwd, "does-not-exist"))

    def test_unavailable_runtime_rejected(self):
        status = hr.HermesStatus(hr.RuntimeState.MISSING, reason="missing")
        with self.assertRaises(hr.HermesError) as caught:
            hr.build_invocation(status, role=hr.Role.PLANNER,
                                prompt_path=self.prompt, cwd=self.cwd)
        self.assertEqual(caught.exception.code, "unavailable")

    def test_budget_bounds(self):
        for budget in (hr.Budget(max_turns=0), hr.Budget(max_turns=500),
                       hr.Budget(run_budget_seconds=5),
                       hr.Budget(run_budget_seconds=99_999),
                       hr.Budget(max_cost_micro_usd=-1),
                       hr.Budget(max_turns=True)):
            with self.subTest(budget=budget):
                with self.assertRaises(hr.HermesError):
                    self.build(budget=budget)

    def test_budget_allows_cost_under_cap(self):
        budget = hr.Budget(max_cost_micro_usd=1000)
        self.assertTrue(budget.allows(1000))
        self.assertFalse(budget.allows(1001))
        self.assertFalse(budget.allows(-1))
        self.assertFalse(budget.allows(True))

    def test_invocation_records_role_and_issue(self):
        invocation = self.build(role=hr.Role.WORKER, issue_number=135)
        self.assertEqual(invocation.env["HERMES_NOVA3_ROLE"], "worker")
        self.assertEqual(invocation.env["HERMES_NOVA3_ISSUE"], "135")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prompt = os.path.join(self.tmp.name, "p.md")
        with open(self.prompt, "w", encoding="utf-8") as handle:
            handle.write("x")
        self.invocation = hr.build_invocation(
            available_status(), role=hr.Role.WORKER,
            prompt_path=self.prompt, cwd=self.tmp.name,
        )

    def test_success_parses_stream_json(self):
        stdout = "\n".join([
            json.dumps({"type": "tool", "tool": "file_read"}),
            json.dumps({"type": "assistant", "content": "final answer"}),
            "a non-JSON banner line",
        ])
        result = hr.run_oneshot(self.invocation, run=FakeRunner(default=FakeCompleted(0, stdout)))
        self.assertTrue(result.ok)
        self.assertIn("final answer", result.text)
        self.assertEqual(result.events[0]["tool"], "file_read")

    def test_nonzero_exit_does_not_echo_stderr(self):
        secret = "gho_" + "C" * 30
        runner = FakeRunner(default=FakeCompleted(3, "", f"fatal: token {secret}"))
        result = hr.run_oneshot(self.invocation, run=runner)
        self.assertEqual(result.state, hr.RunState.FAILED)
        self.assertEqual(result.exit_code, 3)
        self.assertNotIn(secret, result.reason)
        self.assertNotIn(secret, result.text)

    def test_timeout_is_reported(self):
        runner = FakeRunner(default=subprocess.TimeoutExpired(["hermes"], 1))
        result = hr.run_oneshot(self.invocation, run=runner, timeout=0.01)
        self.assertEqual(result.state, hr.RunState.TIMEOUT)

    def test_tty_detection_prevents_hanging_session(self):
        result = hr.run_oneshot(self.invocation, run=FakeRunner(),
                                still_tty=lambda: True)
        self.assertEqual(result.state, hr.RunState.FAILED)
        self.assertIn("TTY", result.reason)

    def test_rate_limit_is_classified(self):
        runner = FakeRunner(default=FakeCompleted(0, "{}", "Error: rate limit exceeded"))
        result = hr.run_oneshot(self.invocation, run=runner)
        self.assertEqual(result.state, hr.RunState.FAILED)
        self.assertEqual(result.reason, "provider rate limited")

    def test_output_secrets_are_redacted(self):
        secret = "sk-" + "D" * 24
        stdout = json.dumps({"type": "assistant", "content": f"key {secret}"})
        result = hr.run_oneshot(self.invocation, run=FakeRunner(default=FakeCompleted(0, stdout)))
        self.assertNotIn(secret, result.text)
        self.assertIn("[redacted]", result.text)

    def test_never_uses_shell(self):
        captured = {}

        def runner(argv, **kwargs):
            captured.update(kwargs)
            return FakeCompleted(0, "{}")

        hr.run_oneshot(self.invocation, run=runner)
        self.assertNotIn("shell", captured)
        self.assertEqual(captured["stdin"], subprocess.DEVNULL)

    def test_start_failure_is_clean(self):
        result = hr.run_oneshot(self.invocation, run=FakeRunner(default=OSError("nope")))
        self.assertEqual(result.state, hr.RunState.FAILED)


class ProposalExtractionTests(unittest.TestCase):
    def payload(self):
        return {
            "schema_version": 1, "key": "hermes-proposed-task",
            "objective": "Do a bounded thing.", "title": "Bounded thing",
            "body": "Details.", "allowed_paths": ["nova3_team/worker.py"],
            "prerequisites": [], "evidence": [], "acceptance": ["It works."],
            "test_commands": [["python3", "-m", "unittest"]],
            "priority": 40, "risk": "low", "privacy": "public-metadata",
            "requires_owner_review": True,
            "source_standard": {"compiler_abi": "unknown", "tu_object_first": False,
                                "original_match": "not_claimed", "evidence": []},
        }

    def test_valid_fenced_proposal_extracted(self):
        proposal, reason = hr.extract_proposal("Sure:\n```json\n"
                                               + json.dumps(self.payload()) + "\n```\n")
        self.assertEqual(reason, "ok")
        self.assertEqual(proposal["key"], "hermes-proposed-task")

    def test_invalid_proposal_rejected(self):
        bad = dict(self.payload(), allowed_paths=["originals/x.apk"])
        proposal, reason = hr.extract_proposal("```json\n" + json.dumps(bad) + "\n```")
        self.assertIsNone(proposal)
        self.assertTrue(reason.startswith("invalid proposal"))

    def test_no_fence_returns_nothing(self):
        proposal, reason = hr.extract_proposal("I would queue this immediately.")
        self.assertIsNone(proposal)
        self.assertIn("no fenced JSON", reason)

    def test_first_valid_block_wins(self):
        text = ("```json\n{broken\n```\n"
                "```json\n" + json.dumps(self.payload()) + "\n```\n")
        proposal, reason = hr.extract_proposal(text)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(proposal)

    def test_extraction_never_publishes_or_labels(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess allowed")):
            proposal, _ = hr.extract_proposal("```json\n" + json.dumps(self.payload()) + "\n```")
        self.assertIsNotNone(proposal)


class PromptAndReadinessTests(unittest.TestCase):
    def test_planner_prompt_is_bounded_and_rules_based(self):
        prompt = hr.planner_prompt("/tmp/context.json", question="What next?")
        self.assertIn("/tmp/context.json", prompt)
        self.assertIn("never claim byte-level", prompt)
        self.assertIn("Do not claim approval", prompt)
        self.assertLess(len(prompt), 10_000)

    def test_prompt_rejects_empty_and_oversized_questions(self):
        with self.assertRaises(hr.HermesError):
            hr.planner_prompt("/tmp/c.json", question="   ")
        with self.assertRaises(hr.HermesError):
            hr.planner_prompt("/tmp/c.json", question="x" * 9000)

    def test_readiness_describes_optional_backend(self):
        payload = hr.readiness(available_status())
        self.assertEqual(payload["provider_id"], "hermes")
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["model"], hr.DEFAULT_MODEL)
        self.assertIn("planner", payload["roles"])
        self.assertIn("worker", payload["roles"])


class RegistryWiringTests(unittest.TestCase):
    """The Hermes backend must be drivable through the shared registry."""

    def setUp(self):
        from nova3_team import provider_runtime as pr
        self.pr = pr

    def test_registered_once_with_stable_api(self):
        self.assertIn("hermes", self.pr.provider_ids())
        self.assertEqual(self.pr.provider_ids().count("hermes"), 1)
        spec = self.pr.get_provider("hermes")
        self.assertEqual(spec.executable, "hermes")
        self.assertEqual(spec.help_args, ("chat", "--help"))
        self.assertEqual(set(spec.required_help), set(hr.REQUIRED_CAPABILITIES))
        self.assertFalse(spec.version_required)
        self.assertFalse(spec.aux_files)
        self.assertIn("query-file", spec.permission_profile)

    def test_probe_uses_hermes_help_tokens_and_skips_version(self):
        with mock.patch.object(self.pr.shutil, "which",
                               return_value="/opt/hermes/bin/hermes"), \
                mock.patch.object(self.pr, "_probe",
                                  return_value=self.pr._Probe(0, HELP)) as probe:
            status = self.pr.probe_provider("hermes")
        self.assertTrue(status.available)
        self.assertIsNone(status.version)
        probe.assert_called_once_with(["/opt/hermes/bin/hermes", "chat", "--help"])
        self.assertEqual(set(status.capabilities), set(hr.REQUIRED_CAPABILITIES))

    def test_missing_binary_has_no_process_and_denies(self):
        with mock.patch.object(self.pr.shutil, "which", return_value=None), \
                mock.patch.object(self.pr, "_probe") as probe:
            status = self.pr.probe_provider("hermes")
        probe.assert_not_called()
        self.assertEqual(status.state, self.pr.RuntimeState.MISSING)
        with self.assertRaises(hr.HermesError) as caught:
            hr.assert_ready(status)
        self.assertEqual(caught.exception.code, "unverified")

    def test_incomplete_help_capabilities_deny(self):
        with mock.patch.object(self.pr.shutil, "which", return_value="/x/hermes"), \
                mock.patch.object(self.pr, "_probe",
                                  return_value=self.pr._Probe(0, "usage: chat [--query-file PATH]\n")):
            status = self.pr.probe_provider("hermes")
        self.assertEqual(status.state, self.pr.RuntimeState.INCOMPATIBLE)
        with self.assertRaises(hr.HermesError) as caught:
            hr.assert_ready(status)
        self.assertEqual(caught.exception.code, "capabilities")

    def test_readiness_surfaces_through_registry(self):
        available = self.pr.ProviderStatus(
            "hermes", self.pr.RuntimeState.AVAILABLE, "/x/hermes", None,
            capabilities=hr.REQUIRED_CAPABILITIES)
        record = self.pr.readiness("hermes", status=available)
        self.assertEqual(record["provider_id"], "hermes")
        self.assertEqual(record["state"], "available")
        self.assertEqual(record["roles"], ["planner", "worker"])
        self.assertEqual(record["model"], hr.DEFAULT_MODEL)
        self.assertEqual(record["provider"], hr.DEFAULT_PROVIDER)
        self.assertIn("install_url", record)
        legacy = self.pr.readiness(
            "codex", status=self.pr.ProviderStatus(
                "codex", self.pr.RuntimeState.MISSING, reason="missing"))
        self.assertEqual(legacy["roles"], ["worker"])
        self.assertEqual(legacy["state"], "missing")

    def test_inline_prompt_builder_refuses_hermes(self):
        status = self.pr.ProviderStatus(
            "hermes", self.pr.RuntimeState.AVAILABLE, "/x/hermes", None,
            capabilities=hr.REQUIRED_CAPABILITIES)
        with self.assertRaises(self.pr.ProviderUnavailable) as caught:
            self.pr.build_command("hermes", "task text", status=status)
        self.assertIn("file-based", str(caught.exception))

    def test_status_adapter_preserves_evidence(self):
        status = self.pr.ProviderStatus(
            "hermes", self.pr.RuntimeState.AVAILABLE, "/x/hermes", None,
            capabilities=hr.REQUIRED_CAPABILITIES, reason="help ok")
        adapted = hr.status_from_provider(status)
        self.assertEqual(adapted.state, hr.RuntimeState.AVAILABLE)
        self.assertEqual(adapted.executable_path, "/x/hermes")
        self.assertEqual(adapted.capabilities, hr.REQUIRED_CAPABILITIES)
        self.assertIs(hr.status_from_provider(adapted), adapted)


class ConfigTests(unittest.TestCase):
    """Operator configuration is explicit, role-scoped and fail-closed."""

    def config(self, role=hr.Role.WORKER, env=None, **kwargs):
        kwargs.setdefault("status", available_status())
        kwargs.setdefault("tty", lambda: False)
        return hr.load_config(role, env=env or {}, **kwargs)

    def test_defaults_are_pinned_and_bounded(self):
        config = self.config()
        self.assertEqual(config.role, hr.Role.WORKER)
        self.assertEqual(config.provider, hr.DEFAULT_PROVIDER)
        self.assertEqual(config.model, hr.DEFAULT_MODEL)
        self.assertEqual(config.timeout_seconds, 1200)
        self.assertEqual(config.budget.run_budget_seconds, 1200)
        self.assertEqual(config.budget.max_turns, hr.MAX_TURNS)
        self.assertEqual(config.executable, "hermes")

    def test_env_selects_provider_model_turns_and_timeout(self):
        config = self.config(env={
            "NOVA3_HERMES_PROVIDER": "nous",
            "NOVA3_HERMES_ALLOWED_PROVIDERS": "nous, openrouter",
            "NOVA3_HERMES_MODEL": "nous/hermes-4-405b",
            "NOVA3_HERMES_MAX_TURNS": "12",
            "NOVA3_AI_SESSION_TIMEOUT": "600",
        })
        self.assertEqual(config.provider, "nous")
        self.assertEqual(config.model, "nous/hermes-4-405b")
        self.assertEqual(config.budget.max_turns, 12)
        self.assertEqual(config.timeout_seconds, 600)
        self.assertEqual(config.budget.run_budget_seconds, 600)

    def test_unknown_provider_denied_unless_explicitly_allowed(self):
        with self.assertRaises(hr.HermesError) as caught:
            self.config(env={"NOVA3_HERMES_PROVIDER": "opernrouter"})
        self.assertEqual(caught.exception.code, "provider")
        with self.assertRaises(hr.HermesError) as caught:
            self.config(env={"NOVA3_HERMES_ALLOWED_PROVIDERS": "bad provider"})
        self.assertEqual(caught.exception.code, "config")

    def test_invalid_model_denied(self):
        for model in ("../../etc/passwd", "${INJECTION}", "a" * 200):
            with self.subTest(model=model):
                with self.assertRaises(hr.HermesError) as caught:
                    self.config(env={"NOVA3_HERMES_MODEL": model})
                self.assertIn(caught.exception.code, ("model", "config"))

    def test_bad_integers_and_timeout_bounds_denied(self):
        for env in ({"NOVA3_AI_SESSION_TIMEOUT": "soon"},
                    {"NOVA3_AI_SESSION_TIMEOUT": "60"},
                    {"NOVA3_AI_SESSION_TIMEOUT": "3601"},
                    {"NOVA3_HERMES_MAX_TURNS": "zero"},
                    {"NOVA3_HERMES_MAX_TURNS": "0"},
                    {"NOVA3_HERMES_MAX_TURNS": "500"}):
            with self.subTest(**env):
                with self.assertRaises(hr.HermesError) as caught:
                    self.config(env=env)
                self.assertEqual(caught.exception.code, "config")
        with self.assertRaises(hr.HermesError) as caught:
            self.config(timeout_seconds=59)
        self.assertEqual(caught.exception.code, "config")

    def test_role_mismatch_cannot_promote_a_worker(self):
        with self.assertRaises(hr.HermesError) as caught:
            self.config(env={"NOVA3_HERMES_ROLE": "planner"})
        self.assertEqual(caught.exception.code, "role")

    def test_worker_and_planner_configuration_are_separate(self):
        shared = {"NOVA3_HERMES_PLANNER_MODEL": "planner/only",
                  "NOVA3_HERMES_PLANNER_PROVIDER": "nous",
                  "NOVA3_HERMES_ALLOWED_PROVIDERS": "nous"}
        worker = self.config(env=shared)
        self.assertEqual(worker.model, hr.DEFAULT_MODEL)
        self.assertEqual(worker.provider, hr.DEFAULT_PROVIDER)
        planner = self.config(role=hr.Role.PLANNER, env=shared)
        self.assertEqual(planner.model, "planner/only")
        self.assertEqual(planner.provider, "nous")
        self.assertEqual(planner.role, hr.Role.PLANNER)
        # Worker overrides must not bleed into the planner either.
        planner = self.config(role=hr.Role.PLANNER,
                              env={"NOVA3_HERMES_MODEL": "worker/only"})
        self.assertEqual(planner.model, hr.DEFAULT_MODEL)

    def test_parent_tty_does_not_disable_headless_worker(self):
        # The child never inherits the parent's stdin; interactive seeding is
        # guarded by run_oneshot(still_tty=...), not by the caller's terminal.
        self.assertEqual(self.config(tty=lambda: True).role, hr.Role.WORKER)
        self.assertEqual(self.config(role=hr.Role.PLANNER, tty=lambda: True).role,
                         hr.Role.PLANNER)

    def test_unverified_and_incompatible_status_deny(self):
        missing = hr.HermesStatus(hr.RuntimeState.MISSING, reason="absent")
        with self.assertRaises(hr.HermesError) as caught:
            self.config(status=missing)
        self.assertEqual(caught.exception.code, "unverified")
        incomplete = hr.HermesStatus(hr.RuntimeState.AVAILABLE, "/x/hermes",
                                     capabilities=("--oneshot",))
        with self.assertRaises(hr.HermesError) as caught:
            self.config(status=incomplete)
        self.assertEqual(caught.exception.code, "capabilities")
        failed = hr.HermesStatus(hr.RuntimeState.PROBE_FAILED, reason="timeout")
        with self.assertRaises(hr.HermesError) as caught:
            self.config(status=failed)
        self.assertEqual(caught.exception.code, "unverified")

    def test_diagnostics_never_echo_values_or_secrets(self):
        secret = "sk-" + "E" * 24
        with self.assertRaises(hr.HermesError) as caught:
            self.config(env={"NOVA3_HERMES_MODEL": secret,
                             "NOVA3_HERMES_PROVIDER": secret})
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn("api-key", str(caught.exception).lower())

    def test_summary_has_no_prompt_or_secret_fields(self):
        payload = self.config().as_dict()
        self.assertEqual(set(payload), {"role", "provider", "model", "max_turns",
                                        "run_budget_seconds", "timeout_seconds",
                                        "executable"})


class PromptMaterialisationTests(unittest.TestCase):
    def test_prompt_file_is_private_and_verbatim(self):
        with tempfile.TemporaryDirectory() as temp:
            aux = os.path.join(temp, "provider-aux")
            text = "TASK $(id) `whoami` ; rm -rf / 'quoted'\nsecond line"
            path = hr.materialise_prompt(aux, text)
            self.assertEqual(os.path.basename(path), "hermes-prompt.md")
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), text.encode("utf-8"))
            if os.name == "posix":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                self.assertEqual(os.stat(aux).st_mode & 0o777, 0o700)

    def test_rejects_empty_nul_and_oversized(self):
        with tempfile.TemporaryDirectory() as temp:
            for bad in ("", "   ", "a\0b", "x" * (hr.MAX_PROMPT_BYTES + 1)):
                with self.subTest(length=len(bad)):
                    with self.assertRaises(hr.HermesError) as caught:
                        hr.materialise_prompt(temp, bad)
                    self.assertEqual(caught.exception.code, "prompt")


class CliTests(unittest.TestCase):
    def test_status_command_reports_json(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = hr.main(["status"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["provider_id"], "hermes")
        self.assertIn(code, (0, 4))

    def test_extract_command_on_file(self):
        from contextlib import redirect_stdout
        import io

        payload = ProposalExtractionTests().payload()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("```json\n" + json.dumps(payload) + "\n```")
            path = handle.name
        self.addCleanup(os.unlink, path)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = hr.main(["extract", "--file", path])
        self.assertEqual(code, 0)
        self.assertIn("hermes-proposed-task", buffer.getvalue())

    def test_extract_without_file_errors(self):
        from contextlib import redirect_stdout
        import io

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = hr.main(["extract"])
        self.assertEqual(code, 2)


class IntegrationChainTests(unittest.TestCase):
    """One synthetic end-to-end chain: context -> Hermes -> draft -> owner gate."""

    def test_hermes_proposal_cannot_queue_itself(self):
        from nova3_team import planning_bridge as pb
        from nova3_team.tests.test_planning_bridge import FakeTransport, spec_payload, NOW

        # 1. Sanitised context the planner may see.
        snapshot = pb.build_context(
            repository={"full_name": "N-O-V-A-3-RE-Contributors/nova3-reconstruction",
                        "default_branch": "main"},
            issues=[{"number": 135, "title": "Queue gate", "state": "open",
                     "labels": [{"name": "nova:review"}], "body": "Requires #134."}],
            pulls=[], policy_version=1,
        )
        pb.assert_sanitized(snapshot)

        # 2. Planner prompt points at the snapshot, not at private data.
        prompt = hr.planner_prompt("/tmp/snapshot.json", question="What unblocks #136?")
        self.assertIn("/tmp/snapshot.json", prompt)

        # 3. A model reply containing a valid proposal is only a draft.
        payload = spec_payload(key="hermes-draft", title="Hermes-proposed hardening",
                               allowed_paths=["nova3_team/worker.py"])
        proposal, reason = hr.extract_proposal(
            "Here is my plan.\n```json\n" + json.dumps(payload) + "\n```\n")
        self.assertEqual(reason, "ok")
        spec = pb.validate_proposal(proposal)

        # 4. Publishing is dry-run by default and never carries nova:queued.
        transport = FakeTransport()
        result = pb.publish_draft(transport, "N-O-V-A-3-RE-Contributors",
                                  "nova3-reconstruction", spec, apply=False)
        self.assertEqual(result["action"], "would-create")
        self.assertNotIn("nova:queued", result["labels"])
        self.assertEqual(transport.mutations, [])

        # 5. Queueing requires an owner approval plus explicit consent; a
        #    model claim alone is denied.
        denied = pb.queue_transition(
            transport, FakeReplayForChain(), "N-O-V-A-3-RE-Contributors",
            "nova3-reconstruction", transport.issue_number,
            nonce="c" * 64, task_spec_sha256=spec.fingerprint, apply=True,
            confirm=lambda binding: False, clock=lambda: NOW,
        )
        self.assertEqual(denied["action"], "denied")
        self.assertEqual(transport.mutations, [])


class FakeReplayForChain:
    def reserve(self, keys):
        raise AssertionError("a denied transition must never reserve keys")


if __name__ == "__main__":
    unittest.main()
