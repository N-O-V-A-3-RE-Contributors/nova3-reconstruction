"""Offline discovery contracts, not YAML fallback or live-project integration."""

import json
import os
import socket
import subprocess

import pytest

from dsh_runner import profile as profiles, yaml_bridge


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Discovery must only read local JSON, regardless of filename extension."""
    def forbidden(*args, **kwargs):
        raise AssertionError("YAML bridge, network and process calls are forbidden")

    monkeypatch.setattr(yaml_bridge, "loads", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)


def profile_data(root, name):
    return {
        "schema": "dsh-runner-project/1",
        "name": name,
        "repo": f"fixture-owner/{name}",
        "workspace": str(root / "workspaces" / name),
        "execution": {"worker_member": f"agent-{name}"},
        "members": {
            f"agent-{name}": {
                "preset": f"preset-{name}",
                "permission": "read-only",
                "model": f"offline/{name}",
                "effort": "high",
                "brief": f"Local discovery fixture {name}",
            }
        },
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_discovery_orders_paths_by_pattern_group_and_preserves_settings(tmp_path):
    folder = tmp_path / "config" / "projects"
    # Deliberately neither global filename order nor profile-name order.
    # A .yml must sort before a .yaml here, not in a separate extension group.
    expected = [
        (folder / "b-first.yml", profile_data(tmp_path, "zulu")),
        (folder / "z-last.yaml", profile_data(tmp_path, "alpha")),
        (folder / "a-first.json", profile_data(tmp_path, "yankee")),
        (folder / "y-last.json", profile_data(tmp_path, "beta")),
    ]
    # Creation order also disagrees with discovery order.
    for path, data in reversed(expected):
        write_json(path, data)

    found = profiles.list_profiles(tmp_path)

    assert [profile.path for profile in found] == [str(path) for path, _ in expected]
    assert [profile.name for profile in found] == ["zulu", "alpha", "yankee", "beta"]
    for profile, (path, data) in zip(found, expected):
        assert profile.path == str(path)
        assert profile.repo == data["repo"]
        assert profile.workspace == data["workspace"]
        assert profile.execution.worker_member == data["execution"]["worker_member"]
        assert set(profile.members) == set(data["members"])
        for name, settings in data["members"].items():
            member = profile.members[name]
            assert member.name == name
            for field, value in settings.items():
                assert getattr(member, field) == value
        assert profile.worker is profile.members[data["execution"]["worker_member"]]


def test_discovery_ignores_unrelated_extensions_and_nested_files(tmp_path):
    folder = tmp_path / "config" / "projects"
    # Invalid schema makes accidental discovery fail rather than blend in.
    ignored = [
        "notes.txt", "settings.toml", "backup.yaml.bak", "backup.json.bak",
        "nested/hidden.yaml", "nested/hidden.yml", "nested/hidden.json",
    ]
    for name in ignored:
        write_json(folder / name, {"schema": "invalid/1"})
    assert profiles.list_profiles(tmp_path) == []

    visible = folder / "visible.json"
    write_json(visible, profile_data(tmp_path, "visible"))
    assert [profile.path for profile in profiles.list_profiles(tmp_path)] == [str(visible)]


@pytest.mark.parametrize("config_exists", [False, True], ids=["no-config", "no-projects"])
def test_discovery_returns_empty_when_projects_directory_is_missing(tmp_path, config_exists):
    if config_exists:
        (tmp_path / "config").mkdir()
    assert not (tmp_path / "config" / "projects").exists()
    assert profiles.list_profiles(tmp_path) == []


@pytest.mark.parametrize("extension", ["yaml", "yml", "json"])
def test_discovery_propagates_invalid_schema(tmp_path, extension):
    folder = tmp_path / "config" / "projects"
    write_json(folder / "a-valid.yaml", profile_data(tmp_path, "before"))
    invalid = profile_data(tmp_path, "invalid")
    invalid["schema"] = "unsupported-profile/99"
    write_json(folder / f"m-invalid.{extension}", invalid)
    write_json(folder / "z-valid.json", profile_data(tmp_path, "after"))

    with pytest.raises(profiles.ProfileError, match="schema must be"):
        profiles.list_profiles(tmp_path)
