"""Pin the ARM opcode classifier used by tools/compare/gcc443_explain.py.

The classifier deliberately works on encoded fields instead of disassembler
text because Capstone and GNU objdump disagree on VFP mnemonics.  These words
are real instructions from the Premium engine and from GCC 4.4.3 output; the
expected classes were read off the ARM ARM and confirmed with a disassembler
that prints the same word.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location(
        "gcc443_explain", ROOT / "tools" / "compare" / "gcc443_explain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpcodeClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opcode_class = staticmethod(load().opcode_class)

    def test_vfp_memory(self):
        self.assertEqual(self.opcode_class(0xED944A00), "vldr")     # vldr s8, [r4, #0x10]
        self.assertEqual(self.opcode_class(0xEDC47A0F), "vstr")     # vstr s15, [r4, #0x3c]
        self.assertEqual(self.opcode_class(0xED9F2A73), "vldr")     # vldr s4, [pc, #...]

    def test_vfp_immediate_is_one_encoding(self):
        # 0xeef77a00: Capstone says "vmov.f32 s15,#1.0", objdump says
        # "fconsts s15,#112".  It must classify identically to the constant
        # form GCC 4.4.3 emits for `1.0f`.
        self.assertEqual(self.opcode_class(0xEEF77A00), "vmov_imm")
        self.assertEqual(self.opcode_class(0xEEB70A00), "vmov_imm")

    def test_vfp_arithmetic(self):
        self.assertEqual(self.opcode_class(0xEE676A26), "vmul")     # vmul s13, s14, s6
        self.assertEqual(self.opcode_class(0xEE202A82), "vmla")     # vmla s4, s2, s1
        self.assertEqual(self.opcode_class(0xEE311A80), "vadd")     # vadd s2, s2, s1

    def test_branches_and_arm(self):
        self.assertEqual(self.opcode_class(0xEBFFFE67), "bl")
        self.assertEqual(self.opcode_class(0xEA000007), "b")
        self.assertEqual(self.opcode_class(0xE92D4070), "arm32")    # push {r4-r6, lr}
        self.assertEqual(self.opcode_class(0xE3510001), "arm32")    # cmp r1, #1


if __name__ == "__main__":
    unittest.main()
