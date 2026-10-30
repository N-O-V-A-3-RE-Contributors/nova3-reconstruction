# Legacy source triage — task #120

The offline audit preserves the recovered C++ as candidate source. Every block
remains **NOT MATCHED**. A current source hash, consistent verification-gate
metadata, six passing host tests, or a claimed equal instruction window cannot
establish an independently reproduced original-machine match.

## Owner-local invocation

From the updated repository root, with Python 3.10+ on a system supporting POSIX
directory descriptors and `O_NOFOLLOW`:

```sh
python -B tools/triage_legacy_blocks.py --repo . \
  --out .cache/legacy-triage-task120-owner.json
```

Choose an unused output filename on subsequent runs. The tool refuses to
overwrite files. The full worksheet and its `future_task_inputs` stay in the
checkout's ignored `.cache/`, with file permissions `0600`. Only aggregate counts
are printed. Do not publish the worksheet or local comparison reports.

Without `--out`, the command reads metadata and source without writing a report.
`python -B -m nova3_team.legacy_triage` provides the same interface. No APK, ELF,
compiler, disassembler, log, assembly snapshot, network access or account is
required or invoked. Recovered source and historical evidence are never written.

Optional repository-relative `--gate-manifest` uses the existing
`verification_gate.check` consistency rules; it does not change gate policy.
`--donor-plan` accepts bounded `nova3.donor-plan/1` metadata pinned to the selected
retail reference. Neither option reopens original binaries or validates their
claims independently. Worksheet provenance includes the input report hashes.

Exit `0` means a nonempty audit completed, including any quarantined conflicts;
it is not acceptance of the source or of historical evidence. Invalid metadata,
unsafe paths, symlinks, resource-limit violations, existing output files,
unsupported secure file operations and empty inventories exit `2`. Error output
does not echo input values or filesystem paths. `--require-verified` always exits
`2`: this metadata-only tool cannot establish independent machine verification.

## Observed checkout audit, 2026-10-29

Run without optional gate or donor inputs; audit exit `0`:

| Observation | Count |
| --- | ---: |
| Source blocks inventoried | 25 |
| Explicit source-path associations | 8 |
| Inferred entry-address associations | 17 |
| Blocks without associated evidence | 0 |
| Current recorded source SHA-256 | 8 |
| Stale recorded source SHA-256 | 0 |
| Unrecorded source SHA-256 | 17 |
| Orphan explicit source mappings | 0 |
| Independently verified original-machine matches | **0** |

Zero stale hashes does not establish hash validity for the 17 unrecorded blocks.
The mapping counts agree with the existing `legacy_validation.inventory`.

| Conservative category | Count | Next proof step |
| --- | ---: | --- |
| `COMPILER_ABI_GATE_NEEDED` | 6 | Establish compiler/flags/ABI and source-bound build/test metadata. |
| `RESEARCH_ONLY` | 9 | Establish explicit provenance, current source hash and edition/ELF identity. |
| `CONFLICTING_EVIDENCE` | 10 | Review historical positive match claims before compiling or reusing them as proof. |
| `PRIVATE_ORIGINAL_COMPARISON_NEEDED` | 0 | Independently recheck original function windows and comparison evidence locally. |

All ten conflict classifications here are `UNRECHECKED_POSITIVE_MATCH_CLAIM`.
This quarantines assertions for review; it does not demonstrate that the source
is incorrect. No source is deleted or downgraded in the historical ledger.

Reported metadata is present for compile/build in 17 blocks, tests in 19,
comparisons in 13, and provenance in 15. These are field-presence counts, not
executed builds, rerun tests, independent oracle comparisons or accepted proof.
No optional gate manifest was supplied, so zero gate-checked blocks are reported.

The default edition/reference pin is the documented Premium retail identity.
It is an expected identity, not an ELF opened or hashed by this tool. Original
function bounds, ARM/Thumb state, compiler identity, flags, relocations and donor
verification remain unknown unless independently established elsewhere. No
original binary was required for this audit; original-local comparisons remain
pending even when references are available on the owner's machine.

## Future bounded task inputs

The worksheet contains one proposed input per source block, never automatically
created issues or compilation jobs. Each keeps a deterministic path-derived ID,
current source SHA, historical evidence hashes, explicit/inferred association,
expected edition/reference pin, reported reference identities and function
metadata, conflicts, local include checks, effort cues and next proof step.
IDs survive source edits; hashes identify the particular audited contents.
No missing sizes, modes, original toolchain versions or boundaries are invented.

Ordering prefers non-conflicting blocks, explicit provenance, fewer unresolved
literal local includes, fewer quoted includes, smaller source size, then stable
ID. Include checks hash only safe source/header paths; parent-directory includes,
system/macro includes and the broader dependency graph remain unknown. This is
an effort estimate, not proof that a block builds independently. Reported donor
byte equality is recorded as `REPORTED_EXACT_NOT_RECHECKED` and cannot improve
priority: verified donor cues are unavailable to this read-only metadata audit.

Review a single source-bound candidate from the six compiler/ABI cases first.
Bind its actual dependencies and select a bounded proof task. Repair inferred
associations before treating them as source ownership. Resolve quarantined
claims separately. A comparison manifest with a conflicting known address,
size or mode is also quarantined; absent function metadata remains unknown.

Future owner-local proof requires the exact original reference SHA, independently
validated function range/mode, current source SHA, recorded compiler version and
flags, actual candidate object hash, comparison/relocation policy and independent
test evidence. Keep separate claims for source present, host tests passed,
original behaviour compared, ARM object built and original instruction-window
match. This audit accepts none of those as a new verified grade. It is independent
of task #117, compiler pilots and synthetic translation-unit work.

## Validation and resume state

```sh
python -B -m unittest \
  nova3_team.tests.test_legacy_triage \
  nova3_team.tests.test_legacy_validation \
  nova3_team.tests.test_verification_gate \
  nova3_team.tests.test_donor_mapping \
  nova3_team.tests.test_preflight
```

Result: **76 tests passed**, exit `0`. Fixtures are synthetic source/metadata;
no original binaries, external tools or network services are used. Coverage
includes stale hashes, missing/invalid evidence, traversal and symlinks,
duplicate identities, false match claims, equal-window metadata without proof,
gate consistency and drift, empty inventory, stable IDs/order, bounded reads,
private exclusive output, sanitized errors and input immutability.

The real-checkout `--require-verified` invocation exited `2` as intended. The
session's private worksheet is `.cache/legacy-triage-task120-round1.json`;
`git check-ignore` confirmed it is ignored. Its 25 entries and future task inputs
are the local resume point. Only the four task-allowed source/test/CLI/document
paths changed; recovered C++, historical evidence and gate policy are untouched.
