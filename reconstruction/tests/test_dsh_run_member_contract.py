"""Offline characterization of HostClient.run_member's API boundary only.

The optional dsh_runner Python source must be importable; otherwise each test
explicitly skips. No installed/running Host, private inputs, or session files are
needed. Passing these tests says nothing about live runner readiness or game
behaviour/equivalence.
"""
from pathlib import Path
import os
import socket
import subprocess
import urllib.request

import pytest


TEAM_ID = "  synthetic/team:α  "
MEMBER_ID = " synthetic/member:β "
FIRST_MESSAGE = "\n  Synthetic message: 'quoted' \\ path\nUnicode: γ\t  "
EXPECTED_CALL = (
    "/dsh-agents/api/run",
    {
        "teamId": TEAM_ID,
        "memberId": MEMBER_ID,
        "firstMessage": FIRST_MESSAGE,
    },
)


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    """Fail closed if the subject bypasses the recording transport fake."""
    def forbidden(*args, **kwargs):
        pytest.fail("run_member contract tests prohibit network/subprocess use")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)


@pytest.fixture
def host_api():
    return pytest.importorskip(
        "dsh_runner.host", reason="optional dsh_runner Python source unavailable"
    )


class RecordingCall:
    """Synthetic transport: record each call, then return or raise verbatim."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, path, payload=None):
        self.calls.append((path, payload))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def make_client(host_api, monkeypatch):
    def make(response=None, error=None):
        client = host_api.HostClient(
            base="http://synthetic-host.invalid",
            home=Path("synthetic-unused-host-home"),
        )
        recording = RecordingCall(response=response, error=error)
        monkeypatch.setattr(client, "_call", recording)
        return client, recording

    return make


def launch(client):
    return client.run_member(
        team_id=TEAM_ID, member_id=MEMBER_ID, first_message=FIRST_MESSAGE
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(
            {"run": {"sessionId": "nested-id"}, "sessionId": "top-id"},
            "nested-id", id="nested-wins",
        ),
        pytest.param({"run": {"sessionId": "nested-id"}}, "nested-id",
                     id="nested-only"),
        pytest.param({"sessionId": "top-id"}, "top-id", id="missing-run"),
        pytest.param({"run": {}, "sessionId": "top-id"}, "top-id",
                     id="missing-nested-id"),
        pytest.param({"run": None, "sessionId": "top-id"}, "top-id",
                     id="null-run"),
        *[
            pytest.param(
                {"run": {"sessionId": value}, "sessionId": "top-id"},
                "top-id", id=f"falsey-nested-{label}",
            )
            for label, value in (
                ("empty", ""), ("null", None), ("false", False),
                ("zero", 0), ("list", []), ("dict", {}),
            )
        ],
        pytest.param({"run": {"sessionId": " \t "}, "sessionId": "top-id"},
                     " \t ", id="nonempty-is-not-trimmed"),
    ],
)
def test_payload_and_session_id_selection(make_client, response, expected):
    client, recording = make_client(response=response)

    assert launch(client) == expected
    # Exact equality checks the endpoint, field names, unchanged values, absence
    # of extra fields, and that only one request was made.
    assert recording.calls == [EXPECTED_CALL]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="missing-both"),
        pytest.param({"run": {}}, id="missing-nested"),
        pytest.param({"run": {"sessionId": ""}}, id="empty-nested"),
        pytest.param({"sessionId": ""}, id="empty-top"),
        pytest.param({"run": {"sessionId": ""}, "sessionId": ""},
                     id="empty-both"),
        pytest.param({"run": {"sessionId": None}, "sessionId": None},
                     id="null-both"),
        pytest.param({"sessionId": False}, id="false-top"),
        pytest.param({"sessionId": 0}, id="zero-top"),
    ],
)
def test_missing_or_empty_selected_id_is_unavailable(host_api, make_client, response):
    client, recording = make_client(response=response)

    with pytest.raises(host_api.HostUnavailable):
        launch(client)

    assert recording.calls == [EXPECTED_CALL]


@pytest.mark.parametrize("invalid", [True, 7, 1.5, ["id"], {"id": "value"}],
                         ids=["bool", "int", "float", "list", "dict"])
@pytest.mark.parametrize("location", ["top", "nested"])
def test_truthy_nonstring_selected_id_is_unavailable(
    host_api, make_client, invalid, location
):
    if location == "nested":
        # Selection precedes validation: an invalid truthy nested ID must NOT
        # fall back to a perfectly valid top-level string.
        response = {"run": {"sessionId": invalid}, "sessionId": "valid-top-id"}
    else:
        response = {"run": {"sessionId": ""}, "sessionId": invalid}
    client, recording = make_client(response=response)

    with pytest.raises(host_api.HostUnavailable):
        launch(client)

    assert recording.calls == [EXPECTED_CALL]


def test_transport_unavailable_propagates_without_retry(host_api, make_client):
    original = host_api.HostUnavailable("synthetic transport refusal")
    client, recording = make_client(error=original)

    with pytest.raises(host_api.HostUnavailable) as caught:
        launch(client)

    assert caught.value is original
    assert recording.calls == [EXPECTED_CALL]
