"""Offline tests of CLI parsing/dispatch, not handlers or runner readiness.

Only synthetic argv is used; no profiles, host state, or game references are
needed. These tests make no game-equivalence or machine-code matching claim.
"""
from __future__ import annotations

import importlib
import socket
import subprocess
import urllib.request

import pytest


@pytest.fixture
def isolated_cli(monkeypatch):
    """Install fail-closed I/O guards and replace every dispatch target."""
    def forbidden_io(*args, **kwargs):
        pytest.fail("CLI parsing/dispatch must not use network or subprocess I/O")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_io)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden_io)
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(socket.socket, "sendto", forbidden_io)
    for name in ("Popen", "run", "call", "check_call", "check_output",
                 "getoutput", "getstatusoutput"):
        monkeypatch.setattr(subprocess, name, forbidden_io)

    # Import after the guards, so even import-time I/O cannot launch a runner.
    cli = importlib.import_module("dsh_runner.cli")
    calls = []
    handlers = {}
    sentinels = {}

    def recorder(name, sentinel):
        def fake(args):
            calls.append((name, vars(args).copy()))
            return sentinel
        return fake

    for name in ("profiles", "register", "status"):
        sentinels[name] = object()
        handlers[name] = recorder(name, sentinels[name])
        monkeypatch.setattr(cli, "cmd_" + name, handlers[name])

    return cli, calls, handlers, sentinels


@pytest.mark.parametrize(
    "argv, selected, fields",
    [
        pytest.param(["profiles"], "profiles", {}, id="profiles"),
        pytest.param(
            ["register", "--project", "demo"], "register",
            {"project": "demo", "dry_run": False}, id="register",
        ),
        pytest.param(
            ["register", "--project", "demo", "--dry-run"], "register",
            {"project": "demo", "dry_run": True}, id="register-dry-run",
        ),
        pytest.param(
            ["status", "--project", "demo"], "status",
            {"project": "demo"}, id="status",
        ),
    ],
)
def test_main_dispatches_only_selected_handler(isolated_cli, argv, selected, fields):
    cli, calls, handlers, sentinels = isolated_cli

    result = cli.main(argv)

    assert result is sentinels[selected]
    assert calls == [(selected, {
        "command": selected,
        "func": handlers[selected],
        **fields,
    })]


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="missing-subcommand"),
        pytest.param(["not-a-command"], id="unknown-subcommand"),
        pytest.param(["register"], id="register-missing-project"),
        pytest.param(["register", "--dry-run"], id="dry-run-missing-project"),
        pytest.param(["status"], id="status-missing-project"),
        pytest.param(["register", "--project"], id="register-missing-project-value"),
        pytest.param(["status", "--project"], id="status-missing-project-value"),
        pytest.param(["--unknown"], id="unknown-global-option"),
        pytest.param(["profiles", "--unknown"], id="profiles-unknown-option"),
        pytest.param(
            ["register", "--project", "demo", "--unknown"],
            id="register-unknown-option",
        ),
        pytest.param(
            ["status", "--project", "demo", "--unknown"],
            id="status-unknown-option",
        ),
        pytest.param(
            ["status", "--project", "demo", "--dry-run"],
            id="status-rejects-register-only-option",
        ),
        pytest.param(
            ["profiles", "--project", "demo"],
            id="profiles-rejects-project-option",
        ),
    ],
)
def test_main_rejects_invalid_arguments_without_dispatch(isolated_cli, capsys, argv):
    cli, calls, _, _ = isolated_cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)

    assert exc_info.value.code == 2
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert "error:" in captured.err
