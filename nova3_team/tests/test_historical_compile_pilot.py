"""Offline synthetic fixtures only; no installed NDK or proprietary ELF needed."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import historical_compile_pilot as hp
from nova3_team import historical_toolchain as ht
from nova3_team.tests import test_relocation_probe as fixtures
from nova3_team.tests.test_historical_toolchain import fixture_queries


class HistoricalCompilePilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.gitignore').write_text('/.cache/\n')
        self.ndk = self.root / 'ndk'
        self.ndk.mkdir()
        self.source = self.root / 'candidate.cpp'
        self.source.write_text('// inert, source-owned candidate fixture\n')
        self.sysroot = self.ndk / 'sysroot'
        (self.sysroot / 'usr/include').mkdir(parents=True)
        self.include = self.ndk / 'include'
        self.include.mkdir()
        (self.include / 'fixture.h').write_text('// synthetic header\n')
        self.paths, self.pins = {}, {}
        for name in ('compiler', 'assembler', 'cc1plus'):
            path = self.ndk / name
            path.write_text('inert tool fixture ' + name)
            path.chmod(0o700)
            self.paths[name] = path
            self.pins[name] = ht.file_sha256(path)
        # Same function length as the pilot, with independently synthetic data.
        self.raw = fixtures.RAW + bytes(32)
        self.resolved = fixtures.RESOLVED + bytes(32)
        self.reference = self.root / 'reference'
        self.reference.write_bytes(fixtures.fixture(linked=True, text=self.resolved))
        self.object = fixtures.fixture(text=self.raw)
        for name, value in dict(REPO=self.root, SOURCE=self.source,
                                ADDRESS=fixtures.ADDRESS, SYMBOL='source_fn',
                                REFERENCE_SHA256=ht.file_sha256(self.reference),
                                WINDOW_SHA256=ht.digest(self.resolved)).items():
            patch = mock.patch.object(hp, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.queries = fixture_queries()
        for name in ('assembler', 'cc1plus'):
            self.queries[name]['stdout'] = str(self.paths[name]) + '\n'
        self.processes = []

    def args(self, **overrides):
        args = dict(compiler=self.paths['compiler'], compiler_sha256=self.pins['compiler'],
                    reference=self.reference, source=self.source, out=self.root / '.cache/run',
                    ndk_root=self.ndk, sysroot=self.sysroot, includes=[self.include],
                    tools={k: (self.paths[k], self.pins[k]) for k in ('assembler', 'cc1plus')},
                    count=4, targets=fixtures.TARGETS)
        args.update(overrides)
        return args

    def query(self, cmd, **kwargs):
        # No actual executable fixture is ever run.
        self.assertNotIn('shell', kwargs)
        self.assertNotIn('LD_PRELOAD', kwargs['env'])
        if cmd[0] == str(self.paths['compiler']):
            key = next(k for k, v in ht.QUERIES.items() if cmd[-1] == v)
            value = self.queries[key]['stdout']
        elif cmd[0] == str(self.paths['assembler']):
            self.assertEqual(cmd[1:], ['--version'])
            value = 'GNU assembler configured for arm-linux-androideabi'
        elif cmd[0] == str(self.paths['cc1plus']):
            self.assertEqual(cmd[1:], ['--version'])
            value = 'GNU C++ (GCC) version 4.4.3'
        else:
            self.fail('unexpected executable')
        return subprocess.CompletedProcess(cmd, 0, value.encode(), b'')

    def compile(self, command, directory, env, timeout):
        self.processes.append(command)
        self.assertEqual(command[0], str(self.paths['compiler']))
        self.assertEqual(set(env), {'PATH', 'LC_ALL', 'LANG', 'TMPDIR'})
        self.assertEqual(Path(env['PATH']).parent, self.root / '.cache/run')
        self.assertLessEqual(timeout, 30)
        self.assertNotIn('--target=armv7a-linux-androideabi21', command)
        self.assertIn('-std=c++03', command)
        self.assertIn('-nostdinc', command)
        self.assertIn('-nostdinc++', command)
        self.assertEqual(command[command.index('-c') + 1], str(self.source))
        Path(command[command.index('-o') + 1]).write_bytes(self.object)
        return dict(status='COMPILE_FINISHED', exit_code=0)

    def run_pilot(self, compile=None, **kwargs):
        with mock.patch.object(ht.subprocess, 'run', side_effect=self.query), \
             mock.patch.object(hp, '_compile', side_effect=compile or self.compile):
            return hp.run(**self.args(**kwargs))

    def test_sequential_profiles_private_report_and_no_false_promotion(self):
        report = self.run_pilot()
        self.assertEqual(len(self.processes), 4)
        self.assertEqual(report['status'], 'NOT_MATCHED')
        self.assertEqual(report['source_sha256'], ht.file_sha256(self.source))
        self.assertEqual(report['reference_sha256'], ht.file_sha256(self.reference))
        self.assertEqual(report['compiler_metadata']['queries']['gcc_version']['stdout'], '4.4.3\n')
        for item in report['profiles']:
            self.assertEqual(item['status'], 'COMPARED_NOT_CERTIFIED')
            self.assertEqual(item['comparison']['relocated']['status'], 'RESOLVED_WINDOW_EQUAL')
            self.assertEqual(item['comparison']['linked_elf_comparison'], 'NOT_PERFORMED')
            self.assertFalse(item['comparison']['raw']['source_matching_verified'])
        self.assertEqual(report['claims'], hp._claims())
        self.assertEqual(report['claims']['linked_window_match'], 'NOT_TESTED')
        payload = (self.root / '.cache/run/report.json').read_text()
        self.assertEqual(json.loads(payload), report)
        self.assertNotIn('"bytes":', payload)
        self.assertEqual((self.root / '.cache/run').stat().st_mode & 0o777, 0o700)
        self.assertFalse(any(p.suffix in ('.s', '.so', '.ld') for p in (self.root / '.cache/run').rglob('*')))
        self.assertEqual(self.source.read_text(), '// inert, source-owned candidate fixture\n')
        self.assertEqual(len({cmd[-1] for cmd in self.processes}), 4)
        self.assertEqual(len({cmd[1] for cmd in self.processes}), 1)

    def test_raw_equality_does_not_prove_relocations_or_source(self):
        self.object = fixtures.fixture(text=self.resolved)
        report = self.run_pilot()
        raw = report['profiles'][0]['comparison']['raw']
        self.assertEqual(raw['status'], 'BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS')
        self.assertEqual(report['claims']['source_equivalence'], 'NOT_MATCHED')
        self.assertFalse(report['claims']['whole_so_sha_match'])

    def test_missing_reference_compiler_and_wrong_pins_never_execute(self):
        cases = [dict(reference=self.root / 'missing'), dict(compiler=self.root / 'missing'),
                 dict(compiler_sha256='0' * 64), dict(compiler_sha256='bad'),
                 dict(tools={'assembler': (self.paths['assembler'], '0' * 64),
                             'cc1plus': (self.paths['cc1plus'], self.pins['cc1plus'])}),
                 dict(tools={})]
        for extra in cases:
            with self.subTest(extra=extra), mock.patch.object(ht.subprocess, 'run') as run:
                with self.assertRaises((OSError, ValueError)):
                    hp.run(**self.args(**extra))
                run.assert_not_called()
        self.reference.write_bytes(b'wrong edition')
        with mock.patch.object(ht.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                hp.run(**self.args())
            run.assert_not_called()

    def test_wrong_reference_machine_size_hash_mode_and_mapping(self):
        good = self.reference.read_bytes()
        bad_machine = bytearray(good)
        struct.pack_into('<H', bad_machine, 18, 3)
        cases = [bytes(bad_machine), fixtures.fixture(linked=True, text=bytes(40)),
                 fixtures.fixture(linked=True, text=bytes(44)),
                 fixtures.fixture(linked=True, text=self.resolved, thumb=True)]
        for data in cases:
            self.reference.write_bytes(data)
            with self.subTest(length=len(data)), mock.patch.object(hp, 'REFERENCE_SHA256', ht.digest(data)), \
                 mock.patch.object(ht.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    hp.run(**self.args())
                run.assert_not_called()

    def test_wrong_compiler_version_target_or_helper_is_durable_blocker(self):
        for key, value in [('gcc_version', '4.9'), ('target', 'x86_64-linux-gnu'),
                           ('cc1plus', '/untrusted/cc1plus')]:
            with self.subTest(key=key):
                self.queries = fixture_queries()
                for name in ('assembler', 'cc1plus'):
                    self.queries[name]['stdout'] = str(self.paths[name])
                self.queries[key]['stdout'] = value
                report = self.run_pilot(out=self.root / '.cache' / key)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertEqual(report['profiles'], [])
                self.assertTrue((self.root / '.cache' / key / 'report.json').is_file())
        self.assertEqual(self.processes, [])

    def test_support_tool_version_and_target_contradictions_block_compile(self):
        variants = [('assembler', 'GNU assembler for x86_64-linux-gnu'),
                    ('assembler', 'GNU assembler for arm-linux-androideabihf'),
                    ('assembler', 'unknown arm-linux-androideabi'),
                    ('cc1plus', 'GNU C++ version 4.4.30'),
                    ('cc1plus', 'clang version 4.4.3')]
        for index, (tool, banner) in enumerate(variants):
            original_query = self.query
            def bad_query(cmd, **kwargs):
                if cmd[0] == str(self.paths[tool]):
                    return subprocess.CompletedProcess(cmd, 0, banner.encode(), b'')
                return original_query(cmd, **kwargs)
            with self.subTest(tool=tool, banner=banner), \
                 mock.patch.object(ht.subprocess, 'run', side_effect=bad_query), \
                 mock.patch.object(hp, '_compile') as compile:
                report = hp.run(**self.args(out=self.root / '.cache' / f'support-{index}'))
                self.assertEqual(report['status'], 'BLOCKED')
                compile.assert_not_called()

    def test_failed_builds_are_recorded_without_fake_comparisons(self):
        result = dict(status='COMPILE_FINISHED', exit_code=1)
        report = self.run_pilot(compile=lambda *a: result)
        for item in report['profiles']:
            self.assertEqual(item['status'], 'COMPILE_FAILED')
            self.assertIsNone(item['comparison'])
        self.assertEqual(report['claims']['source_equivalence'], 'NOT_MATCHED')

    def test_timeout_stops_next_profile_and_metadata_timeout_is_saved(self):
        report = self.run_pilot(compile=lambda *a: dict(status='COMPILE_TIMEOUT', exit_code=None))
        self.assertEqual(len(report['profiles']), 1)
        self.assertEqual(report['profiles'][0]['status'], 'COMPILE_TIMEOUT')
        with mock.patch.object(ht.subprocess, 'run', side_effect=subprocess.TimeoutExpired([], 10)):
            report = hp.run(**self.args(out=self.root / '.cache/metadata-timeout'))
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['error_type'], 'TimeoutExpired')

    def test_wrong_candidate_machine_mode_size_and_unsupported_relocations(self):
        bad_machine = bytearray(self.object)
        struct.pack_into('<H', bad_machine, 18, 3)
        with self.assertRaises(ValueError):
            hp.compare(self.reference.read_bytes(), bytes(bad_machine), fixtures.TARGETS)
        for obj, status in [(fixtures.fixture(text=self.raw, thumb=True), 'ARM_THUMB_MODE_MISMATCH'),
                            (fixtures.fixture(), 'FUNCTION_SIZE_MISMATCH')]:
            report = hp.compare(self.reference.read_bytes(), obj, fixtures.TARGETS)
            self.assertEqual(report['raw']['status'], status)
            self.assertEqual(report['relocated']['status'], 'NOT_ATTEMPTED')
        obj = fixtures.fixture(text=self.raw, relocations=[(0, 2, 2)])
        self.assertEqual(hp.compare(self.reference.read_bytes(), obj, {})['relocated']['status'],
                         'UNSUPPORTED_OR_UNRESOLVED')
        report = hp.compare(self.reference.read_bytes(), self.object, {})
        self.assertEqual(report['relocated']['status'], 'UNSUPPORTED_OR_UNRESOLVED')

    def test_profiles_isolate_flags_cover_optimization_and_reject_injection(self):
        flags = hp.profiles(8)
        self.assertEqual(len(flags), 8)
        self.assertEqual([next(f for f in p if f.startswith('-O')) for p in flags[:4]],
                         ['-O2', '-Os', '-O1', '-O3'])
        for value in (3, 9, True, '4'):
            with self.assertRaises(ValueError):
                hp.profiles(value)
        for flag in ('--target=armv7a-linux-androideabi21', '-mthumb', '-mfloat-abi=hard',
                     '-fplugin=payload', '-specs=payload', '@file', '-std=c++17'):
            with self.assertRaises(ValueError):
                ht.validate_flags(flags[0] + [flag])
        with mock.patch.object(ht.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                hp.run(**self.args(timeout=31))
            run.assert_not_called()

    def test_output_include_bounds_and_source_are_strict(self):
        link = self.ndk / 'escape'
        link.symlink_to(self.root)
        for extra in [dict(out=self.root / 'public'), dict(includes=[link]),
                      dict(includes=[]), dict(includes=[self.ndk]),
                      dict(includes=[self.include] * 9), dict(source=self.reference)]:
            with self.subTest(extra=extra), mock.patch.object(ht.subprocess, 'run') as run:
                with self.assertRaises((OSError, ValueError)):
                    hp.run(**self.args(**extra))
                run.assert_not_called()
        self.run_pilot()
        with self.assertRaises(ValueError):
            hp.run(**self.args())  # No overwrite/reuse of output directory.

    def test_header_provenance_bounds_symlinks_and_mutations(self):
        before = hp.header_manifest([self.include])
        (self.include / 'fixture.h').write_text('// changed\n')
        self.assertNotEqual(before, hp.header_manifest([self.include]))
        with mock.patch.object(hp, 'MAX_HEADERS', 0):
            with self.assertRaises(ValueError):
                hp.header_manifest([self.include])
        with mock.patch.object(hp, 'MAX_HEADER_BYTES', 0):
            with self.assertRaises(ValueError):
                hp.header_manifest([self.include])
        (self.include / 'link.h').symlink_to(self.source)
        with self.assertRaises(ValueError):
            hp.header_manifest([self.include])

    def test_missing_system_headers_and_symlink_escape_do_not_run_tools(self):
        headers = self.sysroot / 'usr/include'
        headers.rmdir()
        for linked in (False, True):
            if linked:
                headers.symlink_to(self.root)
            with self.subTest(linked=linked), mock.patch.object(ht.subprocess, 'run') as run:
                with self.assertRaises((OSError, ValueError)):
                    hp.run(**self.args())
                run.assert_not_called()

    def test_offline_runner_has_no_install_network_or_privilege_actions(self):
        with mock.patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
            self.run_pilot()
        script = (Path(__file__).resolve().parents[2] / 'tools/run_historical_pilot.sh').read_text()
        self.assertIn('exec python3 -m nova3_team.historical_compile_pilot "$@"', script)
        for executable in ('sudo', 'curl', 'wget', 'pacman', 'apt-get', 'git', 'gh'):
            self.assertNotRegex(script, r'(?m)^\s*(?:exec\s+)?' + executable + r'\s')

    def test_compiler_or_headers_changing_during_build_blocks_claim(self):
        def changed(*args):
            self.compile(*args)
            self.source.write_text('// changed during compile')
            return dict(status='COMPILE_FINISHED', exit_code=0)
        report = self.run_pilot(compile=changed)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['claims'], hp._claims())
        self.assertEqual(len(report['profiles']), 1)

    def test_subprocess_timeout_kills_group_and_success_saves_logs(self):
        for name, code, timeout, status in [('success', 'print("synthetic")', 5, 'COMPILE_FINISHED'),
                                          ('timeout', 'import time; time.sleep(10)', 1, 'COMPILE_TIMEOUT')]:
            directory = self.root / name
            directory.mkdir()
            report = hp._compile([sys.executable, '-c', code], directory,
                                 {'PATH': os.defpath, 'LC_ALL': 'C'}, timeout)
            self.assertEqual(report['status'], status)
            self.assertEqual(report['stdout_sha256'], ht.file_sha256(directory / 'stdout.log'))
        with mock.patch.object(hp.subprocess, 'Popen') as popen:
            child = popen.return_value.__enter__.return_value
            child.wait.return_value = 0
            child.pid = 12345
            directory = self.root / 'mocked'
            directory.mkdir()
            with mock.patch.object(hp.os, 'killpg') as kill:
                hp._compile(['/pinned/compiler', '-c', 'fixture.cpp'], directory, {}, 3)
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertNotIn('shell', popen.call_args.kwargs)
            kill.assert_called_once_with(12345, hp.signal.SIGKILL)

    def test_cli_sanitizes_errors_and_forbids_extra_flags_and_duplicate_targets(self):
        args = []
        for key in ('compiler', 'reference', 'source', 'out', 'ndk_root', 'sysroot'):
            args += ['--' + key.replace('_', '-'), str(self.args()[key])]
        args += ['--include', str(self.include)]
        for key in ('assembler', 'cc1plus'):
            args += ['--' + key, str(self.paths[key])]
        for key in ('compiler', 'assembler', 'cc1plus'):
            args += ['--' + key + '-sha256', self.pins[key]]
        with mock.patch.object(ht.subprocess, 'run', side_effect=self.query), \
             mock.patch.object(hp, '_compile', side_effect=self.compile), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(hp.main(args + ['--profiles', '4']), 0)
        self.assertIn('NOT MATCHED', output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(hp.main(args + ['--target', 's=1', '--target', 's=2']), 2)
        self.assertNotIn(str(self.root), error.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            hp.main(args + ['--flag=-fplugin=payload'])


if __name__ == '__main__':
    unittest.main()
