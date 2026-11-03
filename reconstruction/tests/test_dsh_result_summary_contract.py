"""Offline serialization contracts for directly constructed public results.

Synthetic values only: no backend execution, profiles, Host readiness checks or
transcript reads. Path omission here characterizes one field, not a privacy or
game-equivalence guarantee. The dsh_runner package must be importable.
"""
from dataclasses import asdict
import json

import pytest

from dsh_runner.backends import RunResult
from dsh_runner.host import SessionReport


RUN_KEYS = {
    "status", "backend", "session_id", "steps", "tool_calls", "elapsed_s",
    "detail", "visible_in_agents_tab",
}
SESSION_KEYS = {
    "session_id", "status", "turn_kind", "error_code", "steps", "tool_calls",
    "last_event_age_s",
}


@pytest.mark.parametrize(
    "session_id,steps,tool_calls,visible",
    [
        (None, 0, 0, False),
        ("synthetic-run-007", 7, 13, True),
        ("synthetic-run-008", 0, 19, False),
        ("", 23, 0, True),
    ],
)
def test_run_summary_preserves_fields(session_id, steps, tool_calls, visible):
    result = RunResult(
        status="error", backend="synthetic-backend", session_id=session_id,
        steps=steps, tool_calls=tool_calls, elapsed_s=8.375,
        detail="synthetic detail", visible_in_agents_tab=visible,
    )
    before = asdict(result)
    expected = {
        "status": "error", "backend": "synthetic-backend",
        "session_id": session_id, "steps": steps, "tool_calls": tool_calls,
        "elapsed_s": 8.4, "detail": "synthetic detail",
        "visible_in_agents_tab": visible,
    }

    summary = result.summary()

    assert set(summary) == RUN_KEYS
    assert summary == expected
    assert summary["visible_in_agents_tab"] is visible
    assert json.loads(json.dumps(summary)) == expected
    assert asdict(result) == before


@pytest.mark.parametrize("detail_length", [399, 400, 401])
@pytest.mark.parametrize(
    "elapsed_s,expected_elapsed_s",
    [(0.0, 0.0), (12.34, 12.3), (12.36, 12.4), (1.25, 1.2), (1.75, 1.8)],
)
def test_run_summary_truncates_and_rounds_without_mutation(
    detail_length, elapsed_s, expected_elapsed_s,
):
    # Non-ASCII characters distinguish character truncation from byte truncation.
    detail = ("abc\u03bb" * 101)[:detail_length]
    result = RunResult(
        status="completed", backend="synthetic", session_id="synthetic-boundary",
        steps=17, tool_calls=29, elapsed_s=elapsed_s, detail=detail,
        visible_in_agents_tab=True,
    )
    before = asdict(result)
    expected_detail = detail if detail_length <= 400 else detail[:-1]

    summary = result.summary()

    assert set(summary) == RUN_KEYS
    assert summary == {
        **before, "detail": expected_detail, "elapsed_s": expected_elapsed_s,
    }
    assert len(summary["detail"]) == min(detail_length, 400)
    assert asdict(result) == before
    assert result.summary() == summary
    assert result.detail == detail
    assert result.elapsed_s == elapsed_s


@pytest.mark.parametrize(
    "turn_kind,error_code,steps,tool_calls,last_event_age_s",
    [
        (None, None, 0, 0, None),
        ("synthetic-turn", "synthetic-error", 11, 31, 6.789),
        ("", "", 0, 37, 0.0),
    ],
)
def test_session_summary_preserves_observations_but_omits_path(
    turn_kind, error_code, steps, tool_calls, last_event_age_s,
):
    path_sentinel = "/synthetic/DO-NOT-SERIALIZE/session-sentinel.jsonl"
    report = SessionReport(
        session_id="synthetic-session-009", status="running",
        turn_kind=turn_kind, error_code=error_code, steps=steps,
        tool_calls=tool_calls, last_event_age_s=last_event_age_s,
        path=path_sentinel,
    )
    before = asdict(report)
    expected = {
        "session_id": "synthetic-session-009", "status": "running",
        "turn_kind": turn_kind, "error_code": error_code, "steps": steps,
        "tool_calls": tool_calls, "last_event_age_s": last_event_age_s,
    }

    summary = report.summary()
    serialized = json.dumps(summary)

    assert set(summary) == SESSION_KEYS
    assert summary == expected
    assert "path" not in summary
    assert path_sentinel not in serialized
    assert json.loads(serialized) == expected
    assert asdict(report) == before
    assert report.path == path_sentinel


@pytest.mark.parametrize("result_type", [RunResult, SessionReport])
def test_summary_defaults(result_type):
    if result_type is RunResult:
        result = RunResult(status="completed", backend="synthetic")
        expected = {
            "status": "completed", "backend": "synthetic", "session_id": None,
            "steps": 0, "tool_calls": 0, "elapsed_s": 0.0, "detail": "",
            "visible_in_agents_tab": False,
        }
    else:
        result = SessionReport(session_id="synthetic-default", status="completed")
        expected = {
            "session_id": "synthetic-default", "status": "completed",
            "turn_kind": None, "error_code": None, "steps": 0,
            "tool_calls": 0, "last_event_age_s": None,
        }

    assert result.summary() == expected


@pytest.mark.parametrize("result_type", [RunResult, SessionReport])
@pytest.mark.parametrize(
    "status,expected_ok",
    [
        ("completed", True),
        ("error", False),
        ("running", False),
        ("missing", False),
        ("unknown-synthetic-status", False),
        ("timeout", False),
        ("cancelled", False),
        ("unavailable", False),
        ("aborted", False),
        ("", False),
        ("Completed", False),
        ("completed ", False),
    ],
)
def test_ok_is_true_only_for_exact_completed_status(result_type, status, expected_ok):
    if result_type is RunResult:
        result = RunResult(status=status, backend="synthetic")
    else:
        result = SessionReport(session_id="synthetic-status", status=status)

    assert result.ok is expected_ok
    assert result.summary()["status"] == status
