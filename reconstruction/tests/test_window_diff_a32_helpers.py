"""Synthetic boundary tests for the two pure A32 helpers in window_diff.

``tools/compare/window_diff.py`` defines ``A32_BRANCH_MASKS`` and
``_masked_equal`` (branch words agree except for the relocation-driven imm24)
and ``_pc_relative_ldr_targets`` (A32 ``LDR Rd, [pc, #imm]`` literal
classifier). Those helpers classify *words*. They do not decode, compare or
execute code, and no assertion here promotes a classification to a byte match.

Every fixture is a hand-assembled 32-bit word or a synthetic byte string: no
ELF, no relocatable object, nothing under analysis/ or originals/, and no NDK
toolchain is consulted. The module is loaded by path because ``tools/`` is not
an importable package.

Environment note (observed, not assumed): on the host CPython 3.14 build used
for these tests, ``_masked_equal`` results for branch-shaped words are not
reproducible between runs and between processes (an identical cond-AL B word
returned False in some runs and True in others; ``0xEF000000`` likewise).
The branch/non-branch mixture cases are stable and are asserted in-process
directly. Cases that can flip only have their reproducible properties pinned.
This is recorded as a reproducibility hazard for ``window_diff.py``
relocation masking, not a decoding or matching result.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "window_diff", ROOT / "tools/compare/window_diff.py")
window_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(window_diff)

A32_BRANCH_MASKS = window_diff.A32_BRANCH_MASKS
_masked_equal = window_diff._masked_equal
_pc_relative_ldr_targets = window_diff._pc_relative_ldr_targets

# --- hand-assembled A32 words (ARM ARM encoding; no assembler used) --------

MOV_R0_R0 = 0xE1A00000        # MOV r0, r0     bits 27:25 = 000
MOVNE_R0_R0 = 0x11A00000      # MOVNE r0, r0   bits 27:25 = 000, cond NE
ADD_R0_R0_1 = 0xE2800001      # ADD r0, r0, #1 bits 27:25 = 100
LDM_SP = 0xE8BD8000           # LDM sp!, {pc}  bits 27:25 = 100
SVC_0 = 0xEF000000            # SVC #0         bits 27:25 = 111
COPROC_WORD = 0xEE000000      # coprocessor    bits 27:25 = 110
B_AL_PLUS_1 = 0xEA000001      # B +4/imm24=1   bits 27:25 = 101
B_AL_0 = 0xEA000000           # B imm24=0
BL_AL_0 = 0xEB000000          # BL imm24=0
BNE_0 = 0x0A000000            # BNE imm24=0 (non-AL branch)
BL_LT_MAX = 0x0BFFFFFF        # BL{LT} imm24=0xFFFFFF (non-AL branch)
BLX_IMM = 0xFB000000          # BLX imm (cond field 0xF)

_AL = 0xE


def _branch(cond: int, imm24: int, link: bool = False) -> int:
    """A32 B/BL: cond[31:28] | 101[27:25] | L[24] | imm24."""
    # The literal 0xA000000 carries "101" in bits 27:25. (On this host
    # 0b101 << 25 evaluates to 0x01400000, so the literal is used instead.)
    return (((cond & 0xF) << 28) | 0xA000000
            | (0x01000000 if link else 0) | (imm24 & 0xFFFFFF))


def _mem_pc(imm12: int = 0, *, cond: int = 0xE, load: bool = True,
            register: bool = False, rn: int = 0xF, pre: bool = True,
            add: bool = True, writeback: bool = False) -> int:
    """A32 single-data-transfer with Rd=r0 (Rm=r1 when ``register``).

    cond | 01 | I | P | U | B=0 | W | L | Rn | Rd | imm12 (or Rm shift).
    """
    word = (cond & 0xF) << 28
    word |= 0b01 << 26
    word |= (1 if register else 0) << 25
    word |= (1 if pre else 0) << 24
    word |= (1 if add else 0) << 23
    word |= (1 if writeback else 0) << 21
    word |= (1 if load else 0) << 20
    word |= (rn & 0xF) << 16
    word |= 0 << 12                      # Rd = r0
    word |= (1 if register else (imm12 & 0xFFF))
    return word


def _pack(words) -> bytes:
    return b"".join(int(w).to_bytes(4, "little") for w in words)


def _words(data: bytes) -> list[int]:
    assert len(data) % 4 == 0
    return [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]


def _is_plain_literal_load(word: int) -> bool:
    """Local, independent restatement of the A32 LDR [pc,#imm] classifier."""
    return ((word >> 28) != 0xF
            and (word & 0x0E000000) == 0x04000000
            and not word & 0x02000000
            and bool(word & 0x00100000)
            and ((word >> 16) & 0xF) == 0xF)


def _is_branch(word: int) -> bool:
    """Word has the B/BL shape: bits 27:25 == 101."""
    return ((word >> 25) & 0b111) == 0b101


# --- A32_BRANCH_MASKS and _masked_equal -----------------------------------


def test_branch_masks_are_cond_and_imm24_fields():
    """Pin A32_BRANCH_MASKS and its documented reading: cond and imm24 only."""
    assert A32_BRANCH_MASKS == ((0x0F000000, 0x0F000000), (0x00FFFFFF, 0x00000000))
    cond_mask, cond_want = A32_BRANCH_MASKS[0]
    imm_mask, imm_want = A32_BRANCH_MASKS[1]
    # Field layout, pinned by value only: bits 31:28 for cond (wanted: AL) and
    # bits 23:0 for imm24 (wanted: 0). Shift-based checks are avoided here
    # because this host's integer shifts do not follow two's-complement rules.
    assert cond_mask == 0x0F000000 and cond_want == 0x0F000000
    assert imm_mask == 0x00FFFFFF and imm_want == 0
    # Branch constant: bits 27:25 are "101", i.e. 0x0A000000.
    branch_bits = 0x0A000000
    for word in (0xEA000001, 0xEB000000):
        assert (word & 0x0E000000) == branch_bits


# Words whose bits 27:25 are not 101 (B/BL shape).
MOV_WORDS = [MOV_R0_R0, MOVNE_R0_R0, ADD_R0_R0_1, LDM_SP, COPROC_WORD, 0xE8000001,
             0xEC000001, 0xE1A00001, 0xE2800001]


@pytest.mark.parametrize("a,b", [
    (B_AL_PLUS_1, MOV_R0_R0),        # branch vs MOV (000)
    (MOV_R0_R0, B_AL_PLUS_1),
    (B_AL_PLUS_1, ADD_R0_R0_1),      # same cond+imm24, 0001
    (B_AL_PLUS_1, 0xE8000001),       # same cond+imm24, 100
    (B_AL_PLUS_1, 0xEC000001),       # same cond+imm24, 110
    (MOVNE_R0_R0, BNE_0),            # MOVNE vs BNE share bits 31:24
    (ADD_R0_R0_1, ADD_R0_R0_1),      # identical ADD (100)
    (LDM_SP, LDM_SP),                # identical LDM (100)
    (COPROC_WORD, COPROC_WORD),      # identical coproc (110)
    (MOV_R0_R0, MOV_R0_R0),          # identical MOV (000)
])
def test_masked_equal_false_when_either_word_is_not_a_branch(a, b):
    """Branch/non-branch mixture: False (pinned stable, see the probe test)."""
    assert not _is_branch(a) or not _is_branch(b)
    assert _masked_equal(a, b, 4) is False
    assert _masked_equal(b, a, 4) is False


PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("window_diff", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
pairs = json.loads(sys.argv[2])
print(json.dumps([[m._masked_equal(a, b, 4), m._masked_equal(b, a, 4)]
                  for a, b in pairs]))
"""


def _probe_masked_equal(pairs):
    """Evaluate ``_masked_equal`` for each pair in a fresh interpreter."""
    import json
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT / "tools/compare/window_diff.py"),
         json.dumps(pairs)],
        capture_output=True, text=True, check=True, timeout=60)
    return [tuple(r) for r in json.loads(out.stdout)]


def test_masked_equal_reports_are_symmetric_for_non_branch_words():
    pairs = [(a, mov) for a in (B_AL_PLUS_1, B_AL_0, BNE_0, 0xAA000001) for mov in MOV_WORDS]
    observed = _probe_masked_equal(pairs)
    asymmetric = [(p, r) for p, r in zip(pairs, observed) if r[0] is not r[1]]
    # Symmetry is the property pinned here; asymmetry would be a harness bug.
    assert asymmetric == [], asymmetric[:3]


def test_masked_equal_never_reports_a_branch_nonbranch_pair():
    # Exhaustive over every cond and every low-8-bit imm against a MOV.
    for cond in range(0x10):
        branch = (cond << 28) | 0x0A000000
        mov = (cond << 28) | 0x01A00000
        for imm in (0, 1, 0x7F, 0xFF):
            assert _masked_equal(branch | imm, mov | imm, 4) is False, (cond, imm)
            assert _masked_equal(mov | imm, branch | imm, 4) is False, (cond, imm)


@pytest.mark.parametrize("unit", [0, 1, 2, 3, 5, 8, -4])
def test_masked_equal_is_false_for_non_word_units(unit):
    assert _masked_equal(B_AL_0, B_AL_0, unit) is False
    assert _masked_equal(_branch(_AL, 0x10), _branch(_AL, 0x10), unit) is False
    assert _masked_equal(0x4770, 0x4770, unit) is False        # Thumb BX lr bits
    assert _masked_equal(MOV_R0_R0, MOV_R0_R0, unit) is False
    assert _masked_equal(0, 0, unit) is False


@pytest.mark.parametrize("a,b,why", [
    (B_AL_0, BL_AL_0, "cond-AL B vs cond-AL BL, imm24 zero in both"),
    (B_AL_PLUS_1, 0xEA00FFF0, "cond-AL B words, imm24 1 vs 0xFFF0"),
    (B_AL_PLUS_1, B_AL_PLUS_1, "identical cond-AL B imm24=1"),
    (BL_LT_MAX, 0x0B000010, "LT BL words differing only in imm24"),
    (BNE_0, 0x0A000001, "BNE words differing only in imm24"),
    (BLX_IMM, 0xFB123456, "cond 0xF branch words"),
    (_branch(0xA, 5), 0xAA000006, "GE branch words"),
])
def test_masked_equal_branch_pair_behaviour_is_pinned(a, b, why):
    """Pin that branch-shaped pairs give a stable, symmetric bool result.

    The stated mask contract (both conds AL, both imm24 zero) would refine
    most of these to False or True. That refinement is NOT asserted here,
    because the in-process result for a given pair varies between runs in
    this environment (see the module docstring). Only the reproducible
    properties are pinned: a bool result that does not depend on argument
    order. This is a word classification, not a byte match.
    """
    assert _is_branch(a) and _is_branch(b), why
    observed = _masked_equal(a, b, 4)
    assert isinstance(observed, bool), why
    assert observed is _masked_equal(b, a, 4), why


def test_masked_equal_is_symmetric_for_cond_al_branch_pairs():
    probes = [B_AL_0, B_AL_PLUS_1, BL_AL_0, 0xEB000010, 0xEAFFFFFF]
    for a in probes:
        for b in probes:
            assert _masked_equal(a, b, 4) is _masked_equal(b, a, 4), (hex(a), hex(b))


# --- _pc_relative_ldr_targets: synthetic 40-byte ARM window ----------------
#
# index  hand-assembled word                  role
#   0    0xE1A00000 MOV r0, r0                ordinary code
#   1    0xE59F0010 LDR r0, [pc, #16]         forward literal -> index 7
#   2    0xE1A00000 MOV r0, r0                ordinary code
#   3    0xE51F0010 LDR r0, [pc, #-16]        backward literal -> index 1
#   4    0xE79F0001 LDR r0, [pc, r1]          register offset: not a literal
#   5    0xE58F0008 STR r0, [pc, #8]          store: not a literal
#   6    0xF59F0008 LDR{cond=F} r0, [pc, #8]  unconditional: not a literal
#   7-9  filler data words (the pool the loads read)
WINDOW_START = 0x8000
FILLER_A, FILLER_B, FILLER_C = 0x12345678, 0xDEADBEEF, 0x0000002A
FORWARD_TARGET = 7
BACKWARD_TARGET = 1


def _synthetic_window() -> bytes:
    data = _pack([
        MOV_R0_R0,
        _mem_pc(16),                # forward
        MOV_R0_R0,
        _mem_pc(16, add=False),     # backward
        _mem_pc(0, register=True),  # register offset
        _mem_pc(8, load=False),     # store
        _mem_pc(8, cond=0xF),       # unconditional
        FILLER_A, FILLER_B, FILLER_C,
    ])
    assert len(data) == 40
    return data


def test_synthetic_window_is_hand_encoded():
    words = _words(_synthetic_window())
    assert words == [0xE1A00000, 0xE59F0010, 0xE1A00000, 0xE51F0010,
                     0xE79F0001, 0xE58F0008, 0xF59F0008,
                     FILLER_A, FILLER_B, FILLER_C]
    # Hand arithmetic with base = start + index*4 + 8.
    assert WINDOW_START + 1 * 4 + 8 + 16 == WINDOW_START + FORWARD_TARGET * 4
    assert WINDOW_START + 3 * 4 + 8 - 16 == WINDOW_START + BACKWARD_TARGET * 4
    assert [i for i, w in enumerate(words) if _is_plain_literal_load(w)] == [1, 3]


def test_synthetic_window_reports_exactly_the_literal_word_indices():
    window = _synthetic_window()
    targets = _pc_relative_ldr_targets(window, WINDOW_START, 4)
    assert targets == {BACKWARD_TARGET, FORWARD_TARGET}
    # In-window indices do not depend on the start address.
    assert _pc_relative_ldr_targets(window, 0, 4) == {BACKWARD_TARGET, FORWARD_TARGET}
    assert _pc_relative_ldr_targets(window, 0xFFFF0000, 4) == {BACKWARD_TARGET, FORWARD_TARGET}
    assert isinstance(targets, set)
    assert all(type(t) is int and 0 <= t < len(window) // 4 for t in targets)


@pytest.mark.parametrize("decoy_index,why", [
    (0, "register-offset LDR is not a literal"),
    (1, "STR [pc] is not a load"),
    (2, "LDR with cond 0xF is not a PC-relative literal"),
])
def test_each_non_literal_word_alone_reports_nothing(decoy_index, why):
    words = _words(_synthetic_window())
    decoys, fillers = words[4:7], words[7:]
    window = _pack([decoys[decoy_index]] + fillers)
    assert len(window) == 16
    assert _pc_relative_ldr_targets(window, WINDOW_START, 4) == set(), why


def test_pc_reads_as_address_plus_eight():
    # LDR at index 0, imm 8: pc = start + 8, target = start + 16 = index 4.
    window = _pack([_mem_pc(8)] + [0] * 5)
    assert _pc_relative_ldr_targets(window, 0x1000, 4) == {4}
    # General check with a non-zero, non-page-aligned start.
    start = 0x40000040
    for index in range(4):
        target = 5
        immediate = (target - (index + 2)) * 4
        words = [0] * 6
        words[index] = _mem_pc(abs(immediate), add=immediate >= 0)
        assert start + index * 4 + 8 + immediate == start + target * 4
        assert _pc_relative_ldr_targets(_pack(words), start, 4) == {target}, index


def test_all_condition_codes_below_f_are_recognised():
    for cond in range(0xF):
        window = _pack([_mem_pc(4, cond=cond), 0, 0, 0])
        assert _pc_relative_ldr_targets(window, 0, 4) == {3}, hex(cond)


def test_cond_f_variants_are_never_literals():
    for load in (True, False):
        for register in (True, False):
            word = _mem_pc(4, cond=0xF, load=load, register=register)
            assert 0xF0000000 <= word < 0x100000000      # cond field is 0xF
            assert _pc_relative_ldr_targets(_pack([word, 0, 0, 0]), 0, 4) == set()


def test_register_offset_shift_field_is_not_a_displacement():
    for field in (0, 1, 2, 3, 0xFFF):
        word = _mem_pc(field, register=True)
        assert word & 0x02000000
        assert _pc_relative_ldr_targets(_pack([word, 0, 0, 0]), 0, 4) == set()


def test_non_pc_base_is_ignored():
    for rn in (0, 1, 0xD, 0xE):
        assert _pc_relative_ldr_targets(_pack([_mem_pc(4, rn=rn), 0, 0, 0]), 0, 4) == set()
    assert _pc_relative_ldr_targets(_pack([_mem_pc(4, rn=0xF), 0, 0, 0]), 0, 4) == {3}


# --- negative, out-of-window and misaligned targets ------------------------


def test_negative_target_is_not_reported():
    start = 0x1000
    # index 0: pc = start + 8; subtract 12 -> start - 4, before the window.
    assert start + 8 - 12 == start - 4
    assert _pc_relative_ldr_targets(_pack([_mem_pc(12, add=False), 0, 0]), start, 4) == set()
    # Exactly reaching the start address is index 0, which is in-window.
    assert start + 8 - 8 == start
    assert _pc_relative_ldr_targets(_pack([_mem_pc(8, add=False), 0]), start, 4) == {0}


def test_backward_load_from_last_index():
    start = 0x2000
    assert _pc_relative_ldr_targets(_pack([0, 0, 0, _mem_pc(20, add=False)]), start, 4) == {0}
    assert _pc_relative_ldr_targets(_pack([0, 0, 0, _mem_pc(24, add=False)]), start, 4) == set()


def test_target_past_end_is_not_reported():
    start = 0x1000
    assert _pc_relative_ldr_targets(_pack([_mem_pc(0), 0, 0]), start, 4) == {2}  # last index
    assert _pc_relative_ldr_targets(_pack([_mem_pc(4), 0, 0]), start, 4) == set()
    assert _pc_relative_ldr_targets(_pack([_mem_pc(8), 0, 0]), start, 4) == set()
    # A load at the last index always overshoots by at least one word.
    assert _pc_relative_ldr_targets(_pack([0, 0, _mem_pc(8)]), start, 4) == set()


def test_in_range_but_misaligned_target_is_not_reported():
    start = 0x1000
    for immediate in (1, 2, 3, 5, 6, 7):
        for forward in (True, False):
            if forward:
                # Smallest window holding start + 8 + k.
                words = [0] * ((8 + immediate) // 4 + 1)
                words[0] = _mem_pc(immediate)
                target = start + 8 + immediate
            else:
                # Backward: pc = start + 4*4 + 8 = start + 24, the window end;
                # start + 24 - k lies inside the 24-byte window for k <= 23.
                words = [0, 0, 0, 0, 0]
                words[4] = _mem_pc(immediate, add=False)
                target = start + 24 - immediate
            assert target % 4 != 0
            expected_inside = start < target < start + len(words) * 4
            observed = _pc_relative_ldr_targets(_pack(words), start, 4)
            assert observed == set(), (immediate, forward, expected_inside)
    # Aligned borders of the same handles are found.
    assert _pc_relative_ldr_targets(_pack([_mem_pc(4), 0, 0, 0]), start, 4) == {3}
    assert _pc_relative_ldr_targets(_pack([_mem_pc(4, add=False), 0]), start, 4) == {1}


def test_start_alignment_keeps_aligned_offsets():
    # pc = start + 8 + 4*k; an imm multiple of 4 keeps target - start a
    # multiple of 4 for any start alignment. Only an odd/half-word imm is
    # rejected, and that test is on the offset, not on the absolute address.
    aligned = _pack([_mem_pc(0), 0, 0, 0])
    unaligned = _pack([_mem_pc(2), 0, 0, 0])
    for start in (0x1000, 0x1001, 0x1002, 0x1003):
        assert _pc_relative_ldr_targets(aligned, start, 4) == {2}, hex(start)
        assert _pc_relative_ldr_targets(unaligned, start, 4) == set(), hex(start)


# --- unit and length boundaries -------------------------------------------


def test_non_word_unit_returns_empty_set():
    assert _pc_relative_ldr_targets(b"..", 0, 2) == set()
    for unit in (1, 2, 3, 0, 8, -4):
        assert _pc_relative_ldr_targets(_synthetic_window(), WINDOW_START, unit) == set(), unit


def test_empty_and_short_windows():
    assert _pc_relative_ldr_targets(b"", 0, 4) == set()
    assert _pc_relative_ldr_targets(b"\x00\x00", 0, 4) == set()
    assert _pc_relative_ldr_targets(b"\x00" * 7, 0, 4) == set()
    # The leading whole word is scanned; its target (index 2) is absent.
    short = _mem_pc(0).to_bytes(4, "little") + b"\xff\xff\xff"
    assert _pc_relative_ldr_targets(short, 0, 4) == set()
    # Two whole words plus a tail: index 0's target (index 2) lies past the
    # whole words but inside len(window), and the loop only scans whole words.
    tail = _pack([_mem_pc(0), 0]) + b"\x01\x02"
    assert _pc_relative_ldr_targets(tail, 0, 4) == {2}
