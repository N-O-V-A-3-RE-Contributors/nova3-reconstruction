"""Offline HostClient.report contracts, not game-equivalence or age-boundary tests.

The optional dsh_runner package must be importable; no Host or zstd is needed.
All session data below is synthetic and confined to pytest temporary folders.
"""

import json
import os
import socket
import subprocess
import urllib.request

import pytest


host = pytest.importorskip(
    "dsh_runner.host", reason="optional dsh_runner package is not installed"
)

MTIME = 1_700_000_000.0
NOW = MTIME + 10.0
SESSION_ID = "synthetic-event-contract"


def _event(**fields):
    return json.dumps(fields)


def _turn(kind, **reason):
    return _event(phase="turn_end", reason={"kind": kind, **reason})


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("transcript contract must not request network or run subprocesses")

    monkeypatch.setattr(host.HostClient, "_call", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def report_text(tmp_path, monkeypatch):
    folder = tmp_path / "sessions" / "synthetic-workspace" / SESSION_ID
    folder.mkdir(parents=True)
    transcript = folder / "session.v4.jsonl.zstd"
    # Only existence and mtime are read: this is deliberately not compressed data.
    transcript.write_text("synthetic placeholder", encoding="utf-8")
    os.utime(transcript, (MTIME, MTIME))
    client = host.HostClient(home=tmp_path)

    def parse(text):
        decompressed = []

        def synthetic_decompress(path):
            assert path == transcript
            decompressed.append(path)
            return text

        monkeypatch.setattr(host, "_decompress", synthetic_decompress)
        result = client.report(SESSION_ID, now=NOW)
        assert decompressed == [transcript]
        assert result.session_id == SESSION_ID
        assert result.path == str(folder)
        return result

    return parse


@pytest.mark.parametrize(
    "ignored_line",
    [
        "",
        " \t ",
        "prose mentioning tool_call, step_end and turn_end",
        '{"type": "tool_call",',
        '{"phase": "step_end",}',
        '{"phase": "turn_end", "reason":',
        '[{"type": "tool_call", "phase": "step_end"}]',
        '"tool_call step_end"',
        "null",
        "true",
        "42",
        "{}",
    ],
    ids=[
        "blank", "whitespace", "prose", "malformed-tool", "malformed-step",
        "malformed-turn", "array", "string", "null", "boolean", "number",
        "empty-object",
    ],
)
def test_ignored_lines_do_not_change_counts(report_text, ignored_line):
    events = [
        _event(type="tool_call"),
        _event(phase="step_end"),
        _event(phase="step_end"),
        _turn("completed"),
    ]
    clean = report_text("\n".join(events))
    noisy = report_text("\n".join([ignored_line, *events, ignored_line]))
    assert clean.steps == noisy.steps == 2
    assert clean.tool_calls == noisy.tool_calls == 1
    assert clean.status == noisy.status == "completed"
    assert clean.turn_kind == noisy.turn_kind == "completed"


@pytest.mark.parametrize(
    ("events", "tools", "steps"),
    [
        ([], 0, 0),
        ([{"type": "tool_call"}], 1, 0),
        ([{"phase": "step_end"}], 0, 1),
        ([{"type": "tool_call", "phase": "step_end"}], 1, 1),
        ([
            {"type": "tool_call"},
            {"type": "tool_call"},
            {"phase": "step_end"},
            {"phase": "step_end"},
            {"phase": "step_end"},
            {"type": "tool_call", "phase": "step_end"},
        ], 3, 4),
    ],
    ids=["none", "tool-only", "step-only", "both-fields", "mixed"],
)
def test_tool_calls_and_steps_are_counted_independently(report_text, events, tools, steps):
    text = "\n".join([*(_event(**event) for event in events), _turn("completed")])
    report = report_text(text)
    assert report.tool_calls == tools
    assert report.steps == steps
    assert report.status == report.turn_kind == "completed"


@pytest.mark.parametrize(
    ("first", "last"),
    [("completed", "aborted"), ("aborted", "completed")],
    ids=["completed-then-aborted", "aborted-then-completed"],
)
def test_final_turn_end_determines_status_and_kind(report_text, first, last):
    report = report_text("\n".join([
        _turn(first),
        _event(type="tool_call"),
        _turn(last),
        # Trailing non-terminal events must not replace the final turn's kind.
        _event(phase="step_end"),
        "trailing prose",
    ]))
    assert report.status == last
    assert report.turn_kind == last
    assert report.ok is (last == "completed")
    assert report.tool_calls == 1
    assert report.steps == 1


def test_error_turn_preserves_synthetic_error_code(report_text):
    code = "SYNTHETIC_TRANSCRIPT_ERROR_238"
    report = report_text("\n".join([
        _event(type="tool_call"),
        _turn("error", error={"code": code}),
    ]))
    assert report.status == "error"
    assert report.turn_kind == "error"
    assert report.error_code == code
    assert report.ok is False
    assert report.tool_calls == 1
    assert report.steps == 0
