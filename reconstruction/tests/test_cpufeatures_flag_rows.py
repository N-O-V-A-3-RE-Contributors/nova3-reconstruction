"""Flag-row scoring for tools/compare/cpufeatures_pilot.py.

The pilot records measured flag rows for the cpu-features windows.  These
tests feed synthetic fixture rows through the scoring function and check the
per-row fields and that every row is reported MEASURED, never matched.  They
need no reference binary, no compiler and no network.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "tools/compare/cpufeatures_pilot.py"

CAPSTONE = pytest.importorskip("capstone")


def _load_pilot():
    spec = importlib.util.spec_from_file_location("cpufeatures_pilot", PILOT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pilot = _load_pilot()


def thumb_bytes(*words: int) -> bytes:
    return b"".join(w.to_bytes(2, "little") for w in words)


# Fixture reference image: a 32-byte code island at 0x1000.
SXTB = 0xB240   # sxtb r0, r0      (thumb1)
LDRSB = 0x56E0  # ldrsb r0, [r4, r0] (thumb1)
NOP = 0xBF00
_WORDS = (NOP, SXTB, LDRSB, NOP, 0x4770, 0x0000, 0x1C00, 0x2301,
          0x3301, 0x0000, 0x1C08, 0x1C10, 0x1C18, 0x1C20, 0x1C28, 0x1C30)
# Synthetic file image; code at the 0x1000 row address == bytes 0x1000..0x1020.
REFERENCE = bytes(0x1000) + thumb_bytes(*_WORDS) + bytes(96)


def fixture_rows() -> list[dict]:
    return [
        # Full window equal to the reference: still not reported as matched.
        {"label": "fixture-equal", "flags": [], "address": 0x1000, "size": 32,
         "symbol": "android_cpuInit"},
        # Window truncated to 8 bytes after the sxtb/ldrsb.
        {"label": "fixture-short", "flags": ["-fsigned-char"], "address": 0x1000,
         "size": 8, "symbol": "android_cpuInit",
         "matching_words": 2},
        # Window that does not exist in this image -> UNKNOWN.
        {"label": "fixture-out-of-range", "flags": ["-O3"], "address": 0x7FF0,
         "size": 32, "symbol": "android_cpuInit"},
    ]


REQUIRED_FIELDS = ("label", "flags", "size", "sxtb", "ldrsb", "matching_words",
                   "status", "reporting_state", "matched", "byte_match")


def test_every_row_is_measured_with_required_fields():
    rows = pilot.score_rows(REFERENCE, fixture_rows())
    assert len(rows) == 3
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert field in row, (row["label"], field)
        assert row["reporting_state"] == "MEASURED"
        assert row["matched"] is False
        assert row["byte_match"] is False
        # NOT MATCHED or UNKNOWN for the flag under investigation; the
        # synthetic all-equal row is still never a match.
        assert row["status"] in ("NOT_MATCHED", "UNKNOWN",
                                 "ALL_WORDS_EQUAL_NOT_A_MATCH")


def test_per_row_values():
    rows = {r["label"]: r for r in pilot.score_rows(REFERENCE, fixture_rows())}

    equal = rows["fixture-equal"]
    assert equal["flags"] == []
    assert equal["size"] == 32
    assert equal["sxtb"] == 1 and equal["ldrsb"] == 1
    assert equal["ref_sxtb"] == 1 and equal["ref_ldrsb"] == 1
    assert equal["matching_words"] == 16
    assert equal["status"] == "ALL_WORDS_EQUAL_NOT_A_MATCH"
    assert equal["matched"] is False and equal["byte_match"] is False

    short = rows["fixture-short"]
    assert short["flags"] == ["-fsigned-char"]
    assert short["size"] == 8
    assert short["matching_words"] == 2
    assert short["status"] == "NOT_MATCHED"

    unknown = rows["fixture-out-of-range"]
    assert unknown["status"] == "UNKNOWN"
    assert unknown["size"] is None
    assert unknown["matching_words"] is None
    assert unknown["reporting_state"] == "MEASURED"


def test_counts_follow_bytes_not_claims():
    rows = pilot.score_rows(REFERENCE, [
        {"label": "unsigned", "flags": ["-funsigned-char"], "address": 0x1000,
         "size": 32, "sxtb": 0, "ldrsb": 0, "matching_words": 10},
    ])
    assert rows[0]["sxtb"] == 0 and rows[0]["ldrsb"] == 0
    assert rows[0]["matching_words"] == 10
    assert rows[0]["status"] == "NOT_MATCHED"


def test_no_reference_means_unknown_not_match():
    rows = pilot.score_rows(None, fixture_rows())
    assert [r["status"] for r in rows] == ["UNKNOWN"] * 3
    assert all(r["matched"] is False for r in rows)


def test_mk_text_evidence_parsing(tmp_path):
    mk = tmp_path / "Android.mk"
    mk.write_text("LOCAL_PATH := $(call my-dir)\n"
                  "LOCAL_MODULE := cpufeatures\n"
                  "LOCAL_SRC_FILES := cpu-features.c\n"
                  "LOCAL_CFLAGS := -O2 -fsigned-char   # comment\n"
                  "LOCAL_CPPFLAGS += -fno-exceptions\n")
    parsed = pilot.declared_flags_from_mk(mk)
    assert parsed["error"] is None
    assert parsed["flags"] == ["-O2", "-fsigned-char", "-fno-exceptions"]

    unexpandable = tmp_path / "Other.mk"
    unexpandable.write_text("LOCAL_CFLAGS := $(VOX_LOCAL_CFLAGS)\n")
    assert pilot.declared_flags_from_mk(unexpandable)["error"]

    no_flags = tmp_path / "Empty.mk"
    no_flags.write_text("LOCAL_MODULE := cpufeatures\n")
    parsed = pilot.declared_flags_from_mk(no_flags)
    assert parsed["error"] is None and parsed["flags"] == []


def test_verdict_never_reports_match():
    rows = pilot.score_rows(REFERENCE, fixture_rows())
    assert pilot.cpuInit_verdict(rows) in ("NOT_MATCHED", "UNKNOWN")
    equal_only = [r for r in rows if r["label"] == "fixture-equal"]
    assert pilot.cpuInit_verdict(equal_only) == "UNKNOWN"


def test_flag_row_mode_writes_measured_report(tmp_path):
    """CLI refuse path or measured rows: never a match claim in the JSON."""
    reference = tmp_path / "ref.so"
    reference_bytes = REFERENCE
    reference.write_bytes(reference_bytes)

    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(PILOT), "--reference", str(reference),
         "--flags", "", "--label", "empty", "--workdir", str(tmp_path / "wd"),
         "--cc", sys.executable, "--out", str(out)],
        capture_output=True, text=True, timeout=120)
    # A non-compiler refuses the compile; the pilot must not crash silently.
    assert proc.returncode in (0, 2), proc.stderr
    if proc.returncode == 2:
        assert not out.exists()
        return
    report = json.loads(out.read_text())
    assert report["rows_measured_not_matched"] is True
    assert report["android_cpuInit_result"] in ("NOT_MATCHED", "UNKNOWN")
    for row in report["rows"]:
        assert row["reporting_state"] == "MEASURED"
        assert row["byte_match"] is False
    as_text = out.read_text()
    assert '"byte_match": true' not in as_text
