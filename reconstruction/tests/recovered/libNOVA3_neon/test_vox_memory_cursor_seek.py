"""Source-only behavioral tests; no reference ELF, assets, or analysis tools needed.

Oracle: choose an anchor and direction in mathematical integers, project onto
signed 32-bit register values, then validate against a live signed size query.
Premium window provenance is recorded in the implementation. These tests are
not an instruction-window match or a full original-class ABI test.
"""
import os
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import tempfile
import unittest


MIN = -(1 << 31)
MAX = (1 << 31) - 1
ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "reconstruction/recovered/libNOVA3_neon"


def transition(position, sizes, offset, origin):
    """Return (status, next position, query count), without C++ arithmetic."""
    queries = 0

    def query():
        nonlocal queries
        value = sizes[min(queries, len(sizes) - 1)]
        queries += 1
        return value

    anchors = {0: 0, 1: position}
    if origin == 2:
        anchor, direction = query() - 1, -1
    elif origin in anchors:
        anchor, direction = anchors[origin], 1
    else:
        anchor, direction = position, 0
    target = (anchor + direction * offset - MIN) % (1 << 32) + MIN
    accepted = target >= 0 and target <= query()
    return (0, target, queries) if accepted else (-1, position, queries)


class MemoryCursorSeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        if not compiler or not shutil.which(compiler[0]):
            raise RuntimeError("A host C++ compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="nova-seek-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        harness = work / "main.cpp"
        harness.write_text(r'''
#include "vox_memory_cursor_seek.h"
#include <iostream>
struct Sizes {
    int first, later, calls;
    static int query(void* p) {
        auto& s = *static_cast<Sizes*>(p);
        return s.calls++ == 0 ? s.first : s.later;
    }
};
int main() {
    int position, first, later, offset, origin;
    while (std::cin >> position >> first >> later >> offset >> origin) {
        Sizes sizes{first, later, 0};
        vox::StreamMemoryBufferCursor cursor{{&sizes, &Sizes::query}, position};
        const int result = cursor.Seek(offset, origin);
        if (cursor.size_access.context != &sizes ||
            cursor.size_access.query != &Sizes::query ||
            sizes.first != first || sizes.later != later) return 2;
        std::cout << result << ' ' << cursor.position << ' ' << sizes.calls << '\n';
    }
}
''', encoding="utf-8")
        cls.executable = work / "seek-test"
        subprocess.run(compiler + ["-std=c++17", "-O2", "-Wall", "-Wextra",
                                  "-Werror", "-pedantic", "-fsanitize=undefined",
                                  "-fno-sanitize-recover=undefined", "-I", str(SOURCE),
                                  str(SOURCE / "vox_memory_cursor_seek.cpp"),
                                  str(harness), "-o", str(cls.executable)], check=True,
                       capture_output=True, text=True)

    def compare(self, cases):
        cases = list(cases)
        data = "".join(f"{p} {sizes[0]} {sizes[-1]} {off} {origin}\n"
                       for p, sizes, off, origin in cases)
        run = subprocess.run([str(self.executable)], input=data, text=True,
                             capture_output=True, check=True)
        actual = [tuple(map(int, line.split())) for line in run.stdout.splitlines()]
        self.assertEqual(len(actual), len(cases))
        for case, observed in zip(cases, actual):
            self.assertEqual(observed, transition(*case), case)
        return actual

    def test_reviewed_boundary_table(self):
        # Literal expectations distinguish EOF-1-offset from ordinary SEEK_END,
        # inclusive endpoint from strict endpoint, and unknown-origin no-op.
        table = [
            ((4, (10,), 0, 0), (0, 0, 1)),
            ((4, (10,), 10, 0), (0, 10, 1)),
            ((4, (10,), -1, 0), (-1, 4, 0)),
            ((4, (10,), 11, 0), (-1, 4, 1)),
            ((4, (10,), -4, 1), (0, 0, 1)),
            ((4, (10,), 6, 1), (0, 10, 1)),
            ((4, (10,), -5, 1), (-1, 4, 0)),
            ((4, (10,), 7, 1), (-1, 4, 1)),
            ((4, (10,), 0, 2), (0, 9, 2)),
            ((4, (10,), -1, 2), (0, 10, 2)),
            ((4, (10,), 9, 2), (0, 0, 2)),
            ((4, (10,), 10, 2), (-1, 4, 1)),
            ((4, (10,), -2, 2), (-1, 4, 2)),
            ((0, (0,), 0, 2), (-1, 0, 1)),
            ((0, (0,), -1, 2), (0, 0, 2)),
            ((4, (10,), MAX, 99), (0, 4, 1)),
            ((-1, (10,), 0, -1), (-1, -1, 0)),
            ((11, (10,), 0, 3), (-1, 11, 1)),
            ((MAX, (MAX,), 1, 1), (-1, MAX, 0)),
            ((MIN, (MAX,), MIN, 1), (0, 0, 1)),
            ((4, (0,), MAX, 2), (-1, 4, 1)),
            ((4, (MIN,), MAX, 2), (-1, 4, 2)),
        ]
        self.assertEqual(self.compare(case for case, _ in table),
                         [expected for _, expected in table])

    def test_all_origins_and_endpoint_neighborhoods(self):
        cases = []
        for size in (0, 1, 2, 10, MAX):
            for pos in sorted({0, size // 2, size}):
                for origin in (0, 1, 2, -1, 3, MIN, MAX):
                    offsets = {MIN, MAX, -1, 0, 1}
                    for target in (-1, 0, 1, size - 1, size, size + 1):
                        off = {0: target, 1: target - pos,
                               2: size - 1 - target}.get(origin, target)
                        if MIN <= off <= MAX:
                            offsets.add(off)
                    cases.extend((pos, (size,), off, origin) for off in offsets)
        self.compare(cases)

    def test_live_size_queries(self):
        self.compare((p, sizes, off, origin)
                     for p in (0, 4, 10, -1)
                     for sizes in ((10, 0), (0, 10), (10, 5), (10, -1))
                     for off in (-1, 0, 4, 9, 10)
                     for origin in (0, 1, 2, 3))

    def test_success_failure_success_transitions(self):
        # Feed each actual next position into the next invocation.
        pos = 0
        for off, origin, status in ((4, 0, 0), (20, 1, -1),
                                    (-1, 2, 0), (1, 1, -1),
                                    (-10, 1, 0), (0, 3, 0)):
            result, pos, _ = self.compare([(pos, (10,), off, origin)])[0]
            self.assertEqual(result, status)
        self.assertEqual(pos, 0)

    def test_register_overflow_and_signed_sizes(self):
        values = (MIN, MIN + 1, -11, -1, 0, 1, 10, MAX - 1, MAX)
        self.compare((p, (size,), off, origin)
                     for p in values for size in values for off in values
                     for origin in (0, 1, 2, -1, 3))
        rng = random.Random(361)
        self.compare((rng.randint(MIN, MAX),
                      (rng.randint(MIN, MAX), rng.randint(MIN, MAX)),
                      rng.randint(MIN, MAX), rng.choice((0, 1, 2, -1, 3)))
                     for _ in range(2000))


if __name__ == "__main__":
    unittest.main()
