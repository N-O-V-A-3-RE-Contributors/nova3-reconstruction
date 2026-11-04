"""Offline fixtures; no proprietary code, binaries, scripts or network needed."""
import unittest
from .import_donor_layout import SCHEMA, has_code, relative_source, validate_rows, lookup_primary


def ledger(address=16, size=8, name='_Z4testv', identity='donor:10:arm'):
    return {'functions': [{'id': identity, 'address': address, 'symbols': [
        {'name': name, 'size': size}]}]}


def row(**overrides):
    return dict({'addr': '00000010', 'size': '8', 'mangled': '_Z4testv',
                 'source': r'trunk\src\test.cpp', 'object': 'unit.cpp',
                 'confidence': 'high'}, **overrides)


class DonorLayoutTests(unittest.TestCase):
    def test_cached_lookup_is_bound_to_primary_hash_and_address(self):
        index = {'schema': SCHEMA, 'primary_sha256': 'hash', 'functions': [
            {'primary_function_id': 'hash:00000010:arm'},
            {'primary_function_id': 'hash:00000020:arm'},
            {'primary_function_id': None}]}
        self.assertEqual(len(lookup_primary(index, 'hash', 16)), 1)
        with self.assertRaises(ValueError):
            lookup_primary(index, 'different-build', 16)
        with self.assertRaises(ValueError):
            lookup_primary(index, 'hash', -1)

    def test_comments_are_not_reconstruction(self):
        for text in ('', '// address and name\n', ' /* notes */\n// other\n'):
            self.assertFalse(has_code(text))
        for text in ('// notes\nvoid f() {}', '"// not a comment"', '#include <x>'):
            self.assertTrue(has_code(text))
        with self.assertRaises(ValueError):
            has_code('/* truncated')

    def test_source_paths_cannot_escape_or_expose_local_absolute_paths(self):
        self.assertEqual(relative_source(r'trunk\src\test.cpp'), 'trunk/src/test.cpp')
        self.assertIsNone(relative_source('(unattributed)'))
        for path in ('../private', '/home/owner/private', r'C:\private', 'x\x00y'):
            with self.assertRaises(ValueError):
                relative_source(path)

    def test_valid_donor_row_maps_by_symbol_not_address(self):
        records, counts = validate_rows([row()], ledger(), ledger(address=96, identity='primary:60:arm'))
        self.assertEqual(counts['primary_unique_mangled_name'], 1)
        self.assertEqual(records[0]['primary_function_id'], 'primary:60:arm')
        self.assertEqual(records[0]['source_hint'], 'trunk/src/test.cpp')
        self.assertEqual(records[0]['attribution'], 'INFERRED')
        self.assertFalse(records[0]['implementation_claim'])
        self.assertFalse(records[0]['behaviour_verified'])
        self.assertFalse(records[0]['byte_match'])

    def test_bad_bounds_or_symbol_are_rejected_even_with_high_confidence(self):
        records, counts = validate_rows([row(size='9'), row(addr='20'), row(mangled='other')], ledger(), ledger())
        self.assertEqual(records, [])
        self.assertEqual(counts['donor_symbol_mismatch'], 3)

    def test_duplicate_rows_and_unsafe_paths_do_not_inflate_counts(self):
        records, counts = validate_rows([row(), row(), row(source='../secret')], ledger(), ledger())
        self.assertEqual(len(records), 1)
        self.assertEqual(counts['duplicate_rows'], 1)
        self.assertEqual(counts['invalid_rows'], 1)

    def test_ambiguous_cross_binary_symbol_never_gets_a_unique_primary_id(self):
        primary = ledger()
        primary['functions'] += ledger(address=40, identity='primary:28:arm')['functions']
        records, counts = validate_rows([row()], ledger(), primary)
        self.assertEqual(counts['primary_ambiguous'], 1)
        self.assertIsNone(records[0]['primary_function_id'])
