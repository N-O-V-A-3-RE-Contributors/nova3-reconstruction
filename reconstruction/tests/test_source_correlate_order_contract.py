"""Offline orchestration contract for ``build_source_correspondence.correlate``
(task #316).

``correlate`` (``tools/source_ref/build_source_correspondence.py``) is the only
step between the extracted symbol JSONL and the committed correspondence
document.  This module characterises *only* that orchestration with recording
fakes:

* ``Bodies`` is replaced, so the private reference source tree is never read;
* ``collect_evidence_names`` is replaced, so ``c++filt`` / subprocess is never
  run;
* ``match_symbol`` is replaced, so matching heuristics are never exercised.

It asserts: call order (bodies -> reference collection -> one match per row in
file order), blank/whitespace-only line skipping, one-time reference
collection over the parsed rows, and how identity fields
(``address``/``size``/``mode``/``mangled``/``demangled``/``file_hint``) and a
matcher result carrying ``status``/``confidence``/``file``/``line`` combine.

Observed precedence: ``correlate`` builds the identity projection and then
calls ``out.update(match)``, so a matcher key that repeats a projection key
*would* overwrite it.  The inspected matcher source never emits any identity
key; the colliding case is pinned only as a characterisation of today's
behaviour, and the non-colliding case (the ones the real matcher produces) is
asserted as the contract.

This test covers orchestration only.  It is NOT reconstruction, equivalence or
instruction-window evidence for any function, and it says nothing about
demangling internals, matching heuristics or summary generation.  It reads a
temporary synthetic JSONL file and no ELF, APK, OBB or private source tree;
no subprocess is started (asserted indirectly by ``collect_evidence_names``
never reaching ``demangle_many``/``c++filt``, and directly by patching
``subprocess.run`` in the module to raise).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

bsc = pytest.importorskip(
    "tools.source_ref.build_source_correspondence",
    reason="tools/source_ref/build_source_correspondence.py is absent",
)


# Three synthetic rows.  ``mangled``/``demangled`` are deliberately distinct
# so a matcher result that leaks a name is detectable; ``file_hint`` values
# point at paths that do not exist under any root and are never opened.
ROWS = [
    {
        "address": "0x00001000",
        "size": 12,
        "mode": "arm",
        "mangled": "_ZN3Foo3barEv",
        "demangled": "Foo::bar()",
        "file_hint": "nonexistent/foo.cpp",
    },
    {
        "address": "0x00001010",
        "size": 4,
        "mode": "thumb",
        "mangled": "_Z3bazv",
        "demangled": "baz()",
        "file_hint": "nonexistent/baz.cpp",
    },
    {
        "address": "0x00001020",
        "size": 40,
        "mode": "arm",
        "mangled": "_ZN3Qux3runEi",
        "demangled": "Qux::run(int)",
        "file_hint": "",
    },
]
IDENTITY_FIELDS = ("address", "size", "mode", "mangled", "demangled", "file_hint")

# Lines of the synthetic symbols file, in order.  Non-blank lines map, through
# NONBLANK_POSITIONS, to ROWS; blank and whitespace-only lines must be skipped.
LINES = [
    "",
    json.dumps(ROWS[0]),
    "   ",
    "",
    json.dumps(ROWS[1]),
    "\t \t",
    json.dumps(ROWS[2]),
    "",
    "  ",
]
NONBLANK_POSITIONS = tuple(i for i, line in enumerate(LINES) if line.strip())

# Matcher results: the first carries only the four keys the contract names and
# adds no identity field; the others carry extra heuristic keys, including a
# hostile ``address``/``demangled`` that must not overwrite the input row.
MATCH_RESULTS = [
    {"status": "exact_body_found", "confidence": "strong",
     "file": "foo/foo.cpp", "line": 42},
    {"status": "not_found", "confidence": "weak", "file": "baz/baz.cpp",
     "line": 7, "address": "0xdeadbeef", "demangled": "overwritten"},
    {"status": "same_file_different_revision", "confidence": "file_hint",
     "file": "qux/qux.cpp", "line": 3, "extra_evidence": 1},
]
REFERENCE_NAMES = {"_Z3foov": "foo()", "_ZN3BarC1Ev": "Bar::Bar()"}


class Recording(object):
    """Shared call log; entries are ``(name, payload)`` in call order."""

    def __init__(self):
        self.calls = []

    def names(self):
        return [name for name, _ in self.calls]


class FakeBodies(object):
    def __init__(self, recording, root):
        self.root = root
        recording.calls.append(("Bodies", root))

    def lines(self, rel):  # pragma: no cover - correlate must not touch bodies
        raise AssertionError("correlate must not read bodies; matcher is faked")

    def body(self, rel, line):  # pragma: no cover - ditto
        raise AssertionError("correlate must not read bodies; matcher is faked")


def _install_fakes(monkeypatch, recording):
    def fake_bodies(root):
        return FakeBodies(recording, root)

    def fake_collect(rows):
        # Snapshot so later mutation of the rows cannot change the record.
        recording.calls.append(("collect_evidence_names", [dict(r) for r in rows]))
        return dict(REFERENCE_NAMES)

    def fake_matcher(row, index, bodies, demangled_refs, free_index):
        recording.calls.append((
            "match_symbol",
            {
                "row": dict(row),
                "index": index,
                "bodies": bodies,
                "demangled_refs": demangled_refs,
                "free_index": free_index,
            },
        ))
        position = len([n for n in recording.names() if n == "match_symbol"]) - 1
        return dict(MATCH_RESULTS[position])

    def forbidden_subprocess(*args, **kwargs):  # pragma: no cover - must not fire
        raise AssertionError("correlate must not run a subprocess")

    monkeypatch.setattr(bsc, "Bodies", fake_bodies)
    monkeypatch.setattr(bsc, "collect_evidence_names", fake_collect)
    monkeypatch.setattr(bsc, "match_symbol", fake_matcher)
    monkeypatch.setattr(bsc, "demangle_many", forbidden_subprocess)
    monkeypatch.setattr(bsc.subprocess, "run", forbidden_subprocess)


def _run(tmp_path, monkeypatch):
    """Write the synthetic JSONL, run correlate with fakes, return the record."""
    symbols = tmp_path / "symbols.jsonl"
    symbols.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    recording = Recording()
    _install_fakes(monkeypatch, recording)
    root = tmp_path / "never_read_reference_root"
    index = {"by_class": {}, "free": {}}
    free_index = {"sentinel": "free-index"}
    rows = bsc.correlate(symbols, index, root, free_index)
    return rows, recording, root, index, free_index


@pytest.fixture()
def correlated(tmp_path, monkeypatch):
    return _run(tmp_path, monkeypatch)


def test_synthetic_fixture_has_blank_and_whitespace_lines():
    # Guard against a fixture change silently making the skipping test vacuous.
    assert len(NONBLANK_POSITIONS) == len(ROWS) == 3
    assert [json.loads(LINES[i]) for i in NONBLANK_POSITIONS] == ROWS
    blankish = [line for line in LINES if not line.strip()]
    assert len(blankish) == len(LINES) - 3
    assert any(line == "" for line in blankish)
    assert any(line and not line.strip() for line in blankish)


def test_call_order_is_bodies_then_collect_then_one_match_per_row(correlated):
    _, recording, root, _, _ = correlated
    assert recording.names() == [
        "Bodies", "collect_evidence_names", "match_symbol", "match_symbol",
        "match_symbol",
    ]
    assert recording.calls[0][1] == root


def test_collect_evidence_names_called_once_with_parsed_rows(correlated):
    _, recording, _, _, _ = correlated
    collects = [payload for name, payload in recording.calls
                if name == "collect_evidence_names"]
    assert len(collects) == 1
    assert collects[0] == ROWS

    first_match = recording.names().index("match_symbol")
    first_collect = recording.names().index("collect_evidence_names")
    assert first_collect < first_match
    assert first_match == 2, "one match per non-blank row, starting after collection"


def test_match_symbol_receives_row_and_reference_result_in_file_order(correlated):
    _, recording, _, index, free_index = correlated
    matches = [payload for name, payload in recording.calls if name == "match_symbol"]
    assert [payload["row"] for payload in matches] == ROWS
    for payload in matches:
        assert payload["demangled_refs"] == REFERENCE_NAMES
        assert payload["demangled_refs"] is not None
        assert payload["index"] is index
        assert payload["free_index"] is free_index
        assert type(payload["bodies"]).__name__ == "FakeBodies"
        assert payload["bodies"].root.name == "never_read_reference_root"


def test_identity_fields_come_from_input_row(correlated):
    # ``correlate`` builds the projection from the input row and then calls
    # ``out.update(match)``: for non-colliding keys the identity fields are the
    # input values and the matcher's four named keys are added.  Colliding keys
    # are covered by ``test_observed_precedence_*`` below.
    rows, _, _, _, _ = correlated
    assert len(rows) == len(ROWS) == 3
    for out, row, match in zip(rows, ROWS, MATCH_RESULTS):
        for field in IDENTITY_FIELDS:
            if field in match:
                continue
            assert out[field] == row[field], field
        for key in ("status", "confidence", "file", "line"):
            assert out[key] == match[key]


def test_observed_precedence_colliding_match_keys_win(correlated):
    # CHARACTERISATION, not endorsement: today the matcher result overwrites
    # any projection key it repeats.  The inspected matcher source never emits
    # address/size/mode/mangled/demangled/file_hint, so this only constrains
    # what happens if one ever does; the test records actual behaviour rather
    # than asserting a contract the code does not provide.
    rows, _, _, _, _ = correlated
    assert rows[1]["address"] == MATCH_RESULTS[1]["address"]
    assert rows[1]["demangled"] == MATCH_RESULTS[1]["demangled"]


def test_minimal_matcher_result_only_adds_its_keys(correlated):
    rows, _, _, _, _ = correlated
    first = rows[0]
    assert IDENTITY_FIELDS[0] in first
    # Output is exactly the projection plus the matcher's four keys.
    assert set(first) == set(IDENTITY_FIELDS) | {"status", "confidence",
                                                 "file", "line"}
    assert "evidence_total" not in first


def test_matcher_extra_keys_pass_through(correlated):
    rows, _, _, _, _ = correlated
    assert rows[2]["extra_evidence"] == 1
    assert "extra_evidence" not in rows[0]


def test_output_row_count_equals_non_blank_input_lines(correlated):
    rows, _, _, _, _ = correlated
    symbols_count = sum(1 for line in LINES if line.strip())
    assert len(rows) == symbols_count == len(NONBLANK_POSITIONS)


def test_without_free_index_match_symbol_gets_none(tmp_path, monkeypatch):
    symbols = tmp_path / "symbols.jsonl"
    symbols.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    recording = Recording()
    _install_fakes(monkeypatch, recording)
    rows = bsc.correlate(symbols, {}, tmp_path)
    assert len(rows) == 3
    matches = [payload for name, payload in recording.calls if name == "match_symbol"]
    assert all(payload["free_index"] is None for payload in matches)
