"""Offline HostBackend adapter contracts, not live Agents or game equivalence.

All profiles and reports are synthetic. No Host client, transcript, subprocess,
model, network, zstd executable, or real clock/sleep is needed.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dsh_runner import backends, host, profile as profiles


SESSION_ID = "synthetic-session-219"
WORKSPACE = Path("/synthetic/host-polling-contract")
PROMPT = "synthetic polling task"
POLL_S = 2.0


class SyntheticClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RecordingClient:
    def __init__(self, clock, reports, *, creation_delay=0.0):
        self.clock = clock
        self.reports = list(reports)
        self.creation_delay = creation_delay
        self.creations = []
        self.report_calls = []

    def create_session(self, **kwargs):
        self.creations.append(kwargs)
        self.clock.now += self.creation_delay
        return SESSION_ID

    def report(self, session_id):
        self.report_calls.append((session_id, self.clock.now))
        assert self.reports, "unexpected polling after termination or deadline"
        return self.reports.pop(0)


@pytest.fixture
def synthetic_profile():
    return profiles.parse({
        "schema": profiles.SCHEMA,
        "name": "host-polling-contract",
        "repo": "synthetic/polling-contract",
        "workspace": str(WORKSPACE),
        "execution": {
            "backend": "host",
            "isolation": "per-task-checkout",
            "timeout_s": 300,
            "max_steps": 10,
        },
        "members": {"worker": {
            "preset": "standard",
            "permission": "read-only",
            "model": "synthetic/offline",
            "effort": "high",
        }},
        "routing": {},
        "safety": {},
    })


@pytest.fixture
def clock(monkeypatch):
    clock = SyntheticClock()
    # Replace the backend's time binding, not the process-wide time module.
    monkeypatch.setattr(backends, "time", SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep))
    return clock


def make_report(status, *, steps=1, tool_calls=2, error_code=None):
    return host.SessionReport(
        session_id=SESSION_ID, status=status, turn_kind=status,
        steps=steps, tool_calls=tool_calls, error_code=error_code)


def run_backend(synthetic_profile, client, *, timeout_s):
    backend = backends.HostBackend(synthetic_profile, client=client, poll_s=POLL_S)
    return backend.run(
        prompt=PROMPT, cwd=WORKSPACE, log_path=WORKSPACE / "unused.log",
        timeout_s=timeout_s)


def assert_single_creation(client):
    assert client.creations == [{
        "workspace_path": str(WORKSPACE),
        "prompt": PROMPT,
        "permission": "read-only",
        "model": "synthetic/offline",
        "effort": "high",
        "title": "host-polling-contract: host-polling-contract",
        "agent_preset": "standard",
    }]


@pytest.mark.parametrize("running_count", [1, 3], ids=["one-running", "three-running"])
@pytest.mark.parametrize(
    "terminal_status,error_code,expected_status",
    [
        ("completed", None, "completed"),
        ("error", "SYNTHETIC_ERROR", "error"),
        ("aborted", "SYNTHETIC_ABORT", "error"),
    ],
)
def test_running_to_terminal_preserves_result_and_stops_polling(
    synthetic_profile, clock, running_count, terminal_status, error_code,
    expected_status,
):
    reports = [make_report("running") for _ in range(running_count)]
    reports.append(make_report(
        terminal_status, steps=17, tool_calls=29, error_code=error_code))
    client = RecordingClient(clock, reports)

    result = run_backend(synthetic_profile, client, timeout_s=30)

    assert_single_creation(client)
    assert client.report_calls == [
        (SESSION_ID, 100.0 + index * POLL_S)
        for index in range(running_count + 1)
    ]
    assert client.reports == []
    # No sleep or report is allowed after the terminal response.
    assert clock.sleeps == [POLL_S] * running_count
    assert result.status == expected_status
    assert result.ok is (terminal_status == "completed")
    assert result.session_id == SESSION_ID
    assert result.steps == 17
    assert result.tool_calls == 29
    # RunResult exposes the report's error_code as detail, not a separate field.
    assert result.detail == (error_code or "")
    assert result.backend == "host"
    assert result.visible_in_agents_tab is True
    assert result.elapsed_s == running_count * POLL_S


@pytest.mark.parametrize(
    "timeout_s,creation_delay,running_count",
    [
        pytest.param(5, 5.0, 0, id="expires-before-first-report"),
        pytest.param(6, 0.0, 3, id="expires-after-running-reports"),
    ],
)
def test_deadline_expiry_preserves_session_and_is_not_success(
    synthetic_profile, clock, timeout_s, creation_delay, running_count,
):
    # Session creation can consume the deadline without any report being read.
    client = RecordingClient(
        clock, [make_report("running") for _ in range(running_count)],
        creation_delay=creation_delay)

    result = run_backend(synthetic_profile, client, timeout_s=timeout_s)

    assert_single_creation(client)
    assert client.report_calls == [
        (SESSION_ID, 100.0 + creation_delay + index * POLL_S)
        for index in range(running_count)
    ]
    assert client.reports == []
    assert clock.sleeps == [POLL_S] * running_count
    assert result.status == "timeout"
    assert result.ok is False
    assert result.session_id == SESSION_ID
    assert result.backend == "host"
    assert result.visible_in_agents_tab is True
    assert result.elapsed_s == timeout_s
    assert result.detail == "host session did not reach a terminal turn"
