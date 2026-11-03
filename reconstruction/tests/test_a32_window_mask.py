"""Metadata-only regression checks; synthetic encodings are source-owned fixtures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "a32_window_mask", ROOT / "tools/compare/a32_window_mask.py")
mask = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mask)

ROW_FIELDS = {
    "address", "symbol", "symtab_size", "cited_size", "size_agreement", "mode",
    "literal_subset_count", "directive_word_count", "verdict", "reason", "declaration",
}


@pytest.fixture(scope="module")
def premium():
    reference = mask.default_reference()
    if not reference.is_file():
        pytest.skip("local Premium ELF unavailable: counts require the read-only reference")
    data = mask._read(reference)
    assert hashlib.sha256(data).hexdigest() == mask.REFERENCE_SHA256
    return reference, data


def assert_counts_only(report):
    assert set(report) == {"schema", "reference_sha256", "declaration",
                           "evidence_status", "rows"}
    assert report["schema"] == "nova3.a32-window-mask/1"
    assert report["declaration"] == "NO MATCH"
    assert len(report["rows"]) == 4
    for row in report["rows"]:
        assert set(row) == ROW_FIELDS
        assert all(type(v) in (int, str) for v in row.values())
        assert re.fullmatch(r"0x[0-9a-f]{8}", row["address"])
        assert row["verdict"] in {"ok", "stale", "missing"}
        assert row["declaration"] == "NO MATCH"
        for name in ("literal_subset_count", "directive_word_count"):
            assert type(row[name]) is int
            assert -1 <= row[name] <= row["cited_size"] // 4
        assert row["literal_subset_count"] <= 0x10000
    text = json.dumps(report)
    assert not re.search(r"\b(?:svc|bkpt|pld|wfi|ldr)\b", text, re.IGNORECASE)
    # No decoded values disguised as hex strings outside entry-address fields.
    assert re.findall(r"0x[0-9a-f]+", text) == [r["address"] for r in report["rows"]]


def test_local_premium_counts_and_bounds(premium):
    _, data = premium
    before = hashlib.sha256(data).hexdigest()
    report = mask.worksheet(data)
    assert_counts_only(report)
    assert report == mask.worksheet(data)  # deterministic, no timestamps
    assert report["evidence_status"] == "COUNTS_RECORDED"
    expected = ((0x006E7E60, 564, 0), (0x0073F0A4, 228, 0),
                (0x000D2F98, 2632, 1), (0x00108C8C, 240, 0))
    for row, (address, size, subset) in zip(report["rows"], expected):
        assert int(row["address"], 16) == address
        assert row["symtab_size"] == row["cited_size"] == size
        assert row["size_agreement"] == "yes"
        assert row["mode"] == "ARM" and row["verdict"] == "ok"
        assert row["literal_subset_count"] == subset
        assert row["directive_word_count"] == 0
    assert hashlib.sha256(data).hexdigest() == before


@pytest.mark.parametrize("encoding", [
    0xEF000123, 0x1F000123,                 # conditional exception patterns
    0xE120AB7C,                           # breakpoint with nonzero immediate
    0xF5D1F123, 0xF551F123,               # prefetch immediate, either offset sign
    0xF7D1F002, 0xF751F002,               # prefetch register, either offset sign
    0xE320F003, 0x1320F003,               # wait hint, two conditions
])
def test_censored_encoding_patterns(encoding):
    assert mask.count_window(encoding.to_bytes(4, "little"), 0x20000) == (0, 1)


@pytest.mark.parametrize("encoding", [
    0xFF000123, 0xE120AB6C, 0x1120AB7C,   # reserved condition / near misses
    0xF591F123, 0xF7D1F012,               # different prefetch / invalid register form
    0xE320F002, 0xF320F003, 0xE1A00000,
])
def test_nearby_encodings_are_not_censored(encoding):
    assert mask.count_window(encoding.to_bytes(4, "little"), 0x20000) == (0, 0)


def _encoded(*values):
    return b"".join(value.to_bytes(4, "little") for value in values)


def test_literal_subset_is_unique_and_strictly_below_threshold():
    # Synthetic loads target offsets 20,20,24,28,32; repeated references count
    # once. The inclusive edge 0x10000 is deliberately outside this subset.
    window = _encoded(0xE59F000C, 0xE59F0008, 0xE59F0008, 0xE59F0008,
                      0xE59F0008, 0xFFFF, 0x10000, 0x10001, 0)
    assert mask.count_window(window, 0x20000) == (2, 0)


def test_literal_target_is_data_even_when_it_has_a_censored_pattern():
    # Target offset 8 contains a synthetic exception pattern, as does offset 4.
    assert mask.count_window(_encoded(0xE59F0000, 0xEF000123, 0xEF000123),
                             0x20000) == (0, 1)
    # Backward literal and a target exactly outside the bounded window.
    assert mask.count_window(_encoded(7, 0xE51F000C), 0x20000) == (1, 0)
    assert mask.count_window(_encoded(0xE59F0000), 0x20000) == (0, 0)


@pytest.mark.parametrize("window,address", [(b"\0", 0x20000), (b"\0" * 4, 0x20002)])
def test_unaligned_a32_windows_are_rejected(window, address):
    with pytest.raises(ValueError, match="aligned"):
        mask.count_window(window, address)


@pytest.mark.parametrize("changes,verdict,reason", [
    ({"size": 8}, "stale", "size_disagrees"),
    ({"address": 0x20004}, "stale", "address_disagrees"),
    ({"thumb": True}, "stale", "not_a32"),
    ({"table": ".dynsym"}, "missing", "symbol_absent"),
    ({"name": "neighbour"}, "missing", "symbol_absent"),
])
def test_changed_metadata_is_not_scanned(monkeypatch, changes, verdict, reason):
    def forbidden(*args):
        pytest.fail("untrusted extent was scanned")
    monkeypatch.setattr(mask, "count_window", forbidden)
    observation = dict(address=0x20000, name="fixture", table=".symtab",
                       size=4, thumb=False, file_offset=0)
    observation.update(changes)
    row = mask._row(b"\0" * 8, [observation], (0x20000, "fixture", 4))
    assert row["verdict"] == verdict and row["reason"] == reason
    assert row["literal_subset_count"] == row["directive_word_count"] == -1


def test_conflicting_symbols_fail_closed():
    observation = dict(address=0x20000, name="fixture", table=".symtab",
                       size=4, thumb=False, file_offset=0)
    row = mask._row(b"\0" * 8, [observation, observation], (0x20000, "fixture", 4))
    assert row["verdict"] == "stale" and row["reason"] == "ambiguous_symbol"
    alias = dict(observation, name="alias", size=8)
    row = mask._row(b"\0" * 8, [observation, alias], (0x20000, "fixture", 4))
    assert row["reason"] == "conflicting_bounds_or_mode"
    assert row["literal_subset_count"] == -1


def test_missing_reference_and_wrong_hash():
    report = mask.worksheet(None)
    assert_counts_only(report)
    assert report["evidence_status"] == "NO NEW EVIDENCE"
    assert all(r["verdict"] == "missing" and r["symtab_size"] == -1
               and r["literal_subset_count"] == -1 for r in report["rows"])
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mask.worksheet(b"source-owned invalid reference")


def test_cli_local_reference_is_counts_only_and_read_only(premium, tmp_path):
    reference, _ = premium
    before = reference.stat()
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix="a32-test-") as directory:
        output = Path(directory) / "counts.json"
        command = [sys.executable, str(ROOT / "tools/compare/a32_window_mask.py"),
                   "--reference", str(reference), "--out", str(output)]
        run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert run.stdout.count("NOT A MATCH") == 4
        assert_counts_only(json.loads(output.read_text()))
        original = output.read_bytes()
        again = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert again.returncode == 0 and output.read_bytes() == original
        assert set(Path(directory).iterdir()) == {output}
    after = reference.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert not list(tmp_path.iterdir())  # no cwd cache or other side effects


def test_cli_missing_reference_reports_no_new_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mask, "ROOT", tmp_path)
    output = tmp_path / ".cache/report.json"
    assert mask.main(["--reference", str(tmp_path / "absent.so"),
                      "--out", str(output)]) == 0
    stdout = capsys.readouterr().out
    assert stdout.count("NOT A MATCH") == 4 and "NO NEW EVIDENCE" in stdout
    assert_counts_only(json.loads(output.read_text()))
    assert set(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")
               if p.is_file()) == {".cache/report.json"}


@pytest.mark.parametrize("relative", ["outside.json", ".cache/../outside.json",
                                      ".cache/report.txt", ".cache"])
def test_output_must_stay_under_cache(monkeypatch, tmp_path, relative):
    monkeypatch.setattr(mask, "ROOT", tmp_path)
    assert mask.main(["--out", str(tmp_path / relative)]) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("cache_root", [False, True])
def test_output_symlinks_cannot_escape_cache(monkeypatch, tmp_path, cache_root):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(mask, "ROOT", repo)
    cache = repo / ".cache"
    if cache_root:
        cache.symlink_to(outside, target_is_directory=True)
        output = cache / "report.json"
    else:
        cache.mkdir()
        (cache / "escape").symlink_to(outside, target_is_directory=True)
        output = cache / "escape/report.json"
    assert mask.main(["--out", str(output)]) == 2
    assert not list(outside.iterdir())


def test_existing_hardlink_does_not_modify_external_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mask, "ROOT", tmp_path)
    cache = tmp_path / ".cache"
    cache.mkdir()
    outside = tmp_path / "original.txt"
    outside.write_text("source-owned sentinel")
    output = cache / "report.json"
    output.hardlink_to(outside)
    assert mask.main(["--reference", str(tmp_path / "absent.so"),
                      "--out", str(output)]) == 0
    assert outside.read_text() == "source-owned sentinel"
    assert_counts_only(json.loads(output.read_text()))


@pytest.mark.parametrize("alias", [False, True])
def test_output_cannot_replace_its_reference(monkeypatch, tmp_path, alias):
    monkeypatch.setattr(mask, "ROOT", tmp_path)
    cache = tmp_path / ".cache"
    cache.mkdir()
    reference = cache / "reference.json"
    sentinel = b"source-owned reference sentinel"
    reference.write_bytes(sentinel)
    output = reference
    if alias:
        output = cache / "output.json"
        output.symlink_to(reference)

    def forbidden(*args):
        pytest.fail("reference/output collision must be rejected before reading")

    monkeypatch.setattr(mask, "_read", forbidden)
    assert mask.main(["--reference", str(reference), "--out", str(output)]) == 2
    assert reference.read_bytes() == sentinel
    assert sorted(cache.iterdir()) == sorted({reference, output})
    if alias:
        assert output.is_symlink()
