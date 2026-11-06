"""Tests for the ladder step-4 per-subsystem coverage summary.

Every fixture is synthetic JSON written to ``tmp_path``; the repository's
``coordination/source_correspondence.json`` is never read here.  The checks
cover the documented subsystem order (including zero rows), column sums versus
``len(tracked_units)``, byte-identical repeated output, the read-only guarantee
(SHA-256 unchanged) and the malformed-document refusal (exit 2, stderr only).
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

from tools.compare import ladder_coverage_summary as coverage  # noqa: E402

SCHEMA = "nova3-source-correspondence/1"
MODULE = "tools.compare.ladder_coverage_summary"


def tracked(
    *,
    address: str,
    unit: str,
    file: str,
    status: str,
    confidence: str,
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


def make_document(
    units: list[dict],
    *,
    status_names: tuple[str, ...] | None = None,
    confidence_names: tuple[str, ...] | None = None,
    functions: int | None = None,
) -> dict:
    """Build a document whose declared status/confidence objects cover every
    fixture row plus any extra names explicitly requested (zero-count names)."""
    counts: dict[str, int] = {}
    for row in units:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    for name in status_names or ():
        counts.setdefault(name, 0)
    confidence: dict[str, int] = {name: 0 for name in confidence_names or ()}
    for row in units:
        confidence.setdefault(row["confidence"], 0)
    if functions is None:
        functions = sum(counts.values())
    return {
        "schema": SCHEMA,
        "functions": functions,
        "status": dict(sorted(counts.items())),
        "confidence": dict(sorted(confidence.items())),
        "tracked_units": units,
    }


def write_document(tmp_path: Path, document: dict, name: str = "correspondence.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


EXACT = "exact_body_found"
DIFF = "same_file_different_revision"
MISSING = "not_found"

# Deliberately scrambled; gameplay/other is populated so the order check is
# meaningful, and a later fixture removes every gameplay/other row.
FIXTURE_UNITS = [
    tracked(
        address="0x00005000",
        unit="gameplay unit",
        file="NOVA3/trunk/src/game/Game.cpp",
        status=EXACT,
        confidence="weak",
        evidence="0/1",
    ),
    tracked(
        address="0x00004000",
        unit="video source unit",
        file="NOVA3/extern/glitch/source/glitch/video/CTexture.cpp",
        status=DIFF,
        confidence="signature_match",
        evidence="1/1",
    ),
    tracked(
        address="0x00003000",
        unit="core unit",
        file="NOVA3/extern/glitch/include/glitch/core/matrix4.h",
        status=EXACT,
        confidence="definition_only",
        evidence="0/0",
    ),
    tracked(
        address="0x00009000",
        unit="vox neon unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template_neon.cpp",
        status=DIFF,
        confidence="weak",
        evidence="2/2",
    ),
    tracked(
        address="0x00001000",
        unit="vox unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template.cpp",
        status=EXACT,
        confidence="weak",
        evidence="1/1",
    ),
    tracked(
        address="0x00006000",
        unit="another gameplay unit",
        file="./NOVA3\\trunk\\src\\game\\AI.cpp",
        status=MISSING,
        confidence="none",
        evidence="0/0",
    ),
    tracked(
        address="0x00002000",
        unit="video include unit",
        file="NOVA3/extern/glitch/include/glitch/video/CFrameBuffer.h",
        status=EXACT,
        confidence="strong",
        evidence="3/3",
    ),
]


def split_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Return (header cells, body rows as cell lists) from the Markdown table."""
    lines = [line for line in text.splitlines() if line.startswith("|")]
    assert len(lines) >= 3, text

    def cells(line: str) -> list[str]:
        inner = line.strip()
        assert inner.startswith("|") and inner.endswith("|")
        return [part.strip() for part in inner[1:-1].split("|")]

    header = cells(lines[0])
    for row_line in lines[2:]:
        assert len(cells(row_line)) == len(header), row_line
    return header, [cells(line) for line in lines[2:]]


def row_map(body: list[list[str]], header: list[str]) -> dict[str, dict[str, int | str]]:
    result: dict[str, dict[str, int | str]] = {}
    for row in body:
        record: dict[str, int | str] = {}
        for name, value in zip(header, row):
            record[name] = value if name in ("subsystem", "next rank") else int(value)
        result[str(record["subsystem"])] = record
    return result


def test_subsystem_order_and_zero_rows(tmp_path: Path, capsys) -> None:
    path = write_document(tmp_path, make_document(FIXTURE_UNITS))
    assert coverage.main(["--input", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""

    header, body = split_table(captured.out)
    subsystems = [row[0] for row in body]
    assert subsystems == ["vox", "glitch::core", "glitch::video", "gameplay/other", "total"]

    rows = row_map(body, header)
    assert rows["vox"]["tracked"] == 2
    assert rows["glitch::core"]["tracked"] == 1
    assert rows["glitch::video"]["tracked"] == 2
    assert rows["gameplay/other"]["tracked"] == 2
    assert rows["total"]["tracked"] == len(FIXTURE_UNITS)

    # Matrix normalization: "./" and backslashes still land in gameplay/other.
    assert rows["gameplay/other"]["status:not_found"] == 1

    # Next rank comes from the ladder order: vox rows are ranks 1-2, core the
    # third row, video ranks 4-5, gameplay/other rank 6.
    assert rows["vox"]["next rank"] == "1"
    assert rows["glitch::core"]["next rank"] == "3"
    assert rows["glitch::video"]["next rank"] == "4"
    assert rows["gameplay/other"]["next rank"] == "6"
    assert rows["total"]["next rank"] == "-"

    # Columns follow the declared (sorted) names, not first-seen order.
    assert header[:2] == ["subsystem", "tracked"]
    status_columns = [name for name in header if name.startswith("status:")]
    confidence_columns = [name for name in header if name.startswith("confidence:")]
    assert status_columns == sorted(status_columns)
    assert confidence_columns == sorted(confidence_columns)
    assert header[-1] == "next rank"
    # Unsorted names collapse to the same declared order regardless of input order.
    reordered = make_document(FIXTURE_UNITS)
    reordered["status"] = dict(reversed(list(reordered["status"].items())))
    reordered["confidence"] = dict(reversed(list(reordered["confidence"].items())))
    reordered_path = write_document(tmp_path, reordered, name="reordered.json")
    assert coverage.main(["--input", str(reordered_path)]) == 0
    assert capsys.readouterr().out == captured.out


def test_empty_subsystem_prints_zero_row(tmp_path: Path, capsys) -> None:
    units = [
        row
        for row in FIXTURE_UNITS
        if "trunk" not in row["file"]
    ]
    path = write_document(tmp_path, make_document(units))
    assert coverage.main(["--input", str(path)]) == 0
    header, body = split_table(capsys.readouterr().out)
    rows = row_map(body, header)

    empty = rows["gameplay/other"]
    assert empty["tracked"] == 0
    assert all(value == 0 for key, value in empty.items() if key not in ("subsystem", "next rank"))
    assert empty["next rank"] == "-"
    # Declared names without any tracked row still get a column.
    assert "status:exact_body_found" in header
    assert "confidence:weak" in header


def test_counts_sum_to_tracked_and_total(tmp_path: Path, capsys) -> None:
    document = make_document(
        FIXTURE_UNITS,
        status_names=("declared_but_unused",),
        confidence_names=("declared_but_unused",),
    )
    path = write_document(tmp_path, document)
    assert coverage.main(["--input", str(path)]) == 0
    header, body = split_table(capsys.readouterr().out)
    status_columns = [name for name in header if name.startswith("status:")]
    confidence_columns = [name for name in header if name.startswith("confidence:")]
    assert "status:declared_but_unused" in status_columns
    assert "confidence:declared_but_unused" in confidence_columns

    rows = row_map(body, header)
    tracked_total = 0
    for name, row in rows.items():
        status_sum = sum(row[column] for column in status_columns)
        confidence_sum = sum(row[column] for column in confidence_columns)
        assert status_sum == row["tracked"], name
        assert confidence_sum == row["tracked"], name
        if name != "total":
            tracked_total += row["tracked"]
    assert tracked_total == len(FIXTURE_UNITS)
    assert rows["total"]["tracked"] == len(document["tracked_units"])


def test_empty_tracked_units_prints_only_zero_subsystems(tmp_path: Path, capsys) -> None:
    path = write_document(tmp_path, make_document([], status_names=(EXACT,), confidence_names=("weak",)))
    assert coverage.main(["--input", str(path)]) == 0
    header, body = split_table(capsys.readouterr().out)
    assert [row[0] for row in body] == ["vox", "glitch::core", "glitch::video", "gameplay/other", "total"]
    assert all(row_map(body, header)[row[0]]["tracked"] == 0 for row in body)


def run_cli(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", MODULE, "--input", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_repeated_runs_are_byte_identical_and_input_unchanged(tmp_path: Path, capsys) -> None:
    path = write_document(tmp_path, make_document(FIXTURE_UNITS), name="source_correspondence.json")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    before_bytes = path.read_bytes()

    assert coverage.main(["--input", str(path)]) == 0
    first = capsys.readouterr().out
    assert coverage.main(["--input", str(path)]) == 0
    second = capsys.readouterr().out

    subprocess_first = run_cli(path)
    subprocess_second = run_cli(path)

    assert subprocess_first.returncode == 0, subprocess_first.stderr
    assert subprocess_first.stdout == subprocess_second.stdout == first == second
    assert first.endswith("\n")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert path.read_bytes() == before_bytes
    assert subprocess_first.stderr == ""


def test_malformed_documents_exit_2_without_table(tmp_path: Path) -> None:
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{not json", encoding="utf-8")

    mismatch = make_document(FIXTURE_UNITS, functions=len(FIXTURE_UNITS) + 1)
    mismatch_path = write_document(tmp_path, mismatch, name="mismatch.json")

    units = [dict(row) for row in FIXTURE_UNITS]
    del units[2]["confidence"]
    broken = make_document(FIXTURE_UNITS)
    broken["tracked_units"] = units
    missing_key_path = write_document(tmp_path, broken, name="missing-key.json")

    wrong_schema = make_document(FIXTURE_UNITS)
    wrong_schema["schema"] = "nova3-source-correspondence/0"
    wrong_schema_path = write_document(tmp_path, wrong_schema, name="wrong-schema.json")

    undeclared_status = make_document(FIXTURE_UNITS)
    undeclared_status["status"].pop(MISSING, None)
    # Keep the aggregate total consistent so only the row check can refuse it.
    undeclared_status["functions"] -= 1
    undeclared_path = write_document(tmp_path, undeclared_status, name="undeclared-status.json")

    missing_file = tmp_path / "does-not-exist.json"

    for path, needle in (
        (invalid_json, "invalid JSON"),
        (mismatch_path, "inconsistent status totals"),
        (missing_key_path, "confidence"),
        (wrong_schema_path, "schema must be"),
        (undeclared_path, "not declared"),
        (missing_file, "cannot read"),
    ):
        result = run_cli(path)
        assert result.returncode == 2, (path, result.stdout, result.stderr)
        assert result.stdout == "", (path, result.stdout)
        assert result.stderr.startswith("error: "), (path, result.stderr)
        assert needle in result.stderr, (path, result.stderr)
        assert "|" not in result.stdout


def test_undeclared_tracked_confidence_exits_2_without_table(tmp_path: Path) -> None:
    document = make_document(FIXTURE_UNITS)
    del document["confidence"]["weak"]
    path = write_document(tmp_path, document, name="undeclared-confidence.json")

    result = run_cli(path)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("error: "), result.stderr
    assert "confidence" in result.stderr
    assert "'weak'" in result.stderr
    assert "not declared" in result.stderr


def test_invalid_utf8_exits_2_without_table(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.json"
    path.write_bytes(b'{"schema": "\xff"}')

    result = run_cli(path)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("error: "), result.stderr
    assert "utf-8" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("count", [-1, True, False, 1.5, "1", None, [], {}])
def test_invalid_confidence_counts_exit_2_without_table(tmp_path: Path, count) -> None:
    document = make_document(FIXTURE_UNITS)
    document["confidence"]["weak"] = count
    path = write_document(tmp_path, document)

    result = run_cli(path)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("error: "), result.stderr
    assert "confidence count 'weak' must be a non-negative integer" in result.stderr


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_invalid_confidence_names_exit_2_without_table(tmp_path: Path, name: str) -> None:
    document = make_document(FIXTURE_UNITS)
    document["confidence"][name] = 0
    path = write_document(tmp_path, document)

    result = run_cli(path)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("error: "), result.stderr
    assert "'confidence' names must be non-empty strings" in result.stderr


@pytest.mark.parametrize("name", [None, 1, False])
def test_non_string_confidence_names_rejected_by_summary(name) -> None:
    # JSON object keys are always strings; exercise the parsed-document API.
    document = make_document(FIXTURE_UNITS)
    document["confidence"][name] = 0

    with pytest.raises(coverage.CorrespondenceError, match="names must be non-empty strings"):
        coverage.build_summary(document)


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", MODULE, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "--input" in result.stdout
    assert "inventory only" in result.stdout.lower()
