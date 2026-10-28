"""Source-only metadata fixtures; never execute a real/fake compiler or game code."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from nova3_team import historical_toolchain as ht


def fixture_queries():
    values = dict(version="arm-linux-androideabi-g++ (GCC) 4.4.3\n",
                  gcc_version="4.4.3\n", target="arm-linux-androideabi\n",
                  specs="*cc1:\n%{mthumb:-mthumb}\n", sysroot="\n",
                  linker="/fixture/bin/arm-linux-androideabi-ld\n",
                  assembler="/fixture/bin/arm-linux-androideabi-as\n",
                  cc1plus="/fixture/libexec/cc1plus\n")
    return {k: dict(option=[ht.QUERIES[k]], exit_code=0, stdout=v,
                    stdout_sha256=ht.digest(v.encode()), stderr_sha256=ht.digest(b""))
            for k, v in values.items()}


def fake_query(cmd, **kwargs):
    if cmd[-1] in ht.QUERIES.values():
        name = next(k for k, v in ht.QUERIES.items() if v == cmd[-1])
        value = fixture_queries()[name]["stdout"]
    else:
        raise AssertionError("unexpected metadata operation")
    return subprocess.CompletedProcess(cmd, 0, stdout=value.encode(), stderr=b"")


class HistoricalToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.compiler = self.root / "bin" / "arm-linux-androideabi-g++"
        self.compiler.parent.mkdir()
        self.compiler.write_bytes(b"source-owned inert fixture; never executed")
        self.compiler.chmod(0o700)
        self.pin = ht.file_sha256(self.compiler)

    def report(self):
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            return ht.fingerprint(self.compiler, trusted_sha256=self.pin)

    def test_discovery_is_bounded_and_does_not_execute(self):
        hidden = self.root / "unsearched" / "bin"
        hidden.mkdir(parents=True)
        (hidden / self.compiler.name).write_text("ignored")
        with mock.patch.object(ht.subprocess, "run") as run:
            report = ht.discover([self.root])
        run.assert_not_called()
        self.assertEqual(len(report["candidates"]), 5)
        present = [x for x in report["candidates"] if x["present"]]
        self.assertEqual(len(present), 1)
        self.assertEqual(present[0]["sha256"], self.pin)
        self.assertFalse(report["GCC_4_4_3_CANDIDATE_AVAILABLE"])

    def test_missing_root_and_missing_compiler(self):
        report = ht.discover([self.root / "missing"])
        self.assertEqual(report["status"], "MISSING_HISTORICAL_COMPILER")
        with self.assertRaises(FileNotFoundError):
            ht.fingerprint(self.root / "missing")

    def test_ndk_prebuilt_layout(self):
        directory = self.root / "toolchains" / "arm-linux-androideabi-4.4.3" / "prebuilt" / "linux-x86" / "bin"
        directory.mkdir(parents=True)
        (directory / self.compiler.name).write_bytes(b"another inert fixture")
        report = ht.discover([self.root])
        self.assertEqual(sum(x["present"] for x in report["candidates"]), 2)

    def test_environment_root_and_discovery_limits(self):
        with mock.patch.dict(os.environ, {"NOVA3_HISTORICAL_TOOLCHAIN_ROOT": str(self.root)}):
            self.assertEqual(ht.discover()["roots"][0], str(self.root))
        for roots in ([], [self.root] * 17):
            with self.assertRaises(ht.ToolchainError):
                ht.discover(roots)

    def test_hash_only_default_never_executes(self):
        with mock.patch.object(ht.subprocess, "run") as run:
            report = ht.fingerprint(self.compiler)
        run.assert_not_called()
        self.assertEqual(report["status"], "UNTRUSTED_NOT_EXECUTED")
        self.assertEqual(report["compiler_executable_sha256"], self.pin)
        self.assertEqual(report["queries"], {})

    def test_pin_required_and_changed_sha_rejected_before_execution(self):
        for pin in ("bad", "A" * 64, "0" * 64):
            with self.subTest(pin=pin), mock.patch.object(ht.subprocess, "run") as run:
                with self.assertRaises(ht.ToolchainError):
                    ht.fingerprint(self.compiler, trusted_sha256=pin)
                run.assert_not_called()
        self.compiler.write_bytes(b"changed fixture")
        with self.assertRaisesRegex(ht.ToolchainError, "SHA-256"):
            ht.fingerprint(self.compiler, trusted_sha256=self.pin)

    def test_nonexecutable_and_nonregular_inputs(self):
        self.compiler.chmod(0o600)
        with self.assertRaisesRegex(ht.ToolchainError, "not executable"):
            ht.fingerprint(self.compiler, trusted_sha256=self.pin)
        with self.assertRaises(ht.ToolchainError):
            ht.file_sha256(self.root)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ht.ToolchainError):
            ht.file_sha256(fifo)
        with mock.patch.object(ht, "MAX_FILE", 1):
            with self.assertRaises(ht.ToolchainError):
                ht.file_sha256(self.compiler)

    def test_trusted_queries_are_metadata_only_with_sanitized_environment(self):
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query) as run:
            report = ht.fingerprint(self.compiler, trusted_sha256=self.pin)
        self.assertTrue(report["GCC_4_4_3_CANDIDATE_AVAILABLE"])
        self.assertEqual(run.call_count, 8)
        for call in run.call_args_list:
            self.assertEqual(len(call.args[0]), 2)
            self.assertLessEqual(call.kwargs["timeout"], 30)
            self.assertEqual(call.kwargs["env"], {"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"})
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", call.kwargs)
        self.assertEqual(report["arm_attributes"]["status"], "NOT_BUILT")
        for grade in ("PROFILE_REPRODUCIBLE", "EXACT_ORIGINAL_COMPILER_PROVEN",
                      "EXACT_HISTORICAL_TOOLCHAIN_VERIFIED", "SOURCE_EQUIVALENCE_VERIFIED", "WHOLE_SO_HASH_MATCH"):
            self.assertFalse(report[grade])
        self.assertEqual(report["LINKED_WINDOW_MATCH"], "NOT_TESTED")

    def test_triples_reject_linux_hardfloat_host_and_spoofed_prefix(self):
        for triple in ("arm-linux-gnueabi", "arm-linux-androideabihf", "x86_64-linux-gnu",
                       "arm-linux-androideabi-evil", "aarch64-linux-android"):
            q = fixture_queries()
            q["target"]["stdout"] = triple
            self.assertEqual(ht.assess_metadata(q), "CONTRADICTORY_TARGET_TRIPLE")

    def test_unsupported_and_contradictory_versions(self):
        for banner, version in (("GCC 4.4.30", "4.4.3"), ("GCC 4.4.3", "4.6"),
                                ("clang version 4.4.3", "4.4.3"),
                                ("GCC 4.9", "4.9"), ("unknown 4.4.3", "4.4.3")):
            q = fixture_queries()
            q["version"]["stdout"] = banner
            q["gcc_version"]["stdout"] = version
            self.assertEqual(ht.assess_metadata(q), "UNSUPPORTED_COMPILER_VERSION")

    def test_missing_failed_or_empty_specs_metadata(self):
        self.assertEqual(ht.assess_metadata({}), "INCOMPLETE_COMPILER_METADATA")
        q = fixture_queries()
        q["specs"]["exit_code"] = 1
        self.assertEqual(ht.assess_metadata(q), "INCOMPLETE_COMPILER_METADATA")
        q = fixture_queries()
        q["specs"]["stdout"] = ""
        self.assertEqual(ht.assess_metadata(q), "INCOMPLETE_COMPILER_METADATA")

    def test_query_timeout_and_oversized_output(self):
        with mock.patch.object(ht.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                ht.fingerprint(self.compiler, trusted_sha256=self.pin)
        with mock.patch.object(ht.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"x" * 20, b"")), mock.patch.object(ht, "MAX_OUTPUT", 10):
            with self.assertRaisesRegex(ht.ToolchainError, "output"):
                ht.fingerprint(self.compiler, trusted_sha256=self.pin)

    def test_tool_pins_checked_before_queries_and_never_follow_gcc_program_path(self):
        with mock.patch.object(ht.subprocess, "run") as run:
            with self.assertRaises(OSError):
                ht.fingerprint(self.compiler, trusted_sha256=self.pin,
                               tools={"linker": (self.root / "missing-ld", "0" * 64)})
            run.assert_not_called()
        report = self.report()
        self.assertEqual(report["tools"], {})
        self.assertIn("/fixture/bin/", report["queries"]["linker"]["stdout"])
        with self.assertRaises(ht.ToolchainError):
            ht.fingerprint(self.compiler, tools={"linker": (self.compiler, self.pin)})
        with self.assertRaises(ht.ToolchainError):
            ht.fingerprint(self.compiler, tools={"arbitrary": (self.compiler, self.pin)})

    def test_explicit_binutils_fingerprinted_separately(self):
        linker = self.root / "ld"
        linker.write_bytes(b"inert linker fixture")
        linker.chmod(0o700)
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            report = ht.fingerprint(self.compiler, trusted_sha256=self.pin,
                                    tools={"linker": (linker, ht.file_sha256(linker))})
        self.assertEqual(report["tools"]["linker"]["sha256"], ht.file_sha256(linker))
        self.assertFalse(report["PROFILE_REPRODUCIBLE"])

    def test_generated_profiles_are_sequential_bounded_and_never_build(self):
        with mock.patch.object(ht.subprocess, "run") as run:
            plans = ht.generate_profiles(None, count=8)
        run.assert_not_called()
        self.assertEqual(len(plans), 8)
        for plan in plans:
            self.assertEqual(plan["status"], "MISSING_HISTORICAL_COMPILER")
            self.assertNotIn("compiler", plan)
            self.assertNotEqual(plan["schema"], ht.MATRIX_PROFILE_SCHEMA)
            self.assertTrue(plan["sequential"])
            self.assertEqual(plan["compile_timeout_seconds"], 30)
            self.assertEqual(plan["link_timeout_seconds"], 30)
        for count in (0, 9, True, "4"):
            with self.assertRaises(ht.ToolchainError):
                ht.generate_profiles(None, count=count)

    def test_safe_flags_and_nonexecuting_explicit_option_parsing(self):
        good = ht.profile_flags(1)[0]
        for flag in ("-fplugin=/tmp/payload", "-B/tmp", "@response", "-specs=/tmp/specs",
                     "--sysroot=/tmp", "-Wl,-T,file", "-ooutput", "-O2;touch /tmp/unsafe",
                     "-std=c++17", "-mthumb", "-mfloat-abi=hard"):
            with self.subTest(flag=flag), self.assertRaises(ht.ToolchainError):
                ht.validate_flags(good + [flag])
        for flags in (None, "-O2", good + ["-O2"], good + ["-O3"], good[:-1],
                      good + ["-fomit-frame-pointer", "-fno-omit-frame-pointer"], good + [1]):
            with self.assertRaises(ht.ToolchainError):
                ht.validate_flags(flags)
        with self.assertRaises(ht.ToolchainError):
            ht.generate_profiles(None, flags=good, count=2)
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(ht.subprocess, "run") as run:
            rc = ht.main(["profiles", "--count", "1", *["--flag=" + f for f in good]])
        run.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())[0]["flags"], good)

    def test_candidate_profiles_preserve_version_sha_but_no_match_claim(self):
        report = self.report()
        profiles = ht.generate_profiles(report)
        self.assertEqual(profiles[0]["schema"], ht.MATRIX_PROFILE_SCHEMA)
        self.assertEqual(profiles[0]["compiler_executable_sha256"], self.pin)
        self.assertEqual(profiles[0]["version_sha256"], ht.digest(fixture_queries()["version"]["stdout"].encode()))
        self.assertFalse(profiles[0]["PROFILE_REPRODUCIBLE"])
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            self.assertEqual(ht.validate_profile(profiles[0], trusted_sha256=self.pin)["status"], "GCC_4_4_3_CANDIDATE_AVAILABLE")

    def test_profile_sha_change_rejected_even_when_version_text_unchanged(self):
        profile = ht.generate_profiles(self.report(), count=1)[0]
        self.compiler.write_bytes(b"different compiler with same alleged version")
        with mock.patch.object(ht.subprocess, "run") as run:
            with self.assertRaisesRegex(ht.ToolchainError, "SHA-256"):
                ht.validate_profile(profile, trusted_sha256=self.pin)
            run.assert_not_called()

    def test_changed_specs_rejected(self):
        profile = ht.generate_profiles(self.report(), count=1)[0]
        def changed(cmd, **kwargs):
            if cmd[-1] == "-dumpspecs":
                return subprocess.CompletedProcess(cmd, 0, stdout=b"changed specs", stderr=b"")
            return fake_query(cmd, **kwargs)
        with mock.patch.object(ht.subprocess, "run", side_effect=changed):
            with self.assertRaisesRegex(ht.ToolchainError, "changed"):
                ht.validate_profile(profile, trusted_sha256=self.pin)

    def test_revalidation_needs_independent_pins_and_detects_binutils_changes(self):
        linker = self.root / "ld"
        linker.write_bytes(b"inert source-owned linker fixture")
        linker.chmod(0o700)
        pin = ht.file_sha256(linker)
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            report = ht.fingerprint(self.compiler, trusted_sha256=self.pin,
                                    tools={"linker": (linker, pin)})
            profile = ht.generate_profiles(report, count=1)[0]
            with self.assertRaisesRegex(ht.ToolchainError, "independent owner"):
                ht.validate_profile(profile, trusted_sha256=self.pin)
            ht.validate_profile(profile, trusted_sha256=self.pin, trusted_tool_sha256=(pin,))
            with self.assertRaisesRegex(ht.ToolchainError, "independent owner"):
                ht.validate_profile(profile, trusted_sha256="0" * 64, trusted_tool_sha256=(pin,))
            linker.write_bytes(b"changed linker; banner may still match")
            with self.assertRaisesRegex(ht.ToolchainError, "SHA-256"):
                ht.validate_profile(profile, trusted_sha256=self.pin, trusted_tool_sha256=(pin,))

    def test_malformed_query_fields_cannot_establish_a_candidate(self):
        for value in (None, [], "GCC 4.4.3"):
            self.assertEqual(ht.assess_metadata(value), "INCOMPLETE_COMPILER_METADATA")
        for value in (None, 443, {}):
            q = fixture_queries()
            q["gcc_version"]["stdout"] = value
            self.assertEqual(ht.assess_metadata(q), "INCOMPLETE_COMPILER_METADATA")

    def test_symlink_retargeting_and_change_during_query_fail_pins(self):
        alias = self.root / "compiler-alias"
        alias.symlink_to(self.compiler)
        self.assertEqual(ht.file_sha256(alias), self.pin)
        def changed(cmd, **kwargs):
            self.compiler.write_bytes(b"changed during query")
            return fake_query(cmd, **kwargs)
        with mock.patch.object(ht.subprocess, "run", side_effect=changed):
            with self.assertRaisesRegex(ht.ToolchainError, "SHA-256"):
                ht.fingerprint(alias, trusted_sha256=self.pin)

    def test_malformed_profile_and_forged_version_pin(self):
        for profile in (None, [], {}, ht.generate_profiles(None, count=1)[0]):
            with self.assertRaises(ht.ToolchainError):
                ht.validate_profile(profile, trusted_sha256=self.pin)
        profile = ht.generate_profiles(self.report(), count=1)[0]
        profile["compiler"] = "/different/compiler"
        with self.assertRaises(ht.ToolchainError):
            ht.validate_profile(profile, trusted_sha256=self.pin)
        profile = ht.generate_profiles(self.report(), count=1)[0]
        profile["version_sha256"] = "0" * 64
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            with self.assertRaisesRegex(ht.ToolchainError, "changed"):
                ht.validate_profile(profile, trusted_sha256=self.pin)

    def test_cli_malformed_json_and_partial_tool_options_fail_closed(self):
        bad = self.root / "bad.json"
        for text in ("{", "[]", '{"schema":"wrong"}'):
            bad.write_text(text)
            with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(ht.subprocess, "run") as run:
                self.assertEqual(ht.main(["profiles", "--fingerprint", str(bad)]), 2)
                run.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(ht.subprocess, "run") as run:
            self.assertEqual(ht.main(["fingerprint", str(self.compiler), "--linker", str(self.compiler)]), 2)
            run.assert_not_called()

    def test_arm_attributes_never_claimed_without_an_object(self):
        from nova3_team.tests.test_object_compare import elf32
        obj = self.root / "fixture.o"
        obj.write_bytes(elf32(relocatable=True))
        with mock.patch.object(ht.subprocess, "run", side_effect=fake_query):
            with self.assertRaisesRegex(ht.ToolchainError, "pinned readelf"):
                ht.fingerprint(self.compiler, trusted_sha256=self.pin, arm_object=obj)
        def query(cmd, **kwargs):
            if "-A" in cmd:
                return subprocess.CompletedProcess(cmd, 0, b"Tag_CPU_arch: v7\n", b"")
            return fake_query(cmd, **kwargs)
        with mock.patch.object(ht.subprocess, "run", side_effect=query):
            report = ht.fingerprint(self.compiler, trusted_sha256=self.pin, arm_object=obj,
                                    tools={"readelf": (self.compiler, self.pin)})
        self.assertEqual(report["arm_attributes"]["status"], "OBSERVED_CANDIDATE_ONLY")
        self.assertEqual(report["arm_attributes"]["object_sha256"], ht.file_sha256(obj))


if __name__ == "__main__":
    unittest.main()
