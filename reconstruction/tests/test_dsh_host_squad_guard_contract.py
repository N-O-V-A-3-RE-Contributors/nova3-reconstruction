"""Offline characterization of HostBackend._legacy_squad_run guards only.

The legacy squad path is dead code for ``HostBackend.run`` (which now routes to
``client.create_session``); this module calls the private method directly to
pin its guard order and its report-to-result mapping. Every profile, client,
state, team and report here is synthetic. No live Host, network, subprocess,
transcript, zstd executable, real clock or sleep is involved. Passing these
tests says nothing about the native bridge, a live DSH Host, or anything in the
game.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


# -- fixtures ---------------------------------------------------------------

FORBIDDEN_CALLS: list[str] = []
BACKENDS = None


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    """Fail closed on network/socket/subprocess entry points (CLI-test pattern)."""
    import socket
    import subprocess
    import urllib.request

    def make_guard(name):
        def guard(*args, **kwargs):
            FORBIDDEN_CALLS.append(name)
            pytest.fail(
                f"squad guard contract tests prohibit external I/O: {name}")
        return guard

    for module, names in (
        (urllib.request, ("urlopen",)),
        (urllib.request.OpenerDirector, ("open",)),
        (socket, ("create_connection", "getaddrinfo")),
        (socket.socket, ("connect", "connect_ex", "sendto", "send")),
        (subprocess, ("Popen", "run", "call", "check_call", "check_output",
                      "getoutput", "getstatusoutput")),
    ):
        for name in names:
            monkeypatch.setattr(
                module, name, make_guard(f"{module.__name__}.{name}"))
    monkeypatch.setattr("os.system", make_guard("os.system"))
    # Import after the guards, so even import-time I/O cannot reach a Host.
    global BACKENDS
    package = pytest.importorskip(
        "dsh_runner", reason="optional dsh_runner Python source unavailable")
    BACKENDS = getattr(package, "backends", None)
    if BACKENDS is None:
        pytest.skip("dsh_runner.backends unavailable")
    yield


@pytest.fixture
def host_api():
    return pytest.importorskip(
        "dsh_runner.host", reason="optional dsh_runner Python source unavailable")

WORKSPACE = "/synthetic/legacy-squad-contract/workspace"
SQUAD = "synthetic-squad"
WORKER_MEMBER = "worker"
TEAM_ID = "synthetic-team-id"
MEMBER_ID = "synthetic-member-id"
PROMPT = "synthetic first message: 'quoted' \\ path"
SESSION_ID = "synthetic-legacy-session-1"
POLL_S = 2.0
TIMEOUT_S = 30


@dataclass(frozen=True)
class Member:
    name: str
    preset: str = "standard"
    permission: str = "read-only"
    model: str = "synthetic/offline"
    effort: str = "high"


@dataclass(frozen=True)
class Execution:
    backend: str = "host"
    isolation: str = "shared-workspace"
    timeout_s: int = 1200
    max_steps: int = 240
    squad: str = SQUAD
    worker_member: str = ""


@dataclass(frozen=True)
class ProfileLike:
    """Profile-shaped synthetic object carrying only what the method reads."""

    name: str = "legacy-squad-contract"
    workspace: str = WORKSPACE
    execution: Execution = Execution()
    members: dict = None

    def __post_init__(self):
        if self.members is None:
            object.__setattr__(self, "members", {WORKER_MEMBER: Member(WORKER_MEMBER)})

    @property
    def worker(self):
        return self.members[WORKER_MEMBER]


class SyntheticClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RecordingClient:
    """Fake Host client: logs every call with its arguments, returns scripted values."""

    def __init__(self, *, state=None, state_error=None, team=None,
                 run_member_error=None, reports=()):
        self._state = state
        self._state_error = state_error
        self._team = team
        self._run_member_error = run_member_error
        self._reports = list(reports)
        self.calls: list[tuple[str, tuple, dict]] = []

    @property
    def method_names(self):
        return [name for name, _args, _kwargs in self.calls]

    def state(self):
        self.calls.append(("state", (), {}))
        if self._state_error is not None:
            raise self._state_error
        return self._state

    def team(self, name):
        self.calls.append(("team", (name,), {}))
        return self._team

    def workspace_id(self, path):
        self.calls.append(("workspace_id", (path,), {}))
        return next((item.get("id") for item in (self._state or {}).get("workspaces") or []
                     if item.get("path") == path), None)

    def run_member(self, **kwargs):
        self.calls.append(("run_member", (), dict(kwargs)))
        if self._run_member_error is not None:
            raise self._run_member_error
        return SESSION_ID

    def report(self, session_id):
        self.calls.append(("report", (session_id,), {}))
        assert self._reports, "unexpected report() call"
        return self._reports.pop(0)


def report_for(host_api, status, *, steps=0, tool_calls=0, error_code=None):
    return host_api.SessionReport(
        session_id=SESSION_ID, status=status, turn_kind=status,
        steps=steps, tool_calls=tool_calls, error_code=error_code)


def hijack_session_creation(client: RecordingClient) -> None:
    """Fail if the method ever reaches HostBackend.run's newer route."""
    def forbidden(*args, **kwargs):
        FORBIDDEN_CALLS.append("create_session")
        pytest.fail("_legacy_squad_run must not create a native session")

    client.create_session = forbidden  # type: ignore[attr-defined]


@pytest.fixture
def clock(host_api, monkeypatch):
    clock = SyntheticClock()
    # Replace the backend module's time binding only; no real clock is read.
    monkeypatch.setattr(BACKENDS, "time", SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep))
    return clock


@pytest.fixture
def log_path(tmp_path):
    path = Path(tmp_path) / "synthetic-legacy-squad.log"
    assert isinstance(path, Path)
    return path


def run_legacy(profile, client, log_path, *, timeout_s=TIMEOUT_S):
    backend = BACKENDS.HostBackend(profile, client=client, poll_s=POLL_S)
    assert backend.poll_s >= 1.0
    return backend._legacy_squad_run(
        prompt=PROMPT, cwd=Path(profile.workspace), log_path=log_path,
        timeout_s=timeout_s)


def assert_refused(client: RecordingClient, result):
    assert result.status == "unavailable"
    assert result.ok is False
    assert isinstance(result.detail, str) and result.detail.strip()
    assert result.detail != ""
    names = client.method_names
    assert "run_member" not in names
    assert "report" not in names


def registered_state(*, teams=None):
    return {"workspaces": [{"id": "other-workspace", "path": "/synthetic/other"},
                           {"id": "synthetic-workspace-id", "path": WORKSPACE}],
            "teams": list(teams or [])}


def matching_team():
    return {"id": TEAM_ID, "name": SQUAD,
            "members": [{"id": "other-member", "name": "reviewer"},
                        {"id": MEMBER_ID, "name": WORKER_MEMBER}]}


# -- the four refusals, in the documented order -----------------------------

def test_refusal_1_state_unavailable(clock, log_path, host_api):
    refusal = host_api.HostUnavailable("synthetic state refusal text")
    client = RecordingClient(state_error=refusal)
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_refused(client, result)
    assert result.detail == "synthetic state refusal text"
    assert result.session_id is None
    # state() raising short-circuits every later lookup.
    assert client.calls == [("state", (), {})]


def test_refusal_2_workspace_not_registered(clock, log_path, host_api):
    state = {"workspaces": [{"id": "w1", "path": "/synthetic/somewhere-else"}],
             "teams": [matching_team()]}
    client = RecordingClient(state=state, team=matching_team())
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_refused(client, result)
    assert WORKSPACE in result.detail
    assert result.session_id is None
    # Only the state lookup happened: no team() query follows.
    assert client.calls == [("state", (), {})]


@pytest.mark.parametrize(
    "team_value",
    [pytest.param(None, id="none"),
     pytest.param({}, id="empty-dict"),
     pytest.param([], id="empty-list")],
)
def test_refusal_3_squad_not_registered(clock, log_path, team_value, host_api):
    client = RecordingClient(state=registered_state(), team=team_value)
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_refused(client, result)
    assert SQUAD in result.detail
    assert result.session_id is None
    assert client.calls == [("state", (), {}), ("team", (SQUAD,), {})]


@pytest.mark.parametrize(
    "team",
    [
        pytest.param({"id": TEAM_ID, "name": SQUAD}, id="no-members-key"),
        pytest.param({"id": TEAM_ID, "name": SQUAD, "members": None}, id="null-members"),
        pytest.param({"id": TEAM_ID, "name": SQUAD, "members": []}, id="empty-members"),
        pytest.param({"id": TEAM_ID, "name": SQUAD, "members": [
            {"id": "m-other", "name": "reviewer"}]}, id="wrong-name"),
        pytest.param({"id": TEAM_ID, "name": SQUAD, "members": [
            {"id": "m-case", "name": WORKER_MEMBER.upper()}]}, id="name-case-differs"),
    ],
)
def test_refusal_4_worker_member_missing(clock, log_path, team, host_api):
    client = RecordingClient(state=registered_state(), team=team)
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_refused(client, result)
    assert WORKER_MEMBER in result.detail
    assert result.session_id is None
    assert client.calls == [("state", (), {}), ("team", (SQUAD,), {})]


# -- success: recorded run_member arguments and report mapping --------------

def assert_success_calls(client: RecordingClient):
    assert client.calls == [
        ("state", (), {}),
        ("team", (SQUAD,), {}),
        ("run_member", (), {"team_id": TEAM_ID, "member_id": MEMBER_ID,
                            "first_message": PROMPT}),
        ("report", (SESSION_ID,), {}),
    ]
    assert client.method_names.count("run_member") == 1


def test_completed_report_maps_to_completed(clock, log_path, host_api):
    client = RecordingClient(
        state=registered_state(), team=matching_team(),
        reports=[report_for(host_api, "completed", steps=17, tool_calls=29)],
    )
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_success_calls(client)
    assert result.status == "completed"
    assert result.ok is True
    assert result.backend == "host"
    assert result.session_id == SESSION_ID
    assert result.steps == 17
    assert result.tool_calls == 29
    assert result.visible_in_agents_tab is True
    assert result.detail == ""
    assert result.elapsed_s == 0.0
    # The report is scripted to be terminal: nothing polled, nothing slept.
    assert clock.sleeps == []


def test_success_uses_workspace_and_member_from_state_and_team(host_api):
    """A differently-shaped registered state still resolves by path then name."""
    profile = ProfileLike(
        workspace="/synthetic/alt-root",
        members={WORKER_MEMBER: Member(WORKER_MEMBER, preset="review")})
    team = {"id": "alt-team", "name": SQUAD, "members": [
        {"id": "alt-member", "name": WORKER_MEMBER}]}
    client = RecordingClient(
        state={"workspaces": [{"id": "alt-ws", "path": "/synthetic/alt-root"}],
               "teams": [team]},
        team=team,
        reports=[report_for(host_api, "completed", steps=3, tool_calls=4)],
    )
    hijack_session_creation(client)
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "synthetic.log"
        backend = BACKENDS.HostBackend(profile, client=client, poll_s=1.0)
        result = backend._legacy_squad_run(
            prompt=PROMPT, cwd=Path(profile.workspace), log_path=log,
            timeout_s=TIMEOUT_S)
    assert result.status == "completed"
    assert client.calls[2] == (
        "run_member", (),
        {"team_id": "alt-team", "member_id": "alt-member",
         "first_message": PROMPT})


def test_error_report_maps_to_error(clock, log_path, host_api):
    client = RecordingClient(
        state=registered_state(), team=matching_team(),
        reports=[report_for(host_api, "error", steps=5, tool_calls=8,
                            error_code="SYNTHETIC_ERROR")],
    )
    hijack_session_creation(client)

    result = run_legacy(ProfileLike(), client, log_path)

    assert_success_calls(client)
    assert result.status == "error"
    assert result.ok is False
    assert result.session_id == SESSION_ID
    assert result.steps == 5
    assert result.tool_calls == 8
    assert result.visible_in_agents_tab is True
    assert result.detail == "SYNTHETIC_ERROR"


def test_report_is_called_with_the_run_member_session_id(clock, log_path, host_api):
    client = RecordingClient(
        state=registered_state(), team=matching_team(),
        reports=[report_for(host_api, "completed")])
    hijack_session_creation(client)

    run_legacy(ProfileLike(), client, log_path)

    run_kwargs = next(kwargs for name, _a, kwargs in client.calls
                      if name == "run_member")
    report_args = next(args for name, args, _k in client.calls if name == "report")
    assert report_args == (SESSION_ID,)
    # The legacy path never consults workspace_id() itself (state() supplies it).
    assert "workspace_id" not in client.method_names
    assert set(run_kwargs) == {"team_id", "member_id", "first_message"}


def test_constructor_rejects_shared_workspace_without_squad(host_api):
    profile = ProfileLike(execution=Execution(squad=""))
    with pytest.raises(BACKENDS.BackendError):
        BACKENDS.HostBackend(profile, client=RecordingClient(), poll_s=1.0)
