"""Offline regressions for the Hermes -> permanent worker execution path (#136).

Every subprocess and GitHub call is fake. Nothing logs in, calls a model,
publishes an issue, applies a label or spends money.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nova3_team import hermes_runtime as hr
from nova3_team import worker

HELP = (
    "usage: hermes chat [-h] [-q QUERY | --query-file PATH] [--oneshot]\n"
    "  --format {text,stream-json}  --provider PROVIDER  -m, --model MODEL\n"
    "  -t, --toolsets TOOLSETS  --max-turns N  --run-budget SECONDS\n"
)


def status(path="/opt/hermes/bin/hermes"):
    return hr.HermesStatus(hr.RuntimeState.AVAILABLE, path, None,
                           tuple(hr.REQUIRED_CAPABILITIES))


class FakeGh:
    def __init__(self, body="Allowed paths: nova3_team/worker.py"):
        self.body = body
        self.comments = []

    def comment(self, number, text):
        self.comments.append((number, text))

    def issue(self, number):
        return {"number": number, "title": "t", "body": self.body, "labels": []}


class HermesCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.aux = Path(self.tmp.name) / "private" / "provider-aux"
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in list(os.environ):
            if name.startswith("NOVA3_HERMES") or name == "NOVA3_AI_SESSION_TIMEOUT":
                os.environ.pop(name, None)

    def build(self, prompt="TASK #1 $(id) `whoami`; rm -rf /"):
        return worker.hermes_command(prompt, self.work, self.aux, status())

    def test_worker_argv_is_file_based_and_bounded(self):
        invocation = self.build()
        argv = list(invocation.argv)
        self.assertEqual(argv[:2], ["/opt/hermes/bin/hermes", "chat"])
        self.assertIn("--query-file", argv)
        prompt_path = argv[argv.index("--query-file") + 1]
        self.assertTrue(prompt_path.endswith("hermes-prompt.md"))
        self.assertEqual(argv[argv.index("--oneshot") + 1], "--quiet")
        self.assertEqual(argv[argv.index("--format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--provider") + 1], hr.DEFAULT_PROVIDER)
        self.assertEqual(argv[argv.index("--model") + 1], hr.DEFAULT_MODEL)
        toolsets = argv[argv.index("--toolsets") + 1].split(",")
        self.assertEqual(set(toolsets), set(hr.WORKER_TOOLSETS))
        self.assertEqual(argv[argv.index("--max-turns") + 1], str(hr.MAX_TURNS))
        self.assertEqual(argv[argv.index("--run-budget") + 1], "1200")
        self.assertEqual(invocation.env["HERMES_NOVA3_ROLE"], "worker")

    def test_task_text_never_enters_argv(self):
        prompt = "TASK #1 $(id) `whoami`; rm -rf /"
        argv = list(self.build(prompt).argv)
        self.assertFalse(any(prompt in element for element in argv))
        prompt_path = argv[argv.index("--query-file") + 1]
        with open(prompt_path, "rb") as handle:
            self.assertEqual(handle.read(), prompt.encode("utf-8"))
        if os.name == "posix":
            self.assertEqual(os.stat(prompt_path).st_mode & 0o777, 0o600)

    def test_no_forbidden_flags_or_secret_arguments(self):
        argv = list(self.build().argv)
        for flag in hr.FORBIDDEN_FLAGS:
            self.assertNotIn(flag, argv)
        for element in argv:
            self.assertNotIn("--api-key", element)
            self.assertNotIn("--yolo", element)
            self.assertNotIn("--accept-hooks", element)

    def test_planner_role_configuration_is_denied_in_worker_path(self):
        os.environ["NOVA3_HERMES_ROLE"] = "planner"
        with self.assertRaises(hr.HermesError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, "role")
        self.assertFalse(self.aux.exists())
        self.assertFalse((self.aux / "hermes-prompt.md").exists())

    def test_interactive_tty_denies_before_prompt_file(self):
        with mock.patch.object(worker.sys.stdin, "isatty", return_value=True):
            with self.assertRaises(hr.HermesError) as caught:
                self.build()
        self.assertEqual(caught.exception.code, "tty")
        self.assertFalse((self.aux / "hermes-prompt.md").exists())

    def test_unavailable_status_denies_before_prompt_file(self):
        missing = hr.HermesStatus(hr.RuntimeState.MISSING, reason="absent")
        with self.assertRaises(hr.HermesError) as caught:
            worker.hermes_command("task", self.work, self.aux, missing)
        self.assertEqual(caught.exception.code, "unverified")
        self.assertFalse((self.aux / "hermes-prompt.md").exists())

    def test_timeout_and_exit_mapping_with_real_local_binaries(self):
        # /bin/true ignores the Hermes flags and exits 0: argv shape is real,
        # no model is contacted.
        with tempfile.TemporaryDirectory() as temp:
            prompt = os.path.join(temp, "p.md")
            with open(prompt, "w", encoding="utf-8") as handle:
                handle.write("x")
            success = hr.build_invocation(status("/bin/true"), role=hr.Role.WORKER,
                                          prompt_path=prompt, cwd=temp,
                                          budget=hr.Budget(max_turns=1, run_budget_seconds=30))
            result = hr.run_oneshot(success, timeout=10)
            self.assertEqual(result.state, hr.RunState.SUCCEEDED)
            self.assertEqual(result.exit_code, 0)
            failure = hr.build_invocation(status("/bin/false"), role=hr.Role.WORKER,
                                          prompt_path=prompt, cwd=temp,
                                          budget=hr.Budget(max_turns=1, run_budget_seconds=30))
            result = hr.run_oneshot(failure, timeout=10)
            self.assertEqual(result.state, hr.RunState.FAILED)
            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.reason, "Hermes exited 1")
            slow_path = os.path.join(temp, "slow-hermes")
            with open(slow_path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nsleep 5\n")
            os.chmod(slow_path, 0o700)
            slow = hr.build_invocation(status(slow_path), role=hr.Role.WORKER,
                                       prompt_path=prompt, cwd=temp,
                                       budget=hr.Budget(max_turns=1, run_budget_seconds=30))
            result = hr.run_oneshot(slow, timeout=0.2)
            self.assertEqual(result.state, hr.RunState.TIMEOUT)


class WorkerGateOrderTests(unittest.TestCase):
    """Authorization must complete before any Hermes configuration or launch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state"
        self.issue = {"number": 1, "title": "task",
                      "body": "Allowed paths: nova3_team/worker.py"}

    def test_denied_claim_never_reaches_hermes(self):
        gh = FakeGh()
        with mock.patch.object(worker, "claim", return_value=None), \
                mock.patch.object(worker.recovery, "load", return_value={}), \
                mock.patch.object(worker, "hermes_command") as build, \
                mock.patch.object(worker, "run_provider") as launch, \
                mock.patch.object(worker.hermes_runtime, "probe") as probe:
            result = worker.work_one(gh, "o/r", "user", self.issue, "hermes",
                                     self.root, 1, None, provider_status=status())
        self.assertIsNone(result)
        build.assert_not_called()
        launch.assert_not_called()
        probe.assert_not_called()
        self.assertEqual(gh.comments, [])

    def test_missing_allowed_paths_releases_before_hermes(self):
        gh = FakeGh(body="No scope here")
        issue = {"number": 1, "title": "task", "body": "No scope here"}
        with mock.patch.object(worker, "claim", return_value="token"), \
                mock.patch.object(worker.recovery, "load", return_value={}), \
                mock.patch.object(worker.recovery, "update"), \
                mock.patch.object(worker, "hermes_command") as build, \
                mock.patch.object(worker, "run_provider") as launch:
            result = worker.work_one(gh, "o/r", "user", issue, "hermes",
                                     self.root, 1, None, provider_status=status())
        self.assertFalse(result)
        build.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(gh.comments, [(1, "/nova release token")])

    def test_claim_is_requested_before_configuration(self):
        order = []
        gh = FakeGh()

        def fake_claim(*args, **kwargs):
            order.append("claim")
            return None

        with mock.patch.object(worker, "claim", side_effect=fake_claim), \
                mock.patch.object(worker.recovery, "load", return_value={}), \
                mock.patch.object(worker, "hermes_command",
                                  side_effect=lambda *a, **k: order.append("build")):
            worker.work_one(gh, "o/r", "user", self.issue, "hermes",
                            self.root, 1, None, provider_status=status())
        self.assertEqual(order, ["claim"])


class RegistrySelectionTests(unittest.TestCase):
    def test_hermes_is_selectable_through_the_cli_choices(self):
        from nova3_team import provider_runtime
        self.assertIn("hermes", provider_runtime.provider_ids())

    def test_command_for_does_not_inline_hermes_prompts(self):
        from nova3_team import provider_runtime
        registry_status = provider_runtime.ProviderStatus(
            "hermes", provider_runtime.RuntimeState.AVAILABLE, "/opt/hermes/bin/hermes",
            None, capabilities=hr.REQUIRED_CAPABILITIES)
        with mock.patch.object(worker, "_codex_json_supported") as probe:
            with self.assertRaises(provider_runtime.ProviderUnavailable):
                worker.command_for("hermes", "task", status=registry_status)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
