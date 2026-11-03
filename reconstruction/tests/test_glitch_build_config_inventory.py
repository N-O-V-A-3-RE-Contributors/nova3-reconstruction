"""Tests for the read-only Glitch build-configuration inventory scanner.

All headers are synthetic source-owned fixtures.  The tests assert textual
inventory behaviour, the NEXT.md recovered-configuration assertion, and that
the scanner neither writes nor launches a compiler/preprocessor.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools/compare/glitch_build_config_inventory.py"

_SPEC = importlib.util.spec_from_file_location(
    "glitch_build_config_inventory", TOOL_PATH
)
tool = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(tool)


def _header(tmp_path: Path, text: str, name: str = "fixture.h") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_inventory_exact_ordered_records(tmp_path):
    path = _header(
        tmp_path,
        """\
/* A commented-out definition must not become a record:
#define GLITCH_FAST_MATH 1
*/
#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL
#define GLITCH_FAST_MATH 0
#define ROUNDING_ERROR_64 1e-8
#if 0
#define ROUNDING_ERROR_64 1e-7
#endif
""",
    )

    report = tool.inventory([path])

    assert report["schema"] == tool.SCHEMA
    assert report["macros"] == list(tool.MACROS)
    assert report["preprocessor_invoked"] is False
    assert report["records"] == [
        {
            "file": str(path),
            "line": 4,
            "directive": "define",
            "macro": "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL",
            "replacement": "",
            "defined": True,
            "order": 1,
        },
        {
            "file": str(path),
            "line": 5,
            "directive": "define",
            "macro": "GLITCH_FAST_MATH",
            "replacement": "0",
            "defined": True,
            "order": 2,
        },
        {
            "file": str(path),
            "line": 6,
            "directive": "define",
            "macro": "ROUNDING_ERROR_64",
            "replacement": "1e-8",
            "defined": True,
            "order": 3,
        },
        {
            "file": str(path),
            "line": 8,
            "directive": "define",
            "macro": "ROUNDING_ERROR_64",
            "replacement": "1e-7",
            "defined": True,
            "order": 4,
        },
    ]


def test_undef_and_order_across_files(tmp_path):
    first = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#undef GLITCH_FAST_MATH\n",
        name="a.h",
    )
    second = _header(
        tmp_path,
        "#define ROUNDING_ERROR_64 0.00000001\n",
        name="b.h",
    )

    records = tool.scan_paths([first, second])

    assert [(r["order"], r["directive"], r["macro"]) for r in records] == [
        (1, "define", "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL"),
        (2, "undef", "GLITCH_FAST_MATH"),
        (3, "define", "ROUNDING_ERROR_64"),
    ]
    assert records[1]["replacement"] == ""
    assert records[1]["defined"] is False


def test_recovered_configuration_passes_on_next_md_values(tmp_path):
    path = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define ROUNDING_ERROR_64 0.00000001\n",
    )

    report = tool.inventory([path])

    assert tool.evaluate_recovered_config(report["records"]) == []


def test_recovered_configuration_rejects_defined_fast_math(tmp_path):
    path = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define GLITCH_FAST_MATH 0\n"
        "#define ROUNDING_ERROR_64 1e-8\n",
    )

    failures = tool.evaluate_recovered_config(tool.scan_paths([path]))

    assert len(failures) == 1
    assert "GLITCH_FAST_MATH" in failures[0]
    # A value of zero does not make the textual definition disappear.
    assert "expected not defined" in failures[0]


def test_recovered_configuration_rejects_wrong_rounding_value(tmp_path):
    path = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define ROUNDING_ERROR_64 1e-7\n",
    )

    failures = tool.evaluate_recovered_config(tool.scan_paths([path]))

    assert len(failures) == 1
    assert "ROUNDING_ERROR_64" in failures[0]
    assert "1e-8" in failures[0]


def test_cli_help_exits_zero():
    completed = subprocess.run(
        [sys.executable, "-m", "tools.compare.glitch_build_config_inventory", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL" in completed.stdout


def test_cli_assert_recovered_config_pass_and_fail(tmp_path):
    passing = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define ROUNDING_ERROR_64 1e-8\n",
        name="passing.h",
    )
    failing = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define GLITCH_FAST_MATH 0\n"
        "#define ROUNDING_ERROR_64 1e-8\n",
        name="failing.h",
    )

    command = [
        sys.executable,
        "-m",
        "tools.compare.glitch_build_config_inventory",
        "--assert-recovered-config",
    ]
    passed = subprocess.run(
        [*command, str(passing)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0, passed.stderr

    failed = subprocess.run(
        [*command, str(failing)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    assert "GLITCH_FAST_MATH" in failed.stderr
    assert "FAIL" in failed.stderr

    wrong_rounding = _header(
        tmp_path,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define ROUNDING_ERROR_64 1e-7\n",
        name="wrong-rounding.h",
    )
    failed_rounding = subprocess.run(
        [*command, str(wrong_rounding)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed_rounding.returncode != 0
    assert "ROUNDING_ERROR_64" in failed_rounding.stderr


def test_scanner_writes_nothing_and_never_launches_a_tool(tmp_path, monkeypatch):
    fixture_dir = tmp_path / "headers"
    fixture_dir.mkdir()
    _header(
        fixture_dir,
        "#define GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL\n"
        "#define GLITCH_FAST_MATH 0\n"
        "#define ROUNDING_ERROR_64 1e-8\n",
        name="nested.h",
    )

    def snapshot() -> dict[str, bytes]:
        return {
            str(path.relative_to(fixture_dir)): path.read_bytes()
            for path in fixture_dir.rglob("*")
            if path.is_file()
        }

    before = snapshot()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("scanner invoked an external process")

    for name in (
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "getoutput",
        "getstatusoutput",
    ):
        monkeypatch.setattr(subprocess, name, forbidden, raising=False)
    for name in (
        "system",
        "popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
    ):
        monkeypatch.setattr(os, name, forbidden, raising=False)

    report = tool.inventory([fixture_dir])

    assert report["preprocessor_invoked"] is False
    assert len(report["records"]) == 3
    assert snapshot() == before
