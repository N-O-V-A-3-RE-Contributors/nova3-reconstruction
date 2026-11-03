"""Execution-budget validation only; no runner or game behaviour is exercised."""

from copy import deepcopy

import pytest

from dsh_runner import profile


def minimal_profile():
    return {
        "schema": "dsh-runner-project/1",
        "name": "budget-test",
        "repo": "example/project",
        "workspace": "/profile-test",
        "members": {"worker": {}},
        "execution": {},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_s", 300),
        ("timeout_s", 3600),
        ("max_steps", 10),
        ("max_steps", 400),
    ],
)
def test_execution_budget_accepts_inclusive_boundaries(field, value):
    data = minimal_profile()
    data["execution"][field] = value
    before = deepcopy(data)

    parsed = profile.parse(data)

    assert getattr(parsed.execution, field) == value
    assert data == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_s", 299),
        ("timeout_s", 3601),
        ("max_steps", 9),
        ("max_steps", 401),
    ],
)
def test_execution_budget_rejects_outside_boundaries(field, value):
    data = minimal_profile()
    data["execution"][field] = value
    before = deepcopy(data)

    with pytest.raises(profile.ProfileError, match=rf"execution\.{field}\b"):
        profile.parse(data)

    assert data == before


@pytest.mark.parametrize("field", ["timeout_s", "max_steps"])
@pytest.mark.parametrize(
    "value",
    ["300", 300.0, 300.5, None, True, False],
    ids=["numeric-string", "integral-float", "fractional-float", "null", "true", "false"],
)
def test_execution_budget_rejects_non_integers(field, value):
    data = minimal_profile()
    data["execution"][field] = value
    before = deepcopy(data)

    with pytest.raises(profile.ProfileError, match=rf"execution\.{field}\b"):
        profile.parse(data)

    assert data == before


@pytest.mark.parametrize(
    "execution",
    [None, {}, {"timeout_s": 1200}, {"max_steps": 240}],
    ids=["no-execution-section", "both-omitted", "max-steps-omitted", "timeout-omitted"],
)
def test_execution_budget_defaults_do_not_mutate_input(execution):
    data = minimal_profile()
    if execution is None:
        del data["execution"]
    else:
        data["execution"] = deepcopy(execution)
    before = deepcopy(data)

    parsed = profile.parse(data)

    assert parsed.execution.timeout_s == 1200
    assert parsed.execution.max_steps == 240
    assert data == before
