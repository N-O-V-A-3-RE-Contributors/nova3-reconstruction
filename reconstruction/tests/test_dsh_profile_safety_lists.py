"""Lexical profile validation only: no Host, YAML, filesystem or commands.

Successful parsing proves neither filesystem confinement nor command safety.
Verification strings below are inert data and must never be executed.
"""

import pytest


profile = pytest.importorskip(
    "dsh_runner.profile", reason="optional dsh_runner profile parser is not installed"
)
SAFETY_FIELDS = ("safe_paths", "denied_paths", "verify")
PATH_FIELDS = ("safe_paths", "denied_paths")


@pytest.fixture
def valid_mapping():
    """Fresh synthetic profile; its workspace need not exist."""
    return {
        "schema": "dsh-runner-project/1",
        "name": "lexical-tests",
        "repo": "synthetic/profile-tests",
        "workspace": "/synthetic/profile-tests",
        "execution": {
            "backend": "headless",
            "isolation": "per-task-checkout",
            "timeout_s": 300,
            "max_steps": 10,
            "worker_member": "worker",
        },
        "members": {"worker": {"permission": "read-only"}},
        "routing": {},
        "safety": {
            "safe_paths": ["src/module.py"],
            "denied_paths": ["private/"],
            "verify": ["synthetic-check --case=baseline"],
        },
    }


def test_synthetic_mapping_parses(valid_mapping):
    parsed = profile.parse(valid_mapping)
    assert parsed.safety.safe_paths == ("src/module.py",)
    assert parsed.safety.denied_paths == ("private/",)
    assert parsed.safety.verify == ("synthetic-check --case=baseline",)


@pytest.mark.parametrize("field", PATH_FIELDS)
@pytest.mark.parametrize(
    "path",
    ["/absolute/file", "/", "..", "../file", "src/../file", "src/..",
     " \t/absolute/file \t", " \t../file \t"],
)
def test_absolute_and_parent_components_rejected(valid_mapping, field, path):
    valid_mapping["safety"][field] = [path]
    with pytest.raises(profile.ProfileError, match="must be workspace-relative"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("field", PATH_FIELDS)
@pytest.mark.parametrize(
    "path",
    ["..cache", "..cache/file", "src/..cache/file", "src/..cache",
     "cache..", "src/cache..", ".../file", "src/file"],
)
def test_relative_names_are_not_parent_components(valid_mapping, field, path):
    valid_mapping["safety"][field] = [path]
    assert getattr(profile.parse(valid_mapping).safety, field) == (path,)


@pytest.mark.parametrize("field", SAFETY_FIELDS)
@pytest.mark.parametrize("count", [40, 41])
def test_list_entry_limit(valid_mapping, field, count):
    entries = [f"synthetic-entry-{index}" for index in range(count)]
    valid_mapping["safety"][field] = entries
    if count == 40:
        assert getattr(profile.parse(valid_mapping).safety, field) == tuple(entries)
    else:
        with pytest.raises(profile.ProfileError, match=rf"safety\.{field} has too many entries"):
            profile.parse(valid_mapping)


@pytest.mark.parametrize("field", SAFETY_FIELDS)
@pytest.mark.parametrize("value", ["entry", "", 0, False, {}, {"entry": "value"}, ("entry",)])
def test_non_lists_rejected(valid_mapping, field, value):
    valid_mapping["safety"][field] = value
    with pytest.raises(profile.ProfileError, match=rf"safety\.{field} must be a list"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("field", SAFETY_FIELDS)
@pytest.mark.parametrize("item", [None, 0, 1.5, False, {}, [], ("entry",), b"entry",
                                  "", " ", "\t", " \t\r\n "])
def test_invalid_entries_rejected(valid_mapping, field, item):
    valid_mapping["safety"][field] = ["valid-entry", item]
    with pytest.raises(profile.ProfileError, match=rf"safety\.{field}\[\]"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("field", SAFETY_FIELDS)
@pytest.mark.parametrize("value", [None, []])
def test_optional_empty_lists_accepted(valid_mapping, field, value):
    # The parser explicitly treats null as an absent optional string list.
    valid_mapping["safety"][field] = value
    assert getattr(profile.parse(valid_mapping).safety, field) == ()


@pytest.mark.parametrize(
    "command",
    ["synthetic-check\nsecond-check", "synthetic-check\r\nsecond-check",
     "synthetic-check\n\nsecond-check", " \tsynthetic-check\nsecond-check \t"],
)
def test_embedded_newline_verify_rejected(valid_mapping, command):
    valid_mapping["safety"]["verify"] = [command]
    with pytest.raises(profile.ProfileError, match=r"safety\.verify entries must be single commands"):
        profile.parse(valid_mapping)


@pytest.mark.parametrize("field", SAFETY_FIELDS)
def test_entry_whitespace_stripped_without_changing_contents(valid_mapping, field):
    # _text strips outer whitespace before the lexical checks; inner spaces stay.
    entries = ([" \tsynthetic-check  --case=one \t", " second-check --case=two "]
               if field == "verify" else [" \tsrc/..cache/file \t", " other/file "])
    valid_mapping["safety"][field] = entries
    assert getattr(profile.parse(valid_mapping).safety, field) == tuple(
        entry.strip() for entry in entries
    )
