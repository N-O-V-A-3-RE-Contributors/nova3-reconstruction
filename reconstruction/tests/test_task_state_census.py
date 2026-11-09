"""Tests for tools/compare/task_state_census.py (task #315).

Synthetic in-memory fixtures only: the pure-function tests touch no file at
all, and the CLI tests read/write exclusively inside a pytest temporary
directory.  The committed coordination/ trees are never opened here.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "compare" / "task_state_census.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("nova3_task_state_census", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CENSUS = _load_tool()


def _tasks(*pairs):
    return {
        "schema_version": 1,
        "tasks": [{"id": task_id, "status": status} for task_id, status in pairs],
    }


# ---------------------------------------------------------------------------
# Purity / determinism
# ---------------------------------------------------------------------------

_IO_NAMES = (
    "open", "input", "glob", "listdir", "scandir", "stat", "lstat", "getenv",
    "putenv", "system", "popen", "socket", "urlopen", "read_text", "write_text",
    "read_bytes", "write_bytes", "exec", "eval", "import_module",
)


def test_census_is_pure_and_performs_no_io(monkeypatch):
    tasks = _tasks(("a", "pending"), ("b", "blocked"), ("c", "blocked"))
    overlays = [
        {"__path__": "a.json", "task_key": "a", "grade": "matched"},
        {"__path__": "b.json", "task_id": "b"},
        {"__path__": "z.json", "task_key": "zz"},
    ]

    def _boom(*_args, **_kwargs):
        raise AssertionError("census() touched I/O")

    for name in _IO_NAMES:
        if hasattr(builtins, name):
            monkeypatch.setattr(builtins, name, _boom)
    for name in ("open", "stat", "lstat", "listdir", "scandir", "getenv"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom, raising=False)
    for name in ("open", "glob", "exists", "is_file", "is_dir", "iterdir", "read_text", "write_text"):
        if hasattr(Path, name):
            monkeypatch.setattr(Path, name, _boom, raising=False)

    result = CENSUS.census(tasks, overlays)

    assert result["counts"]["tasks_total"] == 3
    assert result["counts"]["task_status"] == {"blocked": 2, "pending": 1}
    assert result["counts"]["orphans"] == 1


def test_census_result_is_independent_of_task_order():
    pairs = [(f"t{i}", status) for i, status in enumerate(
        ["pending", "blocked", "blocked", "review", "pending", "done"]
    )]
    overlays = [
        {"__path__": "t0.json", "task_key": "t0", "grade": "unverified"},
        {"__path__": "t2.json", "task_id": "t2", "grade": "NOT MATCHED"},
        {"__path__": "orphan.json", "task_key": "nope", "grade": "UNKNOWN"},
        {"__path__": "no-key.json"},
    ]
    forward = CENSUS.census(_tasks(*pairs), overlays)
    backward = CENSUS.census(_tasks(*reversed(pairs)), list(reversed(overlays)))
    assert forward == backward
    # Sorted keys everywhere so serialized output is also stable.
    assert list(forward["counts"]["task_status"]) == sorted(forward["counts"]["task_status"])
    assert list(forward["tasks"]) == sorted(forward["tasks"])


def test_duplicate_task_status_resolution_is_order_independent():
    one = CENSUS.census(_tasks(("x", "pending"), ("x", "blocked")), [])
    two = CENSUS.census(_tasks(("x", "blocked"), ("x", "pending")), [])
    assert one == two
    assert one["tasks"]["x"]["status"] == "blocked"


def test_bare_task_list_is_accepted():
    result = CENSUS.census([{"id": "k", "status": "pending"}], [{"task_key": "k"}])
    assert result["counts"]["tasks_with_overlay"] == 1


# ---------------------------------------------------------------------------
# Overlay shapes, orphan and duplicate accounting
# ---------------------------------------------------------------------------

def test_both_overlay_shapes_are_recognised():
    tasks = _tasks(("functions-abc", "pending"), ("functions-def", "blocked"), ("7", "pending"))
    overlays = [
        {"__path__": "abc.json", "task_key": "functions-abc", "schema": "x"},
        {"__path__": "def.json", "task_id": "functions-def", "schema_version": 1},
        {"__path__": "seven.json", "task_id": 7},  # integer task_id matches "7"
        {"__path__": "none.json", "schema_version": 1, "result": "PARTIAL"},
    ]
    result = CENSUS.census(tasks, overlays)
    counts = result["counts"]
    assert counts["tasks_with_overlay"] == 3
    assert counts["tasks_without_overlay"] == 0
    assert counts["orphans"] == 0
    assert counts["distinct_task_keys"] == 3
    assert counts["unkeyed_overlays"] == 1
    assert counts["overlays_total"] == 4


def test_orphan_listed_exactly_once_even_when_duplicated():
    tasks = _tasks(("functions-a", "pending"))
    overlays = [
        {"__path__": "a1.json", "task_key": "functions-a"},
        {"__path__": "o1.json", "task_key": "functions-missing-1"},
        {"__path__": "o2.json", "task_id": "functions-missing-1"},
        {"__path__": "o3.json", "task_id": "functions-missing-2"},
    ]
    result = CENSUS.census(tasks, overlays)
    assert result["orphans"] == ["functions-missing-1", "functions-missing-2"]
    assert result["counts"]["orphans"] == 2
    assert result["counts"]["distinct_task_keys"] == 3
    assert result["counts"]["tasks_with_overlay"] == 1
    assert result["counts"]["tasks_without_overlay"] == 0
    assert result["duplicates"]["functions-missing-1"]["count"] == 2
    assert result["duplicates"]["functions-missing-1"]["linked"] is False


def test_duplicate_overlays_count_once_linked_and_listed_as_duplicates():
    tasks = _tasks(("functions-a", "pending"), ("functions-b", "pending"))
    overlays = [
        {"__path__": "a1.json", "task_key": "functions-a", "grade": "matched"},
        {"__path__": "a2.json", "task_key": "functions-a", "grade": "NOT MATCHED"},
        {"__path__": "b1.json", "task_id": "functions-b"},
    ]
    result = CENSUS.census(tasks, overlays)
    assert result["counts"]["tasks_with_overlay"] == 2
    assert result["counts"]["overlays_total"] == 3
    assert result["counts"]["distinct_task_keys"] == 2
    assert result["counts"]["duplicate_keys"] == 1
    dup = result["duplicates"]["functions-a"]
    assert dup["count"] == 2
    assert dup["duplicate_count"] == 1
    assert dup["linked"] is True
    assert dup["overlays"] == ["a1.json", "a2.json"]
    assert result["tasks"]["functions-a"]["overlay_count"] == 2
    # Both duplicate overlays keep their own recorded grade bucket.
    assert dup["buckets"] == ["matched_recording", "not_matched"]
    assert result["grade_buckets"]["matched_recording"] == 1
    assert result["grade_buckets"]["not_matched"] == 1
    assert result["overlay_buckets"]["a1.json"] == "matched_recording"
    assert result["overlay_buckets"]["a2.json"] == "not_matched"


# ---------------------------------------------------------------------------
# Grade buckets: never upgraded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overlay, bucket",
    [
        ({"task_key": "k"}, "unstated"),
        ({"task_key": "k", "result": "PARTIAL", "status": "TESTED"}, "unstated"),
        ({"task_id": "k", "verification": "NOT_VERIFIED"}, "unstated"),
        ({"task_key": "k", "grade": None}, "unstated"),
        ({"task_key": "k", "grade": ""}, "unstated"),
        ({"task_key": "k", "grade": "UNKNOWN"}, "other_uninterpreted"),
        ({"task_key": "k", "grade": "unverified"}, "not_matched"),
        ({"task_key": "k", "grade": "NOT MATCHED"}, "not_matched"),
        ({"task_key": "k", "grade": "NOT MATCHED: two veneer matches only"}, "not_matched"),
        ({"task_key": "k", "grade": "NOT MATCHED; cleanup differs"}, "not_matched"),
        ({"task_key": "k", "verification_grade": "behavioural"}, "other_uninterpreted"),
        ({"task_key": "k", "grade": "PARTIAL: 3 of 4 MATCHED"}, "other_uninterpreted"),
        ({"task_key": "k", "grade": "matched"}, "matched_recording"),
        ({"task_key": "k", "grade": " MATCHED "}, "matched_recording"),
    ],
)
def test_grade_buckets_never_upgrade(overlay, bucket):
    graded = dict(overlay, **{"__path__": "o.json"})
    result = CENSUS.census(_tasks(("k", "pending")), [graded])
    assert set(result["grade_buckets"]) == set(CENSUS.GRADE_BUCKETS)
    for name, n in result["grade_buckets"].items():
        assert n == (1 if name == bucket else 0), (name, n)
    assert result["overlay_buckets"] == {"o.json": bucket}
    assert result["counts"]["overlays_total"] == 1


def test_only_explicit_matched_grade_reaches_matched_recording():
    overlays = [
        {"__path__": f"o{i}.json", "task_key": "k", "grade": grade}
        for i, grade in enumerate(
            ["UNKNOWN", "unverified", "NOT MATCHED", "PARTIAL", "TESTED", None, "x"]
        )
    ]
    overlays.append({"__path__": "none.json", "task_key": "k"})
    result = CENSUS.census(_tasks(("k", "pending")), overlays)
    assert result["grade_buckets"]["matched_recording"] == 0
    assert result["grade_buckets"]["other_uninterpreted"] == 4
    assert result["grade_buckets"]["not_matched"] == 2
    assert result["grade_buckets"]["unstated"] == 2
    result = CENSUS.census(_tasks(("k", "pending")), overlays + [
        {"__path__": "yes.json", "task_key": "k", "grade": "matched"},
    ])
    assert result["grade_buckets"]["matched_recording"] == 1
    assert result["overlay_buckets"]["yes.json"] == "matched_recording"


def test_census_does_not_read_nested_or_textual_claims_as_grades():
    tasks = _tasks(("k", "pending"))
    overlay = {
        "__path__": "k.json",
        "task_key": "k",
        "matching_claim": "MATCHED",
        "result": "Four wrappers matched exactly",
        "counts": {"matched": 4},
    }
    result = CENSUS.census(tasks, [overlay])
    assert result["grade_buckets"]["unstated"] == 1
    assert result["grade_buckets"]["matched_recording"] == 0
    assert result["overlay_buckets"] == {"k.json": "unstated"}


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def test_render_markdown_has_bucket_table_and_notice():
    tasks = _tasks(("a", "pending"), ("b", "blocked"))
    overlays = [
        {"__path__": "a.json", "task_key": "a", "grade": "matched"},
        {"__path__": "z.json", "task_key": "zz", "grade": "UNKNOWN"},
    ]
    text = CENSUS.render_markdown(CENSUS.census(tasks, overlays))
    lines = text.splitlines()
    assert "| bucket | count |" in lines
    assert "| orphans | 1 |" in lines
    assert "| grade:unstated | 0 |" in lines
    assert "| grade:matched_recording | 1 |" in lines
    assert "| grade:other_uninterpreted | 1 |" in lines
    assert "zz" in text
    assert "equivalence evidence" in text
    assert "not reconstruction" in text


def test_docstring_says_bookkeeping_only():
    doc = CENSUS.__doc__.lower()
    assert "bookkeeping only" in doc
    assert "not reconstruction" in doc
    assert "not equivalence evidence" in doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_prints_table_exits_zero_and_leaves_inputs_unchanged(tmp_path):
    tasks_path = tmp_path / "tasks.json"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _write(tasks_path, {
        "schema_version": 1,
        "notes": "synthetic",
        "tasks": [
            {"id": "functions-aaa", "status": "pending"},
            {"id": "functions-bbb", "status": "pending"},
            {"id": "functions-ccc", "status": "blocked"},
        ],
    })
    overlay_files = {
        "functions-aaa.json": {"schema_version": 1, "task_key": "functions-aaa", "result": "PARTIAL"},
        "functions-bbb.json": {"schema_version": 1, "task_id": "functions-bbb", "matching": "NOT MATCHED"},
        "functions-orphan.json": {"schema": "x", "task_id": 9, "verification_grade": "behav"},
        "compiler-pilot.json": {"schema": "y", "result": "MISMATCH"},
    }
    for name, data in overlay_files.items():
        _write(evidence / name, data)
    inputs = [tasks_path] + sorted(evidence.glob("*.json"))
    before = {p: _sha256(p) for p in inputs}
    before_listing = sorted(p.name for p in tmp_path.rglob("*"))

    proc = subprocess.run(
        [sys.executable, "-m", "tools.compare.task_state_census",
         str(tasks_path), *[str(p) for p in inputs[1:]]],
        cwd=str(TOOL_PATH.parents[2]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert "| bucket | count |" in lines
    assert "| task_status:pending | 2 |" in lines
    assert "| tasks_with_overlay | 2 |" in lines
    assert "| orphans | 1 |" in lines
    assert "| grade:unstated | 3 |" in lines
    assert proc.stderr == ""

    after = {p: _sha256(p) for p in inputs}
    assert after == before
    assert sorted(p.name for p in tmp_path.rglob("*")) == before_listing


def test_cli_reports_bad_input_as_exit_two(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    digest = _sha256(bad)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.compare.task_state_census", str(bad)],
        cwd=str(TOOL_PATH.parents[2]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
    assert _sha256(bad) == digest


def test_cli_json_mode_is_deterministic(tmp_path):
    tasks_path = tmp_path / "tasks.json"
    _write(tasks_path, {"tasks": [{"id": "a", "status": "pending"}]})
    overlay = tmp_path / "a.json"
    _write(overlay, {"task_key": "a", "grade": "matched"})
    cmd = [sys.executable, "-m", "tools.compare.task_state_census",
           "--json", str(tasks_path), str(overlay)]
    one = subprocess.run(cmd, cwd=str(TOOL_PATH.parents[2]), capture_output=True,
                         text=True, timeout=120)
    two = subprocess.run(cmd, cwd=str(TOOL_PATH.parents[2]), capture_output=True,
                         text=True, timeout=120)
    assert one.returncode == 0 and two.returncode == 0, (one.stderr, two.stderr)
    assert one.stdout == two.stdout
    data = json.loads(one.stdout)
    assert data["counts"]["task_status"] == {"pending": 1}
    assert data["grade_buckets"]["matched_recording"] == 1
    assert data["overlay_buckets"] == {"a.json": "matched_recording"}


def test_tool_source_has_no_network_elf_or_write_calls():
    source = TOOL_PATH.read_text(encoding="utf-8")
    for needle in ("urlopen", "requests", "socket", "subprocess", "ctypes", "lief",
                   "pyelftools", "write_text", "write_bytes",
                   "os.unlink", "shutil", "tempfile"):
        assert needle not in source, needle
    assert 'mode="w"' not in source
