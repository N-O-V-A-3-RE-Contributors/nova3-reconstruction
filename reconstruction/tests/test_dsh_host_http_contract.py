"""Offline transport contracts for dsh_runner.host, not HostBackend guards.

Only urlopen is faked: request construction and response interpretation use the
real HostClient. No Host, credentials, model execution or game files are needed.
"""

import io
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request

import pytest

from dsh_runner.host import HostClient, HostUnavailable


BASE = "http://127.0.0.1:43210"
TIMEOUT = 1.75
RUN_ARGS = {
    "team_id": "team-offline",
    "member_id": "member-offline",
    "first_message": 'Offline only: "quoted" \\ path\nUnicode: café 🚀',
}
RUN_PAYLOAD = {
    "teamId": RUN_ARGS["team_id"],
    "memberId": RUN_ARGS["member_id"],
    "firstMessage": RUN_ARGS["first_message"],
}


class RecordingURLopen:
    """Return in-memory HTTP bodies, or raise at the transport boundary."""

    def __init__(self):
        self.calls = []
        self.responses = []
        self.body = b"{}"
        self.error = None

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        response = io.BytesIO(self.body)
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def offline_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network requests and model/process launches are forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    transport = RecordingURLopen()
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    return transport


@pytest.fixture
def client():
    # Exercise trailing-slash normalization and a non-default timeout. No home
    # directory is read by either HTTP method.
    return HostClient(BASE + "/", timeout=TIMEOUT)


def assert_request(transport, method, path, payload=None):
    assert len(transport.calls) == 1
    request, timeout = transport.calls[0]
    assert isinstance(request, urllib.request.Request)
    assert request.get_method() == method
    assert request.full_url == BASE + path
    assert request.get_header("Origin") == BASE
    assert timeout == TIMEOUT
    if payload is None:
        assert request.data is None
        assert request.get_header("Content-type") is None
    else:
        assert request.get_header("Content-type") == "application/json"
        assert isinstance(request.data, bytes)
        # Exact keys and values, without constraining irrelevant JSON spacing.
        assert json.loads(request.data.decode("utf-8")) == payload
    assert all(response.closed for response in transport.responses)


def test_state_serializes_get_and_returns_response(client, offline_transport):
    expected = {"ok": True, "teams": [{"id": "team-offline"}], "runs": []}
    offline_transport.body = json.dumps(expected).encode("utf-8")

    assert client.state() == expected

    assert_request(offline_transport, "GET", "/dsh-agents/api/state")


@pytest.mark.parametrize(
    "response, session_id",
    [
        pytest.param({"run": {"sessionId": "nested-session"}}, "nested-session",
                     id="nested-session-id"),
        pytest.param({"sessionId": "top-session"}, "top-session",
                     id="top-level-session-id"),
        pytest.param({"run": {"sessionId": "nested-session"},
                      "sessionId": "top-session"}, "nested-session",
                     id="nested-takes-precedence"),
        pytest.param({"run": {"sessionId": ""}, "sessionId": "top-session"},
                     "top-session", id="empty-nested-falls-back"),
    ],
)
def test_run_member_serializes_post_and_returns_session(
    client, offline_transport, response, session_id
):
    offline_transport.body = json.dumps(response).encode("utf-8")

    assert client.run_member(**RUN_ARGS) == session_id

    assert_request(offline_transport, "POST", "/dsh-agents/api/run", RUN_PAYLOAD)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="absent"),
        pytest.param({"run": {}}, id="absent-in-run"),
        pytest.param({"sessionId": ""}, id="empty-top-level"),
        pytest.param({"run": {"sessionId": ""}}, id="empty-nested"),
        pytest.param({"run": {"sessionId": ""}, "sessionId": ""}, id="both-empty"),
        pytest.param({"sessionId": None}, id="null-top-level"),
        pytest.param({"run": {"sessionId": None}}, id="null-nested"),
        pytest.param({"sessionId": 123}, id="non-string-top-level"),
        pytest.param({"run": {"sessionId": 123}}, id="non-string-nested"),
    ],
)
def test_run_member_rejects_missing_or_invalid_session(
    client, offline_transport, response
):
    offline_transport.body = json.dumps(response).encode("utf-8")

    with pytest.raises(HostUnavailable, match="returned no session id"):
        client.run_member(**RUN_ARGS)

    assert_request(offline_transport, "POST", "/dsh-agents/api/run", RUN_PAYLOAD)


@pytest.mark.parametrize("operation", ["state", "run_member"])
@pytest.mark.parametrize(
    "error, body, message",
    [
        pytest.param(
            urllib.error.HTTPError(BASE, 503, "Unavailable", {}, None),
            b"{}", "HTTP 503", id="http-error"),
        pytest.param(urllib.error.URLError("offline refusal"), b"{}",
                     "cannot reach the DSH Host", id="url-error"),
        pytest.param(OSError("offline socket failure"), b"{}",
                     "cannot reach the DSH Host", id="os-error"),
        pytest.param(None, b"{not JSON", "not JSON", id="invalid-json"),
        # Even a success-shaped session id cannot override an explicit refusal.
        pytest.param(None, b'{"ok":false,"code":"DENIED","sessionId":"unused"}',
                     "DENIED", id="ok-false-with-code"),
        pytest.param(None, b'{"ok":false,"run":{"sessionId":"unused"}}',
                     "refused", id="ok-false-without-code"),
    ],
)
def test_transport_and_response_failures_raise_host_unavailable(
    client, offline_transport, operation, error, body, message
):
    offline_transport.error = error
    offline_transport.body = body

    with pytest.raises(HostUnavailable, match=message):
        if operation == "state":
            client.state()
        else:
            client.run_member(**RUN_ARGS)

    if operation == "state":
        assert_request(offline_transport, "GET", "/dsh-agents/api/state")
    else:
        assert_request(offline_transport, "POST", "/dsh-agents/api/run", RUN_PAYLOAD)


@pytest.mark.parametrize("body", [b"", b"[]", b"null", b'"text"', b"42"])
def test_state_normalizes_empty_or_non_object_json(client, offline_transport, body):
    offline_transport.body = body

    assert client.state() == {}

    assert_request(offline_transport, "GET", "/dsh-agents/api/state")
