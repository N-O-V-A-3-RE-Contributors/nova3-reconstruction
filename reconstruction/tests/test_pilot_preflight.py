"""Tests for tools/compare/pilot_preflight.py (task #314).

The helper is a provenance preflight: it compares a previous pilot report's
``source`` path and ``flags`` with the current call, hashes the source bytes and
asks Git (read-only, once) whether the source has an uncommitted edit.  These
tests never assert a byte match, never build any object and never read the
private analysis tree; ``subprocess.run`` is patched throughout, and any call
other than the single ``git status --porcelain -- <path>`` is a failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.compare import pilot_preflight  # noqa: E402
from tools.compare.pilot_preflight import preflight  # noqa: E402

FLAGS = '-std=gnu++98 -marm -mfpu=vfpv3-d16 -O3'
SOURCE_TEXT = '// synthetic fixture source; not game code\nint value() { return 1; }\n'


REAL_RUN = subprocess.run


class _Proc:
    def __init__(self, returncode=0, stdout=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ''


@pytest.fixture(autouse=True)
def _patch_git(monkeypatch):
    """Replace subprocess.run with a recorder; default: clean work tree."""
    calls = []
    state = {'result': _Proc(0, ''), 'raise': None}

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        if state['raise'] is not None:
            raise state['raise']
        return state['result']

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return {'calls': calls, 'state': state}


def _only_git_calls(recorder, path):
    """The recorder must show at most one call per preflight invocation:
    ['git', 'status', '--porcelain', '--', <path>]."""
    assert len(recorder['calls']) <= 1
    for argv in recorder['calls']:
        assert len(argv) == 5
        assert argv[0] == 'git'
        assert argv[1] == 'status'
        assert argv[2] == '--porcelain'
        assert argv[3] == '--'
        assert os.path.realpath(argv[4]) == os.path.realpath(str(path))


@pytest.fixture()
def fixtures(tmp_path):
    src = tmp_path / 'candidate.cpp'
    src.write_bytes(SOURCE_TEXT.encode('utf-8'))
    report_path = tmp_path / 'report.json'
    report_path.write_text(json.dumps({
        'source': str(src),
        'flags': FLAGS,
        'original': {'address': '0x00000000', 'size': 64},
    }, indent=2, sort_keys=True), encoding='utf-8')
    return src, report_path


def _load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_ok_clean_and_single_git_call(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    result = preflight(src, report, expected_flags=FLAGS)

    assert result['status'] == 'ok'
    assert result['git_state'] == 'clean'
    assert result['source_sha256'] == hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    assert result['no_match_claim']
    assert 'no byte-match' in result['no_match_claim']
    _only_git_calls(_patch_git, src)


def test_source_path_mismatch_is_stale(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    report['source'] = 'reconstruction/recovered/libNOVA3_neon/other.cpp'
    result = preflight(src, report, expected_flags=FLAGS)

    assert result['status'] == 'stale'
    assert any('source mismatch' in r and 'other.cpp' in r for r in result['reasons'])
    assert result['source_sha256']
    _only_git_calls(_patch_git, src)


def test_flags_mismatch_is_stale(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    result = preflight(src, report, expected_flags=FLAGS + ' -fPIC')

    assert result['status'] == 'stale'
    assert any(r.startswith('flags mismatch') for r in result['reasons'])
    _only_git_calls(_patch_git, src)


def test_flags_accept_list_form(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    result = preflight(src, report, expected_flags=FLAGS.split())
    assert result['status'] == 'ok'


def test_missing_flags_field_is_stale(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    del report['flags']
    result = preflight(src, report, expected_flags=FLAGS)
    assert result['status'] == 'stale'
    assert any("no 'flags'" in r for r in result['reasons'])


def test_git_absent_does_not_crash(fixtures, _patch_git):
    src, report_path = fixtures
    _patch_git['state']['raise'] = FileNotFoundError(2, 'No such file', 'git')
    result = preflight(src, _load(report_path), expected_flags=FLAGS)

    assert result['status'] == 'ok'
    assert result['git_state'] == 'unknown'
    _only_git_calls(_patch_git, src)


def test_git_absent_still_stale_on_flags(fixtures, _patch_git):
    src, report_path = fixtures
    _patch_git['state']['raise'] = FileNotFoundError(2, 'No such file', 'git')
    result = preflight(src, _load(report_path), expected_flags='-O0')
    assert result['status'] == 'stale'
    assert result['git_state'] == 'unknown'


def test_untracked_source(fixtures, _patch_git):
    src, report_path = fixtures
    _patch_git['state']['result'] = _Proc(0, '?? candidate.cpp\n')
    result = preflight(src, _load(report_path), expected_flags=FLAGS)

    assert result['status'] == 'ok'
    assert result['git_state'] == 'untracked'


def test_nonzero_git_exit_is_unknown(fixtures, _patch_git):
    src, report_path = fixtures
    _patch_git['state']['result'] = _Proc(128, '')
    result = preflight(src, _load(report_path), expected_flags=FLAGS)

    assert result['status'] == 'ok'
    assert result['git_state'] == 'unknown'


def test_dirty_source_is_dirty(fixtures, _patch_git):
    src, report_path = fixtures
    _patch_git['state']['result'] = _Proc(0, ' M candidate.cpp\n')
    result = preflight(src, _load(report_path), expected_flags=FLAGS)

    assert result['status'] == 'dirty'
    assert result['git_state'] == 'dirty'
    assert any('uncommitted' in r for r in result['reasons'])


def test_sha256_matches_bytes_and_tracks_single_byte_edit(fixtures, _patch_git):
    src, report_path = fixtures
    report = _load(report_path)
    first = preflight(src, report, expected_flags=FLAGS)
    _only_git_calls(_patch_git, src)
    del _patch_git['calls'][:]
    raw = src.read_bytes()
    edited = raw[:-2] + b'2\n'
    assert edited != raw and len(edited) == len(raw)
    src.write_bytes(edited)
    second = preflight(src, report, expected_flags=FLAGS)

    _only_git_calls(_patch_git, src)
    assert first['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert second['source_sha256'] == hashlib.sha256(edited).hexdigest()
    assert first['source_sha256'] != second['source_sha256']


def test_reports_are_byte_identical_after_call(fixtures, _patch_git):
    src, report_path = fixtures
    before = report_path.read_bytes()
    preflight(src, json.loads(before), expected_flags=FLAGS)
    _only_git_calls(_patch_git, src)
    del _patch_git['calls'][:]
    preflight(src, json.loads(before), expected_flags='-O0')
    _only_git_calls(_patch_git, src)
    assert report_path.read_bytes() == before


def test_missing_source_is_error_and_never_calls_git(tmp_path, _patch_git):
    result = preflight(tmp_path / 'absent.cpp',
                       {'source': str(tmp_path / 'absent.cpp'), 'flags': FLAGS},
                       expected_flags=FLAGS)
    assert result['status'] == 'error'
    assert result['source_sha256'] is None
    assert result['git_state'] == 'unknown'
    assert _patch_git['calls'] == []


def test_non_dict_report_is_error(fixtures, _patch_git):
    src, _ = fixtures
    result = preflight(src, ['not', 'a', 'dict'], expected_flags=FLAGS)
    assert result['status'] == 'error'
    assert _patch_git['calls'] == []


def test_cli_ok_and_stale_exit_codes(fixtures, _patch_git, capsys):
    src, report_path = fixtures
    rc = pilot_preflight.main(['--source', str(src), '--report', str(report_path),
                               f'--expected-flags={FLAGS}'])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload['status'] == 'ok'
    _only_git_calls(_patch_git, src)
    del _patch_git['calls'][:]
    assert payload['no_match_claim']

    rc = pilot_preflight.main(['--source', str(src), '--report', str(report_path),
                               '--expected-flags=-O0'])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload['status'] == 'stale'
    _only_git_calls(_patch_git, src)


def test_real_git_missing_executable_degrades(tmp_path):
    """With the real subprocess.run and a PATH holding no git, the call raises
    FileNotFoundError; preflight must absorb it as git_state 'unknown'."""
    src = tmp_path / 'candidate.cpp'
    src.write_text(SOURCE_TEXT, encoding='utf-8')
    empty = tmp_path / 'empty-bin'
    empty.mkdir()
    context = pytest.MonkeyPatch()
    try:
        context.setattr(subprocess, 'run', REAL_RUN)
        context.setenv('PATH', str(empty))
        result = preflight(src, {'source': str(src), 'flags': FLAGS},
                           expected_flags=FLAGS)
    finally:
        context.undo()
    assert result['status'] == 'ok'
    assert result['git_state'] == 'unknown'
    assert result['source_sha256'] == hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
