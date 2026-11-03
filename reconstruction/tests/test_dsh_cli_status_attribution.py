"""Offline status-attribution contracts for ``dsh_runner.cli.cmd_status`` only.

``cmd_status`` resolves a project profile, reads the DSH Host's state, selects the
team whose name equals ``profile.execution.squad`` and counts the runs that
reference that team's id. These tests exercise that attribution logic with a
synthetic parsed namespace, a fake ``cli._profile`` and a fake
``cli.host.HostClient``. No YAML/profile file, profile directory, DSH Host,
socket or subprocess is touched, and nothing here is evidence about a running
Host, the game, or any original machine code.

The separately supplied ``dsh_runner`` package is optional in source-only
checkouts; its absence is an explicit skip, not evidence that status was tested.
"""
from __future__ import annotations

import http.client
import importlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

# Keys the successful status object must always include.
STATUS_KEYS = {"project", "backend", "squad_registered", "runs_recorded",
               "workspace_registered"}
# The HostUnavailable branch prints its own smaller object instead.
UNAVAILABLE_KEYS = {"project", "host", "detail"}


@pytest.fixture
def offline_cli(monkeypatch):
    """Import the CLI with every real I/O boundary replaced by a hard failure."""
    def forbidden(*args, **kwargs):
        pytest.fail("cmd_status must not use network, sockets or subprocesses")

    # Network: urllib, http.client and raw sockets (connect + name resolution).
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "gethostbyname", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    # Subprocesses and shell escapes.
    for name in ("Popen", "run", "call", "check_call", "check_output",
                 "getoutput", "getstatusoutput"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    # Import and execution must work without an installed YAML parser, so a
    # profile accidentally read from disk would fail loudly instead of parsing.
    monkeypatch.setitem(sys.modules, "yaml", None)
    try:
        return importlib.import_module("dsh_runner.cli")
    except ModuleNotFoundError as exc:
        if exc.name != "dsh_runner":
            raise
        pytest.skip("dsh_runner package unavailable; CLI status not tested")


def _profile(name="synthetic-project", squad="Synthetic Squad", backend="host",
             workspace="/synthetic/workspace-not-read"):
    return SimpleNamespace(
        name=name,
        workspace=workspace,
        execution=SimpleNamespace(squad=squad, backend=backend),
    )


def _install_fakes(monkeypatch, cli, profile, state=None, error=None,
                   workspace_result="resolved-workspace-id"):
    """Patch cli._profile and cli.host.HostClient; return the shared call trace.

    ``state`` is the dict returned by ``client.state()``. When ``error`` is set,
    ``state()`` raises that exception instead (HostUnavailable path).
    ``workspace_result`` is returned by ``workspace_id()``.
    """
    trace = []

    def fake_profile(name):
        trace.append(("profile", name))
        return profile

    class FakeHostClient:
        def __init__(self):
            trace.append(("client",))

        def state(self):
            trace.append(("state",))
            if error is not None:
                raise error
            return state

        def workspace_id(self, workspace):
            trace.append(("workspace_id", workspace))
            return workspace_result

        # Any other client method is a stub that must not be called.
        def __getattr__(self, attr):
            trace.append(("unexpected", attr))
            pytest.fail(f"cmd_status must not call HostClient.{attr}")

    monkeypatch.setattr(cli, "_profile", fake_profile)
    monkeypatch.setattr(cli.host, "HostClient", FakeHostClient)
    return trace


def _read_status(capsys, keys=STATUS_KEYS):
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)
    assert keys <= set(payload)
    return payload


def test_host_unavailable_returns_3_with_json_detail(offline_cli, monkeypatch, capsys):
    cli = offline_cli
    reason = "cannot reach the DSH Host at http://127.0.0.1:3080: refused"
    error = cli.host.HostUnavailable(reason)
    trace = _install_fakes(monkeypatch, cli, _profile(), error=error)

    result = cli.cmd_status(SimpleNamespace(project="synthetic-project"))

    assert result == 3
    assert trace == [("profile", "synthetic-project"), ("client",), ("state",)]
    payload = _read_status(capsys, UNAVAILABLE_KEYS)
    assert payload["host"] == "unavailable"
    assert payload["project"] == "synthetic-project"
    assert isinstance(payload["detail"], str)
    assert payload["detail"] == reason
    # The unavailable path must not reach the workspace lookup.
    assert ("workspace_id", "/synthetic/workspace-not-read") not in trace


def test_registered_squad_counts_only_runs_referencing_its_id(
        offline_cli, monkeypatch, capsys):
    cli = offline_cli
    state = {
        "teams": [
            {"id": "team-other", "name": "Other Squad"},
            {"id": "team-target", "name": "Synthetic Squad"},
        ],
        "runs": [
            {"id": "run-1", "teamId": "team-target"},
            {"id": "run-2", "teamId": "team-other"},
            {"id": "run-3", "teamId": "team-target"},
        ],
    }
    trace = _install_fakes(monkeypatch, cli, _profile(backend="headless"), state=state)

    result = cli.cmd_status(SimpleNamespace(project="synthetic-project"))

    assert result == 0
    assert trace == [
        ("profile", "synthetic-project"),
        ("client",),
        ("state",),
        ("workspace_id", "/synthetic/workspace-not-read"),
    ]
    payload = _read_status(capsys)
    assert payload == {
        "project": "synthetic-project",
        "backend": "headless",
        "squad_registered": True,
        "runs_recorded": 2,
        "workspace_registered": True,
    }


def test_unknown_squad_reports_unregistered_and_zero_runs(
        offline_cli, monkeypatch, capsys):
    cli = offline_cli
    # The run names the configured squad but no team with that name exists, so
    # there is no authorised id to match and the run must not be counted.
    state = {
        "teams": [{"id": "team-other", "name": "Other Squad"}],
        "runs": [
            {"id": "run-1", "teamId": "team-other"},
            {"id": "run-2", "teamId": "team-other"},
        ],
    }
    trace = _install_fakes(monkeypatch, cli, _profile(), state=state)

    result = cli.cmd_status(SimpleNamespace(project="synthetic-project"))

    assert result == 0
    payload = _read_status(capsys)
    assert payload["project"] == "synthetic-project"
    assert payload["backend"] == "host"
    assert payload["squad_registered"] is False
    assert payload["runs_recorded"] == 0
    # The workspace probe still runs and is recorded even for an unknown squad.
    assert ("workspace_id", "/synthetic/workspace-not-read") in trace


@pytest.mark.parametrize(
    "workspace_result, expected",
    [
        pytest.param("resolved-workspace-id", True, id="registered-workspace"),
        pytest.param(None, False, id="unregistered-workspace"),
        pytest.param("", False, id="empty-workspace-id"),
    ],
)
def test_workspace_registration_follows_workspace_id_result(
        offline_cli, monkeypatch, capsys, workspace_result, expected):
    cli = offline_cli
    state = {
        "teams": [{"id": "team-target", "name": "Synthetic Squad"}],
        "runs": [{"id": "run-1", "teamId": "team-target"}],
    }
    trace = _install_fakes(monkeypatch, cli, _profile(), state=state,
                           workspace_result=workspace_result)

    result = cli.cmd_status(SimpleNamespace(project="synthetic-project"))

    assert result == 0
    # Recorded exactly once with the profile's own workspace, so the
    # assertion below cannot pass vacuously.
    assert trace.count(("workspace_id", "/synthetic/workspace-not-read")) == 1
    payload = _read_status(capsys)
    assert payload["workspace_registered"] == bool(workspace_result)
    assert payload["workspace_registered"] is expected
    # Registration status is independent of the run count.
    assert payload["squad_registered"] is True
    assert payload["runs_recorded"] == 1
