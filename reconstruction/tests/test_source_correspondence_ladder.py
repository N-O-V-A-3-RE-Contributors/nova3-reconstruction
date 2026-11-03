"""Tests for the deterministic source-correspondence next-unit ladder.

The fixture rows deliberately scramble the four documented subsystem classes and
the two vox addresses.  The checks cover ordering, ranks, ``--next`` selection,
malformed-document refusal (status mismatch and missing row keys), the alternate
``include/glitch/video`` path, and the read-only guarantee (SHA-256 unchanged).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compare import source_correspondence_ladder as ladder  # noqa: E402

SCHEMA = "nova3-source-correspondence/1"
REAL_CORRESPONDENCE = ROOT / "coordination/source_correspondence.json"


def tracked(
    *,
    address: str,
    unit: str,
    file: str,
    status: str = "exact_body_found",
    confidence: str = "strong",
    evidence: str = "1/2",
    line: int = 10,
) -> dict:
    return {
        "address": address,
        "unit": unit,
        "file": file,
        "line": line,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
    }


def make_document(units: list[dict], *, functions: int | None = None) -> dict:
    counts: dict[str, int] = {}
    for row in units:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if functions is None:
        functions = len(units)
    return {
        "schema": SCHEMA,
        "functions": functions,
        "status": counts,
        "tracked_units": units,
    }


def write_document(tmp_path: Path, document: dict, name: str = "correspondence.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


FIVE_UNITS = [
    tracked(
        address="0x00005000",
        unit="gameplay unit",
        file="NOVA3/trunk/src/game/Game.cpp",
        confidence="weak",
        evidence="0/1",
    ),
    tracked(
        address="0x00004000",
        unit="video unit",
        file="NOVA3/extern/glitch/source/glitch/video/CTexture.cpp",
        confidence="signature_match",
        evidence="1/1",
    ),
    tracked(
        address="0x00003000",
        unit="core unit",
        file="NOVA3/extern/glitch/include/glitch/core/matrix4.h",
        confidence="definition_only",
        evidence="0/0",
    ),
    tracked(
        address="0x00009000",
        unit="vox neon unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template_neon.cpp",
        confidence="weak",
        evidence="2/2",
    ),
    tracked(
        address="0x00001000",
        unit="vox unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template.cpp",
        confidence="weak",
        evidence="1/1",
    ),
]


EXPECTED_ADDRESSES = [
    "0x00001000",
    "0x00009000",
    "0x00003000",
    "0x00004000",
    "0x00005000",
]
EXPECTED_SUBSYSTEMS = ["vox", "vox", "glitch::core", "glitch::video", "gameplay/other"]


def test_five_scrambled_units_have_exact_ranks(tmp_path: Path, capsys) -> None:
    path = write_document(tmp_path, make_document(FIVE_UNITS))

    assert ladder.main(["--input", str(path)]) == 0
    captured = capsys.readouterr()
    rows = json.loads(captured.out)

    assert captured.err == ""
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5]
    assert [row["address"] for row in rows] == EXPECTED_ADDRESSES
    assert [row["subsystem"] for row in rows] == EXPECTED_SUBSYSTEMS
    # Field set and order are part of the stable output contract.
    assert [list(row) for row in rows] == [
        [
            "rank",
            "subsystem",
            "address",
            "unit",
            "file",
            "line",
            "status",
            "confidence",
            "evidence",
        ]
    ] * 5


def test_next_prints_only_the_vox_row(tmp_path: Path, capsys) -> None:
    path = write_document(tmp_path, make_document(FIVE_UNITS))

    assert ladder.main(["--input", str(path), "--next"]) == 0
    captured = capsys.readouterr()
    row = json.loads(captured.out)

    assert isinstance(row, dict)
    assert row["rank"] == 1
    assert row["subsystem"] == "vox"
    assert row["address"] == "0x00001000"
    assert row["file"].startswith("NOVA3/extern/vox/")
    assert captured.out.count('"rank"') == 1


def test_alternate_include_video_prefix_is_classified_video() -> None:
    row = tracked(
        address="0x1234",
        unit="video include unit",
        file="NOVA3/extern/glitch/include/glitch/video/CFrameBuffer.h",
    )
    document = make_document([row])
    ladder.validate_document(document)
    assert ladder.build_ladder(document)[0]["subsystem"] == "glitch::video"


def test_status_totals_mismatch_is_refused(tmp_path: Path, capsys) -> None:
    path = write_document(
        tmp_path,
        make_document(FIVE_UNITS, functions=len(FIVE_UNITS) + 1),
    )

    assert ladder.main(["--input", str(path)]) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "inconsistent status totals" in captured.err
    assert "sum to 5" in captured.err
    assert "functions is 6" in captured.err


def test_missing_tracked_unit_key_is_refused(tmp_path: Path, capsys) -> None:
    units = [dict(row) for row in FIVE_UNITS]
    del units[2]["confidence"]
    path = write_document(tmp_path, make_document(units))

    assert ladder.main(["--input", str(path)]) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tracked_units[3]" in captured.err
    assert "confidence" in captured.err


def test_malformed_fixtures_exit_nonzero_from_cli(tmp_path: Path) -> None:
    mismatch = write_document(
        tmp_path,
        make_document(FIVE_UNITS, functions=len(FIVE_UNITS) + 1),
        name="mismatch.json",
    )
    units = [dict(row) for row in FIVE_UNITS]
    del units[0]["address"]
    missing_key = write_document(
        tmp_path,
        make_document(units),
        name="missing-key.json",
    )

    for path, needle in (
        (mismatch, "inconsistent status totals"),
        (missing_key, "address"),
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.compare.source_correspondence_ladder",
                "--input",
                str(path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, (path, result.stdout, result.stderr)
        assert result.stdout == ""
        assert needle in result.stderr


def test_wrong_schema_is_refused(tmp_path: Path, capsys) -> None:
    document = make_document(FIVE_UNITS)
    document["schema"] = "nova3-source-correspondence/2"
    path = write_document(tmp_path, document)

    assert ladder.main(["--input", str(path)]) != 0
    assert "schema must be" in capsys.readouterr().err


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.compare.source_correspondence_ladder", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "--next" in result.stdout
    assert "--input" in result.stdout


def test_correspondence_file_sha256_is_unchanged(tmp_path: Path, capsys) -> None:
    path = write_document(
        tmp_path,
        make_document(FIVE_UNITS),
        name="source_correspondence.json",
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    assert ladder.main(["--input", str(path)]) == 0
    assert ladder.main(["--input", str(path), "--next"]) == 0
    capsys.readouterr()

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before


@pytest.mark.skipif(
    not REAL_CORRESPONDENCE.exists(),
    reason="coordination/source_correspondence.json is not present in this checkout",
)
def test_repository_correspondence_ladder(capsys) -> None:
    before = hashlib.sha256(REAL_CORRESPONDENCE.read_bytes()).hexdigest()

    assert ladder.main(["--input", str(REAL_CORRESPONDENCE)]) == 0
    rows = json.loads(capsys.readouterr().out)

    assert len(rows) == 15
    assert [row["rank"] for row in rows] == list(range(1, 16))
    assert rows[0]["subsystem"] == "vox"
    assert rows[0]["address"] == "0x009d53f4"
    assert all(row["subsystem"] == "vox" for row in rows[:2])
    assert hashlib.sha256(REAL_CORRESPONDENCE.read_bytes()).hexdigest() == before
