"""Offline contract for Host session lookup and early missing reports only.

All homes are synthetic. No transcript parsing, age/readiness decisions or game
behaviour is covered. If the optional dsh_runner source package is unavailable,
collected cases skip explicitly; no DSH executable or zstd is required.
"""
from __future__ import annotations

import importlib
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network requests and subprocess execution are forbidden")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)


@pytest.fixture
def client(tmp_path, monkeypatch, offline_only):
    try:
        host = importlib.import_module("dsh_runner.host")
    except ModuleNotFoundError as exc:
        if exc.name not in {"dsh_runner", "dsh_runner.host"}:
            raise
        pytest.skip("optional dsh_runner.host source package is unavailable")

    def no_decompression(*args, **kwargs):
        raise AssertionError("early missing reports must not decompress transcripts")

    def no_http(*args, **kwargs):
        raise AssertionError("session discovery must not call the Host API")

    monkeypatch.setattr(host, "_decompress", no_decompression)
    monkeypatch.setattr(host.HostClient, "_call", no_http)
    home = tmp_path / "host-home"
    home.mkdir()
    return host.HostClient(home=home)


def make_directory(home: Path, relative: str) -> Path:
    folder = home / relative
    folder.mkdir(parents=True)
    return folder


@pytest.mark.parametrize("slug", ["workspace-alpha", "workspace-beta"])
def test_unique_session_directory_is_found_in_either_workspace(client, slug):
    expected = make_directory(client.home, f"sessions/{slug}/session-target")
    make_directory(client.home, "sessions/workspace-alpha/session-unrelated")
    make_directory(client.home, "sessions/workspace-beta/session-other")
    # These same-ID entries are not additional valid session directories.
    decoy = client.home / "sessions/workspace-file/session-target"
    decoy.parent.mkdir()
    decoy.write_text("synthetic regular file", encoding="utf-8")
    make_directory(client.home, "sessions/session-target")
    make_directory(client.home, "sessions/workspace-deep/nested/session-target")

    assert client.session_path("session-target") == expected


@pytest.mark.parametrize(
    "relative,is_file",
    [
        ("sessions/workspace-alpha/session-unrelated", False),
        ("sessions/workspace-alpha/session-target", True),
        ("sessions/session-target", False),
        ("sessions/workspace-alpha/nested/session-target", False),
        ("workspace-alpha/session-target", False),
    ],
    ids=["unrelated-id", "regular-file", "too-shallow", "too-deep", "outside-sessions"],
)
def test_nonmatching_entries_do_not_discover_a_session(client, relative, is_file):
    entry = client.home / relative
    if is_file:
        entry.parent.mkdir(parents=True)
        entry.write_text("synthetic regular file", encoding="utf-8")
    else:
        make_directory(client.home, relative)

    assert client.session_path("session-target") is None


@pytest.mark.parametrize("with_unrelated_session", [False, True], ids=["empty-home", "other-id"])
def test_absent_session_report_has_no_path(client, with_unrelated_session):
    if with_unrelated_session:
        make_directory(client.home, "sessions/workspace-alpha/session-unrelated")

    report = client.report("session-absent")

    assert report.status == "missing"
    assert report.session_id == "session-absent"
    assert report.path is None


@pytest.mark.parametrize("transcript_is_directory", [False, True], ids=["absent", "directory"])
def test_discovered_session_without_transcript_file_reports_folder(client, transcript_is_directory):
    folder = make_directory(client.home, "sessions/workspace-beta/session-pending")
    if transcript_is_directory:
        (folder / "session.v4.jsonl.zstd").mkdir()

    report = client.report("session-pending")

    assert report.status == "missing"
    assert report.session_id == "session-pending"
    assert report.path == str(folder)
