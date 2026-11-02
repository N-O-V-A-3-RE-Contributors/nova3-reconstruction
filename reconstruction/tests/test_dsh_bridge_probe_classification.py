"""Offline characterization of HostClient.bridge_available, not a readiness test.

True only records the current classifier's result: it does not establish plugin
readiness, a reachable Host, or a successful live run. Every _call is replaced;
no session is created and no workspace/team/member is registered. The optional
local dsh_runner package is required to exercise its real implementation; if it
is absent, skip rather than substitute a copy or contact/download anything.
"""

import http.client
import os
from pathlib import Path
import socket
import subprocess
import urllib.request

import pytest


host = pytest.importorskip(
    "dsh_runner.host", reason="offline classifier tests require local dsh_runner"
)
PROBE_PATH = "/dsh-runner/api/session/status?id=probe"


@pytest.fixture(autouse=True)
def prohibit_external_execution(monkeypatch):
    """Fail closed if a regression bypasses the recording _call fake."""
    def forbidden(*args, **kwargs):
        pytest.fail("bridge classification must not use network or subprocesses")

    for target, name in (
        (urllib.request, "urlopen"),
        (http.client.HTTPConnection, "connect"),
        (http.client.HTTPSConnection, "connect"),
        (socket, "socket"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (subprocess, "Popen"),
        (subprocess, "run"),
        (subprocess, "call"),
        (subprocess, "check_call"),
        (subprocess, "check_output"),
        (os, "system"),
        (os, "popen"),
    ):
        monkeypatch.setattr(target, name, forbidden)


@pytest.fixture
def recorded_probe(monkeypatch, prohibit_external_execution):
    """Install a recording fake; enforce exactly one read-only probe per case."""
    calls = []

    def configure(outcome):
        def fake_call(self, path, payload=None):
            calls.append((path, payload))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(host.HostClient, "_call", fake_call)
        # Explicit inert settings avoid dependence on DSH_HOME or user files.
        return host.HostClient(
            base="http://bridge-probe.invalid", home=Path("unused-bridge-probe-home")
        )

    yield configure

    # This excludes retries, creation/registration endpoints, and POST payloads,
    # including when the probe raises instead of returning a boolean.
    assert calls == [(PROBE_PATH, None)]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="empty-mapping"),
        pytest.param({"ok": True}, id="ok"),
        pytest.param({"status": "missing"}, id="missing-probe-session"),
        pytest.param({"sessionId": "probe", "status": "running"}, id="status"),
    ],
)
def test_successful_call_returns_true_without_validating_response(recorded_probe, response):
    assert recorded_probe(response).bridge_available() is True


@pytest.mark.parametrize(
    "message, expected",
    [
        pytest.param("HTTP 404", False, id="bare-404"),
        pytest.param(f"{PROBE_PATH}: HTTP 404", False, id="path-prefixed-404"),
        pytest.param(f"{PROBE_PATH}: HTTP 403", True, id="forbidden"),
        pytest.param(f"{PROBE_PATH}: HTTP 500", True, id="server-error"),
        pytest.param(
            "cannot reach the DSH Host: connection refused", True,
            id="connection-refused-is-not-readiness",
        ),
        pytest.param(
            "cannot reach the DSH Host: connection timed out", True,
            id="connection-timeout-is-not-readiness",
        ),
        pytest.param(f"{PROBE_PATH}: not JSON", True, id="invalid-json"),
        # Document substring matching rather than numeric HTTP-status parsing.
        pytest.param("HTTP 4040", False, id="404-substring"),
        pytest.param("http 404", True, id="case-sensitive"),
    ],
)
def test_host_unavailable_is_classified_by_message_only(recorded_probe, message, expected):
    assert recorded_probe(host.HostUnavailable(message)).bridge_available() is expected


@pytest.mark.parametrize("exception_type", [ValueError, RuntimeError, OSError])
def test_unexpected_exceptions_propagate_unchanged(recorded_probe, exception_type):
    # Even a matching message must not classify a non-HostUnavailable exception.
    error = exception_type("unexpected HTTP 404")
    client = recorded_probe(error)
    with pytest.raises(exception_type) as caught:
        client.bridge_available()
    assert caught.value is error
