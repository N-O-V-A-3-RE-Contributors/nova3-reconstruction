# Cyrus debug versus Premium retail — task #88

Offline measurement, 2026-10-28. **Recommendation: retail-first with debug
annotation donor.** Cyrus does not demonstrate a broader DWARF/type base in
this measurement. Keep Premium authoritative for behavior and sequence AI
reconstruction by its dependencies. Use Cyrus selectively to investigate
individual functions, after checking their mapping and ABI. Do not bulk-import
types, signatures or addresses between these images.

This is an ELF-to-ELF inventory and exact linked byte-window comparison.
Source reconstruction grade: **NOT MATCHED**. No source-implemented game
functions, decompilations, behavioral equivalence or gameplay verification are
established by this task.

## Inputs and compiler evidence

The CLI successfully checked both complete input SHA-256 values:

| Input | SHA-256 | File bytes |
| --- | --- | ---: |
| Premium retail | `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf` | 18,696,442 |
| Cyrus debug | `d6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167` | 18,659,290 |

Both are ELF32 ARM little-endian EABI5 shared objects. Both `.comment`
sections contain GCC **4.4.3** version evidence; this does not establish the
complete compiler/NDK/linker identity. Read-only `readelf -A` additionally
confirmed CPU-name attributes `7-A` for retail and `CORTEX-A8` for Cyrus.
Original source hashes, optimization flags, complete build flags, chronology
and debug/release lineage remain **UNKNOWN**. No parent APK was opened or
executed. APK timestamps were not used as release evidence.

## Symbol and byte-window results

Only defined `STT_FUNC` entries count in the following inventory. Static and
dynamic tables are separate; their sum double-counts many functions.

| Inventory | Premium retail | Cyrus debug |
| --- | ---: | ---: |
| `.symtab` defined FUNC entries | 39,123 | 38,417 |
| `.symtab` distinct normalized entry addresses | 39,092 | 38,386 |
| `.symtab` distinct nonempty names | 39,081 | 38,377 |
| `.symtab` distinct `_Z` mangled names | 32,671 | 31,941 |
| `.dynsym` defined FUNC entries | 3,669 | 3,247 |
| `.dynsym` distinct normalized entry addresses | 3,638 | 3,216 |
| Combined defined FUNC entries (not unique functions) | 42,792 | 41,664 |
| Discovered executable entry addresses | 39,092 | 38,386 |
| Ranges eligible for exact byte hashing | 39,061 | 38,355 |
| Relocation entries inventoried | 58,473 | 58,037 |

The comparison joins **exact `.symtab` names**, without demangling or dropping
overload signatures. `.dynsym` evidence can disqualify conflicting ranges but
does not add names to that join.

| Name comparison | All names | `_Z` mangled names |
| --- | ---: | ---: |
| Shared exact names | 37,806 | 31,645 |
| Retail-only names | 1,275 | 1,026 |
| Cyrus-only names | 571 | 296 |
| Shared names with multiple distinct definitions (rejected) | 32 | 17 |
| Shared names with differing reported size, among unique definitions | 3,515 | 2,705 |
| Exact corresponding byte SHA-256s | 11,050 | 9,753 |
| Different corresponding byte SHA-256s | 26,676 | 21,875 |
| Excluded ranges | 48 | 0 |

After deduplicating aliases, **11,036 distinct address pairs** have equal
code-window SHA-256s and **26,676** differ: 37,712 eligible pairs total. No
cross-build mode mismatches or alias splits/merges were found among otherwise
eligible pairs. Ambiguous same-name definitions are rejected before comparison.

Each candidate uses its own symbol VA, section-relative file offset, explicit
size, ARM EABI entry-mode bit, mapping symbols when present, and a unique
consistent executable file-backed `PT_LOAD`. File offsets need not agree
between images. Partial function overlaps, conflicting same-entry evidence,
unknown/oversize sizes, alignment errors, and ambiguous mappings are excluded.
Symbol discovery is not exhaustive instruction discovery.

Equality concerns whole linked symbol windows, which may include literal pools.
It is only a lower bound on unchanged implementation. Addresses, relocations,
layout, compiler options and link transformations can make equivalent functions
differ. Conversely, equal windows may reference different external data or
callees, so equality does not prove equivalent semantics. Relocation sites
starting inside each range are counted locally; bytes are not normalized or
relocated. This is not a source-to-reference compiler match.

## Actual DWARF coverage

Locally installed pyelftools parsed both inputs. Independent `readelf
--debug-dump=info` counts confirmed 20 CUs and 91 subprogram DIEs per input;
debug strings and paths were not printed or retained in this document.

| Coverage | Premium retail | Cyrus debug |
| --- | ---: | ---: |
| `.debug_info` bytes | 19,269 | 19,269 |
| Compilation units | 20 | 20 |
| Total DIEs, including null entries | 1,581 | 1,581 |
| Named type DIEs | 128 | 128 |
| Named base-type / structure-type DIEs | 113 / 15 | 113 / 15 |
| Distinct named type-name hashes | 27 | 27 |
| Subprogram DIEs, including declarations | 91 | 91 |
| Concrete subprogram DIEs | 83 | 83 |
| Nondeclaration subprogram low PCs at symbol entries | 83 | 83 |
| Line rows, including sequence ends | 1,843 | 1,843 |
| Distinct non-end line addresses | 1,803 | 1,803 |
| Union of line intervals intersecting executable sections, bytes | 15,296 | 15,296 |
| Symbol entry addresses within those line intervals | 133 | 133 |

Line intervals cover approximately **0.132%** of executable section bytes in
each image. They include only about 0.34% of symbol entries; this does not mean
that entire functions or source files have been recovered.

Both images have nine `.debug*` sections. Five have identical full section
SHA-256s: `.debug_abbrev`, `.debug_loc`, `.debug_pubnames`, `.debug_ranges`,
and `.debug_str`. Four differ: `.debug_aranges`, `.debug_frame`, `.debug_info`,
and `.debug_line`. There are no edition-only DWARF sections or named
type-tag/name-hash pairs; all 27 such pairs are shared. Hash identity of type
names does not establish identical type layout, ownership or game relevance.
These small tables may include compiler/runtime/library metadata; no
comprehensive game-class coverage is established.

For AI sequencing, start with Premium functions that already have understood
callers/dependencies and independent test opportunities. Treat unambiguous
equal-byte Cyrus pairs as candidates for shared analysis, with referenced data
and callees still reviewed. Review different-byte pairs separately. Transfer
only individually justified annotations, retaining both binary hashes, VAs,
mode, evidence and uncertainty. Retail-only functions remain reconstruction
work; the smaller Cyrus inventory is not evidence that they are unnecessary.

## Reproduction and report privacy

From this source checkout, pass read-only input paths explicitly. They may
point into a separate local project; do not copy game binaries into Git.
The usual maintainer-project-relative invocation is:

```sh
python -m nova3_team.version_compare \
  --retail analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --debug .cache/cyrus-debug/libNOVA3_neon.so \
  --out .cache/cyrus-vs-retail.json
```

The full report remains under ignored repository `.cache`. It contains local
symbol names, addresses, sizes, candidate digests, exclusions, per-CU/DIE
metadata and line coverage, but no instruction bytes, disassembly, source paths
or raw DWARF strings. CU/type/function names in DWARF are hashed. Never attach
the full report to an issue/PR; publish only reviewed aggregates such as these.
The report records the comparison script's SHA-256 for provenance, separately
from the unknown original game source hashes. Stdout is aggregate-only.

The CLI uses the validated indexer's ELF parser, fixed expected input hashes,
and optional **locally installed** pyelftools. It never installs tools, accesses
the network, or starts an analyzer/game. Missing inputs, wrong hashes or
architecture, malformed tables, unsupported compressed DWARF, or present but
unreadable DWARF block the comparison with exit 2. Missing DWARF is explicitly
reported as absent. The output must resolve inside repository `.cache` with a
`.json` suffix and cannot alias an input. Successful writes use atomic
replacement; a failed run leaves an existing report untouched, so check the
exit code rather than treating an old report as a successful new comparison.

Bounds include 64 MiB per ELF, 4,096 sections/CUs, 500,000 symbol entries,
32 Mi characters of decoded symbol names, 4 MiB per function, 16 MiB per debug
section, 256 KiB for the eagerly decoded line-program section, and 500,000
DIEs/line rows. The POSIX CLI also enforces a 120-second alarm. Compressed
sections and unsupported table encodings fail closed.

## Validation and handoff

Verified locally with Python 3.14.8, pyelftools 0.33 and GNU readelf 2.47.
Synthetic ELF fixtures are generated from source; they contain no game code.
They exercise duplicate names, overload distinctions, alias splits/merges,
mode conflicts, overlapping/unaligned/unmapped ranges, divergent bytes/sizes,
relocations, actual small DWARF/line tables, absent/unreadable DWARF, corrupted
headers, resource bounds, SHA mismatch, output privacy and input preservation.
The DWARF-success fixture test skips if pyelftools is unavailable; the
missing-backend rejection test always runs.

```sh
python -m unittest nova3_team.tests.test_version_compare -v
python -m unittest discover -s nova3_team/tests -v
python -m nova3_team.guard --repo . --all
cmake -S . -B /tmp/nova88-build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/nova88-build -j 2
ctest --test-dir /tmp/nova88-build --output-on-failure
```

The task-specific suite passes 20 tests; the repository suite passes 276 tests.
The host source build succeeds and all 13 CTest checks pass. These validate
the comparison harness and existing source targets, not game equivalence.
Local build/test logs are `/tmp/nova88-{cmake,ctest,unittest}.txt`; no logs or
reference reports belong in the PR. Only the three issue-allowed source/doc
files are changed. No GitHub state was modified and no PR was opened in this
offline session; the supervisor can submit this scoped change for review.

Final local validation (all exit 0): focused synthetic suite 20 tests; full
Python suite 276 tests; clean host CMake/Ninja build and 13/13 CTest tests;
tracked and scoped source-hygiene checks. Two complete real-input CLI runs
produced identical report SHA-256s, took approximately 6.6 seconds each and
peaked at approximately 321 MiB child RSS. Exact timings, report hashes,
independent readelf counts and test/build logs remain in `.cache/task88-*`.
The optional `/usr/bin/time` utility was unavailable; measurements used Python's
standard-library monotonic clock and child resource accounting instead. An
initial slow mapping-symbol scan reached the 120-second limit; the final scan
uses bisect indexing and completed within the measured bounds.

Resume with the reproduction command above and inspect only the private report
for per-function decisions. No input-availability blocker remains. The next
bounded task is a retail use-site/ABI audit for a selected donor annotation;
this comparison does not authorize an automatic DWARF transplant or wholesale
Cyrus decompilation.
