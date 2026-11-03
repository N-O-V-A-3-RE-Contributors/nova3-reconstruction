"""Offline contracts for cmd_register's non-dry-run orchestration only.

No profile files, YAML parser, proprietary inputs or running DSH Host are used.
The separately supplied dsh_runner package is optional in source-only checkouts;
its absence is an explicit skip, not evidence that registration was tested.
"""

import http.client
import importlib
import json
import os
import socket
import subprocess
import sys
import urllib.request
from types import SimpleNamespace

import pytest


@pytest.fixture
def offline_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("registration orchestration must not use network or subprocesses")

    # Guard the real I/O boundaries, even if a fake is accidentally bypassed.
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    # Import and execution must work without an installed YAML parser.
    monkeypatch.setitem(sys.modules, "yaml", None)
    try:
        return importlib.import_module("dsh_runner.cli")
    except ModuleNotFoundError as exc:
        if exc.name != "dsh_runner":
            raise
        pytest.skip("dsh_runner package unavailable; CLI orchestration not tested")


@pytest.mark.parametrize("refresh_available", [True, False],
                         ids=["refreshed-team", "missing-refresh"])
def test_register_forwards_members_and_reports_selected_team(
        offline_cli, monkeypatch, capsys, refresh_available):
    cli = offline_cli
    calls = []
    profile = SimpleNamespace(
        name="synthetic-project",
        repo="example/synthetic-repository",
        workspace="/synthetic/workspace-not-read",
        execution=SimpleNamespace(squad="Synthetic Squad"),
        # Deliberately not alphabetical: registration uses configured values,
        # while the stdout plan lists sorted member keys.
        members={
            "zeta": SimpleNamespace(
                name="zeta", preset="standard", permission="workspace-write",
                model="example/test-model", effort="high", brief="Synthetic brief"),
            "alpha": SimpleNamespace(
                name="alpha", preset="minimal", permission="read-only",
                model="", effort="", brief=""),
        },
    )
    initial_team = {
        "id": "initial-team-id",
        "members": [{"name": "initial-member"}],
    }
    refreshed_team = {
        "id": "refreshed-team-id",
        "members": [{"name": "zeta"}, {"name": "alpha"}],
    }

    def recording_profile(name):
        calls.append(("profile", name))
        return profile

    class RecordingHostClient:
        def __init__(self):
            calls.append(("client",))

        def workspace_id(self, workspace):
            calls.append(("workspace_id", workspace))
            return "resolved-workspace-id"

        def ensure_team(self, squad, *, workspace_id, description):
            calls.append(("ensure_team", squad, workspace_id, description))
            return initial_team

        def ensure_member(self, team_id, payload):
            calls.append(("ensure_member", team_id, dict(payload)))

        def team(self, squad):
            calls.append(("team", squad))
            return refreshed_team if refresh_available else None

    monkeypatch.setattr(cli, "_profile", recording_profile)
    monkeypatch.setattr(cli.host, "HostClient", RecordingHostClient)

    result = cli.cmd_register(SimpleNamespace(project="synthetic-project", dry_run=False))

    assert result == 0
    # Exact trace checks ordering, once-only forwarding, resolved IDs, all
    # payload keys, and empty-string normalization independently of the fakes.
    assert calls == [
        ("profile", "synthetic-project"),
        ("client",),
        ("workspace_id", "/synthetic/workspace-not-read"),
        ("ensure_team", "Synthetic Squad", "resolved-workspace-id",
         "synthetic-project: example/synthetic-repository"),
        ("ensure_member", "initial-team-id", {
            "name": "zeta", "preset": "standard", "permission": "workspace-write",
            "model": "example/test-model", "reasoningEffort": "high",
            "brief": "Synthetic brief",
        }),
        ("ensure_member", "initial-team-id", {
            "name": "alpha", "preset": "minimal", "permission": "read-only",
            "model": None, "reasoningEffort": None, "brief": None,
        }),
        ("team", "Synthetic Squad"),
    ]

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "project": "synthetic-project",
        "workspace": "resolved-workspace-id",
        "squad": "Synthetic Squad",
        "members": ["alpha", "zeta"],
        "team_id": "refreshed-team-id" if refresh_available else "initial-team-id",
        "registered": ["zeta", "alpha"] if refresh_available else ["initial-member"],
    }
