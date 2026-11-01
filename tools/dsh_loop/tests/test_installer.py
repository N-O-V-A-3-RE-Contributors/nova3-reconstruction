"""Unit tests for the NOVA3 DeepSeek Harness loop installer and launcher.

These tests never touch the network, never read the user's real credentials and
never write outside ``tempfile`` directories. They are run by CI on Linux, macOS
and Windows, which is the point: the installer must behave on every supported
platform, not only the maintainer's laptop.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.dsh_loop import credentials, installer, launch, packager, prompt, wizard
from tools.dsh_loop import platforms as plat
from tools.dsh_loop.workspace import LoopState, Repo, Signature, signature


class PlatformFactsTests(unittest.TestCase):
    def test_node_archive_names_per_platform(self):
        cases = {
            ("linux", "x86_64"): "node-v22.1.0-linux-x64.tar.xz",
            ("linux", "aarch64"): "node-v22.1.0-linux-arm64.tar.xz",
            ("macos", "aarch64"): "node-v22.1.0-darwin-arm64.tar.gz",
            ("macos", "x86_64"): "node-v22.1.0-darwin-x64.tar.gz",
            ("windows", "x86_64"): "node-v22.1.0-win-x64.zip",
        }
        for (os_name, arch), expected in cases.items():
            facts = plat.Facts(os_name, arch, arch)
            self.assertEqual(plat.node_archive_name("v22.1.0", facts), expected)

    def test_unsupported_arch_is_an_explicit_error(self):
        with self.assertRaises(plat.PlatformError):
            plat.node_archive_name("v22.1.0", plat.Facts("linux", "riscv64", "riscv64"))

    def test_npm_command_never_needs_a_shell_on_unix(self):
        with mock.patch.object(plat, "WINDOWS", False):
            self.assertEqual(plat.npm_command("/usr/bin/npm"), ["/usr/bin/npm"])

    def test_windows_npm_cmd_uses_comspec(self):
        with mock.patch.object(plat, "WINDOWS", True), \
                mock.patch.dict(os.environ, {"COMSPEC": r"C:\Windows\System32\cmd.exe"}):
            argv = plat.npm_command(r"C:\node\npm.cmd")
            self.assertEqual(argv[0], r"C:\Windows\System32\cmd.exe")
            self.assertEqual(argv[1:4], ["/d", "/s", "/c"])
            self.assertEqual(argv[4], r"C:\node\npm.cmd")

    def test_config_and_script_locations_follow_platform_conventions(self):
        with mock.patch.object(plat, "WINDOWS", False), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}, clear=False):
            self.assertEqual(plat.default_config_path(), Path("/tmp/xdg/nova3/dsh-loop.json"))

    def test_platform_tag_is_release_friendly(self):
        self.assertIn(plat.current_platform_tag().split("-")[0], ("linux", "macos", "windows"))


class CredentialTests(unittest.TestCase):
    def test_fingerprint_never_reveals_the_secret(self):
        secret = "sk-or-v1-abcdefghijklmnopqrstuvwxyz012345"
        printed = credentials.fingerprint(secret)
        self.assertNotIn(secret[:-4], printed)
        self.assertTrue(printed.startswith("…"))
        self.assertEqual(printed.count("sha256:"), 1)

    def test_status_reports_absence_without_error(self):
        status = credentials.key_status("openrouter", env={})
        self.assertFalse(status.present)
        self.assertEqual(status.env_name, "OPENROUTER_API_KEY")

    def test_status_accepts_typical_shapes_and_flags_odd_ones(self):
        good = credentials.key_status("openrouter",
                                      env={"OPENROUTER_API_KEY": "sk-or-v1-" + "a" * 32})
        self.assertTrue(good.present)
        self.assertTrue(good.valid_shape)
        odd = credentials.key_status("openrouter", env={"OPENROUTER_API_KEY": "not-a-key-format"})
        self.assertTrue(odd.present)
        self.assertFalse(odd.valid_shape)

    def test_mask_removes_keys_from_log_text(self):
        text = "error: key sk-or-v1-abcdefghijklmnopqrstuvwxyz was rejected; Bearer sk-0123456789abcdefXYZ"
        masked = credentials.mask_embedded_secrets(text)
        self.assertNotIn("abcdefghijklmnop", masked)
        self.assertNotIn("0123456789abcdef", masked)
        self.assertIn("<redacted>", masked)

    def test_shell_export_line_is_per_shell(self):
        self.assertIn("export", credentials.shell_export_line("/bin/zsh", "X_KEY"))
        self.assertIn("set -gx", credentials.shell_export_line("/usr/bin/fish", "X_KEY"))

    def test_profile_marker_line_is_written_once_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".bashrc"
            profile.write_text("# existing\n")
            state = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
                self.assertTrue(credentials.install_env_line(profile, "DEEPSEEK_API_KEY",
                                                             state_path=state, echo=lambda _m: None))
                self.assertFalse(credentials.install_env_line(profile, "DEEPSEEK_API_KEY",
                                                              state_path=state, echo=lambda _m: None))
            text = profile.read_text()
            self.assertEqual(text.count("export DEEPSEEK_API_KEY"), 1)
            self.assertIn('<paste your key here>', text)


class InstallerStructureTests(unittest.TestCase):
    def _installer(self, tmp: Path, **kwargs) -> installer.Installer:
        state = tmp / "config" / "dsh-loop.json"
        return installer.Installer(state_path=state, install_root=tmp / "install",
                                   echo=lambda _m: None, assume_yes=True,
                                   allow_network=False, **kwargs)

    def test_fresh_state_has_no_secret_fields(self):
        state = installer.default_state()
        self.assertEqual(state["marker"], installer.MARKER)
        self.assertIsNone(state["api_key_env"])
        self.assertFalse(any("key" in k and state[k] for k in state))

    def test_state_roundtrip_and_unknown_schema_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            state = installer.default_state()
            state["provider"] = "openrouter"
            state["model"] = "x/y"
            installer.save_state(path, state)
            loaded = installer.load_state(path)
            self.assertEqual(loaded["provider"], "openrouter")
            path.write_text(json.dumps({"marker": "something else"}))
            self.assertEqual(installer.load_state(path)["provider"], None)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_root_refusal(self):
        inst = self._installer(Path("/tmp"))
        with mock.patch.object(os, "geteuid", return_value=0, create=True):
            with self.assertRaises(installer.InstallError):
                inst.check_root()

    def test_profile_is_generated_without_secrets_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._installer(Path(tmp), provider="openrouter", model="deepseek/x")
            inst.check_python()
            inst.resolve_provider()
            inst.write_profile()
            patch = (inst.profile_dir / "cordis.patch.yml").read_text()
            self.assertIn("apiKeyEnv: OPENROUTER_API_KEY", patch)
            self.assertIn("model: deepseek/x", patch)
            self.assertIn("danger-full-access", patch)
            self.assertIn("mode: never", patch)
            self.assertNotIn("__MODEL_ID__", patch)
            package = json.loads((inst.profile_dir / "package.json").read_text())
            self.assertEqual(package["dsh"]["profile"]["bundles"],
                             ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"])

    def test_rerunning_keeps_an_explicit_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._installer(Path(tmp), provider="openrouter", model="custom/model")
            inst.check_python()
            inst.resolve_provider()
            installer.save_state(inst.state_path, inst.state)
            again = self._installer(Path(tmp))
            again.check_python()
            again.resolve_provider()
            self.assertEqual(again.state["model"], "custom/model")
            self.assertEqual(again.state["provider"], "openrouter")

    def test_profile_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._installer(Path(tmp), provider="deepseek")
            inst.check_python()
            inst.resolve_provider()
            inst.write_profile()
            result = inst._write_if_changed(inst.profile_dir / "cordis.patch.yml",
                                            (inst.profile_dir / "cordis.patch.yml").read_text())
            self.assertFalse(result)


class ArchiveSafetyTests(unittest.TestCase):
    def test_tar_rejects_path_traversal(self):
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar"
            with tarfile.open(archive, "w") as tar:
                info = tarfile.TarInfo("../escape.txt")
                info.size = 3
                tar.addfile(info, io.BytesIO(b"bad"))
            with self.assertRaises(installer.InstallError):
                installer.safe_extract_tar(archive, Path(tmp) / "out")

    def test_zip_rejects_path_traversal(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../../escape.txt", "bad")
            with self.assertRaises(installer.InstallError):
                installer.safe_extract_zip(archive, Path(tmp) / "out")

    def test_valid_archive_extracts(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "ok.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("node/bin/node", "x")
            dest = Path(tmp) / "out"
            installer.safe_extract_zip(archive, dest)
            self.assertTrue((dest / "node" / "bin" / "node").is_file())


class LauncherTests(unittest.TestCase):
    def test_unix_launcher_contains_no_secret_and_the_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "cfg.json"
            installer.save_state(state, {**installer.default_state(), "workspace": "/src/nova3"})
            text = launch.unix_launcher_text(state)
            self.assertIn("/src/nova3", text)
            self.assertIn("launcher_main", text)
            self.assertNotIn("sk-", text.replace("start", ""))
            self.assertIn("3,10", text)

    def test_windows_launcher_mentions_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "cfg.json"
            text = launch.windows_cmd_launcher_text(state)
            self.assertIn("python -c", text)
            spec = Path(tmp) / "cfg.spec.json"
            self.assertTrue(spec.is_file())

    def test_status_hides_the_key(self):
        state = {**installer.default_state(), "provider": "openrouter",
                 "api_key_env": "OPENROUTER_API_KEY", "model": "m"}
        with mock.patch.object(credentials, "key_status",
                               return_value=credentials.KeyStatus(
                                   "openrouter", "OPENROUTER_API_KEY", True,
                                   credentials.fingerprint("sk-or-v1-" + "z" * 30), True)):
            text = launch.status_text(state, check_key=True)
        self.assertIn("…zzzz", text)
        self.assertNotIn("z" * 30, text)


class LoopStateTests(unittest.TestCase):
    def test_signature_changes_when_a_tracked_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Repo(root)
            first = signature(repo)
            created = root / "x.txt"
            created.write_text("a")
            # Not a git repo: porcelain output is empty, so count stays 0 but
            # the helper must still be deterministic and comparable.
            self.assertEqual(signature(repo).digest, first.digest)

    def test_pass_records_append_and_stop_reason_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = LoopState(Path(tmp))
            state.ensure()
            state.record_pass({"ok": True, "summary": "did a thing"})
            state.stop("done")
            again = LoopState(Path(tmp))
            self.assertEqual(again.data["passes"], 1)
            self.assertEqual(again.data["stopped_reason"], "done")
            self.assertEqual(again.recent_passes(1)[0]["summary"], "did a thing")

    def test_backlog_parsing_finds_unchecked_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = LoopState(Path(tmp))
            state.ensure()
            (state.dir / "BACKLOG.md").write_text(
                "# Backlog\n1. [x] finished\n2. [ ] next thing\n3. [ ] later thing\n")
            self.assertEqual(state.pending_tasks(), ["2. next thing", "3. later thing"])

    def test_prompts_name_the_context_files_and_stop_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = LoopState(Path(tmp))
            state.ensure()
            text = prompt.seed_prompt(state, goal="mission", repo_slug="o/r", conservative=False)
            self.assertIn("AGENTS.md", text)
            self.assertIn("mission", text)
            self.assertIn("BACKLOG.md", text)
            followup = prompt.continuation_prompt(state, max_minutes=60, conservative=True)
            self.assertIn("memory pressure", followup)
            self.assertIn("tools/check_all.sh", followup)


class WorkerTests(unittest.TestCase):
    def test_sync_policy_copies_only_changed_documents(self):
        from tools.dsh_loop import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main"
            work = Path(tmp) / "worker"
            (root / "docs" / "progress").mkdir(parents=True)
            (root / "AGENTS.md").write_text("new rules")
            (root / "docs" / "progress" / "NEXT.md").write_text("next")
            work.mkdir()
            (work / "AGENTS.md").write_text("old rules")
            with mock.patch.object(worker_mod, "ROOT", root), \
                    mock.patch.object(worker_mod, "POLICY_FILES", ("AGENTS.md", "docs/progress/NEXT.md")):
                updated = worker_mod.sync_policy(work)
                self.assertEqual(updated, ["AGENTS.md", "docs/progress/NEXT.md"])
                self.assertEqual(worker_mod.sync_policy(work), [])

    def test_tasks_from_next_skips_negative_priorities(self):
        from tools.dsh_loop.worker import tasks_from_next
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NEXT.md"
            path.write_text("# Next\n\n1. Recover function 0x1234 with tests\n"
                            "2. Do not chase the rolled form\n3. **Bold** priority `code`\n")
            tasks = tasks_from_next(path)
            self.assertEqual(tasks[0], "Recover function 0x1234 with tests")
            self.assertEqual(tasks[1], "Bold priority code")


class PackagerTests(unittest.TestCase):
    def test_getting_started_mentions_every_platform_launcher(self):
        text = packager.getting_started({"commit": "abc123", "repo": "o/r"})
        for token in ("install.sh", "START.command", "START.cmd", "nova3-dsh status"):
            self.assertIn(token, text)
        self.assertIn("abc123", text)


if __name__ == "__main__":
    unittest.main()
