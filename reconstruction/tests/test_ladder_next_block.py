"""Tests for the ladder rank-1 ``## Next ladder unit`` block renderer.

The synthetic document follows the ladder tests' fixture shape: two vox rows
(the higher address listed first, to prove the lowest-address vox unit wins), one
gameplay row and one core row, all with distinct addresses.  The synthetic
ledger is the committed ledger's shape
(``{"functions": [{"address": ..., "size": ...}]}``) with an entry only for the
winning address.

Checks cover the golden CLI output, the missing-ledger-entry path (``size:
UNKNOWN``, no failure), schema and confidence refusal, size semantics (ambiguous
or missing sizes stay UNKNOWN, never borrowed from another address), path
escaping, and the read-only guarantee for the live coordination files.
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

from tools.compare import ladder_next_block as block  # noqa: E402

SCHEMA = "nova3-source-correspondence/1"
MODULE = "tools.compare.ladder_next_block"
REAL_CORRESPONDENCE = ROOT / "coordination/source_correspondence.json"
REAL_LEDGER = ROOT / "coordination/function_ledger.json"
REAL_NEXT = ROOT / "docs/progress/NEXT.md"

AXES = (
    "source present",
    "host test",
    "ARM object",
    "instruction-window match",
)


# Test identifiers labelled by the committed confidence vocabulary.
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


def make_document(units: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in units:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "schema": SCHEMA,
        "functions": len(units),
        "status": counts,
        "tracked_units": units,
    }


def write_json(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


THREE_UNITS = [
    tracked(
        address="0x00005000",
        unit="gameplay unit",
        file="NOVA3/trunk/src/game/Game.cpp",
        confidence="weak",
        evidence="0/1",
        line=7,
    ),
    tracked(
        address="0x00009000",
        unit="vox neon unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template_neon.cpp",
        confidence="weak",
        evidence="2/2",
        line=19,
    ),
    tracked(
        address="0x00001000",
        unit="vox unit",
        file="NOVA3/extern/vox/src/vox_driver_callback_template.cpp",
        confidence="weak",
        evidence="1/1",
        line=3383,
    ),
    tracked(
        address="0x00003000",
        unit="core unit",
        file="NOVA3/extern/glitch/include/glitch/core/matrix4.h",
        confidence="definition_only",
        evidence="0/0",
        line=1458,
    ),
]

GOLDEN = """\
## Next ladder unit: vox unit (0x00001000)

- address: 0x00001000 (rank 1); numeric 4096
- size: 56 bytes (0x00000038) (source: coordination/function_ledger.json; the ELF was not read)
- subsystem: vox
- source: NOVA3/extern/vox/src/vox_driver_callback_template.cpp:3383
- correspondence: status exact_body_found, confidence weak, evidence 1/1
- status axes:
  - source present: UNKNOWN
  - host test: UNKNOWN
  - ARM object: UNKNOWN
  - instruction-window match: UNKNOWN

Claims: this block records a source-correspondence *hypothesis* only. The axes above are not evidence of compilation, testing or a machine-code match.

Verify:

1. `python -m tools.compare.source_correspondence_ladder --next` (the correspondence input above)
2. compile `NOVA3/extern/vox/src/vox_driver_callback_template.cpp` as one unit with the recorded toolchain, then compare the instruction window independently.

The operator pastes this block into docs/progress/NEXT.md manually; this tool never edits it.
"""


# Same block, but size: UNKNOWN: the CLI golden runs against the repository's
# default ledger, in which the synthetic address 0x00001000 has no entry.
# The ledger path is printed repository-relative either way.
CLI_GOLDEN = GOLDEN.replace(
    "- size: 56 bytes (0x00000038)",
    "- size: UNKNOWN",
)
assert "UNKNOWN (source: coordination/function_ledger.json; the ELF was not read)" in CLI_GOLDEN


def ledger(*rows: dict) -> dict:
    return {"schema_version": 1, "functions": list(rows)}


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", MODULE, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_block_names_lowest_address_vox_unit_with_ledger_size(
    tmp_path: Path, capfd
) -> None:
    correspondence = write_json(tmp_path, "correspondence.json", make_document(THREE_UNITS))
    sizes = write_json(
        tmp_path,
        "ledger.json",
        ledger(
            {"address": "0x1000", "size": 56},
            {"address": "0x9000", "size": 12},
        ),
    )

    assert block.main(["--input", str(correspondence), "--ledger", str(sizes)]) == 0
    captured = capfd.readouterr()

    assert captured.err == ""
    assert captured.out.startswith("## Next ladder unit: vox unit (0x00001000)\n")
    assert "vox neon unit" not in captured.out
    assert "gameplay unit" not in captured.out
    assert "size: 56 bytes (0x00000038)" in captured.out
    assert "size: UNKNOWN" not in captured.out
    assert "- subsystem: vox\n" in captured.out
    assert (
        "- source: NOVA3/extern/vox/src/vox_driver_callback_template.cpp:3383\n"
        in captured.out
    )
    for axis in AXES:
        assert f"  - {axis}: UNKNOWN\n" in captured.out
    assert captured.out.count("UNKNOWN") == len(AXES)


def block_for_units(units: list[dict], *, size: int | None) -> str:
    row = block.build_row(make_document(units))
    return block.render_block(row, size=size)


def test_missing_ledger_entry_prints_unknown_size_without_failure(
    tmp_path: Path, capfd
) -> None:
    correspondence = write_json(tmp_path, "correspondence.json", make_document(THREE_UNITS))
    sizes = write_json(
        tmp_path,
        "ledger_other.json",
        # Only the non-winning vox unit and an unparsable row are present.
        ledger(
            {"address": "0x9000", "size": 12},
            {"address": "not-an-address", "size": 4},
        ),
    )

    assert block.main(["--input", str(correspondence), "--ledger", str(sizes)]) == 0
    captured = capfd.readouterr()

    assert captured.err == ""
    # An unresolved entry prints UNKNOWN and cites the ledger actually read.
    assert f"- size: UNKNOWN (source: {sizes}; the ELF was not read)" in captured.out
    assert "size: 12" not in captured.out
    assert "0x00000038" not in captured.out


def test_absent_ledger_file_prints_unknown_size(tmp_path: Path, capfd) -> None:
    correspondence = write_json(tmp_path, "correspondence.json", make_document(THREE_UNITS))

    assert block.main(
        ["--input", str(correspondence), "--ledger", str(tmp_path / "absent.json")]
    ) == 0
    assert "- size: UNKNOWN" in capfd.readouterr().out


def test_ambiguous_or_invalid_ledger_size_is_unknown() -> None:
    index = {
        0x1000: [56, 57],  # conflicting sizes: refuse to pick one
        0x2000: [None],  # entry without a usable size
        0x3000: [4, 4],  # duplicate agreement is not ambiguity
        0x4000: [0],  # non-positive size is not a size
    }
    assert block.ledger_size(index, 0x1000) is None
    assert block.ledger_size(index, 0x2000) is None
    assert block.ledger_size(index, 0x3000) == 4
    assert block.ledger_size(index, 0x4000) is None
    assert block.ledger_size(index, 0x5000) is None


@pytest.mark.parametrize("address", ["0x1000", "4096", "0x01000"])
def test_ledger_addresses_match_numerically(address: str) -> None:
    assert block.ledger_size({0x1000: [8]}, block._parse_ledger_address(address)) == 8


def test_cli_matches_embedded_golden(tmp_path: Path) -> None:
    # The golden uses the repository's default ledger. The synthetic 0x00001000
    # is absent from the live ledger, so this also exercises size: UNKNOWN.
    correspondence = write_json(tmp_path, "fixture.json", make_document(THREE_UNITS))

    result = run_cli("--input", str(correspondence))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == CLI_GOLDEN


def test_cli_with_synthetic_ledger_matches_embedded_golden(tmp_path: Path) -> None:
    correspondence = write_json(tmp_path, "fixture.json", make_document(THREE_UNITS))
    sizes = write_json(
        tmp_path,
        "fixture_ledger.json",
        ledger(
            {"address": "0x1000", "size": 56},
            {"address": "0x9000", "size": 12},
        ),
    )
    # Substitute only the fixture's citation; all block content, including the
    # matching byte count, comes from the embedded golden, not the renderer.
    citation = sizes.relative_to(ROOT) if sizes.is_relative_to(ROOT) else sizes
    expected = GOLDEN.replace("coordination/function_ledger.json", citation.as_posix())

    result = run_cli("--input", str(correspondence), "--ledger", str(sizes))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == expected


def test_direct_render_matches_embedded_golden() -> None:
    assert block_for_units(THREE_UNITS, size=56) == GOLDEN


def test_help_exits_zero() -> None:
    result = run_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "--input" in result.stdout
    assert "--ledger" in result.stdout


@pytest.mark.parametrize("wrong_schema", ["nova3-source-correspondence/2", None, ""])
def test_wrong_schema_is_refused(tmp_path: Path, wrong_schema) -> None:
    document = make_document(THREE_UNITS)
    if wrong_schema is None:
        del document["schema"]
    else:
        document["schema"] = wrong_schema
    correspondence = write_json(tmp_path, "bad_schema.json", document)
    sizes = write_json(tmp_path, "ledger.json", ledger({"address": "0x1000", "size": 56}))
    before = hashlib.sha256(sizes.read_bytes()).hexdigest()

    result = run_cli("--input", str(correspondence), "--ledger", str(sizes))

    assert result.returncode != 0
    assert result.stdout == ""
    assert "schema must be" in result.stderr
    assert hashlib.sha256(sizes.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "recorded_confidence",
    ["STRONG", "medium", " exact_body_found", "weak "],
    ids=["uppercase", "unknown-label", "leading-space", "trailing-space"],
)
def test_confidence_outside_vocabulary_is_refused(
    tmp_path: Path, recorded_confidence: str
) -> None:
    units = [dict(row) for row in THREE_UNITS]
    units[2]["confidence"] = recorded_confidence
    correspondence = write_json(tmp_path, "bad_conf.json", make_document(units))
    sizes = write_json(tmp_path, "ledger.json", ledger({"address": "0x1000", "size": 56}))

    result = run_cli("--input", str(correspondence), "--ledger", str(sizes))

    assert result.returncode != 0
    assert result.stdout == ""
    assert "confidence" in result.stderr
    assert "not in the committed vocabulary" in result.stderr


@pytest.mark.parametrize(
    "recorded_confidence",
    block.CONFIDENCE_VOCABULARY,
    ids=block.CONFIDENCE_VOCABULARY,
)
def test_every_committed_confidence_is_accepted(
    tmp_path: Path, recorded_confidence: str
) -> None:
    units = [dict(row) for row in THREE_UNITS]
    units[2]["confidence"] = recorded_confidence
    correspondence = write_json(tmp_path, "ok_conf.json", make_document(units))
    sizes = write_json(tmp_path, "ledger.json", ledger({"address": "0x1000", "size": 56}))

    result = run_cli("--input", str(correspondence), "--ledger", str(sizes))

    assert result.returncode == 0, result.stderr
    assert f"confidence {recorded_confidence}," in result.stdout


def test_element_5_confidence_is_checked_at_rank_1(tmp_path: Path) -> None:
    # The *non-rank-1* gameplay row carries an invented label. The ladder's own
    # validator (which does not check the vocabulary) passes; the renderer must
    # still refuse, because the block cites the document as its source.
    units = [dict(row) for row in THREE_UNITS]
    units[0]["confidence"] = "invented"
    correspondence = write_json(tmp_path, "bad_other.json", make_document(units))
    sizes = write_json(tmp_path, "ledger.json", ledger({"address": "0x1000", "size": 56}))

    result = run_cli("--input", str(correspondence), "--ledger", str(sizes))

    assert result.returncode != 0
    assert result.stdout == ""
    assert "'invented'" in result.stderr


def test_malformed_ledger_is_refused_but_missing_is_not(tmp_path: Path) -> None:
    correspondence = write_json(tmp_path, "correspondence.json", make_document(THREE_UNITS))
    bad = tmp_path / "bad_ledger.json"
    bad.write_text(json.dumps({"functions": "not-a-list"}), encoding="utf-8")

    result = run_cli("--input", str(correspondence), "--ledger", str(bad))

    assert result.returncode != 0
    assert result.stdout == ""
    assert "ledger 'functions' must be an array" in result.stderr


def test_row_text_is_escaped_for_inline_markdown() -> None:
    escaped = block.safe("a<script>&|`b\nc")
    assert "<" not in escaped and ">" not in escaped
    assert "&amp;" in escaped
    assert "\n" not in escaped
    assert "&#96;" in escaped
    assert escaped.count("\\|") == 1


def test_empty_tracked_units_is_refused_without_output() -> None:
    document = {"schema": SCHEMA, "functions": 0, "status": {}, "tracked_units": []}
    with pytest.raises(block.BlockError):
        block.build_row(document)


@pytest.mark.skipif(
    not REAL_CORRESPONDENCE.exists() or not REAL_LEDGER.exists(),
    reason="coordination source-correspondence/ledger files are not in this checkout",
)
def test_live_files_are_unchanged_and_render_rank_one(capfd) -> None:
    watched = [REAL_CORRESPONDENCE, REAL_LEDGER]
    if REAL_NEXT.exists():
        watched.append(REAL_NEXT)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}

    assert block.main([]) == 0
    captured = capfd.readouterr()

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    assert after == before
    assert captured.err == ""
    assert captured.out.startswith(
        "## Next ladder unit: DriverCallbackInterface::Init (0x009d53f4)\n"
    )
    assert "- subsystem: vox\n" in captured.out
    assert (
        "- source: NOVA3/extern/vox/src/vox_driver_callback_template.cpp:3383\n"
        in captured.out
    )
    assert "confidence weak, evidence 1/1" in captured.out
    for axis in AXES:
        assert f"  - {axis}: UNKNOWN\n" in captured.out
