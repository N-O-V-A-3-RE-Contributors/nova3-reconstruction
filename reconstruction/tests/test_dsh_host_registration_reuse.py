"""Offline registration contracts, not transport, reconciliation or game equivalence."""

from copy import deepcopy
from pathlib import Path
import os
import socket
import subprocess
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Registration tests must not use network or subprocesses")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)


class RecordingHost:
    """Only registration endpoints exist; creations become visible in later state."""

    def __init__(self):
        self.teams = []
        self.calls = []
        self.forwarded_members = []
        self.response_team = None

    def __call__(self, path, payload=None):
        self.calls.append((path, deepcopy(payload)))
        if path == "/dsh-agents/api/state":
            assert payload is None
            return {"teams": deepcopy(self.teams)}
        if path == "/dsh-agents/api/team":
            team = {**deepcopy(payload["team"]), "id": "created-team", "members": []}
            self.teams.append(team)
        elif path == "/dsh-agents/api/member":
            self.forwarded_members.append(payload["member"])
            team = next(team for team in self.teams if team["id"] == payload["teamId"])
            team.setdefault("members", []).append(
                {**deepcopy(payload["member"]), "id": "created-member"}
            )
        else:
            raise AssertionError(f"Unexpected Host endpoint: {path}")
        self.response_team = deepcopy(team)
        return {"ok": True, "team": self.response_team}


@pytest.fixture
def registration(monkeypatch, forbid_external_execution):
    # Import after the guards, with no dependency on a live Host or provider.
    from dsh_runner.host import HostClient

    fake = RecordingHost()

    def call(client, path, payload=None):
        return fake(path, payload)

    monkeypatch.setattr(HostClient, "_call", call)
    return HostClient(home=Path("/synthetic/dsh-home")), fake


def test_existing_team_is_reused_by_name_without_reconciling_metadata(registration):
    client, fake = registration
    existing = {
        "id": "selected-team", "name": "Synthetic squad", "workspaceId": "old-workspace",
        "description": "Stored description", "leadPreset": "stored-preset", "order": 7,
        "members": [],
    }
    fake.teams = [{"id": "unrelated", "name": "Other squad"}, deepcopy(existing)]
    before = deepcopy(fake.teams)

    result = client.ensure_team(
        "Synthetic squad", workspace_id="new-workspace",
        description="Requested description", lead_preset="requested-preset",
    )

    assert result == existing
    assert fake.teams == before
    assert fake.calls == [("/dsh-agents/api/state", None)]


@pytest.mark.parametrize(
    "options, description, preset",
    [({}, "", "standard"),
     ({"description": "Synthetic brief", "lead_preset": "synthetic-lead"},
      "Synthetic brief", "synthetic-lead")],
    ids=["defaults", "explicit-metadata"],
)
def test_missing_team_creation_payload_and_repeat_reuse(
    registration, options, description, preset
):
    client, fake = registration
    fake.teams = [{"id": "unrelated", "name": "Other squad", "members": []}]
    expected_payload = {"team": {
        "name": "Synthetic squad", "description": description,
        "workspaceId": "synthetic-workspace", "leadPreset": preset, "order": 0,
    }}

    created = client.ensure_team(
        "Synthetic squad", workspace_id="synthetic-workspace", **options
    )

    assert created is fake.response_team
    assert created == {**expected_payload["team"], "id": "created-team", "members": []}
    assert fake.calls == [
        ("/dsh-agents/api/state", None),
        ("/dsh-agents/api/team", expected_payload),
    ]
    assert client.ensure_team(
        "Synthetic squad", workspace_id="synthetic-workspace", **options
    ) == created
    assert fake.calls == [
        ("/dsh-agents/api/state", None),
        ("/dsh-agents/api/team", expected_payload),
        ("/dsh-agents/api/state", None),
    ]


def test_matching_member_is_reused_only_from_selected_team(registration):
    client, fake = registration
    foreign = {"id": "foreign-member", "name": "worker", "model": "synthetic/foreign"}
    existing = {"id": "selected-member", "name": "worker", "model": "synthetic/stored"}
    fake.teams = [
        {"id": "other-team", "name": "Other squad", "members": [foreign]},
        {"id": "selected-team", "name": "Synthetic squad", "members": [existing]},
    ]
    requested = {"name": "worker", "model": "synthetic/requested", "brief": "New brief"}
    before = deepcopy(fake.teams)
    requested_before = deepcopy(requested)

    assert client.ensure_member("selected-team", requested) == existing
    assert fake.teams == before
    assert requested == requested_before
    assert fake.calls == [("/dsh-agents/api/state", None)]


@pytest.mark.parametrize("foreign_name", ["other-worker", "worker"],
                         ids=["missing-name", "same-name-in-other-team"])
def test_member_creation_preserves_mapping_and_team_scope_then_reuses(
    registration, foreign_name
):
    client, fake = registration
    foreign_team = {
        "id": "other-team", "name": "Other squad",
        "members": [{"id": "foreign-member", "name": foreign_name}],
    }
    selected_team = {
        "id": "selected-team", "name": "Synthetic squad",
        "members": [{"id": "existing-reviewer", "name": "reviewer"}],
    }
    fake.teams = [deepcopy(foreign_team), deepcopy(selected_team)]
    member = {
        "name": "worker", "preset": "synthetic-preset", "permission": "workspace-write",
        "model": "synthetic/model", "reasoningEffort": "high", "brief": "Synthetic task",
        "custom": {"labels": ["offline", "registration"], "enabled": False},
    }
    before = deepcopy(member)
    expected_payload = {"teamId": "selected-team", "member": before}

    created = client.ensure_member("selected-team", member)

    assert created is fake.response_team
    # The creation path returns the response's team, not its added member.
    assert created == {**selected_team, "members": [
        *selected_team["members"], {**before, "id": "created-member"},
    ]}
    assert fake.forwarded_members == [before]
    assert fake.forwarded_members[0] is member
    assert member == before
    assert fake.teams[0] == foreign_team
    assert fake.calls == [
        ("/dsh-agents/api/state", None),
        ("/dsh-agents/api/member", expected_payload),
    ]

    assert client.ensure_member("selected-team", member) == {
        **before, "id": "created-member",
    }
    assert len(fake.forwarded_members) == 1
    assert member == before
    assert fake.calls == [
        ("/dsh-agents/api/state", None),
        ("/dsh-agents/api/member", expected_payload),
        ("/dsh-agents/api/state", None),
    ]
