"""Synthetic ARM metadata only; no historical compiler or game bytes required."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from nova3_team import gcc_layout_diagnostics as gd
from nova3_team import historical_compile_pilot as hp
from nova3_team.object_compare import compare_window
from nova3_team.relocation_probe import ArmElf

ADDRESS = 0x4000
SOURCE = b'// synthetic source pin, never compiled\n'
SYMBOL = 'source_fn'
VERSION = 'arm-linux-androideabi-g++ (GCC) 4.4.3\n'


def fixture(*, linked=False, size=None, code_size=None, thumb=False,
            relocations=None, mappings=True, unknown_name=False):
    size = (44 if linked else 52) if size is None else size
    code_size = (36 if linked else 40) if code_size is None else code_size
    # Inert synthetic contents: mapping symbols alone define the code/data split.
    text = b'\x00' * size
    symbols = bytearray(16)
    strings = bytearray(b'\0')

    def symbol(name, value, length, info, section, visibility=0):
        offset = len(strings)
        strings.extend(name.encode() + b'\0')
        index = len(symbols) // 16
        symbols.extend(struct.pack('<IIIBBH', offset, value, length, info, visibility, section))
        return index

    base = ADDRESS if linked else 0
    symbol(SYMBOL, base | int(thumb), size, 0x12, 1)
    if mappings:
        symbol('$a', base, 0, 0, 1)
        symbol('$d', base + code_size, 0, 0, 1)
    ids = {}
    for n, name in enumerate(('pthread_once', 'g_once', 'android_cpuInit', 'g_cpuCount')):
        local = linked and name != 'pthread_once'
        ids[name] = symbol(name, 0x8000 + n * 4 if local else 0, 0,
                           0 if local else 0x10, 1 if local else 0)
    if unknown_name:
        ids['g_once'] = symbol('PRIVATE_SYMBOL_DO_NOT_EMIT', 0, 0, 0x10, 0)
    desc = [('.text', 1, 6, base, text, 0, 0, 4, 0),
            ('.strtab', 3, 0, 0, bytes(strings), 0, 0, 1, 0),
            ('.symtab', 2, 0, 0, bytes(symbols), 2, 1, 4, 16)]
    if not linked:
        if relocations is None:
            relocations = [(12, ids['pthread_once'], 27), (40, ids['g_once'], 96),
                           (44, ids['android_cpuInit'], 96), (48, ids['g_cpuCount'], 96)]
        content = b''.join(struct.pack('<II', offset, index << 8 | kind)
                           for offset, index, kind in relocations)
        desc.append(('.rel.text', 9, 0, 0, content, 3, 1, 4, 8))
    names = b'\0' + b''.join(d[0].encode() + b'\0' for d in desc) + b'.shstrtab\0'
    desc.append(('.shstrtab', 3, 0, 0, names, 0, 0, 1, 0))
    buf = bytearray(84 if linked else 52)
    sections = [tuple([0] * 10)]
    for name, kind, flags, va, content, link, info, align, entsize in desc:
        buf.extend(bytes(-len(buf) % align))
        offset = len(buf)
        buf.extend(content)
        sections.append((names.index(name.encode()), kind, flags, va, offset,
                         len(content), link, info, align, entsize))
    buf.extend(bytes(-len(buf) % 4))
    shoff = len(buf)
    for section in sections:
        buf.extend(struct.pack('<10I', *section))
    buf[:52] = b'\x7fELF\x01\x01\x01' + bytes(9) + struct.pack(
        '<HHIIIIIHHHHHH', 3 if linked else 1, 40, 1, 0, 52 if linked else 0,
        shoff, 0x05000000, 52, 32 if linked else 0, int(linked), 40,
        len(sections), len(sections) - 1)
    if linked:
        buf[52:84] = struct.pack('<8I', 1, sections[1][4], ADDRESS, ADDRESS, size, size, 5, 4)
    return bytes(buf)


class GccLayoutDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.gitignore').write_text('/.cache/\n')
        self.reference = fixture(linked=True)
        self.obj = fixture()
        for module, values in ((hp, dict(REPO=self.root, ADDRESS=ADDRESS,
                                        NOARG_SYMBOL=SYMBOL,
                                        REFERENCE_SHA256=gd.digest(self.reference),
                                        WINDOW_SHA256=gd.digest(bytes(44)))),
                               (gd, dict(SOURCE_SHA256=gd.digest(SOURCE)))):
            for name, value in values.items():
                patch = mock.patch.object(module, name, value)
                patch.start()
                self.addCleanup(patch.stop)
        self.report = self.prior()

    def prior(self, obj=None):
        obj = self.obj if obj is None else obj
        profiles = []
        for n, flags in enumerate(hp.profiles(4)):
            profiles.append(dict(index=n, symbol=SYMBOL, status='COMPARED_NOT_CERTIFIED',
                                 flags=hp.gcc443_flags(flags), requested_flags=flags,
                                 command=['/pinned/compiler', '-B/pinned/helpers/', '--sysroot=/sysroot',
                                          '-nostdinc', '-nostdinc++', '-x', 'c++', '-pedantic-errors',
                                          *hp.gcc443_flags(flags), '-isystem', '/pinned/include',
                                          '-isystem', '/sysroot/usr/include',
                                          '-c', '/PRIVATE/DO_NOT_FOLLOW', '-o', '/pinned/candidate.o'],
                                 compile=dict(status='COMPILE_FINISHED', exit_code=0),
                                 source_sha256=gd.digest(SOURCE), reference_sha256=hp.REFERENCE_SHA256,
                                 compiler_sha256='1' * 64, compiler_version_sha256=gd.digest(VERSION.encode()),
                                 object_sha256=gd.digest(obj), comparison=dict(raw=compare_window(
                                     self.reference, obj, address=ADDRESS, symbol=SYMBOL))))
        return dict(schema=hp.SCHEMA, status='NOT_MATCHED', profiles=profiles,
                    candidate=dict(candidate='gcc443_noarg', symbol=SYMBOL, compatible=True,
                                   abi='NO_ARGUMENT_GLOBAL_STATE_HYPOTHESIS'),
                    reference_sha256=hp.REFERENCE_SHA256, reference_address=ADDRESS,
                    reference_size=44, reference_window_sha256=hp.WINDOW_SHA256,
                    reference_thumb=False, source_sha256=gd.digest(SOURCE),
                    tool_pins=dict(compiler=dict(sha256='1' * 64, path='/pinned/compiler')),
                    headers=dict(trees=[dict(path='/sysroot/usr/include', sha256='3' * 64),
                                        dict(path='/pinned/include', sha256='4' * 64)]),
                    compiler_metadata=dict(compiler_executable_sha256='1' * 64,
                                           queries={name: dict(stdout=value, exit_code=0,
                                                               stdout_sha256=gd.digest(value.encode()))
                                                    for name, value in (
                                                        ('version', VERSION),
                                                        ('target', 'arm-linux-androideabi\n'),
                                                        ('gcc_version', '4.4.3\n'))}),
                    source='/PRIVATE/DO_NOT_FOLLOW', extra_private_field='DO_NOT_COPY')

    def diagnose(self, *, reference=None, obj=None, source=SOURCE, report=None,
                 index=0, prior_hash=None):
        prior = json.dumps(self.report if report is None else report).encode()
        return gd.diagnose(self.reference if reference is None else reference,
                           self.obj if obj is None else obj, source, prior,
                           prior_sha256=gd.digest(prior) if prior_hash is None else prior_hash,
                           profile_index=index)

    def test_52_versus_44_got_plt_local_binding_and_literal_extents(self):
        result = self.diagnose()
        self.assertEqual(result['size_delta'], 8)
        self.assertEqual(result['mapped_extent_delta'],
                         dict(a32_code_bytes=4, mapped_data_bytes=4))
        self.assertEqual(result['candidate']['mapped_extents'],
                         dict(a32_code_bytes=40, mapped_data_bytes=12))
        self.assertEqual(result['reference']['mapped_extents'],
                         dict(a32_code_bytes=36, mapped_data_bytes=8))
        self.assertEqual(result['relocations']['type_counts'],
                         dict(R_ARM_GOT_PREL=3, R_ARM_PLT32=1))
        self.assertEqual(result['relocations']['site_count'], 4)
        self.assertEqual(result['relocations']['unresolved_external_symbol_count'], 4)
        self.assertEqual(result['candidate']['dependency_bindings']['initializer'],
                         dict(binding='GLOBAL', visibility='DEFAULT', defined=False))
        self.assertEqual(result['reference']['dependency_bindings']['initializer'],
                         dict(binding='LOCAL', visibility='DEFAULT', defined=True))
        self.assertIn('PIC_BINDING_HYPOTHESIS_REQUIRES_CONTROLLED_LINK', result['difference_categories'])
        self.assertIn('RELOCATIONS_OUTSIDE_EXISTING_RESOLVER', result['difference_categories'])
        self.assertEqual(result['root_cause'], 'UNKNOWN')
        self.assertEqual(result['flags'], self.report['profiles'][0]['flags'])
        self.assertEqual(result['claims']['source_equivalence'], 'NOT_MATCHED')
        self.assertEqual(result['claims']['linked_window_match'], 'NOT_TESTED')
        self.assertFalse(result['claims']['abi_verified'])

    def test_equal_window_never_promotes_claims_or_resolves_externals(self):
        obj = fixture(size=44, code_size=36, relocations=[(12, 4, 28)])
        result = self.diagnose(obj=obj, report=self.prior(obj))
        self.assertEqual(result['size_delta'], 0)
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY_NOT_MATCHED')
        self.assertEqual(result['relocations']['resolution'], 'NOT_ATTEMPTED')
        self.assertEqual(result['linked_stage'], 'NOT_PERFORMED')
        self.assertEqual(result['original_prelink_relocations'], 'UNKNOWN')
        self.assertFalse(result['claims']['behavior_verified'])
        self.assertFalse(result['claims']['whole_so_sha_match'])

    def test_private_symbols_fields_and_binary_data_cannot_escape(self):
        obj = fixture(unknown_name=True)
        result = self.diagnose(obj=obj, report=self.prior(obj))
        serialized = json.dumps(result)
        for forbidden in ('PRIVATE', 'DO_NOT_COPY', 'source_fn', 'command', 'disassembly',
                          'instruction', 'addend', 'section_offset', 'encoded'):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(result['relocations']['dependency_site_counts']['OTHER'], 1)
        def check(value):
            self.assertNotIsInstance(value, (bytes, bytearray))
            if isinstance(value, dict):
                self.assertTrue({'bytes', 'code', 'word', 'assembly', 'path', 'symbol',
                                 'offsets', 'instructions'}.isdisjoint(value))
                for child in value.values():
                    check(child)
            if isinstance(value, list):
                for child in value:
                    check(child)
        check(result)

    def test_hashes_changed_source_object_and_report_rejected(self):
        for kwargs in (dict(reference=b'wrong edition'), dict(obj=b'changed object'),
                       dict(source=SOURCE + b'changed'), dict(prior_hash='0' * 64),
                       dict(prior_hash='bad')):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(ValueError):
                self.diagnose(**kwargs)

    def test_stale_report_schema_profile_flags_comparison_and_pins(self):
        mutations = [lambda r: r.update(schema='old'), lambda r: r.update(status='BLOCKED'),
                     lambda r: r.update(reference_size=52), lambda r: r.update(reference_thumb=0),
                     lambda r: r.update(source_sha256='0' * 64),
                     lambda r: r['candidate'].update(symbol='wrong'),
                     lambda r: r['candidate'].update(abi='PRIVATE_PAYLOAD'),
                     lambda r: r['profiles'][0].update(index=True),
                     lambda r: r['profiles'][0].update(flags=['-fPIC']),
                     lambda r: r['profiles'][0].update(requested_flags=['-O0']),
                     lambda r: r['profiles'][0]['compile'].update(exit_code=False),
                     lambda r: r['profiles'][0]['compile'].update(exit_code=1),
                     lambda r: r['profiles'][0].update(compiler_sha256='0' * 64),
                     lambda r: r['profiles'][0]['command'].insert(8, '-O0'),
                     lambda r: r['profiles'][0]['command'].__setitem__(-3, '/different/source'),
                     lambda r: r['headers']['trees'][0].update(sha256='bad'),
                     lambda r: r['headers']['trees'][0].update(path='/different/include'),
                     lambda r: r['compiler_metadata']['queries']['target'].update(stdout='x86_64\n'),
                     lambda r: r['compiler_metadata']['queries']['gcc_version'].update(exit_code=1),
                     lambda r: r['compiler_metadata']['queries']['version'].update(stdout='stale banner'),
                     lambda r: r['compiler_metadata']['queries']['target'].update(stdout_sha256='0' * 64),
                     lambda r: r['profiles'][0]['comparison']['raw'].update(candidate_size=44),
                     lambda r: r['profiles'][0]['comparison']['raw'].update(code_window_equal=True),
                     lambda r: r['profiles'][0]['comparison']['raw'].pop('candidate_code_sha256')]
        for n, mutate in enumerate(mutations):
            report = copy.deepcopy(self.report)
            mutate(report)
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.diagnose(report=report)

    def test_profile_bounds_duplicate_json_fields_and_report_budget(self):
        for index in (-1, True, 4, 100000, '0'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.diagnose(index=index)
        for data in (b'{"schema":1,"schema":2}', b'[]', b'not-json', b' ' * (gd.MAX_REPORT + 1)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                gd._prior(data, gd.digest(data), 0)

    def test_wrong_machine_eabi_mode_truncation_and_relocation_bounds(self):
        good = self.obj
        cases = []
        for offset, fmt, value in ((18, 'H', 3), (36, 'I', 0x04000000)):
            bad = bytearray(good)
            struct.pack_into('<' + fmt, bad, offset, value)
            cases.append(bytes(bad))
        cases += [fixture(thumb=True), good[:-1], fixture(relocations=[(52, 4, 96)]),
                  fixture(relocations=[(1, 4, 96)]), fixture(relocations=[(0, 99, 96)]),
                  fixture(relocations=[(40, 4, 96), (40, 5, 96)]),
                  fixture(code_size=39)]
        for n, obj in enumerate(cases):
            report = copy.deepcopy(self.report)
            report['profiles'][0]['object_sha256'] = gd.digest(obj)
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.diagnose(obj=obj, report=report)

    def test_reference_machine_mode_window_and_mapping_rejected_with_full_hash_pin(self):
        bad_machine = bytearray(self.reference)
        struct.pack_into('<H', bad_machine, 18, 3)
        bad_window = bytearray(self.reference)
        bad_window[84] = 1
        for ref in (fixture(linked=True, thumb=True), fixture(linked=True, size=48),
                    fixture(linked=True, code_size=35), bytes(bad_machine), bytes(bad_window)):
            report = copy.deepcopy(self.report)
            report['reference_sha256'] = gd.digest(ref)
            report['profiles'][0]['reference_sha256'] = gd.digest(ref)
            with mock.patch.object(hp, 'REFERENCE_SHA256', gd.digest(ref)), self.assertRaises(ValueError):
                self.diagnose(reference=ref, report=report)
        bad = bytearray(self.reference)
        struct.pack_into('<I', bad, 56, 80)  # PT_LOAD and section no longer agree.
        report = copy.deepcopy(self.report)
        report['reference_sha256'] = gd.digest(bad)
        report['profiles'][0]['reference_sha256'] = gd.digest(bad)
        with mock.patch.object(hp, 'REFERENCE_SHA256', gd.digest(bad)), self.assertRaises(ValueError):
            self.diagnose(reference=bytes(bad), report=report)

    def test_missing_mapping_symbols_stay_unknown(self):
        obj = fixture(mappings=False)
        result = self.diagnose(obj=obj, report=self.prior(obj))
        self.assertIsNone(result['candidate']['mapped_extents'])
        self.assertIsNone(result['mapped_extent_delta'])
        self.assertIn('CODE_DATA_SPLIT_UNKNOWN', result['difference_categories'])

    def test_visibility_and_header_bits_are_separate_structural_differences(self):
        obj = bytearray(self.obj)
        table = next(s for s in ArmElf(obj).sections if s['type'] == 2)
        # Fixture symbol 5 is the once-state external; only its visibility changes.
        obj[table['offset'] + 5 * 16 + 13] = 2
        struct.pack_into('<I', obj, 36, 0x05000400)
        obj = bytes(obj)
        result = self.diagnose(obj=obj, report=self.prior(obj))
        self.assertEqual(result['candidate']['dependency_bindings']['once_state']['visibility'], 'HIDDEN')
        self.assertIn('DEPENDENCY_VISIBILITY_DIFFERENCE', result['difference_categories'])
        self.assertIn('FLOAT_ABI_HEADER_DIFFERENCE', result['difference_categories'])
        self.assertEqual(result['root_cause'], 'UNKNOWN')
        self.assertFalse(result['claims']['abi_verified'])

    def test_aggregate_metadata_budgets_apply_before_comparison(self):
        for budget in ('MAX_SYMBOLS', 'MAX_RELOCATIONS'):
            with self.subTest(budget=budget), mock.patch.object(gd, budget, 1):
                with mock.patch.object(gd, 'compare_window') as compare:
                    with self.assertRaisesRegex(ValueError, 'aggregate'):
                        self.diagnose()
                    compare.assert_not_called()

    def test_all_four_profile_selections_preserve_exact_effective_flags(self):
        for index in range(4):
            with self.subTest(index=index):
                result = self.diagnose(index=index)
                self.assertEqual(result['flags'], hp.gcc443_flags(hp.profiles(4)[index]))
                self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY_NOT_MATCHED')

    def test_mapping_conflicts_mixed_modes_and_nonzero_section_start(self):
        obj = bytearray(self.obj)
        elf = ArmElf(obj)
        table = next(s for s in elf.sections if s['type'] == 2)
        # Fixture indices: function=1, A32 marker=2, data marker=3.
        struct.pack_into('<I', obj, table['offset'] + 3 * 16 + 4, 0)
        with self.assertRaisesRegex(ValueError, 'conflicting mapping'):
            gd._layout(ArmElf(bytes(obj)), SYMBOL)
        obj = bytearray(self.obj)
        strings = next(s for s in elf.sections if s['type'] == 3 and s['name'] == '.strtab')
        name = self.obj.index(b'$d\0', strings['offset'])
        obj[name + 1] = ord('t')
        with self.assertRaisesRegex(ValueError, 'mixed'):
            gd._layout(ArmElf(bytes(obj)), SYMBOL)
        obj = bytearray(self.obj)
        obj[table['offset'] + 2 * 16 + 12] = 0x10  # Mapping symbol must be local.
        with self.assertRaisesRegex(ValueError, 'invalid ARM mapping'):
            gd._layout(ArmElf(bytes(obj)), SYMBOL)
        # Existing reusable fixture exercises a function starting inside .text.
        from nova3_team.tests.test_relocation_probe import fixture as old_fixture
        layout = gd._layout(ArmElf(old_fixture(prefix=8, text=bytes(44))), SYMBOL)
        self.assertEqual(layout['size'], 44)
        self.assertEqual(layout['mapping_status'], 'UNKNOWN')

    def test_unsupported_relocations_are_inventory_only_and_never_masked(self):
        obj = fixture(relocations=[(40, 4, 255)])
        result = self.diagnose(obj=obj, report=self.prior(obj))
        self.assertEqual(result['relocations']['type_counts'], {'OTHER': 1})
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY_NOT_MATCHED')
        self.assertIn('RELOCATIONS_OUTSIDE_EXISTING_RESOLVER', result['difference_categories'])
        self.assertEqual(result['relocations']['resolution'], 'NOT_ATTEMPTED')

    def paths(self):
        paths = {}
        for name, data in (('reference', self.reference), ('object', self.obj),
                           ('source', SOURCE), ('prior-report', json.dumps(self.report).encode())):
            path = self.root / name
            path.write_bytes(data)
            paths[name] = path
        return paths

    def test_cli_private_output_no_execution_and_sanitized_failure(self):
        paths = self.paths()
        out = self.root / '.cache/layout'
        args = [token for name, path in paths.items() for token in ('--' + name, str(path))]
        args += ['--prior-report-sha256', gd.digest(paths['prior-report'].read_bytes()),
                 '--profile-index', '0', '--out', str(out)]
        with mock.patch('subprocess.Popen') as execute, contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(gd.main(args), 0)
        execute.assert_not_called()
        self.assertEqual(stdout.getvalue(), 'DIAGNOSTIC_ONLY_NOT_MATCHED; private structural report retained\n')
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        self.assertEqual((out / 'report.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in out.iterdir()], ['report.json'])
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(gd.main(args), 2)  # No overwriting an earlier run.
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_output_paths_symlinks_and_special_inputs_rejected_without_writes(self):
        paths = self.paths()
        kwargs = dict(reference=paths['reference'], obj=paths['object'], source=paths['source'],
                      prior_report=paths['prior-report'],
                      prior_sha256=gd.digest(paths['prior-report'].read_bytes()), profile_index=0)
        for out in (self.root / 'public', self.root / '.cache', self.root / '..' / 'outside'):
            with self.subTest(out=out.name), self.assertRaises(ValueError):
                gd.run(**kwargs, out=out)
        alias = self.root / 'alias'
        alias.symlink_to(paths['object'])
        with self.assertRaises(ValueError):
            gd.run(**dict(kwargs, obj=alias), out=self.root / '.cache/symlink')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            gd.run(**dict(kwargs, obj=fifo), out=self.root / '.cache/fifo')
        with mock.patch.object(gd, 'MAX_REPORT', 10), self.assertRaises(ValueError):
            gd.run(**kwargs, out=self.root / '.cache/oversized')
        with paths['object'].open('wb') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            gd.run(**kwargs, out=self.root / '.cache/oversized-object')
        self.assertFalse((self.root / '.cache').exists())


if __name__ == '__main__':
    unittest.main()
