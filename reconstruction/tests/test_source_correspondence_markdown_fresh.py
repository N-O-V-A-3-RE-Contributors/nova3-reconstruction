"""Freshness gate for the published ELF <-> source correspondence report.

``docs/progress/SOURCE_CORRESPONDENCE.md`` publishes figures quoted elsewhere
in the project.  It is generated from ``coordination/source_correspondence.json``
by ``tools.source_ref.build_source_correspondence.render_markdown``; that
function is pure given the summary object.  This module fails when the
committed report is not byte-for-byte the render of the committed JSON, so a
stale published report cannot survive a test run.

The check reads only the two committed text artifacts and imports the pure
renderer.  It does not read the Premium ELF, an APK, an OBB or the private
reference tree, needs no network, and writes nothing.  It deliberately does
not re-verify any correspondence result: the schema tag and the presence of
all 15 tracked-unit addresses are the only content assertions, and in-memory
mutations prove the comparison is not vacuous.

If neither artifact is present, the checkout predates the correspondence
feature (the committed report and summary landed together); the tests skip
with a named reason rather than passing silently.  If only one artifact is
present, the tests fail: a deleted source of truth or a deleted publication
must not turn the gate off.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUMMARY_JSON = ROOT / "coordination/source_correspondence.json"
PUBLISHED_MD = ROOT / "docs/progress/SOURCE_CORRESPONDENCE.md"
SCHEMA = "nova3-source-correspondence/1"
TRACKED_UNIT_COUNT = 15
ADDRESS_RE = re.compile(r"0x[0-9a-f]{8}")

SUMMARY_PRESENT = SUMMARY_JSON.is_file()
PUBLISHED_PRESENT = PUBLISHED_MD.is_file()
FEATURE_PRESENT = SUMMARY_PRESENT or PUBLISHED_PRESENT

if FEATURE_PRESENT:
    from tools.source_ref.build_source_correspondence import render_markdown
else:  # pragma: no cover - only a pre-correspondence checkout takes this path
    render_markdown = None

pytestmark = pytest.mark.skipif(
    not FEATURE_PRESENT,
    reason=("source-correspondence artifacts absent from this checkout: "
            f"{SUMMARY_JSON.relative_to(ROOT).as_posix()} and "
            f"{PUBLISHED_MD.relative_to(ROOT).as_posix()}"),
)


def _load_summary() -> dict:
    assert SUMMARY_PRESENT, (
        f"missing {SUMMARY_JSON.relative_to(ROOT).as_posix()}; the freshness "
        "gate cannot run against a deleted source of truth"
    )
    payload = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "correspondence summary must be a JSON object"
    return payload


def _load_published() -> str:
    assert PUBLISHED_PRESENT, (
        f"missing {PUBLISHED_MD.relative_to(ROOT).as_posix()}; the published "
        "report must exist so it can be compared with its JSON"
    )
    return PUBLISHED_MD.read_text(encoding="utf-8")


def _published_render(summary: dict) -> str:
    """Return exactly what the CLI writes: the render plus one newline."""
    rendered = render_markdown(summary)
    assert rendered.endswith("\n"), "render_markdown must end with a newline"
    assert not rendered.endswith("\n\n"), (
        "render_markdown must not end with a blank line; the CLI adds the "
        "single trailing newline itself"
    )
    return rendered + "\n"


def _assert_fresh(published: str, expected: str) -> None:
    if published == expected:
        return
    common = min(len(published), len(expected))
    offset = next(
        (index for index in range(common) if published[index] != expected[index]),
        common,
    )
    line = published.count("\n", 0, offset) + 1
    raise AssertionError(
        "docs/progress/SOURCE_CORRESPONDENCE.md is stale: it is not the render "
        "of coordination/source_correspondence.json. First difference at "
        f"published line {line} (byte {offset}); re-render the Markdown from "
        "the committed JSON instead of editing either by hand."
    )


def test_published_markdown_is_current_render():
    summary = _load_summary()
    assert summary.get("schema") == SCHEMA

    published = _load_published()
    _assert_fresh(published, _published_render(summary))


def test_render_contains_every_tracked_unit_address():
    summary = _load_summary()
    tracked = summary.get("tracked_units")
    assert isinstance(tracked, list), "tracked_units must be a JSON array"
    assert len(tracked) == TRACKED_UNIT_COUNT

    addresses = [unit.get("address") for unit in tracked]
    assert len(set(addresses)) == TRACKED_UNIT_COUNT, "tracked addresses must be unique"
    for address in addresses:
        assert isinstance(address, str) and ADDRESS_RE.fullmatch(address), address

    rendered = render_markdown(summary)
    for address in addresses:
        assert f"| `{address}` |" in rendered, (
            f"tracked-unit address {address} is missing from the rendered table"
        )


def test_in_memory_drift_changes_render():
    summary = _load_summary()
    published = _load_published()
    _assert_fresh(published, _published_render(summary))

    status_probe = copy.deepcopy(summary)
    status_name = sorted(status_probe["status"])[0]
    status_probe["status"][status_name] += 1
    assert _published_render(status_probe) != published, (
        "changing a status count did not change the rendered report; the "
        "comparison is vacuous"
    )

    unit_probe = copy.deepcopy(summary)
    unit = unit_probe["tracked_units"][0]
    original_status = unit["status"]
    unit["status"] = "__freshness_probe__"
    assert unit["status"] != original_status
    assert _published_render(unit_probe) != published, (
        "changing a tracked-unit status did not change the rendered report; "
        "the comparison is vacuous"
    )

    combined = copy.deepcopy(summary)
    combined["status"][status_name] += 1
    combined["tracked_units"][0]["status"] = "__freshness_probe__"
    assert _published_render(combined) != published
