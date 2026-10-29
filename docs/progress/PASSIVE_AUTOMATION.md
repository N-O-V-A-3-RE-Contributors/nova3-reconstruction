# Passive automation prerequisite: evidence gate (#101)

Passive issue → AI → PR → CI → evidence review remains disabled. This change
adds a read-only metadata consistency check and synthetic CI fixtures. It adds
no scheduler, feeder, merge permission, deployment or original-binary runner.
**Green CI alone is not source matching.**

## Current audit (2026-10-28)

`python -m nova3_team.legacy_validation --repo .` reports 25 recovered source
blocks, 8 explicit evidence associations, 17 inferred associations, zero stale
recorded source hashes and **zero verified target machine matches**. Historical
`matching`/`behavioural` declarations in evidence remain candidate claims; the
inventory requires retrospective review. No historical evidence or recovered
source is changed by this issue.

CI uses `pull_request`/main `push`, `contents: read`, checkout without persisted
credentials, source hygiene, Python tests, legacy inventory, Observatory checks,
and host CMake/CTest. It has no proprietary ELF or independent binary oracle.
The new named fixture check exercises valid claims, invalid claims and the CLI;
it does not scan/promote the historical evidence automatically.

The #97 result was a 44-byte scratch `RELOCATED_LINKED_WINDOW_MATCH`. It did
not recover an original object or establish historical compiler, ABI, behavior
or whole-library equivalence. #99 / PR #100 left historical ARM GCC 4.4.3 and
its sysroot unavailable. `.comment` strings alone do not establish provenance.
No historical NDK is installed or executed by this gate.

Task #103 adds [optional compiler candidate onboarding](HISTORICAL_TOOLCHAIN.md)
to first-run `nova3_easy.py` workers and the Observatory's **H / TOOLCHAIN**
page. Observers and ordinary host-only tasks need no historical compiler.
NDK r8e installation is a manual, consented no-sudo step; metadata execution
requires an explicit compiler SHA pin. `PINNED_VERSION_TARGET_VERIFIED` means
only candidate GCC 4.4.3/ARM metadata, never source acceptance or original
compiler proof. Source matching remains NOT PROVEN, independently verified
original object/source matches remain zero, and #101 remains fail closed.
No passive queue, automatic merge or evidence promotion is enabled.

## Read-only interface and trust boundary

```sh
python -m nova3_team.verification_gate --repo . \
  --manifest coordination/evidence/reviewed-candidates.json \
  --expected-reference-sha256 REVIEWED_64_HEX_ELF_SHA256
```

The manifest must already exist locally; the path above is an example, not a
new repository fixture. Supply the reference pin separately from reviewed
policy; copying the manifest's own pin is not independent identity validation.
There is no inferred Premium/default identity. Exit 0 means only valid metadata
and current source identities. Invalid/missing input exits 2. Add
`--require-verified` for a source-acceptance prerequisite: **it always exits 2**
because this checker cannot independently establish any proof grade. A metadata
pass must never be counted as a verified-source pass.

The gate only opens the explicit JSON and named source files beneath `--repo`.
It neither runs commands nor opens tools, logs, original ELF/object files,
attestation reports or network connections, and writes no reports. Inputs are
limited to 1 MiB per file, 32 nonempty claims, 16 JSON nesting levels and 10,000
JSON nodes. Files must be regular; descriptor-relative reads reject all symlink
components and noncanonical/traversing/absolute paths. Platforms lacking secure
`dir_fd`/`O_NOFOLLOW` reads fail closed. Output contains fixed grade/status names
and counts, never input paths, symbols, compiler strings, flags or diagnostics.

Schema `nova3.source-verification-gate/1` has exactly:

- `schema`, `reference_sha256`, `claims` (nonempty array).
- Each claim: `source: {path, sha256}`, `grade`; optionally `build`, `comparison`,
  `original_local_attestations`.
- Source identities reuse `legacy_validation` inventory conventions:
  `reconstruction/recovered/.../block_XXXXXXXX.cpp`, SHA-256 of current bytes.
  One claim per source block; duplicate or conflicting claims fail closed.
  This checks one source file, not its transitive includes, dependencies or
  compiler authenticity. The conservative scan rejects inline assembly,
  `.byte`/`.incbin`, assembly includes and token pasting, including line-spliced
  assembly tokens. This exclusion is not a complete C++ authenticity audit.
- Every hash is exactly 64 hexadecimal characters (case insensitive). Unknown
  fields, duplicate JSON keys, NaN/Infinity, malformed JSON, unsupported schemas,
  inconsistent stages and stale source/build/reference identities are rejected.

### Metadata grades

| `grade` | Required evidence; permitted interpretation |
| --- | --- |
| `NOT_VERIFIED` | Current source only; no verification evidence established. |
| `RESEARCH_SOURCE` | Current candidate source; optional recorded build/comparison evidence. |
| `HOST_TESTED_SOURCE` | Explicit successful compilation and nonempty passing test metadata. This tool does not rerun the tests. |
| `RAW_MISMATCH` | Successful build metadata plus `relocation_probe` raw mismatch. |
| `FUNCTION_BYTES_DIFFERENT` | Successful build metadata plus `object_compare` byte mismatch. |
| `RELOCATED_LINKED_WINDOW_MATCH` | Successful build metadata plus consistent resolved, linker and original-window stage hashes/counts. Only reported linked instruction-window equality. |

The last grade establishes no original compiler/object/ABI/behavior/full `.so`
claim. Raw mismatch and relocated-window equality can coexist because they
refer to different stages. `LINKED_FUNCTION_WINDOW_EQUAL` and unresolved
relocation equality may be recorded as comparison statuses, with no promotion
to the relocated grade. This is metadata arithmetic/consistency checking, not
independent instruction comparison.

`build` is an explicit envelope reusing `compiler_matrix` source/profile and
experiment field names. Required fields: `producer_schema` =
`nova3.compiler-matrix/1`, `source_sha256`, `reference_sha256`,
`compiler_version`, `compiler_version_sha256`, `flags` (bounded string array
using the existing `compiler_matrix` compile-flag allowlist),
`compile_exit_code`, `test_exit_code` (0–255), `tests_passed`, `log_sha256`.
`object_sha256` is optional for research/host tests and mandatory with any
comparison; it must equal `candidate_object_sha256`. Compiler version, flags,
source/reference hashes and comparison evidence stay separate. A recorded
compiler fingerprint never implies historical provenance.

`comparison` accepts strict **public-safe projections**, not wholesale private
reports. Reuse existing producer field names/statuses without private paths,
instructions, relocation worksheets, assembly, differing-offset arrays or logs:

- `nova3.object-window-comparison/1`: fields in `object_compare.compare_window`
  except `candidate_section` and `evidence_scope`. All five existing
  recovered/verified boolean fields must be explicitly false. Address, sizes,
  mode, relocation count and code-window/hash/status consistency are checked.
- `nova3.relocation-probe/1`: fields `schema`, `address`, `size`,
  `reference_sha256`, `reference_window_sha256`, `candidate_object_sha256`,
  `candidate_symbol`, `candidate_thumb`, `raw`, `resolved`,
  `original_object_match`, `historical_compiler_verified`, `whole_so_sha_match`,
  `behavior_verified`, `original_prelink_relocations_recovered`,
  `relocated_linked_window`. Include `linked_sha256`, `linker_vs_resolved`,
  `linked_vs_reference` together when a linker stage was checked. Each stage
  keeps exactly `status`, `equal`, `compared_bytes`, `differing_byte_count`,
  `expected_sha256`, `actual_sha256` from the existing comparison type. Original
  object must be `NOT_AVAILABLE`; proof booleans false. Only A32 is supported
  by that producer. Every instruction byte remains within the declared window;
  masked/truncated comparison sizes cannot count as equality.

No schema migration or historical evidence rewrite occurs. Reviewed producers
must deliberately export these bounded public-safe projections; feeding an
unredacted private report to the CLI is rejected rather than copied to output.
Synthetic fixtures and complete envelope examples live in
`nova3_team/tests/test_verification_gate.py`.

### Original-local attestations remain separate

The public `grade` rejects `ORIGINAL_OBJECT_MATCH`,
`HISTORICAL_COMPILER_VERIFIED`, `SOURCE_BEHAVIOR_VERIFIED` and
`WHOLE_SO_HASH_MATCH`, even if other grades match. Each may only appear in
`original_local_attestations`, with a separately explicit record containing
`grade`, `source_sha256`, `reference_sha256`, `report_sha256`, plus:

| Attestation | Additional required hashes |
| --- | --- |
| `ORIGINAL_OBJECT_MATCH` | `original_object_sha256`, `candidate_object_sha256` (equal; synthetic objects cannot substitute) |
| `HISTORICAL_COMPILER_VERIFIED` | `compiler_executable_sha256`, `sysroot_sha256`, `compiler_version_sha256`, `flags_sha256` |
| `SOURCE_BEHAVIOR_VERIFIED` | `oracle_report_sha256`, `test_report_sha256` |
| `WHOLE_SO_HASH_MATCH` | `original_so_sha256`, `candidate_so_sha256` (both equal the pinned full reference identity) |

A hash-shaped assertion is not authenticated proof. Output always marks these
`original_local_attestations_not_rechecked`; every proof grade remains
`NOT_VERIFIED`, and independently verified source matches remain zero. This
checker provides no trust/signature bypass or option to promote attestations.

## Prerequisites for any future passive mode

1. A maintainer must explicitly authorize the acceptance policy: independently
   verified historical compiler executable/sysroot/assembler/linker provenance,
   versions, flags, ABI, source/header/dependency identities and repeatable
   builds, **or** a narrowly permitted alternative grade with explicit scope.
   A Clang linked-window match cannot silently substitute for that decision.
2. Use two independent comparison stages where applicable: candidate object vs
   bounded original window with explicit ARM/Thumb/relocation mapping, then
   separately linked output vs original and independent linker-resolution
   cross-check. Add independent behavioral oracle cases when claiming behavior.
   Repeating one implementation or comparing synthetic `.byte` copies is not
   independence. Record exact source, reference, compiler and artifact hashes.
3. Provision a trustworthy private original-hash runner under new maintainer
   authority. Pin/recheck the actual edition ELF on every run, protect signing
   keys and originals, isolate untrusted PR code, bind attestations to the
   exact commit/source/dependency hashes and prevent stale/replayed evidence.
   Publish only reviewed metadata. Never give public PR jobs private originals,
   secrets, self-hosted-runner execution or upload access. A hash-only manifest
   from an untrusted PR cannot certify its own truth.
4. Require meaningful source-only build/tests, parser corruption/edge cases,
   applicable ARM/Android integration and independent regression evidence.
   Keep behavior, byte windows, original-object, compiler and full-library
   claims distinct. Continue legitimate behavioral source work while exact
   historical matching remains unavailable.
5. Human PR review must remain required; changes to this gate, policy, workflow
   or reference pins require maintainer review outside candidate self-approval.
   PR authors/AI cannot grade their own evidence. This issue does not authorize
   auto-merge, task generation, protected-branch changes or a runner rollout.
6. Before unattended execution is authorized, establish one bounded active task
   per worker, explicit issue allowlists, 20-minute sessions, request/token/time
   caps and queue backoff. Quarantine on provider timeout, malformed evidence,
   source/hash drift, identity conflict, failed build/comparison, unavailable
   oracle/compiler, privacy risk or rate-limit exhaustion. Stop rather than
   retry indefinitely. Preserve private checkpoints and require reviewed
   recovery/requeue. Define rollback to the last reviewed commit and invalidate
   attestations on every changed source/dependency/tool/policy identity.

Safe to activate later without expanding trust: use this gate to reject invalid
metadata and stale source identities for explicitly selected candidate manifests,
plus its fixture CI check. It cannot be used as a source-matching acceptance or
merge gate. Authenticated private comparisons, acceptance-grade changes, new
runners and all passive queue/merge behavior need new maintainer authority and
independent original-local evidence.

## Task #101 local validation and handoff

Validated on Python 3.14.8; hosted CI continues to select Python 3.12 (not run
locally). No proprietary ELF was needed or read by the gate or its tests.

| Command/check | Actual result |
| --- | --- |
| `python -m unittest nova3_team.tests.test_verification_gate -v` | Exit 0, 28 tests; synthetic metadata/CLI, malformed input, identity drift, traversal/symlinks/FIFO, false promotions, stage consistency and no writes/commands/network. |
| `python -m unittest discover -s nova3_team/tests -v` | Exit 0, 380 tests including the gate. |
| `cmake -S . -B /tmp/nova101-build -G Ninja -DCMAKE_BUILD_TYPE=Debug` and `cmake --build /tmp/nova101-build -j 2` | Both exit 0; independently rebuilt host targets. |
| `ctest --test-dir /tmp/nova101-build --output-on-failure` | Exit 0, 13/13 tests pass. |
| `python -m nova3_team.guard --repo . --all` plus explicit hygiene check of the four changed/new paths | Exit 0; existing tracked files and new files pass. |
| Legacy inventory; shell syntax checks; `python nova3_easy.py --self-test`; `git diff --check` | All exit 0. |
| `python -m unittest discover -s tools/observatory/tests -v` | Exit 1: 74 tests, one failure and one error in existing `test_discovery_fix` tests. `systemd-analyze verify` fails on sandbox `SO_PASSRIGHTS`/`SO_PASSCRED` socket permissions before the expected assertions. No out-of-scope repair attempted. |
| `python tools/observatory/observer.py --demo --once` | Exit 0; synthetic demo. |

Temporary validation logs are `/tmp/nova101-{gate-tests,team-tests,configure,build,ctest,observatory-tests,observatory-demo}.log`;
they are local, not PR attachments. These checks establish no new original
binary/behavior/object/compiler match. No game source/evidence was changed.

Next review: inspect these four allowed files, run the focused fixture suite and
normal CI on its intended Linux runner, and review the metadata trust boundary.
A future reviewed candidate-manifest producer and private original-hash verifier
are separate work; there is no unattended acceptance path to enable yet.
