"""Offline profile.parse regressions, not permission-enforcement or game tests.

Only synthetic mappings are used; an importable dsh_runner is required. No
profile files, YAML bridge, backend execution or proprietary inputs are needed.
"""

import pytest

from dsh_runner import profile


def member_profile(**settings):
    """Return a fresh minimal profile with exactly one unambiguous member."""
    return {
        "schema": "dsh-runner-project/1",
        "name": "member-validation",
        "repo": "synthetic/demo",
        "workspace": "/synthetic/workspace",
        "members": {"worker": settings},
    }


@pytest.mark.parametrize(
    "permission, accepted",
    [
        ("read-only", True),
        ("workspace-write", True),
        ("danger-full-access", True),
        ("invalid-permission", False),
    ],
)
def test_member_permission_values(permission, accepted):
    data = member_profile(permission=permission)
    if accepted:
        assert profile.parse(data).members["worker"].permission == permission
    else:
        with pytest.raises(profile.ProfileError, match=r"members\.worker\.permission\b"):
            profile.parse(data)


@pytest.mark.parametrize("field", ["model", "effort"])
@pytest.mark.parametrize(
    "identifier",
    ["a", "Z", "0", "_", ".", "/", ":", "@", "-", "Provider_9/Model.v2:fast@route-1"],
)
def test_member_identifier_accepted_characters(field, identifier):
    member = profile.parse(member_profile(**{field: identifier})).members["worker"]
    assert getattr(member, field) == identifier


@pytest.mark.parametrize("field", ["model", "effort"])
@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "\r"])
def test_member_identifier_rejects_embedded_whitespace(field, whitespace):
    # Internal whitespace is invalid; surrounding whitespace is stripped by _text.
    with pytest.raises(profile.ProfileError, match=rf"members\.worker\.{field}\b"):
        profile.parse(member_profile(**{field: f"valid{whitespace}value"}))


@pytest.mark.parametrize("field", ["model", "effort"])
@pytest.mark.parametrize("punctuation", [";", "$", "`", "|", "&", ">", "<", "(", ")", "'", '"', "\\"])
def test_member_identifier_rejects_shell_punctuation(field, punctuation):
    with pytest.raises(profile.ProfileError, match=rf"members\.worker\.{field}\b"):
        profile.parse(member_profile(**{field: f"valid{punctuation}value"}))


def test_omitted_member_settings_use_documented_defaults():
    member = profile.parse(member_profile()).members["worker"]
    assert member.name == "worker"
    assert member.preset == "standard"
    assert member.permission == "workspace-write"
    assert member.model == ""
    assert member.effort == ""
    assert member.brief == ""


@pytest.mark.parametrize("length", [4000, 4001])
def test_member_brief_length_boundary(length):
    brief = "b" * length
    data = member_profile(brief=brief)
    if length == 4000:
        assert profile.parse(data).members["worker"].brief == brief
    else:
        with pytest.raises(profile.ProfileError, match=r"members\.worker\.brief\b"):
            profile.parse(data)
