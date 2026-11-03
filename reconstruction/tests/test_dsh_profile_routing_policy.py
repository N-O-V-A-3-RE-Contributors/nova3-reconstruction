"""Offline lexical policy regressions, not dispatch or live-project isolation tests."""

import pytest

from dsh_runner import profile


def _profile_data(name, routing):
    """Return a self-contained synthetic mapping; no workspace need exist."""
    return {
        "schema": "dsh-runner-project/1",
        "name": name,
        "repo": f"synthetic/{name}",
        "workspace": f"/synthetic/{name}",
        "execution": {
            "backend": "headless",
            "isolation": "per-task-checkout",
            "worker_member": "worker",
        },
        "members": {"worker": {"permission": "workspace-write"}},
        "routing": routing,
        "safety": {},
    }


@pytest.mark.parametrize(
    "task,alpha_purpose,beta_purpose",
    [
        pytest.param("AlPhA report", "routine", "semantic", id="mixed-case-alpha"),
        pytest.param("BeTa report", "semantic", "routine", id="mixed-case-beta"),
        pytest.param("prealphapost", "routine", "semantic", id="alpha-substring"),
        pytest.param("prebetapost", "semantic", "routine", id="beta-substring"),
        pytest.param("ALPHA and BETA", "routine", "routine", id="both-tokens"),
        pytest.param("test docs inventory", "semantic", "semantic", id="nonmatches"),
        pytest.param("", "semantic", "semantic", id="empty-text"),
    ],
)
def test_each_profile_uses_its_own_policy_when_calls_are_interleaved(
    task, alpha_purpose, beta_purpose
):
    alpha = profile.parse(_profile_data("alpha", {
        "routine_tasks": ["alpha"],
        "routine": ["alpha/routine-first", "alpha/routine-second"],
        "semantic": ["alpha/semantic-first", "alpha/semantic-second"],
    }))
    beta = profile.parse(_profile_data("beta", {
        "routine_tasks": ["beta"],
        "routine": ["beta/routine-first", "beta/routine-second"],
        "semantic": ["beta/semantic-first", "beta/semantic-second"],
    }))

    # These are substring expectations, not judgements of task semantics.
    # Revisit both objects after the other has classified the identical text.
    for selected, name, purpose in (
        (alpha, "alpha", alpha_purpose),
        (beta, "beta", beta_purpose),
        (alpha, "alpha", alpha_purpose),
        (beta, "beta", beta_purpose),
        (beta, "beta", beta_purpose),
        (alpha, "alpha", alpha_purpose),
    ):
        assert selected.purpose(task) == purpose
        assert selected.route_for(task) == f"{name}/{purpose}-first"


@pytest.mark.parametrize(
    "task,selected_purpose,other_purpose",
    [
        pytest.param("AlPhA", "routine", "semantic", id="empty-routine-route"),
        pytest.param("unrelated", "semantic", "routine", id="empty-semantic-route"),
    ],
)
def test_empty_selected_routes_do_not_fall_back(task, selected_purpose, other_purpose):
    parsed = profile.parse(_profile_data("empty-route", {
        "routine_tasks": ["alpha"],
        selected_purpose: [],
        other_purpose: ["other/first", "other/second"],
    }))

    assert parsed.purpose(task) == selected_purpose
    assert getattr(parsed.routing, selected_purpose) == ()
    assert getattr(parsed.routing, other_purpose) == ("other/first", "other/second")
    assert parsed.route_for(task) == ""


@pytest.mark.parametrize("explicit_empty", [False, True], ids=["omitted", "empty-list"])
@pytest.mark.parametrize(
    "task,expected_purpose",
    [
        ("TeSt", "routine"),
        ("DoCs", "routine"),
        ("MeTaDaTa", "routine"),
        ("InVeNtOrY", "routine"),
        ("LiNt", "routine"),
        ("contestant", "routine"),  # 'test' is not required to be a whole word.
        ("alpha beta", "semantic"),
        ("", "semantic"),
    ],
)
def test_omitted_and_empty_routine_tasks_use_current_defaults(
    explicit_empty, task, expected_purpose
):
    routing = {
        "routine": ["defaults/routine-first", "defaults/routine-second"],
        "semantic": ["defaults/semantic-first", "defaults/semantic-second"],
    }
    if explicit_empty:
        routing["routine_tasks"] = []
    parsed = profile.parse(_profile_data("defaults", routing))

    # Pin the current tokens independently of the production default constant.
    assert parsed.routing.routine_tasks == ("test", "docs", "metadata", "inventory", "lint")
    assert parsed.purpose(task) == expected_purpose
    assert parsed.route_for(task) == f"defaults/{expected_purpose}-first"
