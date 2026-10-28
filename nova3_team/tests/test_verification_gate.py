"""Synthetic public-safe fixtures only; no original ELF, compiler or network."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import verification_gate as gate

REF = 'a' * 64
WINDOW = 'b' * 64
OBJECT = 'c' * 64
OTHER = 'd' * 64
SOURCE = 'reconstruction/recovered/libFixture/block_00004000.cpp'
MANIFEST = 'coordination/evidence/gate-fixture.json'


def comparison(equal, good, bad, expected=WINDOW, actual=None):
    return dict(status=good if equal else bad, equal=equal, compared_bytes=44,
                differing_byte_count=0 if equal else 4, expected_sha256=expected,
                actual_sha256=(expected if equal else OTHER) if actual is None else actual)


def probe():
    # Field names/shape follow relocation_probe.probe; private relocation
    # worksheets, paths and differing-offset lists are deliberately omitted.
    return dict(schema=gate.PROBE_SCHEMA, address=0x4000, size=44,
                reference_sha256=REF, reference_window_sha256=WINDOW,
                candidate_object_sha256=OBJECT, candidate_symbol='fixture_fn',
                candidate_thumb=False,
                raw=comparison(False, 'RAW_EQUAL', 'RAW_MISMATCH'),
                resolved=comparison(True, 'RESOLVED_WINDOW_EQUAL', 'RESOLVED_WINDOW_MISMATCH'),
                original_object_match='NOT_AVAILABLE', historical_compiler_verified=False,
                whole_so_sha_match=False, behavior_verified=False,
                original_prelink_relocations_recovered=False,
                relocated_linked_window='RELOCATED_LINKED_WINDOW_MATCH', linked_sha256=OTHER,
                linker_vs_resolved=comparison(True, 'LINKER_AGREES', 'LINKER_DISAGREES'),
                linked_vs_reference=comparison(True, 'RELOCATED_LINKED_WINDOW_MATCH',
                                               'RELOCATED_LINKED_WINDOW_MISMATCH'))


def object_report():
    return dict(schema=gate.OBJECT_SCHEMA, kind='source-candidate-to-linked-window',
                reference_sha256=REF, reference_address=0x4000, reference_size=44,
                reference_window_sha256=WINDOW, candidate_object_sha256=OBJECT,
                candidate_symbol='fixture_fn', candidate_code_sha256=OTHER,
                candidate_size=44, candidate_thumb=False, original_thumb=False,
                candidate_relocation_count=0, code_window_equal=False,
                status='FUNCTION_BYTES_DIFFERENT', original_object_recovered=False,
                original_relocations_recovered=False, original_compiler_verified=False,
                original_so_hash_matching_verified=False, source_matching_verified=False)


class VerificationGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nova-gate-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / SOURCE
        self.source.parent.mkdir(parents=True)
        self.source.write_text('int fixture_fn(int x) { return x + 1; }\n')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest = self.root / MANIFEST
        self.manifest.parent.mkdir(parents=True)
        self.claim = dict(source=dict(path=SOURCE, sha256=self.sha), grade='RESEARCH_SOURCE')
        self.doc = dict(schema=gate.SCHEMA, reference_sha256=REF, claims=[self.claim])

    def write(self):
        self.manifest.write_text(json.dumps(self.doc))

    def check(self):
        self.write()
        return gate.check(self.root, MANIFEST, expected_reference_sha256=REF)

    def reject(self, code=None):
        with self.assertRaises(gate.GateError) as caught:
            self.check()
        if code:
            self.assertEqual(str(caught.exception), code)

    def build(self):
        self.claim['build'] = dict(producer_schema=gate.MATRIX_SCHEMA, source_sha256=self.sha,
                                  reference_sha256=REF, compiler_version='synthetic fixture 1.0',
                                  compiler_version_sha256=OTHER, flags=['-O2', '-marm'],
                                  compile_exit_code=0, test_exit_code=0, tests_passed=3,
                                  log_sha256=OTHER, object_sha256=OBJECT)

    def matching(self):
        self.build()
        self.claim.update(grade='RELOCATED_LINKED_WINDOW_MATCH', comparison=probe())

    def test_research_source_is_not_verified(self):
        result = self.check()
        self.assertTrue(result['metadata_valid'])
        self.assertEqual(result['independently_verified_source_matches'], 0)
        self.assertFalse(result['binary_truth_rechecked'])
        self.assertEqual(set(result['claims'][0]['proof_grades'].values()), {'NOT_VERIFIED'})

    def test_unknown_evidence_is_not_verified(self):
        self.claim['grade'] = 'NOT_VERIFIED'
        self.assertEqual(self.check()['claims'][0]['metadata_grade'], 'NOT_VERIFIED')
        self.doc['claims'] = []
        self.reject('invalid_claim_count')

    def test_host_tested_requires_successful_nonempty_test_evidence(self):
        self.claim['grade'] = 'HOST_TESTED_SOURCE'
        self.reject('missing_successful_build')
        self.build()
        self.assertEqual(self.check()['claims'][0]['metadata_grade'], 'HOST_TESTED_SOURCE')
        for field, value in [('compile_exit_code', 1), ('test_exit_code', 1), ('tests_passed', 0)]:
            prior = self.claim['build'][field]
            self.claim['build'][field] = value
            self.reject()
            self.claim['build'][field] = prior

    def test_raw_and_linked_stages_are_distinct_and_no_proof_promoted(self):
        self.matching()
        result = self.check()['claims'][0]
        self.assertEqual(result['reported_comparison_statuses'],
                         ['RAW_MISMATCH', 'RELOCATED_LINKED_WINDOW_MATCH'])
        self.assertEqual(set(result['proof_grades'].values()), {'NOT_VERIFIED'})
        self.claim['grade'] = 'RAW_MISMATCH'
        self.assertTrue(self.check()['metadata_valid'])

    def test_raw_only_probe(self):
        self.matching()
        self.claim['grade'] = 'RAW_MISMATCH'
        report = self.claim['comparison']
        for field in ['linked_sha256', 'linker_vs_resolved', 'linked_vs_reference']:
            del report[field]
        report['relocated_linked_window'] = 'NOT_CHECKED'
        self.assertEqual(self.check()['claims'][0]['reported_comparison_statuses'], ['RAW_MISMATCH'])
        self.claim['grade'] = 'RELOCATED_LINKED_WINDOW_MATCH'
        self.reject('unsupported_grade_promotion')

    def test_object_mismatch_and_equal_window_do_not_establish_source_match(self):
        self.build()
        self.claim.update(grade='FUNCTION_BYTES_DIFFERENT', comparison=object_report())
        self.assertTrue(self.check()['metadata_valid'])
        self.claim['comparison'].update(candidate_code_sha256=WINDOW, code_window_equal=True,
                                        status='LINKED_FUNCTION_WINDOW_EQUAL')
        self.reject('unsupported_grade_promotion')
        self.claim['grade'] = 'RESEARCH_SOURCE'
        self.assertEqual(self.check()['independently_verified_source_matches'], 0)
        self.claim['comparison']['candidate_relocation_count'] = 1
        self.reject('contradictory_comparison')
        self.claim['comparison']['status'] = 'BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS'
        self.assertTrue(self.check()['metadata_valid'])

    def test_proof_grades_are_disallowed_as_public_acceptance_grades(self):
        self.matching()
        for grade in gate.PROOF_GRADES | {'MATCHED_ARM', 'matching', 'VERIFIED', 'typo'}:
            with self.subTest(grade=grade):
                self.claim['grade'] = grade
                self.reject('uncheckable_or_unknown_grade')

    def test_each_explicit_attestation_stays_unrechecked(self):
        records = []
        for grade, fields in gate._ATTESTATION_FIELDS.items():
            record = dict(grade=grade, source_sha256=self.sha, reference_sha256=REF,
                          report_sha256=OTHER, **{key: REF for key in fields})
            records.append(record)
        self.claim['original_local_attestations'] = records
        result = self.check()['claims'][0]
        self.assertEqual(set(result['original_local_attestations_not_rechecked']), gate.PROOF_GRADES)
        self.assertEqual(set(result['proof_grades'].values()), {'NOT_VERIFIED'})
        self.claim['original_local_attestations'].append(deepcopy(records[0]))
        self.reject('invalid_attestations')

    def test_attestations_need_separate_explicit_bound_evidence(self):
        record = dict(grade='SOURCE_BEHAVIOR_VERIFIED', source_sha256=self.sha,
                      reference_sha256=REF, report_sha256=OTHER,
                      oracle_report_sha256=OTHER, test_report_sha256=OTHER)
        self.claim['original_local_attestations'] = [record]
        for field in list(record):
            saved = record.pop(field)
            self.reject()
            record[field] = saved
        record['source_sha256'] = OTHER
        self.reject('attestation_source_mismatch')
        record['source_sha256'] = self.sha
        record['reference_sha256'] = OTHER
        self.reject('reference_identity_mismatch')
        self.claim['original_local_attestations'] = [record, deepcopy(record)]
        record['reference_sha256'] = REF
        self.claim['original_local_attestations'][1]['reference_sha256'] = REF
        self.reject('duplicate_attestation')

    def test_whole_so_attestation_cannot_relabel_window_hash(self):
        self.claim['original_local_attestations'] = [dict(
            grade='WHOLE_SO_HASH_MATCH', source_sha256=self.sha, reference_sha256=REF,
            report_sha256=OTHER, original_so_sha256=WINDOW, candidate_so_sha256=WINDOW)]
        self.reject('false_match_promotion')

    def test_all_comparison_stages_must_agree_internally(self):
        self.matching()
        original = deepcopy(self.claim['comparison'])
        mutations = [
            ('linked_vs_reference', 'equal', False),
            ('linked_vs_reference', 'differing_byte_count', 1),
            ('linked_vs_reference', 'compared_bytes', 40),
            ('linked_vs_reference', 'actual_sha256', OTHER),
            ('linked_vs_reference', 'expected_sha256', OTHER),
            ('linker_vs_resolved', 'expected_sha256', OTHER),
            ('resolved', 'actual_sha256', OTHER),
            ('raw', 'expected_sha256', OTHER),
            ('raw', 'equal', 0),
        ]
        for container, field, value in mutations:
            with self.subTest(container=container, field=field):
                self.claim['comparison'] = deepcopy(original)
                self.claim['comparison'][container][field] = value
                self.reject()
        self.claim['comparison'] = original
        del original['linker_vs_resolved']
        self.reject('missing_linker_stage')

    def test_linker_disagreement_cannot_be_promoted(self):
        self.matching()
        report = self.claim['comparison']
        report['resolved']['actual_sha256'] = OTHER
        report['resolved'].update(equal=False, differing_byte_count=4, status='RESOLVED_WINDOW_MISMATCH')
        report['linker_vs_resolved'] = comparison(False, 'LINKER_AGREES', 'LINKER_DISAGREES',
                                                 expected=OTHER, actual=WINDOW)
        self.reject('false_match_promotion')
        report['relocated_linked_window'] = 'LINKER_DISAGREES'
        self.reject('unsupported_grade_promotion')

    def test_original_identity_is_pinned_at_every_level(self):
        self.matching()
        for container in [self.doc, self.claim['build'], self.claim['comparison']]:
            container['reference_sha256'] = OTHER
            self.reject('reference_identity_mismatch')
            container['reference_sha256'] = REF
        self.write()
        with self.assertRaises(gate.GateError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=OTHER)

    def test_candidate_and_source_identity_bound_to_build(self):
        self.matching()
        self.claim['build']['source_sha256'] = OTHER
        self.reject('build_source_mismatch')
        self.claim['build']['source_sha256'] = self.sha
        self.claim['build']['object_sha256'] = OTHER
        self.reject('build_object_mismatch')

    def test_source_drift_and_missing_source(self):
        self.source.write_text('int fixture_fn() { return 2; }\n')
        self.reject('source_hash_drift')
        self.source.unlink()
        self.write()
        with self.assertRaises(OSError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)

    def test_duplicate_and_conflicting_claims(self):
        self.doc['claims'].append(deepcopy(self.claim))
        self.reject('duplicate_or_conflicting_source')
        self.doc['claims'][1]['grade'] = 'NOT_VERIFIED'
        self.reject('duplicate_or_conflicting_source')

    def test_malformed_hashes_including_short_nonhex_and_wrong_types(self):
        for value in ['a' * 63, 'a' * 65, 'x' * 64, 'a' * 64 + '\n', None, True, 64]:
            self.claim['source']['sha256'] = value
            self.reject('invalid_sha256')
        self.claim['source']['sha256'] = self.sha.upper()
        self.assertTrue(self.check()['metadata_valid'])

    def test_traversal_absolute_noncanonical_and_private_source_paths(self):
        for name in ['../outside.cpp', '/tmp/outside.cpp', 'C:/outside.cpp',
                     SOURCE.replace('/', '//', 1), SOURCE.replace('libFixture/', 'libFixture/../'),
                     SOURCE.replace('/', '\\'), 'originals/block_00004000.cpp',
                     SOURCE + '\x00', './' + SOURCE]:
            self.claim['source']['path'] = name
            self.reject()
        self.write()
        for name in ['../x.json', '/x.json', 'coordination//evidence/gate-fixture.json']:
            with self.assertRaises(gate.GateError):
                gate.check(self.root, name, expected_reference_sha256=REF)

    def test_final_and_parent_symlinks_are_rejected_even_inside_repo(self):
        self.source.unlink()
        target = self.root / 'target.cpp'
        target.write_text('int fixture_fn(int x) { return x + 1; }\n')
        self.source.symlink_to(target)
        self.write()
        with self.assertRaises(OSError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)
        self.source.unlink()
        parent = self.source.parent
        parent.rmdir()
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)
        self.manifest.unlink()
        self.manifest.symlink_to(target)
        with self.assertRaises(OSError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)

    def test_nonregular_and_oversized_inputs(self):
        self.source.unlink()
        os.mkfifo(self.source)
        self.write()
        with self.assertRaises(gate.GateError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)
        self.source.unlink()
        self.source.write_bytes(b'x' * (gate.MAX_FILE + 1))
        self.reject('nonregular_or_oversized_file')
        self.manifest.write_bytes(b' ' * (gate.MAX_FILE + 1))
        with self.assertRaises(gate.GateError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)

    def test_no_synthetic_byte_objects_or_inline_assembly(self):
        for text in ['asm(".byte 1");', '__asm__("nop");', '__as\\\nm__("nop");',
                     '#include "snapshot.s"', 'a ## sm', '.incbin "original.so"']:
            self.source.write_text(text)
            self.claim['source']['sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
            self.reject('synthetic_or_inline_assembly_source')
        self.source.write_text('int fixture_fn() { return 1; }')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.claim['source']['sha256'] = self.sha
        self.matching()
        self.claim['comparison']['candidate_symbol'] = 'nova3_fn_00004000'
        self.reject('synthetic_candidate')

    def test_unknown_fields_or_proof_booleans_cannot_slip_through(self):
        self.matching()
        for container in [self.doc, self.claim, self.claim['source'], self.claim['build'],
                          self.claim['comparison'], self.claim['comparison']['raw']]:
            container['secret_bytes'] = 'DO_NOT_PRINT'
            self.reject('invalid_fields')
            del container['secret_bytes']
        for field in ['historical_compiler_verified', 'whole_so_sha_match', 'behavior_verified']:
            self.claim['comparison'][field] = True
            self.reject('false_match_promotion')
            self.claim['comparison'][field] = False

    def test_strict_json_and_complexity_bounds(self):
        for content in ['{', '[]', 'null', '{"schema":1,"schema":2}',
                        '{"secret":NaN}', '{"secret":Infinity}', '"' + '\\ud800' + '"',
                        '[' * 100 + '0' + ']' * 100]:
            self.manifest.write_text(content)
            with self.assertRaises(gate.GateError):
                gate.check(self.root, MANIFEST, expected_reference_sha256=REF)
        self.manifest.write_bytes(b'\xff')
        with self.assertRaises(gate.GateError):
            gate.check(self.root, MANIFEST, expected_reference_sha256=REF)
        self.doc['claims'] = [self.claim] * (gate.MAX_CLAIMS + 1)
        self.reject('invalid_claim_count')

    def test_compiler_matrix_flag_policy_excludes_assembly_and_injected_inputs(self):
        self.build()
        for flags in [['-x', 'assembler'], ['-include=original.s'], ['@secret.rsp'],
                      ['-Wl,original.o'], ['-O2;false'], ['-O2\nsecret'], [42], 'not-a-list',
                      ['-O2'] * 65]:
            self.claim['build']['flags'] = flags
            self.reject()
        self.claim['build']['flags'] = ['-O2', '-marm', '-std=c++17']
        self.assertTrue(self.check()['metadata_valid'])

    def test_bad_types_ranges_and_missing_required_fields(self):
        self.matching()
        for container, field, values in [
            (self.claim['build'], 'compile_exit_code', [True, -1, 256, '0', None]),
            (self.claim['build'], 'tests_passed', [True, -1, 1000001]),
            (self.claim['comparison'], 'address', [True, -1, 0x100000000, 0x4001]),
            (self.claim['comparison'], 'size', [True, 0, 43, gate.MAX_FILE + 1]),
            (self.claim['comparison'], 'candidate_thumb', [True, 0, None]),
        ]:
            saved = container[field]
            for value in values:
                with self.subTest(field=field, value=value):
                    container[field] = value
                    self.reject()
            container[field] = saved
        for container in [self.doc, self.claim, self.claim['source'], self.claim['build'],
                          self.claim['comparison'], self.claim['comparison']['raw']]:
            for field in list(container):
                saved = container.pop(field)
                if field not in {'build', 'comparison'}:
                    self.reject()
                container[field] = saved

    def test_mode_size_and_unconditional_hash_validation(self):
        self.build()
        self.claim['comparison'] = object_report()
        report = self.claim['comparison']
        report.update(candidate_thumb=True, status='ARM_THUMB_MODE_MISMATCH')
        self.assertTrue(self.check()['metadata_valid'])
        report['candidate_code_sha256'] = 'short'
        self.reject('invalid_sha256')
        report.update(candidate_thumb=False, candidate_code_sha256=OTHER,
                      candidate_size=48, status='FUNCTION_SIZE_MISMATCH')
        self.assertTrue(self.check()['metadata_valid'])
        report.update(reference_address=0x4002, reference_size=42, candidate_size=42,
                      original_thumb=True, candidate_thumb=True, status='FUNCTION_BYTES_DIFFERENT')
        self.assertTrue(self.check()['metadata_valid'])
        report['source_matching_verified'] = True
        self.reject('false_match_promotion')

    def test_missing_files_and_invalid_manifests_have_sanitized_cli_failures(self):
        args = ['--repo', str(self.root), '--manifest', MANIFEST,
                '--expected-reference-sha256', REF]
        for raw in [None, b'{"secret":"PRIVATE_SENTINEL",', b'\xff', b'null']:
            if raw is not None:
                self.manifest.write_bytes(raw)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(gate.main(args), 2)
            self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())
            self.assertNotIn(str(self.root), output.getvalue())
            self.assertFalse(json.loads(output.getvalue())['metadata_valid'])

    def test_cli_is_read_only_sanitized_and_require_verified_fails_closed(self):
        self.matching()
        self.write()
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        args = ['--repo', str(self.root), '--manifest', MANIFEST,
                '--expected-reference-sha256', REF]
        # Gate must not invoke tools even for compiler/attestation verification.
        with patch('subprocess.run', side_effect=AssertionError('external execution forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('external execution forbidden')), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')):
            for suffix, expected_exit in [([], 0), (['--require-verified'], 2)]:
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(gate.main(args + suffix), expected_exit)
                result = json.loads(output.getvalue())
                self.assertEqual(result['independently_verified_source_matches'], 0)
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.claim['comparison']['secret'] = 'PRIVATE_SENTINEL'
        self.write()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(gate.main(args), 2)
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['error'], 'evidence_gate_rejected')


if __name__ == '__main__':
    unittest.main()
