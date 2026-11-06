#!/usr/bin/env python3
"""Provenance preflight for the object-comparison pilots.

The Collada pilots (``collada_matrix4_inverse_pilot.py``,
``collada_matrix_multiply_pilot.py``, ``collada_skin_prepare_cache_pilot.py``)
and ``gcc443_explain.py`` each compile one ``.cpp`` with a local ``FLAGS`` list
and compare the result with a window of the Premium ``.symtab``.  Nothing at
runtime checked that the ``.cpp`` compiled by *this* invocation is still the
file the previous report described, or that the previous report's ``flags``
string is the one the object was built with.  A stale report and a clean one
otherwise look the same.

``preflight(source, report, *, expected_flags)`` is the pure (filesystem-read
only) check:

* it reads the report's ``source``, ``flags`` and ``original`` (size) fields;
* a report whose ``source`` path or ``flags`` string does not match the current
  call is ``stale``, with the mismatch named in ``reasons``;
* it records ``source_sha256`` of the exact bytes it read;
* it asks Git whether the source differs from its HEAD copy by running exactly
  ``git status --porcelain -- <path>`` (read-only) at most once, and reports the
  outcome separately as ``git_state``: ``clean``, ``dirty`` or ``untracked``,
  or ``unknown`` when Git is absent, the path is outside any work tree, or the
  path cannot be resolved.  A missing Git never raises.

It never opens the ELF, APK or OBB, never imports pyelftools/Capstone and never
touches the network.

Honesty boundary: this is a *provenance report*.  It reports which source
bytes and which flags a pilot was invoked with, and whether Git sees an
uncommitted edit in that source.  It is **not** proof of a byte match between
any object and the reference, and not proof that a pilot's numbers are current
or behaviourally equivalent.  A ``status: ok`` only means "the current source
path and flags match the report; Git sees no uncommitted change".

CLI (prints the returned dict as JSON; exit 0 only for ``ok``)::

    python -m tools.compare.pilot_preflight --source S.cpp \\
        --report previous-report.json --expected-flags "-std=gnu++98 -marm ..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

STATUSES = ('ok', 'stale', 'dirty', 'error')
GIT_STATES = ('clean', 'dirty', 'untracked', 'unknown')
GIT_TIMEOUT_SECONDS = 20

# Stated in every output so no consumer can mistake this for a match claim.
NO_MATCH_CLAIM = (
    'provenance preflight only: no byte-match, instruction-window or '
    'behavioural-equivalence claim is made or implied')


def _as_bytes(value) -> bytes | None:
    """Return a bytestring for a path/bytes argument, or None for None."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return os.fsencode(os.fspath(value))


def _display_path(raw: bytes | None) -> str | None:
    """Render a path for JSON output without assuming UTF-8 on disk."""
    if raw is None:
        return None
    return os.fsdecode(raw)


def _sha256_of_file(path: bytes) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _git_status_once(path: bytes) -> tuple[str, str | None]:
    """Run ``git status --porcelain -- <path>`` at most once.

    Returns ``(git_state, detail)``.  The command is read-only, is never
    retried, and any failure (missing Git, not a repository, timeout,
    non-zero exit) degrades to ``unknown`` rather than raising.
    """
    argv = ['git', 'status', '--porcelain', '--', os.fsdecode(path)]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT_SECONDS)
    except (FileNotFoundError, NotADirectoryError):
        return 'unknown', 'git executable not found'
    except subprocess.TimeoutExpired:
        return 'unknown', 'git status timed out'
    except OSError as exc:
        return 'unknown', f'git status could not run: {type(exc).__name__}'
    if proc.returncode != 0:
        return 'unknown', f'git status exited {proc.returncode} (not a work tree?)'
    text = proc.stdout.decode('utf-8', 'replace') if isinstance(proc.stdout, bytes) \
        else (proc.stdout or '')
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 'clean', None
    if all(line.startswith('??') for line in lines):
        return 'untracked', 'source is not tracked by git'
    return 'dirty', 'source differs from its git HEAD copy (uncommitted change)'


def preflight(source, report: dict, *, expected_flags) -> dict:
    """Compare a previous pilot report's provenance against the current call.

    Parameters
    ----------
    source:
        Path to the ``.cpp`` the pilot is about to compile (``str``/``Path``).
    report:
        The previously written report dict.  Only ``source``, ``flags`` and
        ``original`` are read; the object is never mutated.
    expected_flags:
        The flag string or sequence of arguments the current call uses.

    Returns
    -------
    dict with ``status`` in ``('ok', 'stale', 'dirty', 'error')``, ``reasons``
    (list of human-readable strings), ``source_sha256`` (hex or ``None`` if the
    source could not be read) and, when the source was read, ``source_size``,
    plus ``git_state``/``git_detail`` and the fields that were compared.

    ``git_state`` never influences ``ok``/``stale``; an uncommitted edit is
    surfaced as ``dirty`` only when the path and flags already agree.
    """
    result: dict = {
        'status': 'error',
        'reasons': [],
        'source_sha256': None,
        'source_size': None,
        'git_state': 'unknown',
        'git_detail': None,
        'notes': [],
        'no_match_claim': NO_MATCH_CLAIM,
    }
    reasons: list[str] = result['reasons']

    source_raw = _as_bytes(source)
    result['source'] = _display_path(source_raw)
    if isinstance(expected_flags, (list, tuple)):
        expected = ' '.join(str(flag) for flag in expected_flags)
    else:
        expected = '' if expected_flags is None else str(expected_flags)
    result['expected_flags'] = expected

    if source_raw is None:
        reasons.append('no source path was given')
        return result
    if not isinstance(report, dict):
        reasons.append(f'report is not a JSON object (got {type(report).__name__})')
        return result

    # --- source bytes (read-only) ---------------------------------------
    sha256 = None
    size = None
    try:
        sha256, size = _sha256_of_file(source_raw)
    except FileNotFoundError:
        reasons.append(f'source does not exist: {result["source"]}')
    except OSError as exc:
        reasons.append(f'source could not be read: {result["source"]} '
                       f'({type(exc).__name__})')
    else:
        result['source_sha256'] = sha256
        result['source_size'] = size

    # --- report's source path -------------------------------------------
    report_source = report.get('source')
    result['report_source'] = report_source if isinstance(report_source, str) else None
    source_matches = False
    if not isinstance(report_source, str) or not report_source.strip():
        reasons.append("report has no 'source' field")
    else:
        try:
            report_resolved = os.path.realpath(os.fsencode(report_source))
            current_resolved = os.path.realpath(source_raw)
            source_matches = report_resolved == current_resolved
            result['report_source_resolved'] = _display_path(report_resolved)
            result['source_resolved'] = _display_path(current_resolved)
        except (OSError, ValueError):
            source_matches = os.fsdecode(report_source) == result['source']
        if not source_matches:
            reasons.append(
                f"source mismatch: report describes {report_source!r} but this "
                f"call compiles {result['source']!r}")

    # --- report's flags --------------------------------------------------
    report_flags = report.get('flags')
    if report_flags is None:
        reasons.append("report has no 'flags' field")
    else:
        if isinstance(report_flags, (list, tuple)):
            report_flags_text = ' '.join(str(flag) for flag in report_flags)
        elif isinstance(report_flags, str):
            report_flags_text = report_flags
        else:
            report_flags_text = None
            reasons.append(f"report 'flags' is not a string (got "
                           f"{type(report_flags).__name__})")
        result['report_flags'] = report_flags_text
        if report_flags_text is not None and report_flags_text != expected:
            reasons.append(
                f'flags mismatch: report built with {report_flags_text!r} but '
                f'this call uses {expected!r}')

    # --- report's original (size) ----------------------------------------
    # Informational: the current object size is unknown to this pure helper,
    # so a recorded size is only echoed back for the caller to compare.
    original = report.get('original')
    if isinstance(original, dict):
        result['report_original'] = original.get('size')
    elif original is None:
        result['report_original'] = None
    else:
        result['report_original'] = original

    # --- git provenance (at most one read-only call) ---------------------
    if sha256 is not None:
        git_state, git_detail = _git_status_once(source_raw)
        result['git_state'] = git_state
        result['git_detail'] = git_detail

    # --- verdict ----------------------------------------------------------
    # ``reasons`` lists every mismatch and note, regardless of status;
    # ``notes`` lists only the informational ones.  Path/flag agreement is the
    # only basis for ok/stale; git_state adds the separate 'dirty' status.
    notes: list[str] = []
    if sha256 is None:
        result['status'] = 'error'
    elif reasons:
        result['status'] = 'stale'
    elif result['git_state'] == 'dirty':
        result['status'] = 'dirty'
        reasons.append('source differs from its git HEAD copy (uncommitted '
                       'change); a report built from it is experimental')
    else:
        result['status'] = 'ok'
        if result['git_state'] == 'untracked':
            notes.append('source is untracked: there is no HEAD copy to '
                         'compare against')
        elif result['git_state'] == 'unknown':
            notes.append('git provenance unknown; status rests only on the '
                         "report's source path and flags")
    reasons.extend(notes)
    result['notes'] = notes
    return result


def load_report(path) -> dict:
    """Load a JSON report file (read-only).  Raises OSError/ValueError."""
    raw = pathlib.Path(os.fsdecode(os.fspath(path))).read_bytes()
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('report is not a JSON object')
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Provenance preflight for object-comparison pilot reports. '
                    'Makes no match claim; see the module docstring.')
    parser.add_argument('--source', required=True,
                        help='the .cpp the pilot will compile')
    parser.add_argument('--report', required=True,
                        help="a previous pilot report JSON with 'source', "
                             "'flags' and 'original'")
    parser.add_argument('--expected-flags', required=True,
                        help='the flag string (or JSON list) of the current call')
    args = parser.parse_args(argv)

    try:
        report = load_report(args.report)
    except (OSError, ValueError) as exc:
        payload = {
            'status': 'error',
            'reasons': [f'report could not be read: {type(exc).__name__}: {exc}'],
            'source_sha256': None,
            'git_state': 'unknown',
            'no_match_claim': NO_MATCH_CLAIM,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    expected = args.expected_flags
    try:
        parsed = json.loads(expected)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        expected = parsed

    result = preflight(args.source, report, expected_flags=expected)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
