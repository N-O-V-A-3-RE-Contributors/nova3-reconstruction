"""Offline contracts for the optional DSH YAML fallback, not Node's parser.

All parser results are synthetic; neither PyYAML, Node nor a DSH runtime is
needed. If the optional dsh_runner Python source is absent, skip explicitly.
"""
from __future__ import annotations

import builtins
import importlib
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


DOCUMENT = "# Preserve the original YAML, including Unicode and whitespace.\nname: café\nitems:\n  - one\n\n"
ANCHOR = Path("/synthetic/dsh/runtime/package.json")


@pytest.fixture
def fallback(monkeypatch):
    """Force ImportError even if yaml is installed or already imported."""
    def forbidden(*args, **kwargs):
        pytest.fail("YAML fallback attempted a real subprocess or network access")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    try:
        bridge = importlib.import_module("dsh_runner.yaml_bridge")
    except ModuleNotFoundError as exc:
        if exc.name not in {"dsh_runner", "dsh_runner.yaml_bridge"}:
            raise
        pytest.skip("optional dsh_runner.yaml_bridge source is not available")

    original_import = builtins.__import__
    yaml_imports = []

    def without_yaml(name, *args, **kwargs):
        if name == "yaml" or name.startswith("yaml."):
            yaml_imports.append(name)
            raise ImportError("PyYAML deliberately unavailable for this contract")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_yaml)
    anchor = Mock(return_value=ANCHOR)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr=""))
    monkeypatch.setattr(bridge, "_runtime_anchor", anchor)
    monkeypatch.setattr(bridge.subprocess, "run", run)
    yield SimpleNamespace(bridge=bridge, anchor=anchor, run=run)
    assert yaml_imports == ["yaml"]
    anchor.assert_called_once_with()


def assert_node_call(fallback):
    fallback.run.assert_called_once_with(
        ["node", "-e", fallback.bridge.NODE_SCRIPT, str(ANCHOR)],
        input=DOCUMENT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        pytest.param('{"name": "café", "items": [1, null], "nested": {"ok": true}}',
                     {"name": "café", "items": [1, None], "nested": {"ok": True}},
                     id="mapping"),
        pytest.param("{}", {}, id="empty-mapping"),
        pytest.param('[{"name": "not-a-root-mapping"}]', {}, id="list"),
        pytest.param("[]", {}, id="empty-list"),
        pytest.param('"scalar"', {}, id="string"),
        pytest.param("42", {}, id="number"),
        pytest.param("true", {}, id="true"),
        pytest.param("false", {}, id="false"),
        pytest.param("null", {}, id="null"),
    ],
)
def test_successful_fallback_normalizes_root(fallback, stdout, expected):
    fallback.run.return_value = subprocess.CompletedProcess(
        [], 0, stdout=stdout, stderr="synthetic diagnostic"
    )
    result = fallback.bridge.loads(DOCUMENT)
    assert isinstance(result, dict)
    assert result == expected
    assert_node_call(fallback)


def test_absent_runtime_never_starts_node(fallback):
    fallback.anchor.return_value = None
    with pytest.raises(fallback.bridge.YamlUnavailable,
                       match="^no PyYAML and no DSH runtime YAML parser$"):
        fallback.bridge.loads(DOCUMENT)
    fallback.run.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(OSError("synthetic Node launch failure"), id="os-error"),
        pytest.param(subprocess.TimeoutExpired(["node"], 15), id="timeout"),
    ],
)
def test_subprocess_failures_become_yaml_unavailable(fallback, error):
    fallback.run.side_effect = error
    with pytest.raises(fallback.bridge.YamlUnavailable) as caught:
        fallback.bridge.loads(DOCUMENT)
    assert str(caught.value) == str(error)
    assert caught.value.__suppress_context__
    assert_node_call(fallback)


def test_nonzero_exit_rejects_even_valid_json(fallback):
    fallback.run.return_value = subprocess.CompletedProcess(
        [], 1, stdout='{"otherwise": "valid"}', stderr="synthetic parser rejection"
    )
    with pytest.raises(fallback.bridge.YamlUnavailable,
                       match="^the DSH YAML parser rejected this document$"):
        fallback.bridge.loads(DOCUMENT)
    assert_node_call(fallback)


@pytest.mark.parametrize("stdout", ["", "not JSON", '{"truncated":'],
                         ids=["empty", "invalid", "truncated"])
def test_invalid_json_becomes_yaml_unavailable(fallback, stdout):
    fallback.run.return_value = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    with pytest.raises(fallback.bridge.YamlUnavailable,
                       match="^the DSH YAML parser returned no data$") as caught:
        fallback.bridge.loads(DOCUMENT)
    assert caught.value.__suppress_context__
    assert_node_call(fallback)
