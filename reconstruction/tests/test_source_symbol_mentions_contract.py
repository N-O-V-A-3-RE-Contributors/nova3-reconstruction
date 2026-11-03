"""Bounded textual-mention discovery, using only temporary synthetic source.

These tests do not identify definition sites, reconstruct functions, or establish
any equivalence to original game code. No private reference tree is needed.
"""
from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import socket
import subprocess

import pytest

from tools.source_ref import index_source_reference as source_index


@pytest.fixture
def synthetic_scan(tmp_path, monkeypatch):
    """Run the real traversal with file reads confined to this test's tree."""
    opened = []

    def confined(path):
        assert not isinstance(path, int), 'file-descriptor access is prohibited'
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(tmp_path), 'non-synthetic access prohibited'
        return resolved

    def forbidden(*args, **kwargs):
        raise AssertionError('subprocesses and network access are prohibited')

    def guarded_open(original):
        def open_synthetic(file, *args, **kwargs):
            opened.append(confined(file))
            return original(file, *args, **kwargs)
        return open_synthetic

    original_scandir = os.scandir

    def scandir_synthetic(path):
        confined(path)
        return original_scandir(path)

    def scan(symbols):
        # Scope the guards to the call, not pytest's own imports/reporting.
        with monkeypatch.context() as patch:
            for module, name in ((builtins, 'open'), (io, 'open'), (os, 'open')):
                patch.setattr(module, name, guarded_open(getattr(module, name)))
            patch.setattr(os, 'scandir', scandir_synthetic)
            for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
                patch.setattr(subprocess, name, forbidden)
            patch.setattr(os, 'system', forbidden)
            patch.setattr(os, 'popen', forbidden)
            for name in ('socket', 'create_connection', 'getaddrinfo'):
                patch.setattr(socket, name, forbidden)
            return source_index.locate_symbols(tmp_path, symbols)

    return scan, opened


def test_suffix_filter_is_case_insensitive_and_ignores_unsupported(
    tmp_path, synthetic_scan
):
    scan, opened = synthetic_scan
    # Explicit contract, deliberately not derived from SOURCE_SUFFIXES.
    suffixes = (
        '.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.inl', '.ipp', '.inc',
        '.java', '.cs', '.mm', '.m', '.glsl', '.fx', '.hlsl', '.cg',
    )
    expected = {}
    for index, suffix in enumerate(suffixes):
        symbol = f'SyntheticSuffix{index:02d}Token'
        mixed = '.' + suffix[1].upper() + suffix[2:]
        names = [f'{index:02d}_{case}{ext}' for case, ext in enumerate(
            (suffix, suffix.upper(), mixed)
        )]
        for name in names:
            (tmp_path / name).write_text(f'// {symbol}\n', encoding='utf-8')
        expected[symbol] = names

    ignored = ('ignored.txt', 'ignored.py', 'ignored.cpp.bak', 'no_suffix')
    for name in ignored:
        (tmp_path / name).write_text('// UnsupportedOnly\n', encoding='utf-8')

    assert scan((*expected, 'UnsupportedOnly', 'EntirelyAbsent')) == expected
    assert {path.name for path in opened}.isdisjoint(ignored)


def test_substrings_and_comments_are_mentions_not_definitions(
    tmp_path, synthetic_scan
):
    scan, _ = synthetic_scan
    # Neither file declares or defines SyntheticNeedle; both only mention it.
    (tmp_path / 'comments.cpp').write_text(
        '// prefixSyntheticNeedleSuffix\n'
        '/* SyntheticNeedle SyntheticNeedle */\n', encoding='utf-8'
    )
    (tmp_path / 'strings.h').write_text(
        'const char *label = "prefixSyntheticNeedleSuffix";\n', encoding='utf-8'
    )

    assert scan(('SyntheticNeedle', 'prefixSynthetic', 'syntheticneedle',
                 'MissingSymbol')) == {
        'SyntheticNeedle': ['comments.cpp', 'strings.h'],
        'prefixSynthetic': ['comments.cpp', 'strings.h'],
    }


def test_exact_eight_paths_follow_sorted_depth_first_traversal(
    tmp_path, synthetic_scan
):
    scan, _ = synthetic_scan
    # Shuffled files AND directory creation, with root files visited first.
    creation_order = (
        'zeta/a.cpp', 'alpha/z.cpp', 'root_z.cpp', 'beta/b.cpp',
        'alpha/deep/b.cpp', 'root_a.cpp', 'beta/a.cpp', 'alpha/a.cpp',
        'alpha/deep/a.cpp', 'alpha/b.cpp', 'zeta/b.cpp', 'beta/c.cpp',
    )
    for relative in creation_order:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('// BoundedMention BoundedMention\n', encoding='utf-8')
    # A separate symbol still gets its own allowance after the first is full.
    with (tmp_path / 'zeta/b.cpp').open('a', encoding='utf-8') as stream:
        stream.write('// LateMention\n')

    # This is traversal order, not a global sort of relative path strings.
    assert scan(('BoundedMention', 'LateMention', 'MissingSymbol')) == {
        'BoundedMention': [
            'root_a.cpp', 'root_z.cpp',
            'alpha/a.cpp', 'alpha/b.cpp', 'alpha/z.cpp',
            'alpha/deep/a.cpp', 'alpha/deep/b.cpp', 'beta/a.cpp',
        ],
        'LateMention': ['zeta/b.cpp'],
    }


def test_four_mib_is_scanned_but_one_byte_larger_is_not(
    tmp_path, synthetic_scan
):
    scan, opened = synthetic_scan
    limit = 4 * 1024 * 1024
    exact = tmp_path / 'exact.CPP'
    # Put the mention at EOF to require scanning the whole boundary-sized file.
    tail = b'// AtBoundary SharedMention\n'
    exact.write_bytes(b' ' * (limit - len(tail)) + tail)
    oversized = tmp_path / 'larger.cpp'
    head = b'// OversizedOnly SharedMention\n'
    oversized.write_bytes(head + b' ' * (limit + 1 - len(head)))
    assert exact.stat().st_size == limit
    assert oversized.stat().st_size == limit + 1

    assert scan(('AtBoundary', 'SharedMention', 'OversizedOnly', 'Absent')) == {
        'AtBoundary': ['exact.CPP'],
        'SharedMention': ['exact.CPP'],
    }
    assert exact in opened
    assert oversized not in opened
