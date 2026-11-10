"""Offline contract for ``dsh_status.collect`` state projection and ``_ago``.

Task #334. These tests characterise how ``nova3_team.dsh_status.collect`` maps
``supervisor.json``, ``requests*.json`` and the spend ledger onto its report
dict, and what ``_ago`` prints at unit boundaries. Every file lives under
``tmp_path``; ``nova3_team.worker.state_root`` is redirected to a temporary
root, and the spend ledger, ``_openrouter_balance``, ``autopilot.state`` and
``analysis.snapshot``/``estimate`` are replaced with fakes, so nothing is read
under the real home directory and no network or subprocess is touched.

Scope: projection only. These fixtures say nothing about live supervisor
behaviour, and nothing about the game; no equivalence claim is made.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from nova3_team import dsh_status, worker


REPO = 'synthetic-owner/synthetic-repo'
SLOT = 'dsh'
NOW = 1000.0

NOT_STARTED_PROJECTION = {
    'state': 'not started',
    'reason': '',
    'updated_unix': None,
    'updated_ago': None,
    'next_check_in': None,
}


class _FakeLedger:
    """Stand-in for ``dsh_policy.SpendLedger`` recording the path it got."""

    instances = []

    def __init__(self, path, **_kwargs):
        self.path = pathlib.Path(path)
        _FakeLedger.instances.append(self)

    def sessions_today(self, *, now=None):
        return 0

    def tokens_today(self, *, now=None):
        return 0

    def sessions_last_hour(self, *, now=None):
        return 0


def _raise_if_called(*_args, **_kwargs):
    raise AssertionError('collect must not spawn a subprocess here')


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect the state root and inject every external dependency."""
    state_root = tmp_path / 'root'
    state_dir = state_root / '.nova-slots' / SLOT / 'state'
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(worker, 'state_root', lambda repo: state_root)

    _FakeLedger.instances = []
    monkeypatch.setattr(dsh_status.dsh_policy, 'SpendLedger', _FakeLedger)

    def injected_balance():
        return None
    monkeypatch.setattr(dsh_status, '_openrouter_balance', injected_balance)

    sentinel_autopilot = {'schema': 'fake', 'mode': 'unset'}
    monkeypatch.setattr('nova3_team.autopilot.state', lambda repo: sentinel_autopilot)

    sentinel_snapshot = {'schema': 'fake', 'inventory': {}, 'progress': {}, 'assets': {}}
    sentinel_estimate = {'estimate': 'fake'}
    monkeypatch.setattr('nova3_team.analysis.snapshot', lambda root: sentinel_snapshot)
    monkeypatch.setattr('nova3_team.analysis.estimate', lambda snap: sentinel_estimate)

    monkeypatch.setattr(subprocess, 'run', _raise_if_called)

    return {
        'root': state_root,
        'state_dir': state_dir,
        'state_file': state_dir / 'supervisor.json',
        'autopilot': sentinel_autopilot,
        'snapshot': sentinel_snapshot,
        'estimate': sentinel_estimate,
    }


def _collect(now=NOW):
    return dsh_status.collect(REPO, now=now)


def _assert_not_started(report, state_file):
    for key, value in NOT_STARTED_PROJECTION.items():
        assert report[key] == value, key
    assert report['state_file'] == str(state_file)
    assert not report['open_requests']


def _assert_inside(path, root):
    resolved = pathlib.Path(path).resolve()
    assert resolved == root or root in resolved.parents


def test_collect_without_supervisor_reports_not_started(env):
    report = _collect()

    _assert_not_started(report, env['state_file'])
    _assert_inside(report['state_file'], env['root'].resolve())
    # The ledger fake must have been constructed under the temporary root too.
    assert len(_FakeLedger.instances) == 1
    _assert_inside(_FakeLedger.instances[0].path, env['root'].resolve())
    assert _FakeLedger.instances[0].path == env['state_dir'] / 'spend.jsonl'


def test_collect_never_reads_under_real_home(env, monkeypatch):
    """Every file collect touches resolves inside the redirected root."""
    touched = []
    real_read_text = pathlib.Path.read_text

    def tracing_read_text(self, *args, **kwargs):
        touched.append(pathlib.Path(self))
        return real_read_text(self, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, 'read_text', tracing_read_text)

    injected_root = worker.state_root
    seen_roots = []

    def traced_state_root(repo):
        assert repo == REPO
        result = injected_root(repo)
        seen_roots.append(result)
        return result
    monkeypatch.setattr(worker, 'state_root', traced_state_root)

    report = _collect()

    assert seen_roots == [env['root']]
    _assert_inside(report['state_file'], env['root'].resolve())
    for path in touched:
        _assert_inside(path, env['root'].resolve())


def test_backoff_times_are_derived_from_injected_now(env):
    env['state_file'].write_text(json.dumps({
        'state': 'BACKOFF', 'reason': 'x',
        'updated_unix': 940.0, 'next_check_unix': 900.0,
    }), encoding='utf-8')

    report = _collect(now=1000.0)

    assert report['state'] == 'BACKOFF'
    assert report['reason'] == 'x'
    assert report['updated_unix'] == 940.0
    assert report['updated_ago'] == '1m'   # 60 s since update
    assert report['next_check_in'] == 0    # 900 - 1000 clamped at 0
    assert report['state_file'] == str(env['state_file'])


def test_future_next_check_counts_down_from_now(env):
    env['state_file'].write_text(json.dumps({
        'state': 'RUNNING', 'updated_unix': 1000.0, 'next_check_unix': 1125.9,
    }), encoding='utf-8')

    report = _collect(now=1000.0)

    assert report['updated_ago'] == '0s'
    assert report['next_check_in'] == 125   # int() truncation, not rounding


def test_future_updated_time_clamps_updated_ago_at_zero(env):
    env['state_file'].write_text(json.dumps({
        'state': 'RUNNING', 'updated_unix': 1060.0,
    }), encoding='utf-8')

    report = _collect(now=1000.0)

    assert report['updated_ago'] == '0s'
    assert report['updated_unix'] == 1060.0
    assert report['next_check_in'] is None


@pytest.mark.parametrize('seconds, expected', [
    (0, '0s'),
    (1, '1s'),
    (59, '59s'),
    (60, '1m'),
    (61, '1m'),
    (3599, '59m'),
    (3600, '1h'),
    (7260, '2h'),
    (86399, '23h'),
    (86400, '1d'),
    (172800, '2d'),
    (-5, '0s'),
    (-0.5, '0s'),
    (59.9, '59s'),
])
def test_ago_unit_boundaries(seconds, expected):
    assert dsh_status._ago(seconds) == expected


@pytest.mark.parametrize('payload', [
    '[]',
    '[1, 2, 3]',
    '["not", "a", "dict"]',
    '{not valid json',
    '',
    'null',
    '"a string"',
])
def test_malformed_supervisor_matches_missing_file(env, payload):
    env['state_file'].write_text(payload, encoding='utf-8')
    malformed = _collect()

    env['state_file'].unlink()
    missing = _collect()

    assert malformed == missing
    _assert_not_started(malformed, env['state_file'])


def test_unreadable_supervisor_matches_missing_file(env):
    # A directory in the place of the file makes read_text raise OSError.
    env['state_file'].mkdir()
    unreadable = _collect()

    env['state_file'].rmdir()
    missing = _collect()

    assert unreadable == missing
    _assert_not_started(unreadable, env['state_file'])


def test_none_fields_are_tolerated(env):
    env['state_file'].write_text(json.dumps({
        'state': None, 'reason': None, 'task': None, 'last_outcome': None,
        'endpoint': None, 'budget': None, 'assessment': None,
    }), encoding='utf-8')

    report = _collect()

    assert report['state'] == 'not started'
    assert report['reason'] == ''
    assert report['task'] is None
    assert report['task_title'] is None
    assert report['last_outcome'] == {}
    assert report['endpoint'] == {}
    assert report['budget'] == {}
    assert report['assessment'] == {}
    assert report['balance'] is None
    assert report['autopilot'] == env['autopilot']
    assert report['project'] == {'snapshot': env['snapshot'], 'estimate': env['estimate']}


def test_unknown_balance_spawns_no_subprocess(env, monkeypatch):
    # The fixture already makes subprocess.run raise; pin it to a pytest.fail
    # too so a failure names the call rather than surfacing as an AssertionError.
    calls = []

    def must_not_run(*args, **kwargs):
        calls.append(args)
        pytest.fail(f'subprocess.run called: {args!r}')
    monkeypatch.setattr(subprocess, 'run', must_not_run)
    env['state_file'].write_text(json.dumps({'state': 'RUNNING'}), encoding='utf-8')

    report = _collect()

    assert calls == []
    assert report['balance'] is None
    assert report['state'] == 'RUNNING'


def test_real_balance_reader_skips_subprocess_without_key(monkeypatch):
    """The module's own balance reader returns None without a key, no subprocess."""
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: calls.append(a))

    assert dsh_status._openrouter_balance() is None
    assert calls == []


def test_requests_read_from_open_file_and_skip_jsonl(env):
    env['state_dir'].joinpath('requests.open.json').write_text(json.dumps([
        {'kind': 'ping', 'detail': 'a'}, 7, None, {'kind': 'ping', 'detail': 'b'},
    ]), encoding='utf-8')
    env['state_dir'].joinpath('requests.jsonl').write_text(
        json.dumps({'kind': 'ping', 'detail': 'a'}) + '\n', encoding='utf-8')

    report = _collect()

    assert report['open_requests'] == [{'kind': 'ping', 'detail': 'a'},
                                       {'kind': 'ping', 'detail': 'b'}]


def test_open_requests_list_order_is_preserved(env):
    records = [{'kind': f'k{i}', 'detail': f'd{i}'} for i in range(8)]
    env['state_dir'].joinpath('requests.open.json').write_text(
        json.dumps(records), encoding='utf-8')

    report = _collect()

    assert report['open_requests'] == records


def test_requests_jsonl_dedupes_in_requests_order(env):
    # No requests.open.json: collect falls back to the JSONL tail.
    lines = [
        '# a comment, not JSON',
        json.dumps({'kind': 'credit', 'detail': 'top up'}),
        '{bad json',
        json.dumps({'kind': 'stuck', 'detail': 'task 7'}),
        '# another comment',
        json.dumps({'kind': 'credit', 'detail': 'top up'}),  # duplicate pair
        json.dumps({'kind': 'credit', 'detail': 'top up', 'extra': 1}),  # same pair
    ]
    env['state_dir'].joinpath('requests.jsonl').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8')
    assert not env['state_dir'].joinpath('requests.open.json').exists()

    report = _collect()

    # _requests walks lines newest-first, keeps the first seen per
    # (kind, detail) pair and does not re-sort.
    assert report['open_requests'] == [
        {'kind': 'credit', 'detail': 'top up', 'extra': 1},
        {'kind': 'stuck', 'detail': 'task 7'},
    ]
    assert dsh_status._requests(env['state_dir'] / 'requests.jsonl') == report['open_requests']
