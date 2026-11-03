"""Tests for tools/compare/pilot_preflight.py (TASK #303).

The tool is a read-only freshness gate: it fingerprints the pilot's source
``.cpp`` files and its cached object, and refuses to bless a window match when
a source is newer than the object.  These tests use synthetic files under
``tmp_path`` only; nothing is compiled and no real game file is read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compare import pilot_preflight  # noqa: E402
from tools.compare.pilot_preflight import build_manifest  # noqa: E402

SCHEMA = "nova3-pilot-preflight/1"


# --------------------------------------------------------------------------
# Pilot A: the pure function (check_exists=False touches no path)
# --------------------------------------------------------------------------


def test_pilot_a_object_older_than_source_is_stale():
    manifest = build_manifest(
        ["a.cpp"],
        {"a.cpp": 2000.0, "a.o": 1000.0},
        object_path="a.o",
        check_exists=False,
    )
    assert manifest["status"] == "stale"
    assert manifest["newer_sources"] == ["a.cpp"]


def test_pilot_a_object_newer_than_source_is_fresh():
    manifest = build_manifest(
        ["a.cpp"],
        {"a.cpp": 1000.0, "a.o": 2000.0},
        object_path="a.o",
        check_exists=False,
    )
    assert manifest["status"] == "fresh"
    assert manifest["newer_sources"] == []


def test_pilot_a_only_newer_sources_are_listed_and_sorted():
    manifest = build_manifest(
        ["b.cpp", "a.cpp", "c.cpp"],
        {"a.cpp": 10.0, "b.cpp": 30.0, "c.cpp": 20.0, "x.o": 15.0},
        object_path="x.o",
        check_exists=False,
    )
    assert manifest["status"] == "stale"
    assert manifest["newer_sources"] == ["b.cpp", "c.cpp"]


def test_pilot_a_explicit_object_mtime():
    fresh = build_manifest(["a.cpp"], {"a.cpp": 1.0}, object_path="a.o",
                           object_mtime=5.0, check_exists=False)
    assert fresh["status"] == "fresh"

    stale = build_manifest(["a.cpp"], {"a.cpp": 9.0}, object_path="a.o",
                           object_mtime=5.0, check_exists=False)
    assert stale["status"] == "stale"
    assert stale["newer_sources"] == ["a.cpp"]


def test_pilot_a_refusal_statuses():
    empty = build_manifest([], {"a.o": 1.0}, object_path="a.o", check_exists=False)
    assert empty["status"] == "no-sources"

    ghost = build_manifest(["a.cpp"], {"a.cpp": 1.0}, object_path="ghost.o",
                           check_exists=True)
    assert ghost["status"] == "no-object"
    assert ghost["object"]["exists"] is False

    unknown = build_manifest(["a.cpp"], {}, object_path="a.o", check_exists=False)
    assert unknown["status"] == "error"
    # Both the object and the source lack a timestamp.
    assert unknown["unknown_mtime"] == ["a.o", "a.cpp"]


def test_schema_and_sorted_source_map():
    manifest = build_manifest(
        ["z.cpp", "a.cpp"],
        {"a.cpp": 1.0, "z.cpp": 1.0, "o.o": 2.0},
        object_path="o.o",
        check_exists=False,
    )
    assert manifest["schema"] == SCHEMA
    assert list(manifest["sources"]) == ["a.cpp", "z.cpp"]


# --------------------------------------------------------------------------
# Pilot B / C: the CLI on synthetic files
# --------------------------------------------------------------------------


def _make_pair(tmp_path: Path, object_offset: float) -> tuple[Path, Path]:
    src = tmp_path / "a.cpp"
    obj = tmp_path / "a.o"
    src.write_bytes(b"// synthetic source\nint f(void) { return 0; }\n")
    obj.write_bytes(b"\x00synthetic object\x00")
    base = 1_600_000_000
    os.utime(src, (base + 1000.0, base + 1000.0))
    os.utime(obj, (base + object_offset, base + object_offset))
    return src, obj


def _snapshot(*paths: Path) -> dict[str, tuple[str, int, int]]:
    result = {}
    for p in paths:
        st = p.stat()
        result[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(),
                          st.st_mtime_ns, st.st_size)
    return result


def _run_cli(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    prior = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + prior if prior else "")
    return subprocess.run(
        [sys.executable, "-m", "tools.compare.pilot_preflight", *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_pilot_b_documented_spelling_stale_exit_2(tmp_path):
    # Exactly the acceptance spelling: --sources a.cpp --object a.o
    src, obj = _make_pair(tmp_path, object_offset=500.0)  # object older
    before = _snapshot(src, obj)
    before_entries = sorted(os.listdir(tmp_path))

    result = _run_cli(tmp_path, "--sources", "a.cpp", "--object", "a.o")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "stale" in result.stdout
    assert "a.cpp" in result.stdout
    # Pilot C: no writes, deletes or renames.
    assert _snapshot(src, obj) == before
    assert sorted(os.listdir(tmp_path)) == before_entries


def test_pilot_b_repeatable_source_flag(tmp_path):
    src, obj = _make_pair(tmp_path, object_offset=500.0)
    before = _snapshot(src, obj)

    result = _run_cli(tmp_path, "--source", "a.cpp", "--object", "a.o")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "stale" in result.stdout
    assert _snapshot(src, obj) == before


def test_pilot_b_fresh_exit_0(tmp_path):
    src, obj = _make_pair(tmp_path, object_offset=5000.0)  # object newer
    before = _snapshot(src, obj)

    result = _run_cli(tmp_path, "--sources", "a.cpp", "--object", "a.o")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "fresh" in result.stdout
    assert "stale" not in result.stdout
    assert _snapshot(src, obj) == before


def test_pilot_c_cli_json_contract(tmp_path):
    src, obj = _make_pair(tmp_path, object_offset=500.0)
    before = _snapshot(src, obj)
    before_entries = sorted(os.listdir(tmp_path))

    result = _run_cli(tmp_path, "--sources", "a.cpp", "--object", "a.o", "--json")

    assert result.returncode == 2, result.stdout + result.stderr
    block = json.loads(result.stdout)  # stdout is exactly one JSON object
    assert {"schema", "status", "sources", "object"} <= set(block)
    assert block["schema"] == SCHEMA
    assert block["status"] == "stale"
    assert block["sources"] == {
        "a.cpp": hashlib.sha256(src.read_bytes()).hexdigest(),
    }
    assert list(block["sources"]) == sorted(block["sources"])
    assert block["object"]["path"] == "a.o"
    assert block["object"]["sha256"] == hashlib.sha256(obj.read_bytes()).hexdigest()
    # Pilot C again: JSON mode preserves every synthetic input byte-for-byte.
    assert _snapshot(src, obj) == before
    assert sorted(os.listdir(tmp_path)) == before_entries


def test_pilot_c_cli_missing_object_refuses(tmp_path):
    src = tmp_path / "a.cpp"
    src.write_bytes(b"int f(void) { return 0; }\n")
    before = _snapshot(src)

    result = _run_cli(tmp_path, "--sources", "a.cpp", "--object", "a.o")

    assert result.returncode == 3
    assert "no-object" in result.stdout
    assert "stale" not in result.stdout
    assert _snapshot(src) == before


def test_module_has_no_child_process_import():
    text = Path(pilot_preflight.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in text


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "tools.compare.pilot_preflight", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0
    assert "--sources" in result.stdout
    assert "--object" in result.stdout
    assert "--json" in result.stdout
