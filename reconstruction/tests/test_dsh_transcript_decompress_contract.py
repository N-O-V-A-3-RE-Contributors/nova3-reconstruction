"""Offline contract tests for host._decompress, not transcript parsing or age.

All tool discovery and process results are synthetic. No zstd installation,
compressed fixture, Host service, or original game data is needed. Exceptions
are deliberately expected to propagate: this pins current error handling.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from dsh_runner import host


@pytest.fixture
def decompressor_boundary(tmp_path, monkeypatch):
    path = tmp_path / "synthetic session folder" / "transcript with spaces.zstd"
    tool = str(tmp_path / "synthetic tool folder" / "zstd")
    boundary = SimpleNamespace(
        path=path,
        tool=tool,
        calls=[],
        result=subprocess.CompletedProcess(args=[], returncode=0, stdout=b""),
        error=None,
    )

    def fake_which(*args, **kwargs):
        boundary.calls.append(("which", args, kwargs))
        return boundary.tool

    def fake_run(*args, **kwargs):
        boundary.calls.append(("run", args, kwargs))
        if boundary.error is not None:
            raise boundary.error
        return boundary.result

    def forbid_http(*args, **kwargs):
        pytest.fail("decompression must not request the Host or any network URL")

    monkeypatch.setattr(host.shutil, "which", fake_which)
    monkeypatch.setattr(host.subprocess, "run", fake_run)
    monkeypatch.setattr(host.urllib.request, "urlopen", forbid_http)
    return boundary


def assert_invocation(boundary):
    # Exact call shape also forbids shell=True, text=True, and extra options.
    assert boundary.calls == [
        ("which", ("zstd",), {}),
        (
            "run",
            ([boundary.tool, "-d", "-c", "-q", str(boundary.path)],),
            {"capture_output": True, "timeout": 30, "check": False},
        ),
    ]


def test_missing_zstd_returns_none_without_running(decompressor_boundary):
    boundary = decompressor_boundary
    boundary.tool = None

    assert host._decompress(boundary.path) is None
    assert boundary.calls == [("which", ("zstd",), {})]


@pytest.mark.parametrize("returncode", [1, -9], ids=["failure", "signal"])
def test_nonzero_exit_discards_stdout(decompressor_boundary, returncode):
    boundary = decompressor_boundary
    boundary.result = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=b"partial output must not escape"
    )

    assert host._decompress(boundary.path) is None
    assert_invocation(boundary)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (b"", ""),
        (b"plain synthetic text\n", "plain synthetic text\n"),
        (b"valid \xc3\xa9; invalid \xff\xfe end", "valid \u00e9; invalid \ufffd\ufffd end"),
    ],
    ids=["empty", "text", "utf8-replacement"],
)
def test_success_decodes_stdout(decompressor_boundary, output, expected):
    boundary = decompressor_boundary
    boundary.result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=output, stderr=b"ignored synthetic diagnostic"
    )

    assert host._decompress(boundary.path) == expected
    assert_invocation(boundary)


@pytest.mark.parametrize("error_type", [subprocess.TimeoutExpired, OSError])
def test_process_exceptions_currently_propagate(decompressor_boundary, error_type):
    boundary = decompressor_boundary
    argv = [boundary.tool, "-d", "-c", "-q", str(boundary.path)]
    boundary.error = (
        subprocess.TimeoutExpired(cmd=argv, timeout=30)
        if error_type is subprocess.TimeoutExpired
        else OSError("synthetic process launch failure")
    )

    with pytest.raises(error_type) as caught:
        host._decompress(boundary.path)

    assert caught.value is boundary.error
    assert_invocation(boundary)
