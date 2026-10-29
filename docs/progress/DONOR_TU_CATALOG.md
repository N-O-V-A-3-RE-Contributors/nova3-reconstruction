# Task #119: donor-guided TU catalog

Implemented a source-only, offline catalog of **candidate** translation-unit
groups. Premium retail remains the target; Cyrus is a metadata donor. This
does not recover original `.o` files, removed relocations, original compiler
flags, source equivalence or whole-library layout. Source matching remains
**NOT MATCHED**, including for equal donor/retail linked byte windows.

The implementation is independent of the GCC diagnostic in #117 and the
synthetic-object staging tools. It creates no source, assembler, objects,
issues or network requests, and never executes or modifies an input ELF.

## Operator invocation (Linux, local references only)

After merging the source into the owner's project checkout:

```sh
cd ~/Projects/nova3_re
python3 tools/build_donor_tu_catalog.py \
  --retail analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --donor .cache/cyrus-debug/libNOVA3_neon.so \
  --out .cache/donor-tu/run-001.private.json \
  --summary-out .cache/donor-tu/run-001.summary.json
```

Choose fresh filenames for subsequent runs; existing outputs are refused.
Both outputs must resolve inside this checkout's ignored `.cache/`, with no
symlink escape. The worksheet is private. The optional summary, also first
written inside `.cache/`, is suitable for manual review before publication.
Do not upload references or worksheets. Normal stdout contains the same
public-safe aggregate summary; failures print bounded diagnostics without
input names, exception payloads or local paths. Exit 0 means catalog generation
only; exit 2 means rejected evidence, output conflict or a resource failure.

Default reference pins are SHA-256 of the **ELFs**, not their APKs:

| Role | ELF SHA-256 |
| --- | --- |
| Premium retail | `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf` |
| Cyrus donor | `d6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167` |

`--retail-sha256` and `--donor-sha256` allow explicit alternate local pins,
including synthetic fixtures. An alternate hash is labeled
`explicit_unrecognized_reference`, never silently certified as an edition.

Python 3.10+ and the repository modules are required. Present DWARF requires
locally available pyelftools; missing/unreadable DWARF support fails explicitly.
No package installation is attempted. No IDA license is required. IDA Pro can
provide optional independent boundary enrichment in a later review; Ghidra
remains the free fallback. This catalog currently accepts ELF evidence only
and cannot certify an IDA/Ghidra image-base mapping.

## Inference rules and limits

The implementation reuses `version_compare` and its `indexer` ELF reader:
SHA checking, file-backed executable sections and PT_LOAD mapping, static
symbols, A32/Thumb entry bits, mapping-symbol conflicts, equal-entry aliases,
partial/tail overlaps, duplicate-name ambiguity and non-bijective donor pairs.
Same-name size/address changes are retained explicitly, with independent
retail/donor extents and digests. No bytes are normalized or emitted.

1. **DWARF CU clues:** require a real `DW_TAG_compile_unit` top DIE and a
   concrete, non-declaration subprogram DIE at the exact symbol entry with an
   exact matching end. Both absolute and constant-offset `high_pc` forms are
   supported. Where linkage names exist they must agree with an ELF alias.
   CU names/producers are represented by hashes, only when actually present.
   A CU name, enclosing CU address interval, or declaration alone cannot
   assign linked functions. Unsupported range lists, extent/linkage conflicts,
   multiple CUs at an entry, and contradictory retail/donor CU name hashes
   prevent promotion. Raw source paths and names are never emitted.
2. **Retail evidence first:** direct retail CU association has `high`
   confidence when the CU has an evidenced name; unnamed retail CUs and
   donor-only ownership transfers have `medium` confidence. These tiers
   describe clue quality, **not proof of original TU or source equivalence**.
   Equal donor bytes do not promote a tier. Different bytes and sizes remain
   eligible hypotheses if both independently checked ranges are sound.
3. **Scope fallback:** only simple, complete Itanium nested name scopes are
   parsed. Templates, substitutions, thunks, operators and unsupported names
   remain unknown. All aliases in both editions must agree on the complete
   scope. Two or more functions must also be consecutive in both linked
   inventories, retain order and section index within each image, and have
   gaps no larger than 256 bytes. Unpaired/excluded entries, section changes,
   other scopes and reorderings break runs. These groups are always `low`.
   A shared leading namespace or adjacency alone cannot establish a group.
4. **Unclassified is valid:** retain a reason for every unclassified Premium
   entry. Oversize groups remain unclassified rather than inventing original
   TU splits. Functions are never sized using distance to the next symbol.

ELFs are read as bounded regular files, each at most 64 MiB; the shared parser
caps symbol/DWARF/string/line-table expansion before eager backend work.
The catalog accepts at most 100,000 Premium boundaries and 256 members per
group; callers may lower those bounds. The gap parameter is capped at 65,536.
JSON output streams through a private mode-0600 temporary file with a 64 MiB
worksheet cap and 16 KiB summary cap. A failed serialization does not replace
the destination. Files are individually atomic; a later summary-write failure
can leave an already completed private worksheet. A killed worker can leave
an ignored private temporary file.

The CLI uses a spawned worker and 120-second parent timeout on Linux,
Windows and macOS. Linux also caps the worker's address space at 1 GiB
(or the inherited lower limit). Other platforms use the same input/table/
output caps and timeout; no Unix signal or Linux-only tool is required.
The pure Python API is bounded by input/table counts; its caller owns any
wall-clock/process memory policy. Linux was exercised here; Windows/macOS
execution has not been tested on physical hosts.

## Manifest/API contract for later staging

`build_catalog(retail_bytes, donor_bytes, *, retail_sha256=..., donor_sha256=...)`
returns `nova3.donor-tu-catalog/1`. `public_summary(catalog)` emits a nested
allowlist containing only aggregate counts/bins, reference hashes/edition
pins, tool version and hashes of the catalog/shared parser sources.

| Private worksheet field | Contract |
| --- | --- |
| `references` | Input SHA pins and known/unknown edition labels; no paths. |
| `functions[]` | One normalized Premium entry; ID `SHA:8-digit-VA:arm-or-thumb`. Separate retail/donor raw value, mode, VA, file offset, section index, symbol size, window digest, relocation count and exclusions. Aliases contribute only a count and set digest. |
| `retail_cu`, `donor_cu` | Exact subprogram/CU association, CU offset, name/producer hashes and sorted supporting DIE offsets, or null. |
| `donor_window_status` | `exact_bytes`, `different_bytes`, or `UNKNOWN`. Observations about donor windows only. |
| `group_id`, `confidence`, `reason_codes` | Explicit inference or unclassified state; deterministic reasons. |
| `groups[]` | `dwarf_cu` or `scope_order`, evidence, member IDs, confidence and reason codes. Group IDs hash both reference pins, kind, evidence and sorted membership. No envelope is treated as a contiguous original TU. |
| `compiler` | Identity/flags `UNKNOWN`; source matching `NOT MATCHED`. `.comment` versions elsewhere are clues only. |
| `staging_contract` | Automatic staging disabled; original TU unproven; recheck hashes, modes, aliases/dependencies and relocation worksheet before any separate staging experiment. |

IDs are reproducible for fixed pinned inputs and inference policy, independent
of dictionary/symbol iteration order. They intentionally change when the
reference ELF or group evidence/membership changes. Schema/tool/source hashes
record the policy implementation. No compatibility with an existing staging
manifest is implied; a later adapter must explicitly validate this schema and
recheck all original boundaries. No original relocations or dependencies are
inferred from symbol adjacency. Independent original-vs-source comparison,
compiler version/flags, source hash and relocation review remain required for
any matching claim.

## Verification and handoff (2026-10-29)

Executed offline with Python 3.14.8 and pyelftools 0.33, no system changes,
network access, child AI sessions or GitHub mutations. Read-only preflight
found all four reference libraries; Premium fingerprint matched the expected
SHA and retained original compiler `UNKNOWN` despite GCC 4.4.3 comment clues.

```sh
python -m unittest nova3_team.tests.test_donor_tu_catalog \
  nova3_team.tests.test_version_compare \
  nova3_team.tests.test_donor_mapping nova3_team.tests.test_indexer -q
```

Result: **71 tests passed**, exit 0 (21 catalog tests plus 50 existing parser,
donor and indexer regressions). Tests use source-owned synthetic ARM ELF and
real DWARF fixtures, including declarations/invalid top tags, ambiguity,
conflicting linkage/extents, unsupported ranges, equal/different donor bytes,
changed sizes/addresses, alias splits, tail overlaps, empty metadata, unknown
compiler, deterministic IDs, bounded output, privacy filtering, spawned CLI
and parent timeout. No live analyzer, toolchain, game data or IDA is needed.

The pinned local real-input run completed successfully in 12.17 seconds,
with 275,520 KiB peak child RSS on Linux. Its 50,819,888-byte private worksheet
is `.cache/donor-tu-catalog-119-validated.json`; its optional aggregate summary
is `.cache/donor-tu-summary-119-validated.json`. Both remain ignored and
uncommitted. These runtime measurements are observations, not portable limits.
Observed inventory and inference counts:

| Measurement | Result |
| --- | ---: |
| Premium static defined function-symbol entries / normalized boundaries | 39,123 / 39,092 |
| Cyrus static defined function-symbol entries / normalized boundaries | 38,417 / 38,386 |
| DWARF CUs inventoried, Premium / Cyrus | 20 / 20 |
| Candidate groups, direct retail CU / scope-order | 9 / 1,355 |
| Grouped functions, high / low clue confidence | 83 / 4,449 |
| Unclassified Premium boundaries | 34,560 |
| Equal / different donor windows among paired Premium entries | 11,036 / 26,676 |
| Unknown donor window status | 1,380 |
| Proven original TUs / recovered objects / source matches | 0 / 0 / 0 |

Counts explicitly distinguish static symbol entries from normalized entry
boundaries; earlier approximate donor headline counts are not reused as proof.
No new game functions were implemented, decompiled or behaviorally verified by
this catalog, and no end-to-end game functionality is claimed.

Next bounded work: independently review a small high-confidence group against
the pinned ELF/DIE offsets, then design a separate staging adapter with an
explicit relocation/dependency worksheet. Range-list support and complex C++
scope parsing are intentionally unsupported; keep those candidates unknown
until separately implemented and tested. Compiler diagnosis remains outside
this task. Only the four task-allowed source/test/CLI/documentation files were
changed; generated worksheets remain private and ignored.
