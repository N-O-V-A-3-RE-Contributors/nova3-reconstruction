"""Offline contract for ``nova3_team.dsh_status._requests``.

``_requests`` renders the supervisor's newest-first JSONL request history for
the operator status page. This test uses only a temporary JSONL fixture: no
supervisor state, credential, subprocess or network resource is read, and
nothing here is evidence about a running supervisor.

The history intentionally mixes valid object records with duplicate
kind/detail pairs, malformed JSON, ``null``, a JSON array and scalar values.
"""

from __future__ import annotations

import importlib
import json
import socket
import subprocess

import pytest

from nova3_team import dsh_status


def _write_history(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_requests_skips_non_objects_and_keeps_newest_first_contract(tmp_path):
    history = _write_history(
        tmp_path / "requests.jsonl",
        [
            # Oldest record; outside the six-entry window.
            json.dumps({"id": "ancient", "kind": "ancient", "detail": "old"}),
            # Malformed / non-object records must be tolerated, not crash.
            '{"kind": "broken", "detail": ',
            "null",
            '[{"kind": "list-record"}]',
            '"a scalar string"',
            "42",
            # Six distinct object records, newest first from here on.
            json.dumps({"id": "g", "kind": "g", "detail": "g"}),
            json.dumps({"id": "f", "kind": "f", "detail": "f"}),
            json.dumps({"id": "e", "kind": "e", "detail": "e"}),
            json.dumps({"id": "d", "kind": "d", "detail": "d"}),
            json.dumps({"id": "c", "kind": "c", "detail": "c"}),
            json.dumps({"id": "b", "kind": "b", "detail": "b"}),
            # Duplicate kind/detail pair: the newer copy wins, the older copy
            # must not consume one of the six slots.
            json.dumps({"id": "a2", "kind": "dup", "detail": "same"}),
            json.dumps({"id": "a", "kind": "dup", "detail": "same"}),
            # Newest lines are non-objects, exercising the guard immediately.
            "null",
            "[1, 2]",
            '"top scalar"',
            "true",
        ],
    )

    records = dsh_status._requests(history)

    assert [record["id"] for record in records] == ["a", "b", "c", "d", "e", "f"]
    assert all(isinstance(record, dict) for record in records)
    assert len(records) == 6
    assert records[0] == {"id": "a", "kind": "dup", "detail": "same"}


def test_requests_missing_file_returns_empty_list(tmp_path):
    assert dsh_status._requests(tmp_path / "absent.jsonl") == []


def test_requests_only_malformed_or_non_object_lines_returns_empty_list(tmp_path):
    history = _write_history(
        tmp_path / "junk.jsonl",
        [
            '{"kind": "broken"',
            "not json at all",
            "null",
            "[]",
            "[{}]",
            '"scalar"',
            "3.14",
            "true",
            "",
        ],
    )

    assert dsh_status._requests(history) == []


def test_module_imports_without_network_or_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dsh_status import must not use network or subprocesses")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    module = importlib.reload(dsh_status)

    assert callable(module._requests)
