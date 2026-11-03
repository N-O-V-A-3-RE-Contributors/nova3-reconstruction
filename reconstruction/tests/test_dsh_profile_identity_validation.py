"""Offline lexical identity checks, not confinement or game-equivalence tests.

Only synthetic mappings are passed to profile.parse: no YAML parser, runtime,
subprocess, network, proprietary input or workspace directory is needed.
"""

import pytest

from dsh_runner import profile


@pytest.fixture
def valid_mapping():
    """Return a fresh, independently valid profile for each identity case."""
    return {
        "schema": "dsh-runner-project/1",
        "name": "synthetic-project",
        "repo": "synthetic-owner/synthetic-repo",
        "workspace": "/synthetic/profile-identity/workspace",
        "members": {"worker": {}},
    }


@pytest.mark.parametrize("name", ["a0", "a" + "0" * 40, "0a._-"])
def test_valid_project_names(valid_mapping, name):
    valid_mapping["name"] = name
    assert profile.parse(valid_mapping).name == name


@pytest.mark.parametrize(
    "name",
    ["a", "a" * 42, "Ab", "aB", ".ab", "_ab", "-ab", "a b", "a\tb", "a\nb"],
)
def test_invalid_project_names(valid_mapping, name):
    valid_mapping["name"] = name
    with pytest.raises(profile.ProfileError, match=r"\bname\b"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("repo", ["owner/name", "Owner_0.-/Repo_9.-", "0/9"])
def test_valid_repository_identifiers(valid_mapping, repo):
    valid_mapping["repo"] = repo
    assert profile.parse(valid_mapping).repo == repo


@pytest.mark.parametrize(
    "repo",
    [
        "owner", "/name", "owner/", "/", "owner/name/extra", "owner//name",
        "/owner/name", "owner/name/", "own er/name", "owner/na me",
        "owner\t/name", "owner/na\nme", "owner:name/repo", "owner/repo@tag",
    ],
)
def test_invalid_repository_identifiers(valid_mapping, repo):
    valid_mapping["repo"] = repo
    with pytest.raises(profile.ProfileError, match=r"\brepo\b"):
        profile.parse(valid_mapping)


def test_absolute_synthetic_workspace_is_accepted_without_creating_it(valid_mapping):
    # Acceptance here establishes only a leading slash, not existence or isolation.
    assert profile.parse(valid_mapping).workspace == "/synthetic/profile-identity/workspace"


@pytest.mark.parametrize("workspace", ["relative/workspace", "./workspace", "../workspace", "~/workspace"])
def test_relative_workspaces_are_rejected(valid_mapping, workspace):
    valid_mapping["workspace"] = workspace
    with pytest.raises(profile.ProfileError, match=r"\bworkspace\b"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("padding", [" ", "\t", "\n", " \t\r\n"])
def test_surrounding_whitespace_is_trimmed_from_all_identity_fields(valid_mapping, padding):
    expected = {field: valid_mapping[field] for field in ("name", "repo", "workspace")}
    for field, value in expected.items():
        valid_mapping[field] = padding + value + padding
    parsed = profile.parse(valid_mapping)
    for field, value in expected.items():
        assert getattr(parsed, field) == value


@pytest.mark.parametrize("field", ["name", "repo", "workspace"])
def test_missing_identity_field_is_identified(valid_mapping, field):
    del valid_mapping[field]
    with pytest.raises(profile.ProfileError, match=rf"\b{field}\b"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("field", ["name", "repo", "workspace"])
@pytest.mark.parametrize(
    "value",
    ["", " \t\r\n", None, 0, True, 1.5, [], {}],
    ids=["empty", "whitespace-only", "null", "integer", "boolean", "float", "list", "mapping"],
)
def test_empty_or_non_string_identity_field_is_identified(valid_mapping, field, value):
    valid_mapping[field] = value
    with pytest.raises(profile.ProfileError, match=rf"\b{field}\b"):
        profile.parse(valid_mapping)
