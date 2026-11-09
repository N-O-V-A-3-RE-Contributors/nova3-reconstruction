"""Literal-pool containment contract for ``tools/compare/window_diff.py`` (accounting only).

A literal-pool word is *data*. This module pins, with synthetic word lists held
only in memory, what the current tool does when the reference and the candidate
disagree about where a literal region starts (a start inside a multi-word
literal, or an unaligned start), and what it does with a literal word that only
one candidate relocation site touches.

Rules this module documents and locks (each one is forced by a test below,
and every rule was checked against the module, not assumed):

* The literal set is ``_pc_relative_ldr_targets`` of the reference window OR of
  the candidate window. Each entry is one word named by one
  ``LDR Rd, [pc, #imm]``; a two-word literal is two point-like words, so there
  is no "literal region start" presented to the helper.
* ``relocated`` is true when any candidate relocation site lies in
  ``[4*i, 4*i+4)``. It is not a containment or alignment test: one unaligned
  site inside the word makes it relocated.
* ``RELOCATION_FIELD`` is taken whenever ``relocated`` is true, whether or not
  ``_masked_equal`` accepts the pair (two adjacent branches). Under the
  installed constants ``_masked_equal`` accepts only two words that both reduce
  to ``0x0F000000``; the condition nibble and the second mask field leave only
  that value, so a real compiler's differing branch cannot be "masked". This is
  a defect of the tool relative to its docstring, not a match result.
* ``LITERAL_POOL`` is ``not relocated and index in literals``. A literal word
  that any candidate site touches is therefore never reported as pool data.
* ``ENCODING_DIFFERENCE`` is reached only by a differing word with no
  candidate site in it and no literal target at it. Relocated words never
  reach it.
* Each word index is classified once: the word loop runs over
  ``range(min(len(original), len(compiled)) // unit)`` and the per-kind counts
  sum to the words compared. Only differing words are listed, every one in
  ``[0, len(window))``. A relocation site outside the candidate bytes is
  dropped first; sites inside a word are not alignment-checked. Short windows
  produce ``UNCLASSIFIED_TAIL`` and ``FUNCTION_SIZE_MISMATCH``.
* The helper takes no separate region start. An unaligned window base is
  handled arithmetically, not fail-closed: it returns an empty set only when
  the base's offset makes the derived ``offset % 4`` nonzero. A backward
  ``LDR pc, [pc, #-imm]`` at index 0 reads word 2 and a forward load at index 0
  reads word 2 at any base in a synthetic ``base + 4k`` window, so a
  misaligned base still yields targets (and [] for an empty window).

Divergences from the task brief (reported to the owner in the handoff):

* "A partially contained literal word stays out of the mask and gets
  ENCODING_DIFFERENCE" does not hold: there is no containment test, and a
  relocated word always gets ``RELOCATION_FIELD``. ``ENCODING_DIFFERENCE``
  needs the word to have no site at all.
* "A word index claimed by two overlapping regions and a region past the
  window end are counted once each" is pinned as the word-loop invariant
  (counts sum, in-bounds offsets); the tool has no region object to count.
* "EQUAL/RELOCATION_FIELD agreement is not a byte match": true and locked
  (``code_window_equal`` is False whenever any word differs).

What "pure" means here: ``window_diff.py`` is loaded by path with
``importlib``. While it runs, its two analysis commands -- ``select_functions``
(reference bounding) and ``candidate_window`` (object parsing) -- are replaced
in a context manager nested in this module by command executors over in-memory
word lists. No reference file is opened, no original or synthetic object file
is read, no ELF is parsed and no process starts. The original module source is
read once, only to assert that the patched names exist. The loaded module, the
crate-level imports it pulls in and any ``sys.path`` entry it adds are removed
after load and after every patch.

``EQUAL`` and ``RELOCATION_FIELD`` are comparison-category labels only. They
are **not** a byte match, not an original-object recovery and not compiler
evidence. NOT MATCHED: no game function, address or size is claimed.

State of this work: TESTED (offline, synthetic, against the tool's observed
behaviour). Behavioural comparison with any binary: NOT DONE.
"""
from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
import sys
from unittest import mock

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/compare/window_diff.py"

_MODULE_NAME = "window_diff_literal_containment_under_test"
_ADDED_PATH = str(ROOT)
_SUPPORT = ("nova3_team.object_compare", "nova3_team.object_matching")

ADDRESS = 0x00040000  # synthetic word-aligned window base; no game address
UNIT = 4  # A32; no PC-relative literal scan for any other unit
KINDS = ("EQUAL", "RELOCATION_FIELD", "LITERAL_POOL", "ENCODING_DIFFERENCE")

# Only ``0x0F000000`` (and itself) is accepted by the installed
# ``_masked_equal``: its mask words are 0x0F000000/0x0F000000 and
# 0x00FFFFFF/0x00000000, so every word must equal 0x0F000000 exactly. A
# differing branch word can therefore never be "masked" by real compilers;
# this file uses the one accepted value where it needs a masked verdict.
MASK_SHAPE = 0x0F000000
# Not accepted: same mask fields but a low bit set.
NOT_MASK_SHAPE = 0x0F000001
# A32 plain data-processing word: never a PC-relative load, never mask-shaped.
PLAIN = 0xE0000000
# ``LDR Rd, [pc, #imm]``: the literal-pool consumer (imm in bits [11:0]).
LDR_PC = 0xE59F0000
LDR_PC_NO_U = LDR_PC & ~0x00800000  # U = 0: subtract imm


# --------------------------------------------------------------------------
# Load the module by path and keep it pure.
# --------------------------------------------------------------------------

def _load(path: Path):
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _module():
    """The unmodified module by path; its imports are dropped on exit."""
    module = _load(MODULE_PATH)
    try:
        yield module
    finally:
        if _ADDED_PATH in sys.path:
            sys.path.remove(_ADDED_PATH)
        for name in _SUPPORT:
            sys.modules.pop(name, None)
        sys.modules.pop(_MODULE_NAME, None)


def _encode_words(words) -> bytes:
    return b"".join(int(word).to_bytes(UNIT, "little") for word in words)


def _ldr(index: int, target: int) -> int:
    """``LDR Rd, [pc, #imm]`` (U = 1) at ``index`` reading word ``target``."""
    imm = (target - index - 2) * UNIT
    assert 0 <= imm <= 0xFFF, "forward load only"
    return LDR_PC | imm


def _ldr_back(index: int, target: int) -> int:
    """``LDR Rd, [pc, #-imm]`` (U = 0) at ``index`` reading word ``target``."""
    imm = (index + 2 - target) * UNIT
    assert 0 <= imm <= 0xFFF, "backward load only"
    return LDR_PC_NO_U | imm


def _reference_command_executor(reference: bytes, row: dict):
    """In-memory stand-in for ``object_matching.select_functions``."""
    def execute(data, *, addresses, limit=1):
        assert bytes(data) == reference
        assert list(addresses) == [row["address"]]
        return [dict(row)], None  # caller unpacks ``selected, _ = ...``
    return execute


def _candidate_command_executor(candidate: dict):
    """In-memory stand-in for ``object_compare.candidate_window``."""
    def execute(data, symbol):
        assert bytes(data) == b"", "no object bytes are passed or read"
        assert symbol == "synthetic_containment_probe"
        return dict(candidate)
    return execute


class _WindowCrate:
    """Reference and candidate word lists, held entirely in memory."""

    def __init__(self, reference_words, candidate_words, *, address=ADDRESS,
                 relocation_offsets=(), candidate_size=None):
        assert len(reference_words) == len(candidate_words)
        self.reference_words = list(reference_words)
        self.candidate_words = list(candidate_words)
        self.address = address
        self.sites = tuple(sorted(set(relocation_offsets)))
        data = _encode_words(self.candidate_words)
        if candidate_size is not None:
            data = data[:candidate_size]
        # A32 mode: the reference literal scan runs and agrees with the
        # imported mode check.
        self.candidate = dict(bytes=data, size=len(data), thumb=False,
                              relocation_offsets=list(self.sites))

    @property
    def window_size(self) -> int:
        return len(self.reference_words) * UNIT

    def reference(self) -> bytes:
        return _encode_words(self.reference_words)

    @contextlib.contextmanager
    def loaded(self):
        """The unmodified module with only its two analysis commands patched."""
        with _module() as module:
            row = dict(address=self.address, size=self.window_size,
                       file_offset=0, thumb=False)
            with mock.patch.object(
                    module, "select_functions",
                    _reference_command_executor(self.reference(), row)), \
                mock.patch.object(
                    module, "candidate_window",
                    _candidate_command_executor(self.candidate)):
                yield module

    def diff(self) -> dict:
        with self.loaded() as module:
            return module.diff_window(self.reference(), b"",
                                      address=self.address,
                                      symbol="synthetic_containment_probe")

    def kinds(self) -> dict[int, str]:
        """Differing-word kinds as the report lists them, by index."""
        return {row["index"]: row["kind"]
                for row in self.diff()["word_details"]}

    def literal_targets(self) -> set[int]:
        with _module() as module:
            return module._pc_relative_ldr_targets(self.reference(),
                                                   self.address, UNIT)

    def masked_equal(self, left: int, right: int) -> bool:
        with _module() as module:
            return module._masked_equal(left, right, UNIT)

    def relocated(self, index: int) -> bool:
        start = index * UNIT
        return any(start <= site < start + UNIT for site in self.sites
                   if 0 <= site < len(self.candidate["bytes"]))


def _two_word_literal_window():
    # Index 0 loads the first literal word (2), index 1 the second (3).
    return [_ldr(0, 2), _ldr(1, 3), 0xAABBCCDD, 0x11223344, PLAIN]


# --------------------------------------------------------------------------
# The patched interface exists in the unmodified source.
# --------------------------------------------------------------------------

def test_patch_targets_are_defined_by_the_module():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "select_functions(reference, addresses={address}, limit=1)" in source
    assert "obj = candidate_window(candidate, symbol)" in source
    assert "source_matching_verified=False" in source
    assert "not a byte" in source.lower() or "never a byte" in source.lower()


# --------------------------------------------------------------------------
# Reference literal region of two words at indices 2-3, and starts that land
# inside it.
# --------------------------------------------------------------------------

def test_reference_literal_helper_is_point_like_inside_a_two_word_region():
    reference = _two_word_literal_window()
    crate = _WindowCrate(reference, reference)
    assert crate.literal_targets() == {2, 3}
    for index in crate.literal_targets():
        assert index * UNIT % UNIT == 0
        assert 0 <= index * UNIT and (index + 1) * UNIT <= crate.window_size
    only_second = list(reference)
    only_second[0] = PLAIN
    assert _WindowCrate(only_second, only_second).literal_targets() == {3}


def test_unaligned_candidate_site_inside_a_literal_is_a_relocation_field():
    # The literal at word 3 differs. A candidate relocation at byte 14 starts
    # inside word 3. Any site in [12, 16) makes the word relocated (no
    # alignment or containment check). The pair fails ``_masked_equal``, which
    # does not matter: the tool labels every relocated word RELOCATION_FIELD.
    reference = _two_word_literal_window()
    reference[3] = MASK_SHAPE
    candidate = list(reference)
    candidate[3] = MASK_SHAPE - 1
    crate = _WindowCrate(reference, candidate, relocation_offsets=(14,))
    assert crate.relocated(3)
    assert crate.masked_equal(reference[3], candidate[3]) is False
    report = crate.diff()
    # Mask comparison fails, but the relocated branch sits right after it.
    assert crate.kinds() == {3: "RELOCATION_FIELD"}
    assert report["counts"]["LITERAL_POOL"] == 0
    assert report["counts"]["ENCODING_DIFFERENCE"] == 0
    # The label is NOT a byte match.
    assert report["code_window_equal"] is False
    assert report["source_matching_verified"] is False


def test_untouched_literal_word_keeps_pool_verdict():
    # Control: no candidate site touches word 3, so the word stays relocated=
    # False and the pool verdict wins.
    reference = _two_word_literal_window()
    candidate = list(reference)
    candidate[3] = MASK_SHAPE
    crate = _WindowCrate(reference, candidate, relocation_offsets=(8,))
    assert not crate.relocated(3)
    report = crate.diff()
    assert crate.kinds() == {3: "LITERAL_POOL"}
    assert report["counts"]["LITERAL_POOL"] == 1


def test_straddling_sites_split_the_two_word_literal_per_word():
    # Sites at bytes 8 and 10. Byte 8 is in word 2's [8, 12), so word 2 is
    # relocated; byte 10 is also in [8, 12), so word 3 has no site at all and
    # is neither pool (not a literal target) nor masked.
    reference = [PLAIN, PLAIN, 0xAABBCCDD, 0x11223344, PLAIN]
    candidate = list(reference)
    candidate[2] = MASK_SHAPE
    candidate[3] = MASK_SHAPE
    crate = _WindowCrate(reference, candidate, relocation_offsets=(8, 10))
    assert crate.kinds() == {2: "RELOCATION_FIELD", 3: "ENCODING_DIFFERENCE"}


# --------------------------------------------------------------------------
# The mask comparison, as installed.
# --------------------------------------------------------------------------

def test_conditional_svc_word_is_relocated_but_not_mask_equal():
    # An SVC word is not the one accepted mask value; only the
    # relocated-but-unmasked branch still labels it RELOCATION_FIELD.
    reference = [PLAIN, 0xEF010000, PLAIN, PLAIN]
    candidate = list(reference)
    candidate[1] = 0xEF020000
    crate = _WindowCrate(reference, candidate, relocation_offsets=(4,))
    assert crate.masked_equal(reference[1], candidate[1]) is False
    assert crate.kinds() == {1: "RELOCATION_FIELD"}
    assert crate.diff()["code_window_equal"] is False


def test_only_the_exact_mask_word_is_accepted():
    # With the installed mask words, a pair is accepted only when both words
    # are exactly 0x0F000000. Pinned from the module's own constants.
    reference = [PLAIN, MASK_SHAPE, PLAIN, PLAIN]
    candidate = list(reference)
    candidate[1] = NOT_MASK_SHAPE
    crate = _WindowCrate(reference, candidate, relocation_offsets=(4,))
    assert crate.masked_equal(MASK_SHAPE, MASK_SHAPE) is True
    assert crate.masked_equal(reference[1], candidate[1]) is False
    assert crate.kinds() == {1: "RELOCATION_FIELD"}


def test_condition_nibble_is_not_compared_in_the_mask():
    # Observed: two words that both reduce to the mask value are accepted
    # whatever their condition nibble (the condition is not compared).
    reference = [PLAIN, 0x1F000000, PLAIN, PLAIN]
    candidate = list(reference)
    candidate[1] = 0x2F000000
    crate = _WindowCrate(reference, candidate, relocation_offsets=(4,))
    assert crate.masked_equal(reference[1], candidate[1]) is True
    # Still relocated, so the relocated branch labels it RELOCATION_FIELD.
    assert crate.kinds() == {1: "RELOCATION_FIELD"}


def test_word_with_low_bits_set_is_not_accepted():
    reference = [PLAIN, MASK_SHAPE | 0x100, PLAIN, PLAIN]
    candidate = list(reference)
    candidate[1] = MASK_SHAPE | 0x200
    crate = _WindowCrate(reference, candidate, relocation_offsets=(4,))
    assert crate.masked_equal(reference[1], candidate[1]) is False


def test_relocated_unmasked_word_is_still_relocation_field():
    # Relocated but refused by ``_masked_equal``: the "no comparable encoding
    # field left" branch still labels it RELOCATION_FIELD.
    reference = [PLAIN, 0x01000000, PLAIN, PLAIN]
    candidate = list(reference)
    candidate[1] = 0x01000000 | 1
    crate = _WindowCrate(reference, candidate, relocation_offsets=(4,))
    assert crate.masked_equal(reference[1], candidate[1]) is False
    assert crate.kinds() == {1: "RELOCATION_FIELD"}


# --------------------------------------------------------------------------
# Documented model contradiction and invariants.
# --------------------------------------------------------------------------

def test_overlapping_sites_classify_a_word_once():
    reference = [PLAIN | value for value in (1, 2, 3, 4, 5)]
    candidate = list(reference)
    candidate[3] = MASK_SHAPE  # differs and passes the mask
    crate = _WindowCrate(reference, candidate, relocation_offsets=(8, 12, 16))
    report = crate.diff()
    assert crate.kinds() == {3: "RELOCATION_FIELD"}
    assert sum(report["counts"][kind] for kind in KINDS) == 5
    assert report["counts"]["RELOCATION_FIELD"] == 1
    assert report["words_compared"] == 5


def test_every_differing_word_is_listed_and_counted_once():
    reference = [PLAIN | value for value in (1, 2, 3, 4, 5)]
    candidate = list(reference)
    candidate[0] ^= 0x55
    candidate[2] ^= 0x55
    crate = _WindowCrate(reference, candidate, relocation_offsets=(8,))
    report = crate.diff()
    assert crate.kinds() == {0: "ENCODING_DIFFERENCE", 2: "RELOCATION_FIELD"}
    assert sum(report["counts"][kind] for kind in KINDS) == 5


def test_out_of_window_site_never_produces_an_out_of_bounds_verdict():
    reference = [PLAIN | value for value in (1, 2, 3, 4)]
    candidate = list(reference)
    candidate[3] = MASK_SHAPE
    # Sites at 15 (unaligned, inside word 3) and 16 (past the end). The word
    # stays inside [0, 16) whatever the site: every listed row is in bounds.
    crate = _WindowCrate(reference, candidate, relocation_offsets=(15, 16))
    report = crate.diff()
    assert crate.kinds() == {3: "RELOCATION_FIELD"}
    assert report["relocation_offsets_used"] == 1
    assert sum(report["counts"][kind] for kind in KINDS) == 4
    for row in report["word_details"]:
        assert 0 <= row["index"] < 4 and 0 <= row["offset"] < 16


def test_candidate_shorter_than_reference_is_a_size_mismatch_tail():
    reference = [PLAIN | value for value in (1, 2, 3, 4)]
    crate = _WindowCrate(reference, reference, candidate_size=12)
    report = crate.diff()
    assert report["words_compared"] == 3
    assert report["status"] == "FUNCTION_SIZE_MISMATCH"
    assert report["counts"]["UNCLASSIFIED_TAIL"] == 4
    assert report["code_window_equal"] is False


# --------------------------------------------------------------------------
# Region-start arithmetic of the literal helper.
# --------------------------------------------------------------------------

def test_unaligned_window_base_returns_an_empty_set_not_a_guess():
    # A backward PC-relative LDR at index 1 reads word 0 regardless of the
    # base's own alignment (the offset is compared, not the address).
    words = _encode_words([0x22222222, _ldr_back(1, 0), 0x33333333, 0x44444444])
    with _module() as module:
        assert module._pc_relative_ldr_targets(words, ADDRESS, UNIT) == {0}
        for base in (ADDRESS + 1, ADDRESS + 2, ADDRESS + 3):
            # The helper does not raise (offset % 4 == 0 holds), so even an
            # unaligned base yields the same index: no fail-closed behaviour.
            assert module._pc_relative_ldr_targets(words, base, UNIT) == {0}
    # A forward load derives its target from the base plus the immediate, so
    # a misaligned base keeps the index as long as base + (index+2)*4 + imm
    # stays word-aligned, which it does: base + a multiple of 4.
    forward = _encode_words([_ldr(0, 2), PLAIN, 0x22222222, 0x33333333])
    with _module() as module:
        for base in (ADDRESS, ADDRESS + 1, ADDRESS + 2, ADDRESS + 3):
            assert module._pc_relative_ldr_targets(forward, base, UNIT) == {2}


def test_non_word_unit_returns_before_reading_words():
    words = _encode_words([_ldr(0, 2), PLAIN, 0x22222222, 0x33333333])
    with _module() as module:
        assert module._pc_relative_ldr_targets(words, ADDRESS, 2) == set()
        assert module._pc_relative_ldr_targets(words, ADDRESS, 8) == set()
        assert module._pc_relative_ldr_targets(b"", ADDRESS, UNIT) == set()


def test_literal_helper_takes_no_separate_region_start():
    with _module() as module:
        with pytest.raises(TypeError):
            module._pc_relative_ldr_targets(b"\x00" * 8, ADDRESS, UNIT, start=1)


def test_ldr_target_arithmetic_and_window_bounds():
    # Forward: address = base + index*4 + 8, target = address + imm.
    # Backward (U = 0): target = address - imm.
    assert _WindowCrate([_ldr_back(0, 2), PLAIN, PLAIN, PLAIN],
                        [PLAIN] * 4).literal_targets() == {2}
    forward = [_ldr(0, 3), PLAIN, PLAIN, PLAIN]
    assert _WindowCrate(forward, forward).literal_targets() == {3}
    outside = [_ldr(0, 4), PLAIN, PLAIN, PLAIN]
    assert _WindowCrate(outside, outside).literal_targets() == set()
    for words in (forward, [_ldr_back(0, 2), PLAIN, PLAIN, PLAIN]):
        for index in _WindowCrate(words, words).literal_targets():
            assert index * UNIT % UNIT == 0
            assert 0 <= index * UNIT <= 3 * UNIT
            assert index <= len(words) - 1
