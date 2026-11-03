"""Pin the list accounting helpers of tools/compare/gcc443_explain.py.

Only ``class_counts`` (lines 117-118) and ``first_divergence`` (lines 121-127)
are exercised.  This is pure list accounting over synthetic instruction stubs:
nothing is compiled, no subprocess runs, no Capstone/ELF/pyelftools is touched,
and no claim is made that any game function differs from or matches anything.

The module is loaded by path, exactly as tools/dsh_loop/tests/test_opcode_class.py
does, so importing it must stay free of the optional Capstone and pyelftools
imports (those live inside the functions that use them).  ``main()`` and
``compile_one()`` are never called.

Contract pinned here:

* ``class_counts(words)`` is ``collections.Counter(opcode_class(w) for w in
  words)``; empty input gives an empty Counter.  Output order is an
  implementation detail (the tool serialises via ``most_common()``), so only
  Counter equality is asserted.
* ``first_divergence(original, candidate)`` compares the base mnemonic (text
  before the first ``.``).  Equal base sequences give ``None``; a difference at
  index ``i`` gives ``(i, original[i].mnemonic, candidate[i].mnemonic)`` (the
  full mnemonics, suffixes included); a strict prefix gives
  ``(min(len), '(end)', '(end)')``.
"""

from __future__ import annotations

import collections
import importlib.util
import unittest
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "gcc443_explain", ROOT / "tools" / "compare" / "gcc443_explain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Minimal stand-in for a Capstone instruction: ``first_divergence`` only
# reads ``.mnemonic``, so nothing else is provided.
Insn = namedtuple("Insn", "mnemonic")


def insns(*mnemonics):
    return [Insn(mnemonic) for mnemonic in mnemonics]


class AccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = load()
        cls.module = module
        cls.opcode_class = staticmethod(module.opcode_class)
        cls.class_counts = staticmethod(module.class_counts)
        cls.first_divergence = staticmethod(module.first_divergence)

    # -- import contract ---------------------------------------------------

    def test_import_needs_no_compiler_or_subprocess(self):
        self.assertTrue(callable(self.module.class_counts))
        self.assertTrue(callable(self.module.first_divergence))
        self.assertFalse(hasattr(self.module, "capstone"))
        self.assertFalse(hasattr(self.module, "elftools"))

    # -- class_counts ------------------------------------------------------

    def test_class_counts_empty(self):
        counts = self.class_counts([])
        self.assertIsInstance(counts, collections.Counter)
        self.assertEqual(counts, collections.Counter())
        self.assertEqual(len(counts), 0)
        self.assertEqual(sum(counts.values()), 0)

    def test_class_counts_matches_counter_of_opcode_class(self):
        # Fixed synthetic words: each word is chosen so its opcode fields land
        # in a distinct class (the words are pinned individually by
        # tools/dsh_loop/tests/test_opcode_class.py).
        words = [
            0xEBFFFE67,  # bl
            0xEA000007,  # b
            0xED944A00,  # vldr s8, [r4, #0x10]
            0xED9F2A73,  # vldr s4, [pc, #...]
            0xEDC47A0F,  # vstr s15, [r4, #0x3c]
            0xEEF77A00,  # vmov.f32 s15, #1.0 (vmov_imm)
            0xE92D4070,  # push {r4-r6, lr}        (arm32)
            0xE3510001,  # cmp r1, #1              (arm32)
            0xEE676A26,  # vmul s13, s14, s6
            0xEE202A82,  # vmla s4, s2, s1
            0xEE311A80,  # vadd s2, s2, s1
            0xEE210A80,  # vmul-family (opcode fields 0x2/3/0 -> vmla here)
            0xEEB70A00,  # vmov_imm (constant form)
            0xEE700A00,  # VFP co-processor 7: vfp_other catch-all
        ]
        expected = collections.Counter(self.opcode_class(w) for w in words)
        counts = self.class_counts(words)

        # Required coverage classes are really present.
        for name in ("arm32", "b", "bl", "vldr", "vstr", "vfp_other"):
            self.assertIn(name, expected, name)
            self.assertGreater(expected[name], 0, name)

        # Counter equality, never dict order.
        self.assertIsInstance(counts, collections.Counter)
        self.assertEqual(counts, expected)
        self.assertEqual(sum(counts.values()), len(words))

    def test_class_counts_vfp_other_bucket(self):
        # A VFP word whose opcode fields select co-processor 7 (0xEE700A00)
        # is not vldr/vstr/vmov_imm and has no modelled arithmetic form, so it
        # falls into the catch-all.
        word = 0xEE700A00
        self.assertEqual(self.opcode_class(word), "vfp_other")
        self.assertEqual(self.class_counts([word, word]), collections.Counter({"vfp_other": 2}))

    def test_class_counts_is_order_independent(self):
        words = [0xEA000007, 0xE92D4070, 0xEBFFFE67]
        self.assertEqual(self.class_counts(words),
                         self.class_counts(list(reversed(words))))
        self.assertEqual(dict(self.class_counts(words)),
                         dict(self.class_counts(list(reversed(words)))))

    def test_class_counts_accepts_any_iterable(self):
        # A generator is consumed exactly once, same result as a list.
        words = (w for w in [0xEA000007, 0xEBFFFE67, 0xEBFFFE67])
        self.assertEqual(self.class_counts(words),
                         collections.Counter({"b": 1, "bl": 2}))

    # -- first_divergence: equality ---------------------------------------

    def test_first_divergence_equal_lists_is_none(self):
        self.assertIsNone(self.first_divergence([], []))
        self.assertIsNone(self.first_divergence(insns("mov", "ldr", "bx"), insns("mov", "ldr", "bx")))

    def test_first_divergence_ignores_suffix_after_dot(self):
        # Data-type suffixes are not divergences; the base mnemonic is the
        # comparable unit.
        self.assertIsNone(self.first_divergence(
            insns("vmov.f32", "vadd.f32", "vldr"), insns("vmov.f64", "vadd.f64", "vldr")))
        self.assertIsNone(self.first_divergence(
            insns("vldr.f32", "vstr.f64"), insns("vldr", "vstr")))
        self.assertIsNone(self.first_divergence(insns("bls.w"), insns("bls")))

    # -- first_divergence: differences ------------------------------------

    def test_first_divergence_middle_returns_index_and_mnemonics(self):
        self.assertEqual(
            self.first_divergence(
                insns("mov", "ldr", "add", "bx"),
                insns("mov", "ldr", "sub", "bx")),
            (2, "add", "sub"))

    def test_first_divergence_reports_earliest_index(self):
        self.assertEqual(
            self.first_divergence(
                insns("mov", "ldr", "add", "bx"),
                insns("mov", "str", "add", "bx")),
            (1, "ldr", "str"))

    def test_first_divergence_returns_full_mnemonics_with_suffixes(self):
        self.assertEqual(
            self.first_divergence(
                insns("vmov.f32", "vadd.f32"),
                insns("vmov.f32", "vsub.f32")),
            (1, "vadd.f32", "vsub.f32"))

    def test_first_divergence_original_shorter_is_strict_prefix(self):
        self.assertEqual(
            self.first_divergence(insns("mov", "ldr"), insns("mov", "ldr", "add")),
            (2, "(end)", "(end)"))

    def test_first_divergence_candidate_shorter_is_strict_prefix(self):
        self.assertEqual(
            self.first_divergence(insns("mov", "ldr", "add"), insns("mov", "ldr")),
            (2, "(end)", "(end)"))

    def test_first_divergence_empty_against_nonempty(self):
        self.assertEqual(self.first_divergence([], insns("mov")), (0, "(end)", "(end)"))
        self.assertEqual(self.first_divergence(insns("mov"), []), (0, "(end)", "(end)"))

    def test_first_divergence_difference_before_prefix_wins(self):
        # The prefix rule only applies after every common index agrees.
        self.assertEqual(
            self.first_divergence(insns("mov", "ldr"), insns("mov", "str", "add")),
            (1, "ldr", "str"))


if __name__ == "__main__":
    unittest.main()
