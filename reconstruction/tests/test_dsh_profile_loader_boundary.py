"""Profile text/file loader contracts, not runner or game-equivalence evidence.

Use the locally available dsh_runner package; skip explicitly if it is absent.
YAML parsing internals and directory discovery are intentionally out of scope.
All profile data is synthetic, and no network or child process is permitted.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import urllib.request

import pytest

pytest.importorskip("dsh_runner", reason="local dsh_runner package is required")
from dsh_runner import profile, yaml_bridge


@pytest.fixture(autouse=True)
def forbid_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("profile loader tests must not use network or subprocesses")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "popen", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)

    def unexpected_yaml(text):
        pytest.fail("JSON must bypass the YAML bridge")

    # No test can accidentally invoke real YAML parsing or its fallback tools.
    monkeypatch.setattr(yaml_bridge, "loads", unexpected_yaml)


@pytest.fixture
def profile_data():
    return {
        "schema": "dsh-runner-project/1",
        "name": "loader-test",
        "repo": "synthetic/loader-test",
        "workspace": "/synthetic/workspace",
        "members": {"worker": {"brief": "Synthetic café profile"}},
    }


def test_padded_json_bypasses_yaml_and_preserves_path(profile_data):
    supplied_path = "synthetic/unchanged.profile"
    loaded = profile.loads(" \t\n" + json.dumps(profile_data) + "\r\n ",
                           path=supplied_path)

    assert isinstance(loaded, profile.Profile)
    assert loaded.path == supplied_path
    assert loaded.raw == profile_data
    assert loaded.name == "loader-test"


@pytest.mark.parametrize("text", ["name: synthetic", "[not-an-object]", ""])
def test_non_json_is_trimmed_before_bridge_and_validated(
        monkeypatch, profile_data, text):
    calls = []
    profile_data["name"] = "  loader-test  "

    def fake_loads(received):
        calls.append(received)
        return profile_data

    monkeypatch.setattr(yaml_bridge, "loads", fake_loads)
    loaded = profile.loads(" \t\n" + text + "\r\n ", path="synthetic.yml")

    assert calls == [text]
    assert isinstance(loaded, profile.Profile)
    assert loaded.name == "loader-test"  # Real profile validation normalizes it.
    assert loaded.raw == profile_data
    assert loaded.path == "synthetic.yml"


def test_bridge_mapping_cannot_bypass_profile_validation(monkeypatch, profile_data):
    calls = []
    profile_data["workspace"] = "relative/workspace"

    def fake_loads(text):
        calls.append(text)
        return profile_data

    monkeypatch.setattr(yaml_bridge, "loads", fake_loads)
    with pytest.raises(profile.ProfileError) as caught:
        profile.loads(" \nname: synthetic\t ")

    assert calls == ["name: synthetic"]
    assert str(caught.value) == "workspace must be an absolute path"


@pytest.mark.parametrize("text", ["{", "{not valid JSON}"])
def test_malformed_brace_prefixed_json_does_not_try_yaml(text):
    with pytest.raises(json.JSONDecodeError):
        profile.loads(" \n" + text + "\t ")


def test_yaml_unavailable_becomes_profile_error(monkeypatch):
    unavailable = yaml_bridge.YamlUnavailable("synthetic parser unavailable")
    calls = []

    def fake_loads(text):
        calls.append(text)
        raise unavailable

    monkeypatch.setattr(yaml_bridge, "loads", fake_loads)
    with pytest.raises(profile.ProfileError) as caught:
        profile.loads(" \nname: synthetic\t ")

    assert calls == ["name: synthetic"]
    assert str(caught.value) == (
        "cannot parse YAML profile: synthetic parser unavailable"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("filename", ["profile.yaml", "profile.txt", "profile"])
def test_load_profile_reads_utf8_json_regardless_of_extension(
        tmp_path, profile_data, filename):
    path = tmp_path / filename
    path.write_text(" \n" + json.dumps(profile_data, ensure_ascii=False) + "\n ",
                    encoding="utf-8")

    loaded = profile.load_profile(path)

    assert loaded.path == str(path)
    assert loaded.raw == profile_data
    assert loaded.members["worker"].brief == "Synthetic café profile"


def test_load_profile_wraps_read_oserror_with_original_cause(monkeypatch, tmp_path):
    path = tmp_path / "unreadable.profile"
    failure = OSError("synthetic read failure")
    calls = []

    def fail_read(self, *, encoding):
        calls.append((self, encoding))
        raise failure

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(profile.ProfileError) as caught:
        profile.load_profile(path)

    assert calls == [(path, "utf-8")]
    assert str(caught.value) == f"cannot read profile {path}"
    assert caught.value.__cause__ is failure
