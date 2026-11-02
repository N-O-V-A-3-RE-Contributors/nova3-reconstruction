"""Observe Host report age heuristics, without zstd or a live Host.

An age-derived ``error`` is a transcript observation, not evidence that a
session is offline. These synthetic fixtures make no game-equivalence claim.
The source-owned ``dsh_runner`` package must be importable to run these tests.
"""
from __future__ import annotations

import json
import os
from unittest.mock import Mock

import pytest

from dsh_runner import host


MTIME = 1_700_000_000.0
SESSION_ID = "age-boundary-session"
NONTERMINAL = '\n'.join(json.dumps(event) for event in (
    {"type": "tool_call"},
    {"phase": "step_end"},
))


def terminal_text(kind):
    return NONTERMINAL + '\n' + json.dumps({
        "phase": "turn_end", "reason": {"kind": kind},
    })


@pytest.fixture
def observe(tmp_path, monkeypatch):
    folder = tmp_path / "sessions" / "synthetic-workspace" / SESSION_ID
    folder.mkdir(parents=True)
    transcript = folder / "session.v4.jsonl.zstd"
    # Only existence and mtime matter: decoding is always stubbed below.
    transcript.write_bytes(b"synthetic placeholder, not compressed data")
    os.utime(transcript, (MTIME, MTIME))
    assert transcript.stat().st_mtime == MTIME
    client = host.HostClient(home=tmp_path)

    def refuse_host_call(*args, **kwargs):
        pytest.fail("report observation must not contact a Host or launch a model")

    monkeypatch.setattr(host.HostClient, "_call", refuse_host_call)

    def report(text, *, now):
        decompress = Mock(return_value=text)
        monkeypatch.setattr(host, "_decompress", decompress)
        result = client.report(SESSION_ID, now=now)
        decompress.assert_called_once_with(transcript)
        assert result.session_id == SESSION_ID
        assert result.path == str(folder)
        return result

    return report


@pytest.mark.parametrize("age,status", [(119.999, "running"), (120.0, "error")])
def test_undecodable_transcript_120_second_boundary(observe, age, status):
    report = observe(None, now=MTIME + age)
    assert report.status == status
    # Classification uses unrounded age even when the displayed age is 120.0.
    assert report.last_event_age_s == round(age, 1)
    assert report.turn_kind is None
    assert report.error_code is None
    assert report.steps == report.tool_calls == 0
    assert not report.ok


@pytest.mark.parametrize("age,status", [(179.999, "running"), (180.0, "error")])
def test_nonterminal_transcript_180_second_boundary(observe, age, status):
    report = observe(NONTERMINAL, now=MTIME + age)
    assert report.status == status
    assert report.last_event_age_s == round(age, 1)
    assert report.turn_kind is None
    assert report.error_code is None
    assert report.steps == report.tool_calls == 1
    assert not report.ok


@pytest.mark.parametrize("kind", ["completed", "aborted"])
def test_old_terminal_transcript_retains_reported_status(observe, kind):
    report = observe(terminal_text(kind), now=MTIME + 86_400.0)
    assert report.status == kind
    assert report.turn_kind == kind
    assert report.last_event_age_s == 86_400.0
    assert report.error_code is None
    assert report.steps == report.tool_calls == 1
    assert report.ok == (kind == "completed")


@pytest.mark.parametrize("text,status,kind", [
    pytest.param(None, "running", None, id="undecodable"),
    pytest.param(NONTERMINAL, "running", None, id="nonterminal"),
    pytest.param(terminal_text("completed"), "completed", "completed", id="completed"),
    pytest.param(terminal_text("aborted"), "aborted", "aborted", id="aborted"),
])
def test_future_transcript_mtime_clamps_age_to_zero(observe, text, status, kind):
    report = observe(text, now=MTIME - 60.0)
    assert report.last_event_age_s == 0.0
    assert report.status == status
    assert report.turn_kind == kind
