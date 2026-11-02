"""Tests for the read-only Android.mk / Application.mk build-variable extractor.

Everything here is synthetic text created under pytest's ``tmp_path``: no APK,
shared library, game executable or private analysis file is read or executed.
"""
import builtins
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/compare/android_mk_build_vars.py"

_spec = importlib.util.spec_from_file_location("android_mk_build_vars", MODULE_PATH)
build_vars = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_vars)


ANDROID_MK = """\
# Synthetic fixture: two modules in one Android.mk.
LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := libalpha
LOCAL_CFLAGS := -O2 -DALPHA
# The next assignment must not be concatenated with this comment.
LOCAL_CPPFLAGS += -std=c++11 \\
                  -DFEATURE_BETA
LOCAL_C_INCLUDES := $(LOCAL_PATH)/include \\
                    $(LOCAL_PATH)/third_party
LOCAL_ARM_MODE := arm  # trailing comment
LOCAL_ARM_NEON := true

include $(CLEAR_VARS)
LOCAL_MODULE := libbeta
LOCAL_CFLAGS := -O3
LOCAL_CFLAGS += -fno-exceptions
LOCAL_CPPFLAGS ?= -DBETA_ONLY
LOCAL_C_INCLUDES := $(LOCAL_PATH)/beta
LOCAL_ARM_NEON := false
"""

APPLICATION_MK = """\
# Synthetic fixture: application-wide globals.
APP_CFLAGS := -DAPP_LEVEL=1
APP_CPPFLAGS += -fvisibility=hidden
APP_STL := gnustl_static
APP_STL ?= system
APP_PLATFORM := android-9
APP_ABI := armeabi armeabi-v7a
"""


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    jni = tmp_path / "jni"
    jni.mkdir()
    android = jni / "Android.mk"
    application = jni / "Application.mk"
    android.write_text(ANDROID_MK, encoding="utf-8")
    application.write_text(APPLICATION_MK, encoding="utf-8")
    return android, application


def _expected(android: Path, application: Path) -> dict:
    return {
        "schema": build_vars.SCHEMA,
        "inputs": [str(android), str(application)],
        "modules": [
            {
                "source": str(android),
                "LOCAL_MODULE": "libalpha",
                "LOCAL_CFLAGS": "-O2 -DALPHA",
                "LOCAL_CPPFLAGS": "-std=c++11 -DFEATURE_BETA",
                "LOCAL_C_INCLUDES": (
                    "$(LOCAL_PATH)/include $(LOCAL_PATH)/third_party"
                ),
                "LOCAL_ARM_MODE": "arm",
                "LOCAL_ARM_NEON": "true",
            },
            {
                "source": str(android),
                "LOCAL_MODULE": "libbeta",
                "LOCAL_CFLAGS": "-O3 -fno-exceptions",
                "LOCAL_CPPFLAGS": "-DBETA_ONLY",
                "LOCAL_C_INCLUDES": "$(LOCAL_PATH)/beta",
                "LOCAL_ARM_MODE": None,
                "LOCAL_ARM_NEON": "false",
            },
        ],
        "globals": {
            "APP_CFLAGS": "-DAPP_LEVEL=1",
            "APP_CPPFLAGS": "-fvisibility=hidden",
            "APP_STL": "gnustl_static",
            "APP_PLATFORM": "android-9",
            "APP_ABI": "armeabi armeabi-v7a",
        },
    }


def test_help_exits_zero_without_reading_any_input(tmp_path, monkeypatch, capsys):
    def forbidden_read(*_args, **_kwargs):
        raise AssertionError("--help must not read an input file")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    with pytest.raises(SystemExit) as stopped:
        build_vars.main(["--help", str(tmp_path / "does-not-exist.mk")])
    assert stopped.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_help_exits_zero_as_a_module_subprocess():
    result = subprocess.run(
        [sys.executable, "-m", "tools.compare.android_mk_build_vars", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "LOCAL_C_INCLUDES" not in result.stderr


def test_fixture_reports_exact_modules_globals_and_raw_values(
        tmp_path, capsys):
    android, application = _write_fixture(tmp_path)
    assert build_vars.main([str(android), str(application)]) == 0
    payload = capsys.readouterr().out
    assert payload == build_vars.render(_expected(android, application))

    report = json.loads(payload)
    assert [module["LOCAL_MODULE"] for module in report["modules"]] == [
        "libalpha", "libbeta",
    ]
    assert all(module["source"] == str(android) for module in report["modules"])
    # Includes stay in source order and $(LOCAL_PATH) is not expanded.
    alpha = report["modules"][0]
    assert alpha["LOCAL_C_INCLUDES"].split() == [
        "$(LOCAL_PATH)/include",
        "$(LOCAL_PATH)/third_party",
    ]
    assert "$(LOCAL_PATH)" in alpha["LOCAL_C_INCLUDES"]
    # CLEAR_VARS reset the previous module's LOCAL_ARM_MODE.
    assert report["modules"][1]["LOCAL_ARM_MODE"] is None


def test_two_runs_produce_byte_identical_json(tmp_path, capsys):
    android, application = _write_fixture(tmp_path)
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    assert build_vars.main(
        [str(android), str(application), "--output", str(first_output)]) == 0
    stdout_first = capsys.readouterr().out
    assert stdout_first == ""

    assert build_vars.main(
        [str(android), str(application), "--output", str(second_output)]) == 0
    assert first_output.read_bytes() == second_output.read_bytes()

    assert build_vars.main([str(android), str(application)]) == 0
    assert capsys.readouterr().out.encode("utf-8") == first_output.read_bytes()


def test_no_shell_runs_and_nothing_is_written_outside_tmp_path(
        tmp_path, monkeypatch, capsys):
    android, application = _write_fixture(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("the parser must not execute a shell or process")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "popen", "execv", "execve", "spawnv", "spawnve"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)

    writes = []
    real_io_open = io.open
    real_builtins_open = builtins.open

    def recording_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            writes.append(Path(file).resolve())
        return real_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", recording_open)
    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.chdir(tmp_path)

    assert build_vars.main(
        [str(android), str(application), "--output", "build-vars.json"]) == 0
    assert calls == []
    output = tmp_path / "build-vars.json"
    assert output.is_file()
    assert writes == [output.resolve()]
    assert all(path.is_relative_to(tmp_path.resolve()) for path in writes)

    before = list(writes)
    assert build_vars.main([str(android), str(application)]) == 0
    assert calls == []
    assert writes == before
    assert capsys.readouterr().out == output.read_text(encoding="utf-8")


def test_assignment_operators_comments_and_escaped_hash_are_raw():
    text = """\
LOCAL_MODULE := one
LOCAL_CFLAGS := -DA
LOCAL_CFLAGS += -DB
LOCAL_CFLAGS ?= -DC
LOCAL_CPPFLAGS = -Dfirst
LOCAL_CPPFLAGS = -Dsecond
LOCAL_C_INCLUDES := $(LOCAL_PATH)/a\\#b
LOCAL_ARM_MODE := arm  # not part of the value
"""
    report = build_vars.parse_text(text, "fixture.mk")
    module = report["modules"][0]
    assert module["LOCAL_MODULE"] == "one"
    assert module["LOCAL_CFLAGS"] == "-DA -DB"
    assert module["LOCAL_CPPFLAGS"] == "-Dsecond"
    assert module["LOCAL_C_INCLUDES"] == "$(LOCAL_PATH)/a#b"
    assert module["LOCAL_ARM_MODE"] == "arm"
    assert module["LOCAL_ARM_NEON"] is None
    assert report["globals"] == {name: None for name in build_vars.APP_VARIABLES}


def test_only_non_empty_clear_vars_blocks_become_modules():
    text = """\
include $(CLEAR_VARS)
include $(CLEAR_VARS)
LOCAL_MODULE := only
include $(CLEAR_VARS)
"""
    report = build_vars.parse_text(text, "fixture.mk")
    assert [module["LOCAL_MODULE"] for module in report["modules"]] == ["only"]


def test_unreadable_input_fails_without_a_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        build_vars.main([str(tmp_path / "missing" / "Android.mk")])
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert captured.out == ""
