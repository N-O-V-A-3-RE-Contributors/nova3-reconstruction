"""Source-only metadata fixtures; never execute a real/fake compiler or game code."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import nova3_easy as easy

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

    def test_profile_schema_still_matches_matrix_contract(self):
        from nova3_team.object_matching import SCHEMA
        self.assertEqual(ht.MATRIX_PROFILE_SCHEMA, SCHEMA)

    def test_xdg_user_install_discovered_without_executing(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / 'data')}, clear=True):
            ndk = ht.user_local_root()
            compiler = ndk / 'toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin' / self.compiler.name
            compiler.parent.mkdir(parents=True)
            compiler.write_text('inert source-owned fixture')
            with mock.patch.object(ht.subprocess, 'run') as run:
                report = ht.onboarding()
            run.assert_not_called()
            self.assertIn(str(ndk), report['discovery']['roots'])
            self.assertEqual(report['status'], 'AVAILABLE_BUT_UNTRUSTED')
            self.assertIn('/opt/android-ndk-r8e', report['discovery']['roots'])
        with mock.patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}):
            self.assertEqual(ht.user_local_root(), Path.home() / '.local/share/nova3-team/toolchains/android-ndk-r8e')

    def test_custom_environment_root_retains_precedence(self):
        with mock.patch.dict(os.environ, {'NOVA3_HISTORICAL_TOOLCHAIN_ROOT': str(self.root)}):
            report = ht.onboarding()
        self.assertEqual(report['discovery']['roots'][0], str(self.root))
        self.assertEqual(report['status'], 'AVAILABLE_BUT_UNTRUSTED')

    def test_onboarding_missing_and_hash_only_never_executes(self):
        with mock.patch.object(ht.subprocess, 'run') as run:
            missing = ht.onboarding(roots=[self.root / 'absent'])
            untrusted = ht.onboarding(roots=[self.root], compiler=self.compiler)
        run.assert_not_called()
        self.assertEqual(missing['status'], 'MISSING')
        self.assertTrue(missing['warnings'])
        self.assertEqual(untrusted['status'], 'AVAILABLE_BUT_UNTRUSTED')
        self.assertEqual(untrusted['source_matching'], 'NOT_PROVEN')

    def test_explicit_onboarding_pin_verifies_only_metadata(self):
        with mock.patch.object(ht, 'supported_host', return_value=True), mock.patch.object(ht.subprocess, 'run', side_effect=fake_query):
            report = ht.onboarding(roots=[self.root], compiler=self.compiler, trusted_sha256=self.pin)
        self.assertEqual(report['status'], 'PINNED_VERSION_TARGET_VERIFIED')
        self.assertEqual(report['match_to_original'], 'NOT_MATCHED')
        self.assertFalse(report['fingerprint']['EXACT_ORIGINAL_COMPILER_PROVEN'])

    def test_onboarding_mismatch_and_host_dependency_failure(self):
        def wrong(cmd, **kwargs):
            if cmd[-1] == '-dumpmachine':
                return subprocess.CompletedProcess(cmd, 0, b'x86_64-linux-gnu\n', b'')
            return fake_query(cmd, **kwargs)
        with mock.patch.object(ht, 'supported_host', return_value=True):
            with mock.patch.object(ht.subprocess, 'run', side_effect=wrong):
                report = ht.onboarding(roots=[self.root], compiler=self.compiler, trusted_sha256=self.pin)
            self.assertEqual(report['status'], 'COMPILER_REJECTED')
            self.assertIn('CONTRADICTORY_TARGET_TRIPLE', report['warnings'][0])
            for exc in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired([], 10)):
                with mock.patch.object(ht.subprocess, 'run', side_effect=exc):
                    report = ht.onboarding(roots=[self.root], compiler=self.compiler, trusted_sha256=self.pin)
                self.assertEqual(report['status'], 'COMPILER_UNUSABLE')
                self.assertIn('Arch', report['warnings'][0])

    def test_unsupported_hosts_cannot_execute_linux_candidate(self):
        for system, machine, expected in (('Linux', 'x86_64', True), ('Linux', 'AMD64', True),
                                          ('Darwin', 'x86_64', False), ('Linux', 'aarch64', False),
                                          ('Windows', 'AMD64', False), ('Linux', 'i686', False)):
            with mock.patch.object(ht.platform, 'system', return_value=system), mock.patch.object(ht.platform, 'machine', return_value=machine):
                self.assertEqual(ht.supported_host(), expected)
                if not expected:
                    with mock.patch.object(ht.subprocess, 'run') as run:
                        report = ht.onboarding(roots=[self.root], compiler=self.compiler, trusted_sha256=self.pin)
                    self.assertEqual(report['status'], 'UNSUPPORTED_HOST')
                    run.assert_not_called()

    def test_source_candidate_identity_checked_before_compiler_execution(self):
        source = self.root / 'candidate.cpp'
        source.write_text('int source_owned_candidate() { return 42; }\n')
        pin = ht.file_sha256(source)
        report = ht.onboarding(roots=[self.root], source=source, source_sha256=pin)
        self.assertEqual(report['source_candidate'], {'sha256': pin, 'status': 'HASH_CHECKED_ONLY'})
        source.write_text('changed candidate')
        with mock.patch.object(ht.subprocess, 'run') as run:
            with self.assertRaisesRegex(ht.ToolchainError, 'source candidate'):
                ht.onboarding(compiler=self.compiler, trusted_sha256=self.pin, source=source, source_sha256=pin)
            run.assert_not_called()
        with self.assertRaises(ht.ToolchainError):
            ht.onboarding(source=source)

    def test_compressed_archive_checks_size_signature_md5_and_records_sha(self):
        # A tiny inert signature fixture exercises IO/digests, not real bzip2/NDK.
        payload = b'BZh' + b'source-owned archive signature fixture'
        archive = self.root / 'archive.tar.bz2'
        archive.write_bytes(payload)
        with mock.patch.object(ht, 'ARCHIVE_SIZE', len(payload)), mock.patch.object(ht, 'ARCHIVE_MD5', ht.hashlib.md5(payload).hexdigest()):
            report = ht.verify_archive(archive)
            self.assertEqual(report['sha256'], ht.digest(payload))
            self.assertFalse(report['modern_signature_verified'])
            self.assertEqual(report['source_matching'], 'NOT_PROVEN')
            with mock.patch.object(ht, 'ARCHIVE_MD5', '0' * 32):
                with self.assertRaisesRegex(ht.ToolchainError, 'MD5 mismatch'):
                    ht.verify_archive(archive)
            archive.write_bytes(b'tar' + payload[3:])
            with self.assertRaisesRegex(ht.ToolchainError, 'bzip2'):
                ht.verify_archive(archive)
        with self.assertRaises(ht.ToolchainError):
            ht.verify_archive(archive)
        with self.assertRaises(ht.ToolchainError):
            ht.verify_archive(self.root)

    def test_cli_onboarding_is_offline_and_errors_are_observable(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(ht.subprocess, 'run') as run:
            self.assertEqual(ht.main(['onboard', '--root', str(self.root / 'missing')]), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['status'], 'MISSING')
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(ht.subprocess, 'run') as run:
            self.assertEqual(ht.main(['onboard', '--instructions']), 0)
        run.assert_not_called()
        for value in (ht.ARCHIVE_URL, ht.ARCHIVE_MD5, str(ht.ARCHIVE_SIZE), 'NOT PROVEN', '4.4.3'):
            self.assertIn(value, out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(ht.main(['onboard', '--trusted-sha256', self.pin]), 2)
            self.assertEqual(ht.main(['verify-archive', str(self.compiler)]), 2)
        self.assertIn('exact compressed byte count', err.getvalue())
        with mock.patch.object(ht, 'supported_host', return_value=True), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ht.main(['onboard', '--root', str(self.root), '--compiler', str(self.compiler), '--trusted-sha256', '0' * 64]), 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'COMPILER_UNUSABLE')

    def test_launcher_skip_decline_noninteractive_and_seen_do_not_install(self):
        for choice, interactive in (('skip', True), (None, True), (None, False)):
            cfg = {}
            with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(easy.sys.stdin, 'isatty', return_value=interactive), mock.patch.object(easy, 'ask', return_value=False) as ask, mock.patch.object(ht.subprocess, 'run') as run:
                easy.historical_onboarding(cfg, choice)
            run.assert_not_called()
            if choice == 'skip' or not interactive:
                ask.assert_not_called()
            self.assertTrue(cfg['historical_onboarding_seen'])
            self.assertIn('skipped', out.getvalue())
        with mock.patch.object(ht, 'onboarding') as check:
            easy.historical_onboarding({'historical_onboarding_seen': True})
        check.assert_not_called()

    def test_launcher_opt_in_and_cli_instructions_never_install(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(easy, 'ask', return_value=True), mock.patch.object(easy.sys.stdin, 'isatty', return_value=True), mock.patch.object(easy, 'command') as command:
            easy.historical_onboarding({})
            self.assertEqual(easy.main(['--historical-toolchain', 'instructions']), 0)
        command.assert_not_called()
        self.assertIn(ht.ARCHIVE_URL, out.getvalue())

    def test_first_worker_run_offers_onboarding_and_still_launches_when_declined(self):
        cfg = {'repo': 'X/Y', 'provider': 'codex'}
        with contextlib.ExitStack() as stack:
            for name, result in (('load_config', cfg), ('ensure_dependencies', None),
                                 ('github_login', 'gh'), ('resolve_repo', 'X/Y'),
                                 ('workdir', self.root), ('prepare_checkout', self.root),
                                 ('local_identity', None), ('select_provider', 'codex'),
                                 ('exe', '/fixture/codex'), ('provider_login', None),
                                 ('save_config', None), ('python_command', ['python3'])):
                stack.enter_context(mock.patch.object(easy, name, return_value=result))
            stack.enter_context(mock.patch.object(easy.sys.stdin, 'isatty', return_value=True))
            ask = stack.enter_context(mock.patch.object(easy, 'ask', return_value=False))
            command = stack.enter_context(mock.patch.object(easy, 'command', return_value=subprocess.CompletedProcess([], 0)))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(easy.main(['--repo', 'X/Y', '--once']), 0)
        ask.assert_called_once()
        self.assertIn('GCC 4.4.3', ask.call_args.args[0])
        self.assertTrue(cfg['historical_onboarding_seen'])
        self.assertIn('nova3_team.worker', command.call_args.args[0])

    def test_documented_extraction_recipe_is_guarded_with_source_owned_archives(self):
        if not hasattr(tarfile, 'data_filter'):
            self.skipTest('Guarded manual extraction requires updated Python tarfile.data_filter')
        guide = Path(__file__).resolve().parents[2] / 'docs/progress/HISTORICAL_TOOLCHAIN.md'
        recipe = guide.read_text().split("python3 - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        archive = self.root / 'source-owned-fixture.tar.bz2'
        target = self.root / 'user/ndk'

        def extract():
            with mock.patch.dict(os.environ, {'NOVA3_NDK_ARCHIVE': str(archive)}), mock.patch.object(ht, 'verify_archive', return_value={'status': 'SOURCE_OWNED_TEST_FIXTURE'}) as verify, mock.patch.object(ht, 'user_local_root', return_value=target), contextlib.redirect_stdout(io.StringIO()):
                exec(compile(recipe, 'documented-extraction', 'exec'), {})
            verify.assert_called_once_with(archive.resolve())

        def bundle(name, link=None):
            with tarfile.open(archive, 'w:bz2') as stream:
                member = tarfile.TarInfo(name)
                if link is not None:
                    member.type = tarfile.SYMTYPE
                    member.linkname = link
                    stream.addfile(member)
                else:
                    payload = b'inert source-owned fixture'
                    member.size = len(payload)
                    stream.addfile(member, io.BytesIO(payload))

        # A link inside staging but outside the eventual NDK root must fail too.
        for name, link in (('outside/file', None), ('android-ndk-r8e/../outside', None),
                           ('android-ndk-r8e/link', '../outside'),
                           ('android-ndk-r8e/link', '/tmp/outside')):
            bundle(name, link)
            with self.assertRaises(tarfile.FilterError):
                extract()
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob('.r8e-staging-*')), [])
        bundle('android-ndk-r8e/readme.txt')
        extract()
        self.assertEqual((target / 'readme.txt').read_bytes(), b'inert source-owned fixture')
        with self.assertRaisesRegex(SystemExit, 'Existing NDK target'):
            extract()
        self.assertTrue(archive.is_file())


if __name__ == "__main__":
    unittest.main()
