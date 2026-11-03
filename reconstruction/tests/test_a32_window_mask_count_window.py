"""Literal/directive split of ``a32_window_mask.count_window`` on synthetic A32.

The subject is exactly ``count_window`` and ``_censored_encoding`` of
``tools/compare/a32_window_mask.py``. Everything here is an accounting check
of the tool against byte strings built inside this module: no reference ELF is
opened, ``worksheet()``/``main()`` are never called, and no original function,
address, size or cited-bounds row is characterized or claimed.

The pinned accounts, as written in the tool's source:

* ``count_window`` raises ``ValueError`` unless both the window address and
  ``len(window)`` are multiples of four (rejection happens before any word is
  read; a sentinel helper proves it).
* Literal indices come from ``window_diff._pc_relative_ldr_targets`` (A32
  ``LDR Rd, [pc, #imm]`` immediate-form loads only) and are re-clamped by the
  tool so that ``0 <= 4*i <= len(window) - 4``.
* ``subset`` counts literal words whose unsigned little-endian value is
  strictly below 0x10000. The 0x10000 edge is excluded.
* ``directives`` counts words at all other indices that match one of the five
  ``_censored_encoding`` patterns: conditional SVC, BKPT, PLD immediate,
  PLD register and conditional WFI. A literal index never feeds directives,
  and an A32 instruction that misses the window target is still code.
* Therefore ``subset + non-literal instruction words == len(window) // 4`` for
  every accepted window, where the non-literal term counts literal indices as
  data. The tool's own ``subset`` only counts literals below 0x10000, so the
  tool's ``subset + directives`` is *not* the word count: ``directives`` is
  only the non-literal words that are censored encodings. The tests verify the
  criterion with an independent literal-index count and pin the tool's actual
  ``(subset, directives)`` pair separately.

State of this work: TESTED (offline, synthetic, against the tool's observed
behaviour) only. It is not a behavioural comparison with the original binary.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import random
import sys

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/compare/a32_window_mask.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mask = _load("a32_window_mask_count_window_under_test", MODULE_PATH)

BASE = 0x00020000  # synthetic, 4-byte aligned; unrelated to any game address

# One representative word per _censored_encoding branch (see the tool source).
CENSORED = {
    "svc": 0xEF000123,        # conditional SVC (not PC-relative LDR)
    "bkpt": 0xE120AB7C,       # BKPT with a nonzero immediate
    "pld_immediate": 0xF5D1F123,
    "pld_register": 0xF7D1F002,
    "wfi": 0xE320F003,        # conditional WFI/yield hint
}
ORDINARY = 0xE1A00000         # MOV r0, r0: ordinary data-processing word
LDR_PC = 0xE59F0000           # LDR r0, [pc, #imm]: imm is the low 12 bits
LDR_PC_BACK = 0xE51F0000      # LDR r0, [pc, #-imm]


def _encoded(*values: int) -> bytes:
    return b"".join(int(value).to_bytes(4, "little") for value in values)


def _ldr_forward(index: int, target_index: int) -> int:
    """``LDR Rd,[pc,#imm]`` at ``index`` whose target word is ``target_index``."""
    imm = (target_index - index - 2) * 4
    assert 0 <= imm <= 0xFFF
    return LDR_PC | imm


def _ldr_backward(index: int, target_index: int) -> int:
    imm = (index + 2 - target_index) * 4
    assert 0 <= imm <= 0xFFF
    return LDR_PC_BACK | imm


def _account(window: bytes, address: int = BASE):
    """Independent model of ``count_window`` for assertions (not a shortcut)."""
    from tools.compare.window_diff import _pc_relative_ldr_targets
    literals = {i for i in _pc_relative_ldr_targets(window, address, 4)
                if 0 <= i * 4 <= len(window) - 4}
    subset = sum(int.from_bytes(window[4 * i:4 * i + 4], "little") < 0x10000
                 for i in literals)
    directives = sum(
        mask._censored_encoding(int.from_bytes(window[4 * i:4 * i + 4], "little"))
        for i in range(len(window) // 4) if i not in literals)
    return subset, directives, literals


def _assert_ledger(window: bytes, address: int = BASE, *, covered: bool = False):
    """Check ``count_window`` against the independent model and the invariant.

    ``subset + non-literal words == word count`` counts literal indices in the
    non-literal term: the acceptance criterion's ``subset`` term is the *number
    of literal indices*, not the tool's subset (values below 0x10000). The
    tool's ``subset + directives`` is the non-literal words that happen to be
    censored encodings, not every non-literal word, so comparing that sum with
    the word count is false for any window containing an ordinary instruction.

    Literal indices are counted as data only while they cannot simultaneously
    be decoded as a PC-relative load: the helper scans *every* word, so a
    literal value such as 4 may itself encode an LDR. ``covered=True`` (the
    default test windows) requires that no literal slot is load-like; the
    seeded test opts out because random target values may be load-like.
    """
    subset, directives, literals = _account(window, address)
    words = len(window) // 4
    assert mask.count_window(window, address) == (subset, directives)

    def load_like(index: int) -> bool:
        word = int.from_bytes(window[4 * index:4 * index + 4], "little")
        return (word >> 28 != 0xF and word & 0x0E000000 == 0x04000000
                and not word & 0x02000000 and bool(word & 0x00100000)
                and (word >> 16) & 0xF == 0xF)

    data_indices = {i for i in literals if not load_like(i)}
    if covered:
        assert data_indices == literals
    non_literal = words - len(data_indices)
    assert len(data_indices) + non_literal == words
    assert 0 <= directives <= non_literal if covered else True
    assert subset <= len(literals)
    return subset, directives, literals


# --- alignment: ValueError before any word is read --------------------------


@pytest.mark.parametrize("address", [BASE + 1, BASE + 2, BASE + 3,
                                     BASE - 1, BASE - 2])
def test_misaligned_address_raises_value_error(address):
    window = _encoded(ORDINARY, ORDINARY)
    with pytest.raises(ValueError):
        mask.count_window(window, address)


@pytest.mark.parametrize("length", [1, 2, 3, 5, 6, 7, 9])
def test_non_multiple_of_four_length_raises_value_error(length):
    # An aligned address with a misaligned span must still be refused.
    window = bytes(range(length))
    with pytest.raises(ValueError):
        mask.count_window(window, BASE)


def test_alignment_error_message_mentions_four_byte_alignment():
    with pytest.raises(ValueError, match="four-byte aligned"):
        mask.count_window(b"\0" * 4, BASE + 2)
    with pytest.raises(ValueError, match="four-byte aligned"):
        mask.count_window(b"\0" * 6, BASE)


def test_rejection_happens_before_the_literal_helper_runs(monkeypatch):
    def forbidden(*args):
        pytest.fail("literal helper ran on a window that must be rejected")

    monkeypatch.setattr(mask, "_pc_relative_ldr_targets", forbidden)
    with pytest.raises(ValueError):
        mask.count_window(_encoded(ORDINARY), BASE + 2)
    with pytest.raises(ValueError):
        mask.count_window(_encoded(ORDINARY)[:3], BASE)


def test_empty_window_is_aligned_and_counts_nothing():
    assert mask.count_window(b"", BASE) == (0, 0)
    assert mask.count_window(b"", BASE + 4) == (0, 0)


# --- subset: literal target values below 0x10000 ----------------------------


@pytest.mark.parametrize("value,expected", [
    (0x00000000, 1), (0x00000001, 1), (0x0000FFFF, 1),
    (0x00010000, 0), (0x00010001, 0), (0xFFFFFFFF, 0), (0x80000000, 0),
])
def test_literal_word_value_split_against_0x10000(value, expected):
    window = _encoded(_ldr_forward(0, 2), ORDINARY, value)
    assert mask.count_window(window, BASE) == (expected, 0)
    assert _account(window)[2] == {2}


def test_forward_and_backward_literals_inside_window_are_counted():
    # index 0: forward literal at 3; index 4: backward literal at 2.
    window = _encoded(_ldr_forward(0, 3), CENSORED["bkpt"], 0x7, 0x20,
                      _ldr_backward(4, 2), CENSORED["wfi"])
    subset, directives, literals = _assert_ledger(window)
    assert literals == {2, 3}
    assert subset == 2            # both literal values are below 0x10000
    assert directives == 2        # BKPT at 1 and WFI at 5 are code
    assert window[4 * 2:4 * 2 + 4] == _encoded(0x7)
    assert window[4 * 3:4 * 3 + 4] == _encoded(0x20)


def test_repeated_same_target_is_counted_once():
    window = _encoded(_ldr_forward(0, 3), _ldr_forward(1, 3), CENSORED["svc"], 0x10)
    assert mask.count_window(window, BASE) == (1, 1)
    assert _account(window)[2] == {3}


def test_literal_word_with_censored_pattern_feeds_subset_not_directives():
    for name, word in CENSORED.items():
        window = _encoded(_ldr_forward(0, 3), ORDINARY, ORDINARY, word)
        literals = _account(window)[2]
        assert literals == {3}
        subset, directives = mask.count_window(window, BASE)
        # Each fixture word here is >= 0x10000, so the literal adds no subset.
        assert subset == int(word < 0x10000) == 0
        # Whatever the literal's value, it never reaches directives.
        assert directives == 0, name
        # Control: the identical word in a non-literal slot is a directive.
        assert mask.count_window(_encoded(ORDINARY, ORDINARY, ORDINARY, word),
                                 BASE) == (0, 1)


def test_subset_threshold_applies_per_literal_independently():
    # Adjacent literals at 5 and 6, loaded by two LDRs at 0 and 1. The two
    # censored patterns sit at non-literal slots 3 and 4 (slot 2 is a spacer);
    # the target values 0xFFFF and 0x10000 are not load-like when decoded.
    window = _encoded(_ldr_forward(0, 5), _ldr_forward(1, 6), ORDINARY,
                      CENSORED["pld_immediate"], CENSORED["wfi"], 0xFFFF, 0x10000)
    subset, directives, literals = _assert_ledger(window)
    assert literals == {5, 6}
    assert subset == 1 and directives == 2


# --- directives: exactly one account for each censored pattern --------------


@pytest.mark.parametrize("name", sorted(CENSORED))
def test_each_censored_pattern_counts_once_in_directives(name):
    window = _encoded(CENSORED[name])
    assert mask._censored_encoding(CENSORED[name]) is True
    assert mask.count_window(window, BASE) == (0, 1)
    # The same word in each of several non-literal slots counts each time.
    window = _encoded(ORDINARY, CENSORED[name], ORDINARY, CENSORED[name])
    assert mask.count_window(window, BASE) == (0, 2)


def test_all_five_patterns_together_in_non_literal_slots():
    order = ["svc", "bkpt", "pld_immediate", "pld_register", "wfi"]
    window = _encoded(*(CENSORED[name] for name in order))
    subset, directives, literals = _assert_ledger(window)
    assert subset == 0 and literals == set() and directives == 5


def test_ordinary_data_processing_word_is_never_a_directive():
    assert mask._censored_encoding(ORDINARY) is False
    window = _encoded(ORDINARY, ORDINARY, ORDINARY)
    assert mask.count_window(window, BASE) == (0, 0)
    # Beside real patterns, the ordinary word neither adds nor removes counts.
    window = _encoded(ORDINARY, CENSORED["bkpt"], ORDINARY, CENSORED["wfi"], ORDINARY)
    assert mask.count_window(window, BASE) == (0, 2)


# --- out-of-window PC-relative loads are instructions ------------------------


@pytest.mark.parametrize("word", [0xE59FF000,           # forward past the end
                                  _ldr_forward(0, 6),   # target one word beyond
                                  _ldr_backward(0, -1),  # pc-imm underflows below BASE
                                  _ldr_backward(0, -2),  # too far below the front
                                  _ldr_forward(0, 2),    # imm 0 targets pc+8 itself
                                  0xE59F0FFC,           # imm 0xFFC forward
                                  _ldr_forward(1, 9)])  # load in the last slot
def test_pc_relative_ldr_outside_window_contributes_no_literal(word):
    window = _encoded(word, ORDINARY)
    subset, directives, literals = _assert_ledger(window)
    assert literals == set()
    assert subset == 0
    assert directives == 0        # code, but not a censored encoding
    assert mask.count_window(window, BASE) == (0, 0)


def test_start_address_selects_the_target_window():
    # "LDR" at index 0 targets BASE+8; whether that is inside depends on layout.
    window = _encoded(_ldr_forward(0, 2), ORDINARY, 0x5)
    assert mask.count_window(window, BASE) == (1, 0)
    # The same bytes at another aligned address: identical accounting, since
    # only relative offsets matter for a full window.
    assert mask.count_window(window, BASE + 0x1000) == (1, 0)


def test_unaligned_target_after_clamp_never_appears():
    # The helper only emits 4-aligned targets, so the tool's clamp can only
    # drop; pin that every reported literal index satisfies 0 <= 4i <= len-4.
    window = _encoded(_ldr_forward(0, 3), ORDINARY, ORDINARY, 0x3)
    for index in _account(window)[2]:
        assert 0 <= index * 4 <= len(window) - 4


# --- randomized ledger property ---------------------------------------------


def test_seeded_random_windows_obey_the_ledger():
    rng = random.Random(293)
    pool = [ORDINARY, CENSORED["svc"], CENSORED["bkpt"], CENSORED["wfi"],
            CENSORED["pld_immediate"], CENSORED["pld_register"],
            0x00000000, 0x0000FFFF, 0x00010000, 0xFFFFFFFF]
    counter_patterns = literal_slots = 0
    for _ in range(400):
        words = rng.randrange(1, 9)
        values = [rng.choice(pool) for _ in range(words)]
        # Make roughly half the slots PC-relative loads with random immediates.
        for index in range(words):
            if rng.random() < 0.5:
                # index+1 is unreachable by a forward load (imm 0 is pc+8);
                # negative targets reach the backward form.
                target = index + 1 + rng.randrange(1, index + 2 + words)
                if target <= index:
                    values[index] = _ldr_backward(index, target)
                elif target > index + 2 + 0xFFF // 4:
                    values[index] = LDR_PC | 0xFFF          # off the far end
                else:
                    values[index] = _ldr_forward(index, target)
        window = _encoded(*values)
        address = BASE + 4 * rng.randrange(0, 64)
        subset, directives, literals = _assert_ledger(window, address, covered=False)
        # Directives is exactly the censored count over non-literal slots; a
        # censored pattern in a literal slot does not appear in that sum.
        expected = sum(mask._censored_encoding(values[i])
                       for i in range(len(window) // 4) if i not in literals)
        assert directives == expected
        assert subset == sum(int.from_bytes(window[4 * i:4 * i + 4], "little")
                             < 0x10000 for i in literals)
        assert directives <= len(window) // 4
        counter_patterns += sum(values[i] in CENSORED.values()
                                for i in range(words) if i not in literals)
        literal_slots += len(literals)
    # The sample is not vacuous: it exercised both counters.
    assert counter_patterns > 0 and literal_slots > 0
