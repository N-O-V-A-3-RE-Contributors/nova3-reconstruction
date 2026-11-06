# REvanguard3 integration audit — 2026-11-05

## Outcome

The supplied `src_layout` has **2,027 comment-only C/C++ files and zero code
candidates**. These are function-name/address inventories, not reconstructed
implementations. Copying them into production would inflate source-file counts
without adding working game code. No existing recovered implementation was
replaced, and implementation/verification progress counters were not advanced.

The donor ELF is a **different build** from Premium:

| Input | SHA-256 |
|---|---|
| REvanguard3 donor | `d6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167` |
| Current Premium target | `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf` |

Never use donor virtual addresses as Premium addresses. The donor README's
assertions about NDK version, exact source identity, original object boundaries
and whole-binary matching have **not** been promoted to project facts.

## What was integrated

- Independently written importer: `tools/source_ref/import_donor_layout.py`.
- Seven fixture tests: `tools/source_ref/test_donor_layout.py`.
- Small publishable aggregate: `coordination/donor_layout_summary.json`.
- Full metadata-only function/source/object lookup retained privately under
  `.cache/source_ref/revanguard3-layout.json` (ignored, not pushed).

The importer checks the donor hash, validates each TSV address/name/size against
our existing ELF ledger, normalizes safe relative source paths and matches
unique mangled names to Premium. A valid symbol is still only INVENTORIED.
Source and object attribution remain **INFERRED**, independently of the donor's
own confidence. Cross-build symbol-name correspondence proves neither equal
behaviour nor matching machine code.

Initial validation of 38,422 input rows:

- 37,477 rows match donor symbol metadata and pass the input checks.
- 36,885 accepted rows identify one Premium symbol by mangled name.
- 48 have ambiguous Premium destinations; 544 have none.
- 925 rows fail path/shape/range checks; 20 fail donor symbol matching.

These are **row counts, not additional implemented or verified functions**.
Do not automatically admit all of them into the execution queue.

## Reproduce and use the work

```sh
python3 -m unittest tools.source_ref.test_donor_layout -v
python3 -m tools.source_ref.import_donor_layout \
  --donor /path/to/REvanguard3 \
  --primary analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --summary coordination/donor_layout_summary.json

# Fast cached lookup by a Premium address; hash checked before returning hints.
python3 -m tools.source_ref.import_donor_layout \
  --primary analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --lookup-primary 0x006e7e60
```

Use a returned source hint to narrow local source-correspondence searches, then
independently verify the Premium function and the candidate source body before
reconstruction. Missing/unattributed paths are null, not invented source files.
The matrix-multiply example resolves to a different donor address; its source
attribution is unknown, which is preserved rather than guessed.

## Excluded material and provenance

Original `.so`, APK content, assembly, data bytes, generated objects, build
outputs, decompiler listings and private research are not committed or shipped.
No donor script is executed or copied. No top-level license was found in the
supplied framework, so its tooling is not silently relicensed into this project.
The new importer is independently implemented against the metadata format.
The external tree is read-only and remains in its original location.

This integration improves lookup/research infrastructure. It does **not** add
playable functionality or constitute a source decompilation milestone.
