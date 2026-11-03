"""Offline HeadlessBackend adapter contracts, not game-equivalence evidence.

The optional dsh_runner package must be importable; no runtime discovery, model
launch, subprocess, network request or proprietary input is used by these tests.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

backends = pytest.importorskip(
    "dsh_runner.backends", reason="optional dsh_runner package is not installed"
)


@pytest.fixture
def adapter(monkeypatch):
    def forbid_default_runtime(self):
        pytest.fail("the adapter must use only the injected synthetic runtime")

    monkeypatch.setattr(
        backends.HeadlessBackend, "_default_runner", forbid_default_runtime
    )
    runtime = SimpleNamespace(run=Mock())
    # HeadlessBackend forwards the profile without inspecting it.
    profile = object()
    backend = backends.HeadlessBackend(profile, runner=runtime)
    arguments = dict(
        prompt="synthetic adapter contract",
        cwd=Path("synthetic-checkout"),
        log_path=Path("synthetic-checkout/unused.log"),
        timeout_s=17,
    )
    return SimpleNamespace(
        backend=backend, runtime=runtime, profile=profile, arguments=arguments
    )


@pytest.mark.parametrize(
    "provider_status, expected_status, expected_ok",
    [
        ("COMPLETED", "completed", True),
        ("SESSION_TIMEOUT", "timeout", False),
        ("CANCELLED", "cancelled", False),
        ("STEP_LIMIT", "error", False),
        ("WAITING_CREDITS", "unavailable", False),
        ("AUTH", "unavailable", False),
        ("UNKNOWN_PROVIDER_STATUS", "error", False),
    ],
)
def test_provider_status_mapping(adapter, provider_status, expected_status, expected_ok):
    adapter.runtime.run.return_value = SimpleNamespace(status=provider_status)

    result = adapter.backend.run(**adapter.arguments)

    assert isinstance(result, backends.RunResult)
    assert result.status == expected_status
    assert result.ok is expected_ok
    assert result.backend == "headless"
    assert result.visible_in_agents_tab is False
    adapter.runtime.run.assert_called_once_with(
        profile=adapter.profile, **adapter.arguments
    )


@pytest.mark.parametrize(
    "attributes, expected",
    [
        pytest.param(
            dict(session_id="session-native", steps=3, tool_calls=4,
                 elapsed_s=1.5, error_code="QUOTA"),
            ("session-native", 3, 4, 1.5, "QUOTA"),
            id="native-values",
        ),
        pytest.param(
            dict(session_id="session-converted", steps="7", tool_calls="9",
                 elapsed_s="2.25", error_code=503),
            ("session-converted", 7, 9, 2.25, "503"),
            id="numeric-and-detail-conversion",
        ),
        pytest.param(
            dict(session_id=None, steps=None, tool_calls=None,
                 elapsed_s=None, error_code=None),
            (None, 0, 0, 0.0, ""),
            id="explicit-none",
        ),
        pytest.param(
            dict(session_id="", steps="", tool_calls="",
                 elapsed_s="", error_code=0),
            ("", 0, 0, 0.0, ""),
            id="falsey-values",
        ),
        pytest.param({}, (None, 0, 0, 0.0, ""), id="missing-optional-attributes"),
    ],
)
def test_outcome_field_conversion(adapter, attributes, expected):
    adapter.runtime.run.return_value = SimpleNamespace(status="COMPLETED", **attributes)

    result = adapter.backend.run(**adapter.arguments)

    assert (result.session_id, result.steps, result.tool_calls,
            result.elapsed_s, result.detail) == expected
    assert type(result.steps) is int
    assert type(result.tool_calls) is int
    assert type(result.elapsed_s) is float
    assert type(result.detail) is str


def test_missing_status_defaults_to_error(adapter):
    adapter.runtime.run.return_value = SimpleNamespace()

    result = adapter.backend.run(**adapter.arguments)

    assert result.status == "error"
    assert result.ok is False
    assert (result.session_id, result.steps, result.tool_calls,
            result.elapsed_s, result.detail) == (None, 0, 0, 0.0, "")


def test_existing_run_result_is_returned_by_identity(adapter):
    original = backends.RunResult(
        "completed", "synthetic-backend", session_id="existing-session",
        steps=11, tool_calls=13, elapsed_s=3.75, detail="preserve me",
        visible_in_agents_tab=True,
    )
    adapter.runtime.run.return_value = original

    assert adapter.backend.run(**adapter.arguments) is original


def test_injected_backend_error_propagates_by_identity(adapter):
    original = backends.BackendError("synthetic backend failure")
    adapter.runtime.run.side_effect = original

    with pytest.raises(backends.BackendError) as caught:
        adapter.backend.run(**adapter.arguments)

    assert caught.value is original


@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError])
def test_ordinary_runtime_exception_is_wrapped_with_original_message(adapter, exception_type):
    original = exception_type("synthetic runtime failure: detail\nsecond line")
    adapter.runtime.run.side_effect = original

    with pytest.raises(backends.BackendError) as caught:
        adapter.backend.run(**adapter.arguments)

    assert type(caught.value) is backends.BackendError
    assert caught.value is not original
    assert str(caught.value) == str(original)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
