# Offline object matching track (experimental)

This is a **research harness**, not the final N.O.V.A. 3 build. It preserves the existing Ghidra/PyGhidra → function index → evidence-backed C++/CMake pipeline. It adds the Discord-requested translation-unit hypothesis, per-function assembly, global-assembly C++ and objdiff loop without allowing original proprietary executable code into production targets or GitHub.

## What the linked shared library can and cannot recover

An Android ELF `.so` contains **linked** instructions and data. The original compiler's `.o` files, compilation-unit grouping, local relocation records, discarded symbols, intermediate sections and linker layout decisions generally cannot be uniquely recovered from it. Splitting a `.so` by inferred C++ classes does not reverse the linker. An extracted object assembled from linked bytes is a **synthetic reference object**, not an original object.

There are three independent success criteria:

1. **Byte-window match:** a candidate function's instruction bytes match a recorded address range in the original `.so`. This is useful but does not prove identical C++ or relocations.
2. **Object match:** a candidate `.o` matches a synthetic or independently recovered relocatable reference at a documented scope. When the reference came from emitted `.byte` data, a trivial match is *not* decompilation.
3. **Whole-binary SHA-256 match:** the complete rebuilt `.so` matches the original byte for byte. Requires validated compiler/assembler/NDK/linker versions, link order, relocations, ELF layout, build-id, padding and other inputs. Currently **not established**. Do not claim this or synthesize a linker script merely to make a hash appear equal.

The final deliverable remains independently compilable native and Android source. Global assembly is a temporary migration and comparison technique only. Never add the generated `.s` or `.o` to `nova3_core`, Android packaging, source-only release artifacts, commits or PRs.

## Local procedure (Arch Linux)

Reference inputs already expected on the maintainer's machine:

- `analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so` — primary symbol-bearing Premium engine
- `analysis/N.O.V.A.3_premium/lib/armeabi/libnativeinterface.so` — Premium JNI
- `analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libgenerator.so` and `libGloftNAHM.so` — Freemium comparisons

Run from `~/Projects/nova3_re` after synchronizing the PR branch. The following commands never upload an ELF or extracted assembly:

```sh
ELF=analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
python -m nova3_team.object_matching fingerprint "$ELF" --out .cache/objmatch-fingerprint.json
python -m nova3_team.object_matching stage "$ELF" --out .cache/objmatch --limit 16
python -m unittest nova3_team.tests.test_object_matching -v
```

The fingerprint records ELF metadata, ARM attributes, `.comment` and notes where present. **GCC or Clang version, C++ ABI, optimisation, NDK revision and link flags remain UNKNOWN** until specific independent evidence supports them. Compare GCC versions, `readelf -A` ABI attributes, imported libstdc++/libc++, `.ARM.attributes`, instruction idioms and symbol/vtable layout on representative recovered functions before fixing a compiler profile. Local NDK r29 is a *modern candidate tool*, not evidence of the historical game compiler.

`stage` uses this repository's validated ELF symbol ledger to emit up to 16 bounded, nonoverlapping, byte-preserved functions by default:

```text
.cache/objmatch/
  manifest.json             evidence, exclusions, tentative groups and source SHA
  asm/00000000_arm.s        one function per .s (linked byte snapshot)
  source/scope_*.cpp        C++ translation-unit hypotheses using global asm .include
  reference/scope_*.s      synthetic assembler aggregation
  objdiff.json              objdiff units, all marked incomplete
```

Names and units are **heuristic**. `.byte` snapshots preserve bytes, but are not a claim to have reconstructed instruction text or relocations. Replace individual functions carefully with reconstructed C++ and compare against the original `.so` window and independent behavior. For a chosen symbol, stage only a reviewed address via `--address 0xde2cc --limit 1` (substitute the actual observed virtual address). The generator refuses ambiguous, overlapping, unknown-size or out-of-range symbols. It must never infer ARM/Thumb state from odd/even addresses without validated metadata.

For an **explicitly selected candidate cross-compiler**, record its identity before building. Example below uses the already documented Android NDK installation; target and optimisation flags are illustrative and **not original-game flags**:

```sh
python -m nova3_team.object_matching profile \
  /opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++ \
  --flag=--target=armv7a-linux-androideabi21 --flag=-fPIC \
  --out .cache/objmatch-compiler.json

python -m nova3_team.object_matching build \
  --out .cache/objmatch --profile .cache/objmatch-compiler.json
```

The build checks the original ELF SHA and compiler version hash and writes:

- `objects/reference/<group>.o` — **synthetic** reference assembled from linked-byte `.s`
- `objects/build/<group>.o` — candidate from global-asm `.cpp`
- `build_record.json` — local build inputs/output hashes, without equivalence claims

Open `.cache/objmatch/objdiff.json` in the locally installed [encounter/objdiff](https://github.com/encounter/objdiff) app. Its units refer to reference `target_path` and current `base_path`; automatic builds are deliberately disabled while the compiler profile is not validated. A 100% match between two byte-preserving wrappers proves only that the harness reproduced those linked bytes, **not** source recovery. Do not merge the generated object files or carry them into the final APK.

## Ghidra and optional IDA Pro

Keep Ghidra as the free, reproducible backend and the existing `tools/ghidra/export_batch.py`, Ghidra boundary exporter and `nova3_team.indexer` as metadata sources. Indexer `nova3.function-ledger/1` records ELF virtual addresses, Thumb state, exact symbol sizes and evidence; coordinate local analysis with the existing SQLite and clustering tasks. Ghidra rebases the Premium image by `0x10000` in the documented setup; always independently check the byte mapping.

IDA Pro users can run `tools/ida/export_function_index.py` in a loaded database after setting `NOVA3_IDA_INDEX_OUT` to an ignored path, e.g. `.cache/ida-functions.json`. This writes address/chunk/name *metadata only*. It does not call proprietary Hex-Rays APIs and never uploads pseudocode or instructions. IDA image-relative addresses are **not accepted as ELF offsets** until segment mapping and bytes are independently verified. No automatic substitution of IDA's analysis for Ghidra's trusted index has been implemented.

For stripped JNI/Freemium libraries, the current staging command deliberately stops at functions lacking verified ELF symbol ranges. Next work is an adapter consuming a validated Ghidra/IDA function ledger with each exact ARM/Thumb mode, contiguous code ranges, image-base mapping and independent byte check, followed by explicit manual TU ownership review.

## Relocations, link maps, and exact compiler research

Before writing `.ld` or reconstructing relocations:

- Record `readelf -SW -lW -rW -dW -A`, `nm`, `objdump`, symbols, GOT/PLT, TLS, `.ARM.exidx`, `.init_array`, section and segment alignment. Preserve these only offline.
- Classify each suspected original TU with **INFERRED** confidence; evidence may come from DWARF compile units, function clusters, C++ symbol scopes, JNI call graphs, vtables, shared globals, references and source paths. A class is not necessarily one TU.
- Collect actual compiler/linker candidates and compile at least one real C++ reconstruction at multiple flag profiles. Compare disassembly and constant/data placement. Keep a matrix of matches/mismatches; reject an assumed match without evidence.
- Build an explicit relocation worksheet for PC-relative branches, literal pools, GOT references, vtables, data dependencies and ARM/Thumb interworking. Do not invent a `.rel.text` table from linked opcodes or assume PLT/TLS transformations are reversible.
- Derive a linker script only from section/segment layout and observed linker semantics, then test independent clean full links. Record SHA-256 mismatches. A linker script cannot by itself restore unobservable original compilation inputs.

## Irrlicht and JNI source integration

The actual upstream **Irrlicht engine version/fork, license and modifications used by N.O.V.A. 3 are not yet verified**. Do not copy a random version into reconstructed modules or replace game-specific engine code with generic Irrlicht. Search surviving symbols, RTTI, strings, headers and calls to identify the version, inspect source license obligations, then import an explicitly pinned *source-available* version under `reconstruction/third_party/` (with a reproducible manifest and source attribution). Keep engine adapters in `reconstruction/engine/` and recovered JNI interfaces in `reconstruction/android/`; compile them in source-only tests. Original JNI `.so` files remain offline evidence.

## Cyrus donor and compiler-matrix extensions (2026-10-28)

The new local-only `nova3_team.donor_mapping` consumes the **complete**
`nova3_team.version_compare` Premium-versus-Cyrus report, verifies edition
SHA metadata, ranks small named function candidates, and adds bounded
DWARF compile-unit and optional IDA hints. Its output stays under
`.cache/donor-plan.json`. Pass `--donor-plan .cache/donor-plan.json` to
`object_matching stage` to group synthetic source units by *inferred*
ownership. No debug-only function is automatically treated as a Premium
original-TU definition.

`nova3_team.compiler_matrix` compiles source-owned C++ with one to eight
explicit, fingerprinted ARM compiler profiles sequentially and compares
function bytes with the original linked ELF through
`nova3_team.object_compare`. The comparator validates ELF32 ARM ET_REL
symbol bounds and records whether candidate relocations affect the selected
window. Output is metadata/hashes only and stays in ignored local cache.
Code-window equality, even with no detected relocations, is **not** an
original object/ABI/whole-.so match without independent evidence.

`nova3_team.legacy_validation` audits all existing
`reconstruction/recovered/**/block_*.cpp` source and historical
`coordination/evidence/*.json` declarations without discarding old code.
Every block requires current compiler-aware review. Historical short
window-match reports are preserved as claims for independent rechecking,
not automatically promoted or silently downgraded. Full executable commands
and the 11-step Discord acceptance matrix are in
[`docs/DISCORD_IMPLEMENTATION.md`](DISCORD_IMPLEMENTATION.md).

## Automation/agent acceptance conditions

- GitHub holds source, scripts, test results and **allowlisted metadata only**. Never post APKs/OBBs/ELFs, original code bytes, extracted `.s`, obfuscated decompiler dumps, IDA databases or logs with private paths.
- A worker first checks the exact binary SHA and compiler evidence, then performs bounded extraction, compilation, objdiff, and independent tests locally. It submits a PR containing newly written C++ and test/evidence summaries only, with original references kept offline.
- Grading remains `INVENTORIED` → `DISASSEMBLED` → `DECOMPILED` → `UNDERSTOOD` → `IMPLEMENTED` → `TESTED` → `VERIFIED`. A synthetic reference object does not advance a grade.
- Do not auto-merge, automatically enable privileged GitHub Actions, invent missing JNI functions or claim decompilation from a successful `.o` build.
- Existing 13-module engine slice, source-only guard, fixtures, Ghidra data and worker coordination must be preserved. This track is optional until its reference methodology and compiler hypotheses have been validated.
