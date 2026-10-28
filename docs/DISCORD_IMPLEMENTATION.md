# Discord decompilation workflow — executable contract and honest completion gates

This is the authoritative checklist for the object-reconstruction method requested
by the project owner on 2026-10-28. It complements
[OBJECT_MATCHING.md](OBJECT_MATCHING.md) and
[LEGACY_REVALIDATION.md](LEGACY_REVALIDATION.md). **A checked-in tool is not proof
of byte-matched game source.** The shipped product remains independently built
source, never a wrapper around the original Android library.

## Which binary is the target?

The reference for byte comparisons is the **Premium retail**
`lib/armeabi-v7a/libNOVA3_neon.so` (SHA-256
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`).
The `NOVA3_Cyrus_Apel_IGP_Cyrus_ML_MP_GLLive_003.apk` pre-release is a
**metadata donor**, not an interchangeable replacement. Its locally recorded
APK SHA-256 is
`d79feb99d6942c28927b135d4c105e36aef74e8992176f6a96cbbfc3dbe89fb5`.
The existing `nova3_team.version_compare` compares overlapping named
functions and bounded DWARF; changes between builds must remain explicitly
classified. The approximately 41,664 defined debug function-symbol entries
are not a claim of 41,664 unique verified source functions.

The binary's repeated `GCC: (GNU) 4.4.3` `.comment` records are meaningful
historical evidence. They **do not identify the full executable compiler
toolchain**, all per-TU flags, original build scripts, NDK revision, object
link order or a compatible modern replacement. Use explicit candidate
profiles and measured results. Android NDK Clang is permitted for exploratory
compile and functionality tests but is not the proven 2013 compiler.

## Required sequence and measurable gates

| Discord requirement | Reproducible implementation and acceptance |
| --- | --- |
| 1. Categorize functions/classes into translation units | Import ELF names and verified Ghidra/IDA mappings, compare Cyrus DWARF compile-unit paths and call/data dependencies. Record each original-TU grouping as **INFERRED** unless independently proven. One C++ class need not equal one TU. |
| 2–3. Separate a linked `.so` into reference `.o` units | Stage bounded per-function assembly and *synthetic* ET_REL references in ignored cache with source ELF SHA and mappings. A linked ELF does not retain the original `.o` split or all pre-link relocations; never relabel these synthetic references as original. |
| 4. Restore relocations and a linker layout | Inventory `.rel.*`, GOT/PLT, literal pools, TLS, ARM/Thumb transitions, `.ARM.exidx`, symbols and ELF segments. Maintain an explicit relocation confidence worksheet. Generate a linker script **only** for an evidenced link experiment. An identical full `.so` SHA remains an independent, currently unmet condition; missing original pre-link data cannot be invented. |
| 5. Extract per-function `.s` | Put each bounded snapshot under ignored `.cache/objmatch/asm/`. Existing `.byte` snapshots are byte preservation, not readable decompiler output. Disassembled mnemonics, Thumb mode, literal data and local branches must be verified before assembler reconstitution. |
| 6–7. Create C++ units with global assembly | Keep temporary `asm(... .include ...)` wrappers in private research source. Replace each wrapper with *new source-owned C++* after understanding its behaviour. Never ship copied original instruction bytes in production builds. |
| 8–9. Compile and link; obtain build `.o` | Record compiler executable content/version, flags, architecture, binary and candidate SHA, tool stdout/stderr and explicit return codes. Build actual ARM relocatable candidate objects; linker success alone does not establish original equivalence. |
| 10. Compare with objdiff, function by function | Compare original linked instruction windows against candidate ARM code and reference object hypotheses; label data/relocation discrepancies separately. A synthetic `.byte` reference matching itself is a tautology. Promote `MATCHED` only with independent instruction evidence and reproducible toolchain details. |
| 11. Integrate Irrlicht and JNI source | First fingerprint surviving engine RTTI, C++ symbols, vtables and JNI registrations to identify compatible **version/fork**. Pin upstream source checksums/commit/licenses; rebuild, adapt and test game APIs. Do not blindly vendor latest Irrlicht or copy the original JNI library. |

## Local-first AI execution

1. Verify all reference hashes and the *exact* matched ARM/Thumb mode before
   analysing an address. The user's binaries, IDA databases and proprietary
   assembly are private local inputs and **never GitHub uploads**.
2. Use Cyrus `version_compare` results only to shortlist donor functions:
   require aligned identity and separately verified byte mapping to Premium.
   Transfer a name/type/function grouping as a **hypothesis** and record which
   original and donor evidence supported it.
3. Run `python -m nova3_team.object_matching fingerprint ...` on Premium;
   stage a very small representative function batch with
   `python -m nova3_team.object_matching stage ... --limit 1`. Maintain
   reproducible compiler profiles using `profile`, and build synthetic
   reference plus candidate objects with `build` only as a harness check.
4. Replace one staged wrapper with independently written C++ and compile it
   under selected candidate toolchains. Compare both disassembly and
   behavioural evidence; update an explicit per-function match matrix.
5. Only then generate the next bounded GitHub issue with exact
   `Allowed paths:`, target function hashes, expected source/test artifacts,
   separate byte/behavioural acceptance criteria and a short budget.
   Workers may open PRs; a human/independent reviewer decides promotions and
   merges.
6. Run retrospective validation over **all** pre-standard reconstructed
   source. Preserve useful code and tests; missing independent ARM equivalence
   becomes `NOT MATCHED` or `UNKNOWN`, never deletion or fabricated proof.
7. Independently build and test the new source-only runtime; progressively
   integrate the Android JNI shell and selected licensed engine source. Global
   ASM and original ELF bytes are research tools, never runtime requirements.

Use Ghidra for the community's freely reproducible path and optional IDA
9.4/idapro for the maintainer. The two analyzer outputs do not automatically
share an image base; validate ELF virtual-address and byte mappings. On the
four-core N5100 run at most one heavyweight analyzer, with cached results
and resume checkpoints. Store only sanitized summaries, signatures, test
logs and newly written source under GitHub.

## Implemented CLI path (run locally; no game files uploaded)

From the user's actual `~/Projects/nova3_re` checkout after this PR is
merged, with the existing Premium and Cyrus ELF copies:

```sh
bash tools/revalidate_local.sh
```

This single local-only command checks both known reference ELF SHA-256
values, generates the full historical 25-block source inventory/worklist,
compares Cyrus and Premium, creates a linker relocation worksheet with
experimental `.ld`, fingerprints compiler metadata and stages a small
donor-guided research unit. It runs the analysis stages **sequentially**,
keeps all reports under ignored `.cache/revalidation/`, and never submits
the original binaries to GitHub. If local paths differ, set the documented
`NOVA3_RETAIL_ELF` and `NOVA3_CYRUS_ELF` variables.

**Donor-ranking correction:** the first real report exposed a selection bias:
all 64 choices were four-byte functions. The donor planner now seeds bounded
24–96, 97–384 and 385–1024-byte categories before filling the rest, and
prefers exact-size addresses already in the legacy C++ worklist. Supply
`--legacy-worklist .cache/revalidation/legacy-worklist.json`; these are
**source-present hypotheses**, never automatic original object or machine
matching. Existing source and byte-identical Cyrus pairs remain distinct
from an independently reproduced ARM source function.

### Required ARM C++ compiler pilot before a large AI decompilation wave

After a successful six-stage audit, on the maintainer's machine run:

```sh
bash tools/run_compiler_pilot.sh
```

The script independently confirms the original Premium SHA-256, fingerprints
one locally installed Android NDK Clang candidate and its flags, compiles
**existing C++** `block_000d2f10.cpp` for ARM32, and compares its actual
relocatable `.o` function against the original Premium A32 code window.
Results are in a fresh, ignored
`.cache/revalidation/compiler-pilot-*/matrix/matrix.json` directory. Set
`NOVA3_PILOT_CXX=/absolute/path/to/clang++` if the Android NDK is not at
`/opt/android-ndk` or `ANDROID_NDK_HOME`. A compilation or comparison failure
exits nonzero; no original code is published.

The pilot is deliberately a **negative control**: its source-owned
`CpuQueryContext*` calling convention differs from the original no-argument
global-state function. Successful C++ compilation and a genuine mismatch
confirm the comparison machinery, **not** that the historical compiler or
ARM source matching is solved. The next AI issue must use the evidence to
produce a correctly scoped ABI candidate, compare at least two nontrivial
functions under explicit compiler profiles, and report differences. Never
turn an exploratory Clang mismatch into a `MATCHED_ARM` claim.

The shared Observatory's worker-health page uses the repository's dedicated
`nova:presence` issue (#92), with an optional rate-limited, editable comment
per worker. Its absence never blocks source tasks; a stale report does not
prove a worker is offline. The worker must be updated and restarted after
new reporting infrastructure is deployed.

Equivalent individual commands, useful when one phase needs debugging:

```sh
RETAIL=analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
CYRUS=.cache/cyrus-debug/libNOVA3_neon.so

python -m nova3_team.legacy_validation --repo . \
  --out .cache/legacy-source-revalidation.json \
  --plan-out .cache/legacy-revalidation-worklist.json
python -m nova3_team.linker_worksheet --reference "$RETAIL" \
  --out .cache/premium-linker-worksheet.json --experimental-ld
python -m nova3_team.version_compare --retail "$RETAIL" \
  --debug "$CYRUS" --out .cache/cyrus-vs-retail.json
python -m nova3_team.donor_mapping \
  --report .cache/cyrus-vs-retail.json \
  --legacy-worklist .cache/revalidation/legacy-worklist.json \
  --out .cache/donor-plan.json --limit 64
python -m nova3_team.object_matching stage "$RETAIL" \
  --out .cache/objmatch-donor --donor-plan .cache/donor-plan.json \
  --limit 16
```

The donor plan is **metadata-only**. It ranks independently bounded matching
names and uses DWARF compilation-unit ownership as an **inferred hypothesis**.
An optional `--ida .cache/ida-premium-functions.json` requires a byte-identical
Premium ELF input fingerprint and does not certify image-base mappings.
The legacy worklist contains all existing source blocks in deterministic
review order, with exact source hashes, known function ranges and required
compiler/ABI/relocation/oracle checks. It is **not** a queue of approved AI
issues until each candidate is scoped and given real private reference access.
The linker worksheet reads only observable linked ELF sections, PT_LOAD
segments and REL/RELA relocation metadata. Its optional
`.experimental.ld` is an explicitly non-production *layout hypothesis*;
relinking with it is not expected to reproduce the original SHA.

For a single source-owned C++ candidate, record an *exploratory* compiler
profile (the example modern Clang is **not** the historically verified GCC):

```sh
python -m nova3_team.object_matching profile \
  /opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++ \
  --flag=--target=armv7a-linux-androideabi21 --flag=-marm \
  --flag=-O2 --flag=-fPIC --flag=-std=c++17 \
  --out .cache/armv7-clang-candidate.json
python -m nova3_team.compiler_matrix \
  --reference "$RETAIL" \
  --source reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp \
  --symbol nova3_recovered_cpu_count --address 0xd2f10 \
  --profile .cache/armv7-clang-candidate.json \
  --out .cache/matrix-cpu-count
```

The candidate currently has a source-owned context-pointer ABI instead of the
game's no-argument global-state ABI. Expect a size or byte mismatch. This run
**validates the comparison machinery**, not equivalence. Existing
`reconstruction/recovered/` code uses modern C++17 features and will not
necessarily compile with the original GCC 4.4.3; construct explicitly scoped
historical-ABI candidates where byte matching is the goal, while preserving
portable source and its behavioural tests separately.

The `compiler_matrix` output records up to eight sequential compiler
experiments with explicit flags and time budgets. The lower-level
`python -m nova3_team.object_compare` accepts one ARM32 ET_REL candidate
object and reports `LINKED_FUNCTION_WINDOW_EQUAL`,
`BYTE_EQUALITY_WITH_UNRESOLVED_RELOCATIONS`, or an explicit mismatch.
Those are code-window observations, **not proof of original TU, compiler,
relocations, complete source equivalence or whole-library SHA**. Automatic
grading stays conservative until an independent reviewer promotes evidence.

## Independent grades (never collapse these)

* `INVENTORIED`: original address/hash/symbol metadata recorded.
* `DISASSEMBLED`: instruction boundaries/control flow verified.
* `DECOMPILED`: an analyzer produced a hypothesis, not an implementation.
* `UNDERSTOOD`: behaviour, dependencies and edge cases evidenced.
* `IMPLEMENTED`: independently compiled source exists.
* `TESTED`: relevant source tests actually pass, with counts and scope.
* `VERIFIED_BEHAVIOURAL`: independently compared against observed reference
  behaviour, with modelled dependencies and exclusions documented.
* `MATCHED_ARM`: candidate ARM instructions match the original *function
  window* under a recorded compiler/profile and independently validated
  mapping/relocation policy.
* `MATCHED_LIBRARY_SHA256`: a full rebuilt library compares byte-for-byte.
  This is not established and is **not** equivalent to playability.

No tool may infer one of the latter three grades from an earlier grade. In
particular, a host CTest pass, decompiled pseudocode, synthetic object pair,
or source-file count says nothing about machine-code equality.

## Explicit unmet prerequisites

* Reproducible original GCC/assembler/linker identity, flags and link order.
* Original TU boundaries and discarded pre-link relocations that may be
  unobservable in the linked ELF. Record uncertainty rather than fabricate.
* Full relocation inventory/link-script experiments, full original-library
  hash comparison and original Android ABI equivalence.
* Version-confirmed Irrlicht source and game-specific native JNI integration.
* A demonstrated reproducible *real C++* ARM code-window match.
* A running, source-only Android game with measurable campaign functionality.

The research track must remain useful even when those proofs are unavailable:
function-level behaviour and a growing independently compilable engine are
legitimate progress, provided the narrower claim is recorded.
