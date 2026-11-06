"""CLI projection contract for ``build_source_correspondence.main`` (task #322).

This module pins only the *command-line plumbing* of
``tools.source_ref.build_source_correspondence.main``: which exit code the
invocation produces, what lands on stdout/stderr, which files ``--write``
creates, and how many times the module-level ``correlate``/``summarise``
helpers are called.

``correlate`` and ``summarise`` are replaced by recording fakes that emit
fixed synthetic rows (addresses ``0x100`` and ``0x200``).  ``load_index`` is
replaced by a fake returning a minimal in-memory index.  No symbol table is
read, no source tree is scanned and neither ``reference/nova3_source`` nor the
Premium ELF is touched; ``--root``/``--symbols``/``--index`` are tmp_path-only
arguments that the fakes never dereference.

A passing run therefore means the CLI *attributes* status and shapes output
as described.  It is **not** evidence that any correspondence row is correct,
that ``correlate``/``summarise`` behave as documented, or that any function is
reconstructed: row contents here are synthetic.  The real orchestration and
summary aggregation belong to separate planned tasks and are deliberately not
asserted beyond "called exactly once, with these arguments".
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_NAME = "tools.source_ref.build_source_correspondence"
pytest.importorskip(MODULE_NAME, reason="build_source_correspondence is absent")
correspondence = sys.modules[MODULE_NAME]
main = correspondence.main

SCHEMA = "nova3-source-correspondence/1"
NO_FUNCTION_TEXT = "no function at {addr}"


def _row(address: int) -> dict:
    """One synthetic correspondence row; contents are fixed, not analysed."""
    tag = "%x" % address
    return {
        "address": address,
        "size": 0x10,
        "mode": "thumb" if address == 0x100 else "arm",
        "mangled": "_Z" + tag + "stub",
        "demangled": "synthetic_stub_" + tag + "()",
        "file_hint": "",
        "status": "exact_body_found" if address == 0x100 else "not_found",
        "file": "synthetic/stub_" + tag + ".cpp",
        "line": address,
        "evidence_total": 1,
        "evidence_hits": 1 if address == 0x100 else 0,
        "evidence_ratio": 1.0 if address == 0x100 else 0.0,
        "confidence": "strong" if address == 0x100 else "weak",
    }


ROWS = [_row(0x100), _row(0x200)]


class Fakes:
    """Recording stand-ins for the module's orchestration helpers."""

    def __init__(self) -> None:
        self.correlate_calls: list[tuple] = []
        self.summarise_calls: list[list] = []
        self.rows = copy.deepcopy(ROWS)

    def correlate(self, *args, **kwargs):
        self.correlate_calls.append((args, kwargs))
        return copy.deepcopy(self.rows)

    def summarise(self, rows):
        # Fixed, index-free summary: index aggregation is out of scope here.
        self.summarise_calls.append(copy.deepcopy(rows))
        return {
            "schema": SCHEMA,
            "functions": len(rows),
            "status": {"synthetic": len(rows)},
            "strong_evidence": 0,
            "confidence": {},
            "by_evidence_ratio": {},
            "top_subsystems": [],
            "status_by_subsystem": {},
            "not_found_reasons": {},
            "tracked_units": [],
            "notes": [],
        }


def _minimal_index(*_args, **_kwargs) -> dict:
    """A load_index stand-in: no file is opened, no source tree is scanned."""
    return {
        "schema": "nova3-source-defs/2",
        "root": "<tmp>",
        "files": 0,
        "keys": 0,
        "free_keys": 0,
        "definitions": 0,
        "free_definitions": 0,
        "index": {},
        "free": {},
    }


@pytest.fixture()
def fakes(monkeypatch):
    fake = Fakes()
    monkeypatch.setattr(correspondence, "correlate", fake.correlate)
    monkeypatch.setattr(correspondence, "summarise", fake.summarise)
    monkeypatch.setattr(correspondence, "load_index", _minimal_index)
    return fake


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """tmp_path-only working directory and argument paths."""
    monkeypatch.chdir(tmp_path)
    return {
        "tmp": tmp_path,
        "root": tmp_path / "root",
        "symbols": tmp_path / "s.jsonl",
        "index": tmp_path / "i.json",
        "out": tmp_path / "out" / "correspondence.jsonl",
        "summary": tmp_path / "out" / "summary.json",
        "markdown": tmp_path / "out" / "report.md",
    }


def _base_args(paths) -> list[str]:
    return [
        "--root", str(paths["root"]),
        "--symbols", str(paths["symbols"]),
        "--index", str(paths["index"]),
    ]


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    status = main(argv)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


# --------------------------------------------------------------------------
# --show <matching address>
# --------------------------------------------------------------------------

def test_show_existing_address_prints_only_that_row(fakes, paths, capsys):
    status, out, _err = _run(
        capsys, _base_args(paths) + ["--show", "0x200"])

    assert status == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert [row["address"] for row in payload] == [0x200]
    assert payload[0] == ROWS[1]
    assert len(fakes.correlate_calls) == 1
    # --show must not run the writer/aggregator path.
    assert fakes.summarise_calls == []


def test_show_reports_every_row_at_that_address(fakes, paths, capsys):
    duplicate = dict(ROWS[0], demangled="synthetic_duplicate()")
    fakes.rows = [ROWS[0], ROWS[1], duplicate]

    status, out, _err = _run(
        capsys, _base_args(paths) + ["--show", "0x100"])

    assert status == 0
    payload = json.loads(out)
    assert [row["demangled"] for row in payload] == [
        "synthetic_stub_100()", "synthetic_duplicate()"]
    assert len(fakes.correlate_calls) == 1


def test_show_matching_is_exact_and_hex_case_insensitive(fakes, paths, capsys):
    # 0x10 is a prefix of 0x100: only exact address equality may match.
    status, out, err = _run(
        capsys, _base_args(paths) + ["--show", "0x10"])
    assert status == 2
    assert out == ""
    assert NO_FUNCTION_TEXT.format(addr="0x10") in err

    status, out, _err = _run(
        capsys, _base_args(paths) + ["--show", "0X200"])
    assert status == 0
    # int(..., 16) normalises case: the 0x200 row, printed with integer address.
    assert [row["address"] for row in json.loads(out)] == [0x200]
    assert len(fakes.correlate_calls) == 2


# --------------------------------------------------------------------------
# --show <missing address>
# --------------------------------------------------------------------------

def test_show_missing_address_returns_2_with_stderr_diagnostic(fakes, paths,
                                                               capsys):
    status, out, err = _run(
        capsys, _base_args(paths) + ["--show", "0x300"])

    assert status == 2
    assert out == ""
    assert NO_FUNCTION_TEXT.format(addr="0x300") in err
    assert len(fakes.correlate_calls) == 1
    assert fakes.summarise_calls == []


# --------------------------------------------------------------------------
# --write (and --markdown)
# --------------------------------------------------------------------------

def test_write_emits_jsonl_summary_and_returns_zero(fakes, paths, capsys):
    status, out, _err = _run(
        capsys,
        _base_args(paths)
        + ["--write", "--out", str(paths["out"]),
           "--summary", str(paths["summary"]), "--markdown", str(paths["markdown"])],
    )

    assert status == 0
    assert len(fakes.correlate_calls) == 1
    assert len(fakes.summarise_calls) == 1

    # JSONL: one JSON object per line, in row order.
    lines = paths["out"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(ROWS)
    written = [json.loads(line) for line in lines if line.strip()]
    assert [row["address"] for row in written] == [0x100, 0x200]
    assert written == ROWS

    # Summary file: the JSON the fake produced plus the index block main adds.
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["functions"] == len(ROWS)
    assert summary["schema"] == SCHEMA
    assert summary["index"]["files"] == 0
    assert summary["index"]["definitions"] == 0

    # --markdown: the rendered document from the real renderer.
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert markdown == correspondence.render_markdown(summary) + "\n"
    assert markdown.startswith("# ELF <-> pre-release source correspondence")

    # The summary JSON is always printed, even after the writer messages.
    assert f"wrote {paths['out']} ({len(ROWS)} rows)" in out
    tail_start = out.index("{\n")
    assert json.loads(out[tail_start:])["functions"] == len(ROWS)


def test_write_without_markdown_writes_only_jsonl_and_summary(fakes, paths,
                                                              capsys):
    status, out, _err = _run(
        capsys,
        _base_args(paths)
        + ["--write", "--out", str(paths["out"]),
           "--summary", str(paths["summary"])],
    )

    assert status == 0
    assert paths["out"].is_file()
    assert paths["summary"].is_file()
    assert not paths["markdown"].exists()
    assert f"wrote {paths['out']} ({len(ROWS)} rows) and {paths['summary']}" in out
    assert ".md" not in out
    assert len(fakes.summarise_calls) == 1
    # Default paths in the tmp cwd must not have been touched.
    assert not (paths["tmp"] / ".cache/source_ref/correspondence.jsonl").exists()
    assert not (paths["tmp"] / "coordination/source_correspondence.json").exists()


def test_without_write_reports_summary_and_writes_nothing(fakes, paths, capsys):
    status, out, _err = _run(
        capsys,
        _base_args(paths)
        + ["--out", str(paths["out"]), "--summary", str(paths["summary"])],
    )

    assert status == 0
    assert json.loads(out)["functions"] == len(ROWS)
    assert not paths["out"].exists()
    assert not paths["summary"].exists()
    assert not paths["tmp"].joinpath("coordination").exists()
    assert len(fakes.correlate_calls) == 1
    assert len(fakes.summarise_calls) == 1


# --------------------------------------------------------------------------
# invalid --show values are rejected by argparse (exit 2, correlate not run)
# --------------------------------------------------------------------------

def test_argparse_rejects_missing_show_value_before_correlating(fakes, paths,
                                                               capsys):
    # argparse-level rejection: `--show` with no argument.
    with pytest.raises(SystemExit) as excinfo:
        main(_base_args(paths) + ["--show"])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "expected one argument" in captured.err
    assert fakes.correlate_calls == []
    assert fakes.summarise_calls == []


@pytest.mark.xfail(
    strict=True,
    reason=("KNOWN GAP: --show is declared without type= so argparse accepts "
            "'0xZZ', 'xyz', '', '0x'; main() then calls correlate() and dies "
            "with an uncaught ValueError (exit 1), instead of argparse exit 2. "
            "'-1' is also accepted and gives the exit-2 'no function' path. "
            "Fix belongs in tools/source_ref/build_source_correspondence.py, "
            "outside this test's allowed path."),
)
@pytest.mark.parametrize("bad", ["0xZZ", "xyz", "", "0x", "-1"])
def test_invalid_show_value_rejected_by_argparse(fakes, paths, capsys, bad):
    with pytest.raises(SystemExit) as excinfo:
        main(_base_args(paths) + ["--show", bad])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid" in captured.err.lower()
    assert fakes.correlate_calls == []
    assert fakes.summarise_calls == []


def test_unparseable_show_reaches_correlate_then_raises(fakes, paths, capsys):
    """Pin the current (non-conforming) behaviour so a fix trips this test."""
    with pytest.raises(ValueError):
        main(_base_args(paths) + ["--show", "zz-not-hex"])
    assert len(fakes.correlate_calls) == 1
    assert fakes.summarise_calls == []


def test_negative_show_value_reports_no_function(fakes, paths, capsys):
    """'-1' is accepted by argparse; int('-1', 16) is negative, so no row matches."""
    status, out, err = _run(capsys, _base_args(paths) + ["--show", "-1"])

    assert status == 2
    assert out == ""
    assert NO_FUNCTION_TEXT.format(addr="-1") in err
    assert len(fakes.correlate_calls) == 1
