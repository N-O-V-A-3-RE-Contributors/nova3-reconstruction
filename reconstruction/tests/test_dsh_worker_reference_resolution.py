"""Offline parser contracts only, not execution identity or game equivalence.

All inputs are synthetic mappings passed directly to profile.parse: no files,
YAML parser, running Host, proprietary data or external services are needed.
Individual member-setting validation is deliberately outside this module.
"""

import os
import socket
import subprocess
import sys
from itertools import permutations

import pytest

from dsh_runner import profile


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Fail closed if parsing tries networking, processes or YAML loading."""
    def forbidden(*args, **kwargs):
        raise AssertionError("worker resolution must stay offline and in-process")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setitem(sys.modules, "dsh_runner.yaml_bridge", None)


def synthetic_profile(member_names):
    return {
        "schema": "dsh-runner-project/1",
        "name": "worker-reference-test",
        "repo": "synthetic/project",
        "workspace": "/synthetic/workspace",
        "execution": {},
        "members": {name: {} for name in member_names},
    }


@pytest.mark.parametrize("member_names", [("worker", "reviewer"),
                                         ("reviewer", "worker")])
def test_unresolved_explicit_reference_does_not_fall_back_to_worker(member_names):
    data = synthetic_profile(member_names)
    data["execution"]["worker_member"] = "missing"

    with pytest.raises(profile.ProfileError,
                       match=r"execution\.worker_member names no member"):
        profile.parse(data)


@pytest.mark.parametrize("member_names", list(permutations(
    ("planner", "worker", "reviewer"))))
def test_literal_worker_wins_in_every_insertion_position(member_names):
    data = synthetic_profile(member_names)
    assert "worker_member" not in data["execution"]

    parsed = profile.parse(data)

    assert tuple(parsed.members) == member_names
    assert parsed.worker.name == "worker"
    assert parsed.worker is parsed.members["worker"]


@pytest.mark.parametrize("member_names", [("planner", "reviewer"),
                                         ("reviewer", "planner")])
def test_multiple_non_worker_members_require_explicit_selection(member_names):
    data = synthetic_profile(member_names)

    with pytest.raises(profile.ProfileError,
                       match=r"set execution\.worker_member"):
        profile.parse(data)

    # The same otherwise-valid mapping is accepted when either member is chosen.
    for selected in member_names:
        data["execution"]["worker_member"] = selected
        parsed = profile.parse(data)
        assert parsed.worker.name == selected
        assert parsed.worker is parsed.members[selected]
