"""Synthetic contract for the source-correspondence classify/score helpers.

``classify`` and ``score_site`` in ``tools.source_ref.build_source_correspondence``
decide whether a definition site is labelled ``exact_body_found``,
``same_file_different_revision`` or ``not_found``, and account the evidence
hits, ratio, file/body bonuses, depth penalty and member/free kind.  This file
characterises those pure accounting branches only: it uses synthetic wanted
sets and a fake ``Bodies`` and never reads the private reference tree, an ELF,
an APK, an OBB or any other file.  A passing run is not evidence that any
function is reconstructed or equivalent to the original machine code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.source_ref.build_source_correspondence as matcher  # noqa: E402
from tools.source_ref.build_source_correspondence import (  # noqa: E402
    classify,
    score_site,
)

STATUSES = {"not_found", "exact_body_found", "same_file_different_revision"}


class _ForbiddenSubprocess:
    """Stand-in for the matcher's ``subprocess`` module.

    ``demangle_many`` would normally shell out to ``c++filt``; the helpers
    under test must stay pure, so any attempt to use subprocess fails loudly
    instead of silently running a tool or reading anything.
    """

    @staticmethod
    def run(*args, **kwargs):
        raise AssertionError(
            "subprocess.run must not be called by classify or score_site"
        )


@pytest.fixture(autouse=True)
def _forbid_subprocess(monkeypatch):
    monkeypatch.setattr(matcher, "subprocess", _ForbiddenSubprocess())


class _FakeBodies:
    """Minimal ``Bodies`` replacement that returns a known identifier set.

    ``identifiers`` is the only method ``score_site`` may use.  ``lines`` and
    ``body`` raise, so a future implementation that starts reading body text
    (and therefore a file) cannot pass this contract unnoticed.
    """

    def __init__(self, identifiers):
        self._identifiers = identifiers
        self.identifiers_calls = []

    def identifiers(self, rel, line):
        self.identifiers_calls.append((rel, line))
        return self._identifiers

    def lines(self, rel):  # pragma: no cover - only a regression calls this
        raise AssertionError("score_site must not read body lines")

    def body(self, rel, line):  # pragma: no cover - only a regression calls this
        raise AssertionError("score_site must not read body text")


def _assert_status(record):
    assert record["status"] in STATUSES


@pytest.mark.parametrize(
    ("evidence_total", "ratio", "definition_found", "file_match", "opens_body",
     "expected"),
    [
        # No definition site wins regardless of how strong the evidence looks.
        (8, 1.0, False, True, True, "not_found"),
        (0, 0.0, False, True, True, "not_found"),
        # Strong evidence branch: >= 3 hits and ratio >= 0.6.
        (3, 0.6, True, False, True, "exact_body_found"),
        (5, 0.75, True, False, True, "exact_body_found"),
        # Weaker evidence branch: >= 1 hit and ratio >= 0.5.
        (1, 0.5, True, False, True, "exact_body_found"),
        (4, 0.6, True, False, True, "exact_body_found"),
        # 3 / 0.499 is just below both ratio thresholds and stays a revision
        # mismatch even though there are enough hits for the strong branch.
        (3, 0.499, True, True, True, "same_file_different_revision"),
        (2, 0.499, True, False, True, "same_file_different_revision"),
        # Zero evidence, opening body: the file-hint branch first, then the
        # body-only fallback branch when the file hint does not match.
        (0, 0.0, True, True, True, "exact_body_found"),
        (0, 0.0, True, False, True, "exact_body_found"),
        # Zero evidence and no opening body: not even the trivial-body fallback.
        (0, 0.0, True, True, False, "same_file_different_revision"),
        (0, 0.0, True, False, False, "same_file_different_revision"),
    ],
)
def test_classify_branch_order(evidence_total, ratio, definition_found,
                               file_match, opens_body, expected):
    status = classify(evidence_total, ratio, definition_found, file_match,
                      opens_body)
    assert status == expected
    assert status in STATUSES


def test_zero_evidence_body_uses_current_fallback_branch():
    # Exact current fallback: a body is open and the definition exists, so the
    # site is labelled exact even with no identifier evidence and no file hint.
    assert classify(0, 0.0, True, False, True) == "exact_body_found"
    # A closed body never gets that fallback.
    assert classify(0, 0.0, True, False, False) == (
        "same_file_different_revision"
    )


def test_score_site_known_identifiers_accounts_hits_ratio_and_bonuses():
    wanted = {"alpha", "beta", "gamma"}
    bodies = _FakeBodies({"alpha", "beta", "unrelated"})
    record = score_site(
        rel="engine/render/cmatrix.cpp",
        line=120,
        declaration=0,
        wanted=wanted,
        evidence_total=5,
        hint_base="cmatrix.cpp",
        depth=0,
        bodies=bodies,
    )

    assert bodies.identifiers_calls == [("engine/render/cmatrix.cpp", 120)]
    assert record["evidence_hits"] == 2
    assert record["evidence_ratio"] == 0.4
    # 2/5*10 + file hint (2) + body found (1) - depth 0*0.5.
    assert record["score"] == 7.0
    assert record["file"] == "engine/render/cmatrix.cpp"
    assert record["line"] == 120
    assert record["evidence_total"] == 5
    assert record["file_hint_match"] is True
    assert record["body_found"] is True
    assert record["match_depth"] == 0
    assert record["declaration_only"] is False
    assert record["kind"] == "member"
    assert record["status"] == "same_file_different_revision"
    _assert_status(record)


def test_score_site_strong_evidence_without_file_bonus_and_depth_penalty():
    wanted = {"one", "two", "three"}
    bodies = _FakeBodies({"one", "two", "three", "extra"})
    record = score_site(
        rel="engine/audio/mixer.cpp",
        line=42,
        declaration=0,
        wanted=wanted,
        evidence_total=5,
        hint_base="other.cpp",
        depth=1,
        bodies=bodies,
    )

    assert record["evidence_hits"] == 3
    assert record["evidence_ratio"] == 0.6
    # 3/5*10 + no file bonus + body bonus - 1*0.5 depth penalty.
    assert record["score"] == 6.5
    assert record["file_hint_match"] is False
    assert record["body_found"] is True
    assert record["kind"] == "member"
    assert record["status"] == "exact_body_found"
    _assert_status(record)


def test_score_site_missing_body_is_declaration_only_with_file_bonus():
    bodies = _FakeBodies(None)
    record = score_site(
        rel="src/widget.cpp",
        line=7,
        declaration=1,
        wanted={"handle", "draw"},
        evidence_total=4,
        hint_base="widget.cpp",
        depth=2,
        bodies=bodies,
    )

    assert record["evidence_hits"] == 0
    assert record["evidence_ratio"] == 0.0
    # No body: no ratio or body bonus, but the file hint still adds 2; depth 2
    # subtracts 1.
    assert record["score"] == 1.0
    assert record["body_found"] is False
    assert record["file_hint_match"] is True
    assert record["declaration_only"] is True
    assert record["kind"] == "member"
    assert record["status"] == "same_file_different_revision"
    _assert_status(record)


def test_score_site_rounds_repeating_ratio_and_score():
    bodies = _FakeBodies({"only", "extra"})
    record = score_site(
        rel="engine/math/vector.cpp",
        line=1,
        declaration=0,
        wanted={"only"},
        evidence_total=3,
        hint_base="vector.cpp",
        depth=0,
        bodies=bodies,
    )

    assert record["evidence_hits"] == 1
    assert record["evidence_ratio"] == 0.333
    # 1/3*10 + file hint (2) + body found (1), rounded to 3 places.
    assert record["score"] == 6.333
    assert record["status"] == "same_file_different_revision"
    _assert_status(record)


def test_score_site_zero_evidence_body_and_free_kind():
    bodies = _FakeBodies(set())
    record = score_site(
        rel="src/free_function.cpp",
        line=9,
        declaration=0,
        wanted=set(),
        evidence_total=0,
        hint_base="elsewhere.cpp",
        depth=-2,
        bodies=bodies,
    )

    assert record["evidence_hits"] == 0
    assert record["evidence_ratio"] == 0.0
    # Zero evidence and an open body: body bonus only; depth -2 adds 1.
    assert record["score"] == 2.0
    assert record["body_found"] is True
    assert record["file_hint_match"] is False
    assert record["declaration_only"] is False
    assert record["kind"] == "free"
    assert record["status"] == "exact_body_found"
    _assert_status(record)


def test_subprocess_guard_actually_fails_when_called():
    # Proves the fixture guard is effective rather than an unused placeholder.
    with pytest.raises(AssertionError, match="subprocess.run must not be called"):
        matcher.subprocess.run(["c++filt"])
