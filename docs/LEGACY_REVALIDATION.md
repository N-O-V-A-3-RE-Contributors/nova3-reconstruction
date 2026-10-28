# Revalidate historical reconstructed C++ against the new ARM compiler standard

This is a **source-owned metadata audit**, not a claim that existing reconstructed
C++ is bad. Before the Cyrus debugging comparison and historical compiler
investigation, many blocks were reconstructed and tested using modern host
compilers. Those tests remain useful for behavioural equivalence, but they do
**not** establish the original Android ABI, relocation, link layout, or machine
code. Preserve the old work. Do not replace it with assembly snapshots.

## Run a complete source inventory

From the repository root, with no APKs or ELF files required:

```sh
python -m nova3_team.legacy_validation --repo . \
  --out .cache/legacy-source-revalidation.json
```

The command reads every `reconstruction/recovered/**/block_*.cpp` and the
corresponding existing `coordination/evidence/*.json` files. It cross-checks
declared source SHA-256 values against current C++ bytes and records the grades
**reported** by that evidence. It never runs an ELF, decompiler or compiler;
the generated report contains only paths, hashes, addresses and grades, not
proprietary executable contents or pseudocode. Output is required to stay under
the checkout's ignored `.cache/` directory, and the source is never modified.

An exact source-path reference in evidence is preferred. If older evidence
omits one, correlating its entry address with a `block_*.cpp` filename remains
an **inferred** association, not proof of translation-unit membership.

Interpretation:

| Field | Meaning |
| --- | --- |
| `current_target_machine_match: NOT_VERIFIED` | No current independently reproduced GCC/objdiff result has established a byte match. |
| `revalidation_status: REVALIDATION_REQUIRED` | The source needs compiler, ABI and machine-code comparison. Existing behavioural tests are not invalidated. |
| `SOURCE_CHANGED_SINCE_EVIDENCE` | The evidence's recorded source SHA no longer matches; rerun its own tests before further claims. |
| `REVIEW_MATCH_CLAIM` | Historical evidence asserts a match, but this audit deliberately cannot accept it without independently validating compiler, flags and reference bytes. |
| `reported_grade: behavioural` | Historical oracle/regression scope, never automatically promoted to machine matching. |

The CLI exits successfully if it could inventory the tree. This means only
that the audit ran successfully, **not** that the game is matched or playable.
For a strict gate, use `--require-verified`; it intentionally fails until an
independent matching validator is integrated.

## Required next validation (private local machine)

1. Record the SHA-256 of each legally held ELF. Premium retail is the primary
   target; Cyrus debug provides named-symbol and DWARF hints, not a substitute
   target. No APK, assembly, decompiler dumps or private path enters GitHub.
2. Extract verified original function ranges with ARM/Thumb state, vaddr-file
   mapping, data islands and literal pools. Confirm names and boundaries with
   IDA and Ghidra, retaining Ghidra as the freely available option.
3. Stage research-only translation-unit hypotheses and synthetic objects with
   `python -m nova3_team.object_matching`. A synthetic `.o` does not recreate
   the original `.o` or its stripped relocations; a synthetic match is not
   recompilation proof. Do not link those objects into runtime targets.
4. Investigate the repeated `.comment` evidence of GCC 4.4.3. Establish an
   exact *candidate* historical toolchain, ARM ABI, target, C++ runtime,
   optimisation/codegen flags, linker and dependencies using multiple test
   functions. The NDK's modern Clang is not evidence of the original compiler.
5. Compile source-owned candidates, compare disassembly/sections/relocations,
   measure byte windows/objdiff and record original and candidate hashes and
   mismatches. Check independent behaviour before upgrading a source grade.
6. Revalidate recovered blocks in dependency order. For portability and
   maintenance, keep original exact-match evidence separate from equivalent
   source-owned host/native implementations. Do not require whole-`.so` SHA
   equality as a prerequisite for every behavioural milestone.

**Acceptance:** A matching grade requires a separate, reproducible report
with the original reference SHA, original function address/range/mode,
historical compiler identity and flags, candidate source SHA, candidate
relocatable object SHA, a byte comparison with explicit excluded relocation
effects, and independent tests. Until then, mark machine-code equivalence
`NOT_VERIFIED` even if the C++ compiles, passes CTest, or was merged in a PR.

The broader Discord-to-implementation gaps and limitations remain in
[`docs/OBJECT_MATCHING.md`](OBJECT_MATCHING.md). Irrlicht version and original
JNI interfaces must be validated separately before source integration.
