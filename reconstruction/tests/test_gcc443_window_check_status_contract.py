"""Status-ladder contract for ``gcc443_window_check.analyse`` (task #289).

This module is offline status accounting on fabricated ELF. It drives only
``tools/compare/gcc443_window_check.py::analyse`` (through the public
``original_window`` / ``call_sites`` / ``branch_target`` helpers) and asserts:

* the MATCHED_RAW / MATCHED_NORMALIZED_CALLS / DIFFERENT ladder;
* the normalised level fires only for a ``BL`` immediate24 difference, never
  for a non-branch difference or unequal lengths;
* every candidate call site gets the ARM link-formula check
  ``link((S+A)|1-P)>>2`` applied to the reference word, the decoded
  ``linked_target`` and the honouring of the enclosing synthetic symbol;
* the report schema, size fields and the no-instruction-bytes promise;
* ``original_window`` raises ``SystemExit`` when the address has no exact
  ``.symtab`` STT_FUNC bound.

Finding (pinned, not fixed here): ``call_sites`` reads candidate words from
``address + 4*k`` in the candidate's own bytes, so every candidate site is
4-aligned by construction. ``caller_aligned_4`` can only be False when the
``.symtab`` bound is itself unaligned, and that bound also changes the window
length. So the shipped checker can never route *a BL at a non-4-aligned site*
to DIFFERENT by decoding from an odd offset; it reaches DIFFERENT for an
unaligned bound only through the unequal-length rule, which
``test_unequal_lengths_are_different_with_unchecked_call_sites`` pins. An unaligned
bound is outside the checker's documented use (4-aligned function bounds).

Fixtures are synthetic only: the reference image is built in memory with
``nova3_team.tests.test_indexer.elf_fixture`` (payload rewritten through a
local helper) and the candidate ``ET_REL`` by a local builder. No ``analysis/``,
``originals/`` or ``reference/`` path is read, no subprocess is started and no
NDK is needed. Nothing here is a claim about any game function, and no game
symbol name or vendor hash appears.

Reproduce: .venv/bin/python -m pytest reconstruction/tests/test_gcc443_window_check_status_contract.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nova3_team.object_compare import CompareError, candidate_window  # noqa: E402
from nova3_team.tests.test_indexer import elf_fixture  # noqa: E402

CHECKER = ROOT / "tools" / "compare" / "gcc443_window_check.py"
SCHEMA = "nova3.gcc443-window-check/1"

# Synthetic address space shared by reference and candidate: word i of the image
# is at i*4. The candidate ET_REL starts its .text at 0, so one STT_FUNC st_value
# names the same instruction in both images.
TEXT_ADDRESS = 0x0             # image base (elf_fixture .text vaddr is rewritten)
ENTRY_ADDRESS = 0x08           # enclosing synthetic function symbol (word 2)
BL_ADDRESS = 0x10              # the BL site (word 4 of the synthetic image)
CALLEE_ADDRESS = 0x14          # a second STT_FUNC inside the same image (word 5)
SYMBOL = "synthetic_window"
CALLEE = "synthetic_callee"
SHSTRTAB_INDEX = 6             # elf_fixture section order: NULL, text, strtab, symtab, ...

# Well-known A32 encodings (no compilation involved).
NOP = 0xE1A00000               # mov r0, r0
MOV_R1_R0 = 0xE1A01000         # mov r1, r0
MOV_R2_R0 = 0xE1A02000         # mov r2, r0
BX_LR = 0xE12FFF1E             # bx lr


def _bl(site_address: int, target: int) -> int:
    """Encode ``bl target`` from ``site_address`` with the ARM link formula."""
    displacement = ((target | 1) - (site_address + 8)) >> 2
    return 0xEB000000 | (displacement & 0x00FFFFFF)


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("gcc443_window_check_contract", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(data: bytes, index: int) -> tuple[int, int, int]:
    """(payload offset, size, address) of section ``index`` in an ELF32 image."""
    shoff = struct.unpack_from("<I", data, 32)[0]
    offset, size, address = struct.unpack_from("<III", data, shoff + index * 40 + 16)
    return offset, size, address


def _rewrite(data: bytes, section_name: str, payload: bytes) -> bytes:
    """Overwrite a named section payload without changing its size."""
    result = bytearray(data)
    shoff = struct.unpack_from("<I", result, 32)[0]
    names_offset = struct.unpack_from(
        "<I", result, shoff + SHSTRTAB_INDEX * 40 + 16)[0]
    for index in range(1, struct.unpack_from("<H", result, 48)[0]):
        name_offset = struct.unpack_from("<I", result, shoff + index * 40)[0]
        start = names_offset + name_offset
        end = result.index(b"\0", start)
        if bytes(result[start:end]) == section_name.encode():
            offset, size, _ = _section(result, index)
            assert len(payload) == size
            result[offset:offset + size] = payload
            # Rebase the section onto the synthetic address space.
            struct.pack_into("<I", result, shoff + index * 40 + 3 * 4, TEXT_ADDRESS)
            return bytes(result)
    raise AssertionError(f"section {section_name} absent from fixture")


def _make_reference(tmp_path: Path, words: list[int],
                    functions: list[tuple[str, int, int]]) -> tuple[Path, int]:
    """Write a linked-ELF reference with a synthetic .text and .symtab.

    ``elf_fixture`` pads .text to 96 bytes with trailing zeros; only the first
    ``len(words)`` words carry the synthetic image.
    """
    text = b"".join(struct.pack("<I", word) for word in words)
    assert len(text) <= 96
    symbols = [
        dict(name="", value=0, size=0, section=0, info=0),
        dict(name="synthetic_anchor", value=TEXT_ADDRESS, size=len(text),
             section=1, info=0x12),
        *[dict(name=name, value=value, size=size, section=1, info=0x12)
          for name, value, size in functions],
    ]
    data = _rewrite(elf_fixture(symbols), ".text", text + bytes(96 - len(text)))
    path = tmp_path / "synthetic_reference.elf"
    path.write_bytes(data)
    return path, len(text)


def _make_candidate(tmp_path: Path, words: list[int],
                    function_name: str = SYMBOL,
                    function_address: int = ENTRY_ADDRESS) -> tuple[Path, int]:
    """Minimal little-endian ARM32 ``ET_REL`` with one STT_FUNC in .text.

    The synthetic image is the section (vaddr 0), so the STT_FUNC ``st_value``
    is its offset within it, equal to the reference address minus
    ``TEXT_ADDRESS``. The function runs from its offset to the section end.
    """
    text = b"".join(struct.pack("<I", word) for word in words)
    # The image is the section: st_value is the offset within .text.
    value = function_address - TEXT_ADDRESS
    names = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    strtab = b"\0" + function_name.encode() + b"\0"
    symtab = (struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)
              + struct.pack("<IIIBBH", 1, value, len(text) - value, 0x12, 0, 1))
    body = bytearray(52)
    placed = {}
    for name, payload, align in ((".text", text, 4), (".symtab", symtab, 4),
                                 (".strtab", strtab, 1), (".shstrtab", names, 1)):
        body.extend(bytes((-len(body)) % align))
        placed[name] = (len(body), len(payload))
        body.extend(payload)
    body.extend(bytes((-len(body)) % 4))
    shoff = len(body)
    sections = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (names.index(b".text"), 1, 6, 0, placed[".text"][0], placed[".text"][1],
         0, 0, 4, 0),
        (names.index(b".symtab"), 2, 0, 0, placed[".symtab"][0], placed[".symtab"][1],
         3, 1, 4, 16),
        (names.index(b".strtab"), 3, 0, 0, placed[".strtab"][0], placed[".strtab"][1],
         0, 0, 1, 0),
        (names.index(b".shstrtab"), 3, 0, 0, placed[".shstrtab"][0],
         placed[".shstrtab"][1], 0, 0, 1, 0),
    ]
    for section in sections:
        body.extend(struct.pack("<10I", *section))
    body[:52] = (b"\x7fELF\x01\x01\x01" + bytes(9)
                 + struct.pack("<HHIIIIIHHHHHH", 1, 40, 1, 0, 0, shoff, 0, 52,
                               0, 0, 40, len(sections), 4))
    path = tmp_path / "synthetic_candidate.o"
    path.write_bytes(bytes(body))
    return path, len(text)


def _report(checker, tmp_path: Path, reference_words: list[int],
            candidate_words: list[int], address: int = ENTRY_ADDRESS,
            symbol: str = SYMBOL) -> dict:
    image_end = TEXT_ADDRESS + len(reference_words) * 4
    assert image_end >= ENTRY_ADDRESS + 16, "synthetic images are at least one 16-byte window"
    functions = [(SYMBOL, ENTRY_ADDRESS, 16)]
    if CALLEE_ADDRESS < image_end:
        functions.append((CALLEE, CALLEE_ADDRESS, image_end - CALLEE_ADDRESS))
    reference, _ = _make_reference(tmp_path, reference_words, functions)
    candidate, _ = _make_candidate(tmp_path, candidate_words, symbol, address)
    return checker.analyse(reference, candidate, address, symbol)


# Words 0..5 at 0x0..0x14. Words 0..1 are a prologue outside the bound; the
# enclosing 16-byte function bound (SYMBOL) covers words 2..5 (ENTRY_ADDRESS =
# 0x8..0x18); word 4 is the BL slot (BL_ADDRESS = 0x10); a second STT_FUNC
# (CALLEE) starts at word 5 (CALLEE_ADDRESS = 0x14) and spans the rest of the
# image, so it lies inside the same synthetic image.
KERNEL = [NOP, 0, NOP, MOV_R1_R0, 0, BX_LR]
BL_OFFSET = BL_ADDRESS // 4    # 4: the BL slot is word 4 of the image
BOUND = 16                     # bytes of the enclosing synthetic window


def test_identical_windows_are_matched_raw_without_call_sites(checker, tmp_path):
    report = _report(checker, tmp_path, KERNEL, KERNEL)
    assert report["status"] == "MATCHED_RAW"
    assert report["call_sites"] == []


def test_bl_imm24_only_difference_is_normalized_call_with_formula_check(checker, tmp_path):
    # Candidate: relocatable BL to a nearby placeholder; reference: BL to CALLEE.
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    candidate = list(KERNEL)
    candidate[BL_OFFSET] = _bl(BL_ADDRESS, BL_ADDRESS)
    report = _report(checker, tmp_path, reference, candidate)

    assert report["status"] == "MATCHED_NORMALIZED_CALLS"
    assert report["status"] not in ("MATCHED_RAW", "DIFFERENT")
    assert report["original_window_sha256"] != report["candidate_window_sha256"]

    assert [site["address"] for site in report["call_sites"]] == [hex(BL_ADDRESS)]
    site, = report["call_sites"]
    assert site["arm_link_formula_reproduces_original_call"] is True
    assert site["linked_target"] == hex(CALLEE_ADDRESS)
    assert site["relocatable_placeholder_target"] == hex(BL_ADDRESS)
    assert site["caller_aligned_4"] is True
    # Equal lengths, and both are the exact 16-byte .symtab bound, not the section.
    assert report["original_bytes"] == report["candidate_bytes"] == BOUND


@pytest.mark.parametrize("change,label", [
    ([NOP, 0, NOP, MOV_R2_R0, 0, BX_LR], "data_processing_word"),
    ([NOP, 0, NOP, NOP, 0, BX_LR], "bl_to_nop"),
    ([NOP, 0, NOP, MOV_R1_R0, 0, 0xE12FFF10], "branch_word_not_bl"),
])
def test_non_bl_difference_is_different(checker, tmp_path, change, label):
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    report = _report(checker, tmp_path, reference, change)
    assert report["status"] == "DIFFERENT", label
    assert "MATCHED" not in report["status"]


def test_fast_branching_spurious_word_is_different(checker, tmp_path):
    # 0xEA... / 0xEB... with the 0x0E mask both look like BL; "blx imm" (0xFA) must not
    # be normalised away when only one side carries it.
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    candidate = list(KERNEL)
    candidate[BL_OFFSET] = _bl(BL_ADDRESS, BL_ADDRESS)
    assert _report(checker, tmp_path, reference, candidate)["status"] == \
        "MATCHED_NORMALIZED_CALLS"
    # 0xFA... ("blx imm") shares the BL mask's 0x0A000000 bits but is not a BL;
    # the normalised level must not blank it away.
    candidate[BL_OFFSET] = 0xFA000002
    assert _report(checker, tmp_path, reference, candidate)["status"] == "DIFFERENT"


def test_unaligned_bound_is_different_never_matched(checker, tmp_path):
    # An unaligned STT_FUNC bound (0xA) is outside the documented use. The
    # candidate window then runs to the section end (unequal length), so the
    # shipped checker can only report DIFFERENT, never a matched level.
    candidate_words = list(KERNEL)
    candidate_words[BL_OFFSET] = _bl(BL_ADDRESS, BL_ADDRESS)
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    reference_path, _ = _make_reference(
        tmp_path, reference, [(SYMBOL, ENTRY_ADDRESS, BOUND)])
    candidate_path, _ = _make_candidate(
        tmp_path, candidate_words, SYMBOL, ENTRY_ADDRESS + 2)
    report = checker.analyse(reference_path, candidate_path, ENTRY_ADDRESS, SYMBOL)
    assert report["status"] == "DIFFERENT"
    assert report["original_bytes"] != report["candidate_bytes"]


def test_unequal_lengths_are_different_with_unchecked_call_sites(checker, tmp_path):
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    candidate = [NOP, NOP, NOP]
    report = _report(checker, tmp_path, reference, candidate)
    assert report["status"] == "DIFFERENT"
    assert report["original_bytes"] != report["candidate_bytes"]
    assert report["call_sites"] == []

    # The candidate BL site exists but the window lengths differ: the formula
    # check is skipped for every site rather than guessed.
    candidate = [NOP, 0, NOP, MOV_R1_R0, _bl(BL_ADDRESS, BL_ADDRESS)]
    report = _report(checker, tmp_path, reference, candidate)
    assert report["status"] == "DIFFERENT"
    assert report["call_sites"], "candidate BL must still be listed"
    for site in report["call_sites"]:
        assert site["linked_target"] is None
        assert site["arm_link_formula_reproduces_original_call"] is None


def test_report_schema_sizes_and_no_instruction_bytes(checker, tmp_path):
    reference = list(KERNEL)
    reference[BL_OFFSET] = _bl(BL_ADDRESS, CALLEE_ADDRESS)
    candidate = list(KERNEL)
    candidate[BL_OFFSET] = _bl(BL_ADDRESS, BL_ADDRESS)
    candidate_path, _ = _make_candidate(tmp_path, candidate)
    report = _report(checker, tmp_path, reference, candidate)
    window = candidate_window(candidate_path.read_bytes(), SYMBOL)

    assert report["schema"] == SCHEMA
    assert report["original_bytes"] == BOUND       # exact .symtab STT_FUNC st_size
    assert report["candidate_bytes"] == window["size"] == BOUND  # candidate st_size
    # Window hashes cover exactly the bound: words 2..5 of the image.
    first = ENTRY_ADDRESS // 4
    assert report["original_window_sha256"] == hashlib.sha256(
        b"".join(struct.pack("<I", word) for word in reference[first:first + 4])).hexdigest()
    assert report["candidate_window_sha256"] == hashlib.sha256(
        b"".join(struct.pack("<I", word) for word in candidate[first:first + 4])).hexdigest()
    assert report["original_window_sha256"] != report["candidate_window_sha256"]

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    emitted = set(keys(report))
    # "bytes" may only appear as a size field, never as an instruction-data blob.
    assert {key for key in emitted if "bytes" in key.lower()} <= {
        "original_bytes", "candidate_bytes"}
    assert all(isinstance(value, (bool, str, int, list, dict, type(None)))
               for value in report.values())
    text = json.dumps(report)
    # No synthetic instruction word (those that encode no small metadata value,
    # e.g. a size or address already present) may be serialised as machine code.
    for word in {w for w in set(reference) | set(candidate) if w > 0xFFFF or w < 0x10}:
        assert f"{word:08x}" not in text.lower()
        assert f"0x{word:x}" not in text.lower()


def test_original_window_requires_exact_symtab_bound(checker, tmp_path):
    reference, _ = _make_reference(tmp_path, KERNEL, [(SYMBOL, ENTRY_ADDRESS, 16)])
    # Address inside the function but not an exact STT_FUNC st_value.
    with pytest.raises(SystemExit):
        checker.original_window(reference, ENTRY_ADDRESS + 4)
    # Address outside any symbol.
    with pytest.raises(SystemExit):
        checker.original_window(reference, 0x3000)
    assert len(checker.original_window(reference, ENTRY_ADDRESS)) == 16


def test_analyse_propagates_missing_reference_bound(checker, tmp_path):
    candidate, _ = _make_candidate(tmp_path, KERNEL)
    reference, _ = _make_reference(tmp_path, KERNEL, [(SYMBOL, ENTRY_ADDRESS, 16)])
    with pytest.raises(SystemExit):
        checker.analyse(reference, candidate, ENTRY_ADDRESS + 4, SYMBOL)


def test_candidate_parser_is_the_object_compare_helper(checker, tmp_path):
    # analyse() must read its candidate through object_compare.candidate_window:
    # the same fabricated object gives the same bytes and st_size.
    candidate, section_size = _make_candidate(tmp_path, KERNEL)
    window = candidate_window(candidate.read_bytes(), SYMBOL)
    assert window["size"] == BOUND == 16           # st_value to section end
    assert section_size == len(KERNEL) * 4
    assert window["relocation_offsets"] == []
    with pytest.raises(CompareError):
        candidate_window(candidate.read_bytes(), "absent")

    # No relocation section: object_compare rejects nothing, and analyse()
    # reports the exact st_size as the candidate window length.
    reference, _ = _make_reference(tmp_path, KERNEL, [(SYMBOL, ENTRY_ADDRESS, BOUND)])
    report = checker.analyse(reference, candidate, ENTRY_ADDRESS, SYMBOL)
    assert report["candidate_bytes"] == window["size"]
    assert report["status"] == "MATCHED_RAW"
