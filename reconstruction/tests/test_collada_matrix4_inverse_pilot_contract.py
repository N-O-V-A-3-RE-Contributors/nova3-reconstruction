"""Offline contract tests for the getInverse measurement pilot.

Every subprocess is replaced at the module boundary: no compiler, checker or
reference ELF is executed or read.  The fixtures are synthetic paths/files.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import pathlib
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/compare/collada_matrix4_inverse_pilot.py'
SPEC = importlib.util.spec_from_file_location('inverse_pilot_contract', TOOL)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)

EXPECTED_FLAGS = [
    '-std=gnu++98', '-marm', '-mfpu=vfpv3-d16', '-O3', '-mfloat-abi=softfp',
    '-fPIC', '-fno-rtti', '-fno-exceptions',
]
EXPECTED_ADDRESS = '0x000ebb08'
EXPECTED_SYMBOL = '_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_'


def _completed(command, returncode, stdout='', stderr=''):
    return pilot.subprocess.CompletedProcess(
        command, returncode, stdout=stdout, stderr=stderr)


def _window_payload(status):
    return {
        'original_bytes': 776,
        'candidate_bytes': 776,
        'status': status,
        'call_sites': [],
        'original_window_sha256': 'a' * 64,
        'candidate_window_sha256': 'b' * 64,
    }


def _explain_payload():
    return {
        'original_instructions': 191,
        'candidate_instructions': 191,
        'count_deltas': {},
        'classes_only_in_original': [],
        'classes_only_in_candidate': [],
        'first_divergence': None,
    }


def _fake_run(recorded, gcc, *, compile_rc, window_rc, explain_rc, status):
    """Record argv and synthesize each tool's output without running it."""
    def run(command):
        recorded.append(list(command))
        if command[0] == str(gcc):
            if compile_rc == 0:
                target = pathlib.Path(command[command.index('-o') + 1])
                target.write_bytes(b'synthetic relocatable object')
            return _completed(
                command, compile_rc,
                stderr='' if compile_rc == 0 else 'synthetic compile failure')
        if any('gcc443_window_check.py' in arg for arg in command):
            return _completed(
                command, window_rc,
                stdout=json.dumps(_window_payload(status)) if window_rc == 0 else '',
                stderr='synthetic window failure' if window_rc else '')
        if any('gcc443_explain.py' in arg for arg in command):
            return _completed(
                command, explain_rc,
                stdout=json.dumps(_explain_payload()) if explain_rc == 0 else '',
                stderr='synthetic explain failure' if explain_rc else '')
        raise AssertionError(f'unexpected subprocess command: {command!r}')
    return run


def test_measurement_failures_fail_closed_and_successes_report_verbatim(
        tmp_path, monkeypatch, capsys):
    assert pilot.FLAGS == EXPECTED_FLAGS
    assert pilot.ADDRESS_TEXT == EXPECTED_ADDRESS
    assert pilot.SYMBOL == EXPECTED_SYMBOL

    scenarios = [
        # name, compile rc, window rc, explain rc, window status, expected rc
        ('compile-ok-window-fails', 0, 2, 0, 'DIFFERENT', 1),
        ('compile-ok-explain-fails', 0, 0, 2, 'DIFFERENT', 1),
        ('different-window', 0, 0, 0, 'DIFFERENT', 0),
        ('matched-normalized-calls', 0, 0, 0, 'MATCHED_NORMALIZED_CALLS', 0),
    ]

    for (name, compile_rc, window_rc, explain_rc, status,
         expected_rc) in scenarios:
        case_dir = tmp_path / name
        case_dir.mkdir()
        gcc = case_dir / 'fake-g++'
        gcc.write_text('#!/bin/sh\nexit 99\n', encoding='utf-8')
        gcc.chmod(0o755)
        reference = case_dir / 'reference.so'
        reference.write_bytes(b'synthetic reference fixture')
        out = case_dir / 'report.json'
        obj = case_dir / 'inverse.o'

        @contextlib.contextmanager
        def fake_tempdir(*args, **kwargs):
            yield case_dir

        # Keep the object under tmp_path rather than a real temporary directory.
        monkeypatch.setattr(
            pilot, 'tempfile',
            types.SimpleNamespace(TemporaryDirectory=fake_tempdir))
        recorded = []
        monkeypatch.setattr(
            pilot, 'run',
            _fake_run(recorded, gcc, compile_rc=compile_rc,
                      window_rc=window_rc, explain_rc=explain_rc,
                      status=status))

        rc = pilot.main(['--gcc', str(gcc), '--reference', str(reference),
                         '--out', str(out)])

        stdout = capsys.readouterr().out
        if stdout.startswith('wrote '):
            wrote_line, printed_json = stdout.split('\n', 1)
            assert wrote_line == f'wrote {out}'
        else:
            printed_json = stdout
        assert out.read_text(encoding='utf-8') == printed_json
        report = json.loads(printed_json)

        assert rc == expected_rc, name
        assert len(recorded) == (3 if compile_rc == 0 else 1), name
        compile_command = recorded[0]
        assert compile_command[0] == str(gcc)
        assert compile_command[1:1 + len(EXPECTED_FLAGS)] == EXPECTED_FLAGS
        assert compile_command[compile_command.index('-o') + 1] == str(obj)
        assert obj.is_file(), name

        if compile_rc == 0:
            window_command = recorded[1]
            explain_command = recorded[2]
            for command in (window_command, explain_command):
                assert command[command.index('--address') + 1] == EXPECTED_ADDRESS
                assert command[command.index('--symbol') + 1] == EXPECTED_SYMBOL
            assert window_command[window_command.index('--object') + 1] == str(obj)
            assert explain_command[explain_command.index('--flags') + 1] == \
                ' '.join(EXPECTED_FLAGS)
            assert explain_command[explain_command.index('--gcc') + 1] == str(gcc)

        assert report['address'] == EXPECTED_ADDRESS
        assert report['symbol'] == EXPECTED_SYMBOL
        assert report['flags'] == ' '.join(EXPECTED_FLAGS)
        assert report['compiled'] is True

        if window_rc != 0:
            assert 'window_error' in report
            assert 'window' not in report
            assert report['window_error'] == 'synthetic window failure'
        else:
            assert 'window_error' not in report
            assert report['window']['status'] == status
        if explain_rc != 0:
            assert 'explain_error' in report
            assert report['explain_error'] == 'synthetic explain failure'
        else:
            assert 'explain_error' not in report
        assert report['source_matching_verified'] is (
            status == 'MATCHED_NORMALIZED_CALLS' and window_rc == 0)
