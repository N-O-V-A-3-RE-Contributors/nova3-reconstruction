"""Unit tests for the source-correspondence tooling.

These run without the private reference tree and without the reference ELF: they
exercise the parsing/matching helpers directly and verify the *committed*
metadata summary is internally consistent.  A missing committed summary is a
skip, never a pass, so a clean checkout still tells the truth.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.source_ref.build_source_correspondence import (
    Bodies,
    candidate_keys,
    demangled_head,
    evidence_identifier,
    normalise_component,
    scan_file,
    signature_tokens,
    split_qualified,
    strip_templates,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
SUMMARY = REPO / 'coordination' / 'source_correspondence.json'


@pytest.mark.parametrize('text, expected', [
    ('CMatrix4<float>::getInverse(CMatrix4<float>&) const', ['CMatrix4', 'getInverse']),
    ('glitch::core::CMatrix4<float>::getInverse(CMatrix4<float>&) const',
     ['glitch', 'core', 'CMatrix4', 'getInverse']),
    ('void* operator new(unsigned int)', ['operatornew']),
    ('virtual thunk to Foo::bar()', ['Foo', 'bar']),
    ('Foo::baz() const [clone .isra.4]', ['Foo', 'baz']),
    ('glitch::core::CMatrix4<float> glitch::core::operator*<float, collada::SMatrix>('
     'glitch::core::CMatrix4<float> const&, collada::SMatrix const&)',
     ['glitch', 'core', 'operator*']),
])
def test_split_qualified(text, expected):
    assert split_qualified(demangled_head(text)) == expected


def test_strip_templates_nested():
    assert strip_templates('std::map<int, std::vector<float> >') == 'std::map'


def test_normalise_operator_component():
    assert normalise_component('operator *') == 'operator*'
    assert normalise_component(' foo ') == 'foo'


def test_evidence_identifier():
    assert evidence_identifier('Foo::bar(int)') == 'bar'
    assert evidence_identifier('Foo::~Foo()') == 'Foo'
    assert evidence_identifier('operator+(A const&, B const&)') == 'operator'
    assert evidence_identifier('<addr 0x000d2bbc>') is None


def test_candidate_keys_prefers_longest_suffix():
    keys = candidate_keys(['glitch', 'core', 'CMatrix4', 'getInverse'])
    assert keys[0] == 'glitch::core::CMatrix4::getInverse'
    assert keys[-1] == 'getInverse'


def test_signature_tokens_skips_stop_words():
    tokens = signature_tokens('std::vector<CMatrix4<float> > Foo::get(SMatrix const&)')
    assert 'CMatrix4' in tokens and 'SMatrix' in tokens
    assert 'std' not in tokens and 'const' not in tokens


def test_scan_file_finds_member_and_free_definitions(tmp_path):
    source = tmp_path / 'unit.cpp'
    source.write_text(
        'int freeFunction(int a)\n'
        '{\n'
        '    return a;\n'
        '}\n'
        '\n'
        'void\n'
        'Widget::method(int a) const\n'
        '{\n'
        '    (void)a;\n'
        '}\n'
        '\n'
        'bool Widget::declaredOnly(int a) const;\n',
        encoding='utf-8')
    found = {(name, line): flags for name, line, flags in scan_file(source)}
    assert ('freeFunction', 1) in found
    method = [entry for entry in found if entry[0].endswith('Widget::method')]
    assert method and method[0][1] == 7
    declarations = [entry for entry in found if entry[0].endswith('declaredOnly')]
    assert declarations and found[declarations[0]]['declaration'] is True


def test_body_extraction_keeps_string_literals(tmp_path):
    source = tmp_path / 'body.cpp'
    source.write_text(
        'void open_file()\n'
        '{\n'
        '    const char* path = "/proc/cpuinfo";\n'
        '    if (path[0] == \'/\') { close(path); }\n'
        '}\n',
        encoding='utf-8')
    bodies = Bodies(tmp_path)
    text = bodies.body('body.cpp', 1)
    assert text is not None
    assert '/proc/cpuinfo' in text
    identifiers = bodies.identifiers('body.cpp', 1)
    assert {'path', 'close'} <= identifiers


@pytest.mark.skipif(not SUMMARY.is_file(),
                    reason='committed correspondence summary not present')
def test_committed_summary_is_consistent():
    payload = json.loads(SUMMARY.read_text(encoding='utf-8'))
    assert payload['schema'] == 'nova3-source-correspondence/1'
    assert payload['functions'] > 39000
    assert sum(payload['status'].values()) == payload['functions']
    assert set(payload['status']) <= {'exact_body_found',
                                      'same_file_different_revision',
                                      'not_found'}
    assert payload['strong_evidence'] <= payload['status'].get('exact_body_found', 0)
    tracked = {row['address'] for row in payload['tracked_units']}
    assert '0x000ebb08' in tracked
    for row in payload['tracked_units']:
        assert row['status'] in ('exact_body_found', 'same_file_different_revision',
                                 'not_found', 'missing_from_symbol_table')
