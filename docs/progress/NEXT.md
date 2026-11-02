# Next tasks

## 2026-11-02 (LATEST) — source-assisted decompilation is running

Steps 1–2 of the previous plan are **done** (see `HANDOFF.md` and
`docs/progress/SOURCE_CORRESPONDENCE.md`): 39,452 functions extracted from the
Premium `.symtab`, 21,705 matched to a definition site (3,341 with strong body
evidence), and the build configuration recovered from machine code
(`GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL` defined, `GLITCH_FAST_MATH` not,
`ROUNDING_ERROR_64 = 1e-8`).

Now, in order:

1. **`getInverse` 0x000ebb08 (776 B)** — write
   `reconstruction/recovered/libNOVA3_neon/collada_matrix4_inverse.cpp` from the
   Cramer's-rule body at `matrix4.h:1251` (eleven double minors, `iszero(det)` with
   tolerance `1e-8`, `ftN = T(tN)`, 16 explicit products, then the `1/det` scaling
   loop), add a CTest behaviour target, run
   `tools/compare/gcc443_window_check.py` with GCC 4.4.3 at
   `-O3 -mfpu=vfpv3-d16 -mfloat-abi=softfp`. Acceptance: 776 B and the window
   explained word by word; record `MATCHED` or the residual.
2. **`operator*` 0x006e7e60 (564 B)** — restate
   `collada_matrix_multiply.cpp` as the real `matrix4_ops.h` template body
   (`CMatrix4<T> mat2(EM4CONST_NOTHING)` + `rowMatrixProduct34`), then re-run
   `tools/compare/collada_matrix_multiply_pilot.py`. The uncommitted edit in that
   file is an unfinished experiment and is not evidence.
3. **Build recipe** — the `android_neon/release/N.O.V.A.3` directory is generated
   (absent from the tree); recover the TU list from
   `trunk/prj/AndroidPrj/GameSpecific/_project_VisualStudio/Android.vcproj` and
   `tools/{sln2gcc,sln2mk,sln2android}`.
4. **Coverage ladder** — work down `coordination/source_correspondence.json` by
   subsystem (`vox` startup → `glitch::core` → `glitch::video` → gameplay), one
   unit per compile+compare cycle.

## 2026-11-02 (LATEST) — source-assisted decompilation

The pre-release source is materialised at `reference/nova3_source/` (private,
ignored). Read `HANDOFF.md` first. Do these in order:

1. **Correspondence table.** For each Premium `.symtab` function, locate its
   defining source file/body; start with the 18 mapped units and 62 exports.
   Write `coordination/source_correspondence.json` + a Markdown table with
   `exact_body_found` / `same_file_different_revision` / `not_found`. Regenerate
   the source index with
   `.venv/bin/python -m tools.source_ref.index_source_reference --write`.
2. **Recover the build recipe from the tree** (`NOVA3/extern/GLLegacyConfig/android_neon`,
   `NOVA3/trunk/prj/AndroidPrj`, `Android.mk`/`Application.mk`, `UB_*.cpp`,
   `.mk`/`.bat`): compiler, flags, defines, include order, TU grouping.
3. **Source-verify the 18 implemented units** (`getInverse` 0xebb08,
   `operator*` 0x6e7e60, `prepareCache` 0x73f0a4, constructors, `DetectNeon`,
   `Init`, cpu-features), then re-run the existing pilots and
   `tools/compare/gcc443_window_check.py`. Keep source presence, host tests,
   ARM object and instruction-window match as separate claims.
4. **Re-baseline** `coordination/progress_state.json` and
   `python -m nova3_team.analysis`; the ETA is provisional until then.
5. Continue expanding by subsystem from the source tree; no original executable
   or source text may become a production input.

## Settled residuals — parser-checked facts (2026-11-02)

The five settled residuals below are frozen in the JSON block, checked by
`python -m pytest reconstruction/tests/test_next_settled_residuals.py -q` and
stated here as facts, not as matched functions.

```json
{
  "schema": "nova3-settled-residuals/1",
  "reference": {
    "path": "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so",
    "sha256": "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
  },
  "residuals": [
    {
      "id": "operator_star",
      "symbol": "_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_",
      "address": "0x006e7e60",
      "original_size": 564,
      "candidate_size": 540,
      "vldr_original": 76,
      "vldr_candidate": 70,
      "load_gap": 6,
      "status": "NOT MATCHED",
      "evidence": ".cache/gcc443-sweep/sweep1.json (540 B rows incl. -O2 -funroll-loops; identical class mix, vldr delta 6) and .cache/gcc443-sweep/explain-multiply.json"
    },
    {
      "id": "prepare_cache",
      "symbol": "_ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv",
      "address": "0x0073f0a4",
      "original_size": 228,
      "candidate_size": 224,
      "size_gap": 4,
      "status": "NOT MATCHED",
      "evidence": "docs/progress/STATUS.md prepareCache section (224 vs 228 B); pilot tool tools/compare/collada_skin_trivial_pilot.py"
    },
    {
      "id": "android_cpu_init",
      "symbol": "android_cpuInit",
      "address": "0x000d2f98",
      "original_size": 2632,
      "candidate_size": 2648,
      "candidate_body": 2600,
      "size_delta": -16,
      "status": "NOT MATCHED",
      "evidence": "tools/compare/cpufeatures_pilot.py with -fsigned-char (2648 vs 2632 B; 11 sxtb/18 ldrsb reproduced, six feature permutations all 2648)"
    },
    {
      "id": "copy_ctor",
      "symbol": "_ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE",
      "address": "0x00108c8c",
      "original_size": 240,
      "candidate_size": 240,
      "identical_words": 41,
      "status": "NOT MATCHED",
      "evidence": "tools/compare/collada_matrix4_copy_ctor_pilot.py (41/41 non-branch words as a multiset; 25 displacement words differ by bb-reorder)"
    },
    {
      "id": "ctor_econstructor",
      "symbol": "_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE",
      "address": "0x00108c54",
      "original_size": 56,
      "candidate_size": 56,
      "identical_words": 56,
      "status": "MATCHED_MODULO_RELOCATION",
      "evidence": ".cache/gcc443-sweep/ctor-window.json (nova3.gcc443-window-check/1: 56/56 B, code_window_equal=false for the single bl memset relocation word)"
    }
  ]
}
```

## 2026-11-02 (later still) — autopilot engine

Done: DSH execution provider, supervisor, planner, units (`docs/DSH_WORKER.md`).
The first live planning pass filed **#163** (GCC 4.4.3 per-function window
matrix) and two proposals were correctly rejected for unsafe paths.

Next, in order:

1. **Approve or reject #163.** It is a planner proposal with no queue label.
   Approving it (owner approval label + `nova:queued`) makes it the first task
   the DSH worker executes end to end; rejecting it costs nothing.
2. Re-enable discovery once its resource guard is trusted:
   `systemctl --user enable --now nova3-discovery.timer`. Four ELFs exist locally
   but the last status reports only two scanned — worth one inspection pass.
3. The GCC 4.4.3 items from the section below remain the reconstruction work
   items; the planner now proposes them itself when the queue is empty.


## 2026-11-02 (later) — where the GCC 4.4.3 work stands

Done: constructor 0x108c54 is instruction-identical (call explained by the
link formula); measurement tooling fixed (numeric opcode compare,
`tools/compare/gcc443_window_check.py`).

Next, in order:

1. Run `gcc443_window_check.py` over every recovered function that already
   compiles at the right size under 4.4.3 (`computePitch`, `getTransposed`,
   the accessor cluster, `getStream`, `addOffset`, `getPrimitiveCount`,
   `CFPSCounter`), replacing old clang numbers in STATUS with per-function
   rows and report paths.
2. `operator*` @0x6e7e60: only a register/allocation difference remains. Try
   spellings that give the store's destination a live use (e.g. `T& d =
   out[12+c]; d = b[12]*...; d += a[12];`) and `-frename-registers`; keep the
   measured table in `.cache/gcc443-sweep/`.
3. `getInverse` @0x000ebb08 (776 B): still unimplemented; decode from bytes.


## 2026-11-02 — GCC 4.4.3 is now on the machine; the match work is flag-and-spelling work

Android NDK r8e was fetched (`tools/setup/fetch_gcc443.sh`,
`.cache/toolchains/android-ndk-r8e`, SHA-1 `5a2f85ac…`) and its
`arm-linux-androideabi-g++ (GCC) 4.4.3` runs on this host. The compiler
identity is no longer a hypothesis; the remaining unknowns are the flag set and
the source spelling.

Evidence so far (`tools/compare/gcc443_sweep.py`, report
`.cache/gcc443-sweep/sweep1.json`, 420 configurations per function, ABI fixed
to softfp/vfpv3-d16/neon x marm/mthumb x gnu++98 x O2/O3/Os/unroll x align x
fast-math):

* `CMatrix4<float>::CMatrix4(eConstructor)` `0x108c54` (56 B): size 56 already
  at `-std=gnu++98 -marm -mfpu=vfpv3-d16 -O2`; bytes differ.
* `operator*<float, collada::SMatrix>` `0x6e7e60` (564 B): at
  `-mfpu=vfpv3-d16 -O2 -funroll-loops` the instruction-class profile is exact
  (vldr 70 vs 76, everything else equal). The 6-load gap means 540 vs 564 B and is a reload pattern of
  the `a` operand (`vldr; vmul` first instead of `vldr r5; vldr r4; vmul`);
  `__restrict` helps `a` but hurts `b` and is rejected on evidence.

1. **Byte-equality pass with GCC 4.4.3** for every recovered function, starting
   with `0x6e7e60` and `0x108c54`: search source spellings (typed member access,
   row pointers, helper functions, loop order) with `gcc443_explain.py`, then
   confirm with `gcc443_sweep.py`. A spelling that reaches the class profile and
   the exact size is the candidate; only `compare_window` byte equality is a
   match.
2. Re-run the whole recovered-function matrix with GCC 4.4.3 instead of clang-21
   and republish the {matched / size-only / behavioural} table.
3. `CMatrix4<float>::getInverse` `0x000ebb08` (776 B) remains the next
   unimplemented dependency of the Collada matrix group (see the 2026-11-01
   section below).


## The transpose is byte-equal; the last dangling dependency is its inverse sibling (2026-11-01)

`glitch::core::CMatrix4<float>::getTransposed(CMatrix4<float>&) const`
(`0x000eba84`, 132 B) is IMPLEMENTED + TESTED and instruction-window **MATCHED**
with no mask: 33/33 words, 0 relocation/encoding differences at
`-O3 -fvisibility=hidden`, `code_window_equal=True`. Its 16-slot mapping, the
involution property and the element-wise in-place behaviour are re-derived from
the bytes by `tools/compare/collada_matrix4_transpose_pilot.py`, and the copy
constructor's value-3/value-5 paths now run the real arithmetic (CTest
`collada_matrix4_transpose_link_path`).

1. **Bounded next step: recover `glitch::core::CMatrix4<float>::getInverse(
   CMatrix4<float>&) const`** at `0x000ebb08` (776 B, `.symtab` exact). It is the
   only dependency the recovered Collada matrix group still declares and does not
   own, and the copy-with-mode constructor's values 4 and 5 test its result for
   zero (`subs r1,r0,#0` then a conditional branch), so its success/failure
   contract matters as much as its arithmetic. Same pattern: decode → source with
   observed/INFERRED labels → `tools/compare/collada_matrix4_inverse_pilot.py`
   → CTest, and replace the instrumented stand-in in
   `collada_matrix4_transpose_link_path_test.cpp` with the real body. Bound the
   slice: read the element addressing and the determinant path first; if 776 B
   cannot pin the algorithm in one heartbeat, record what was established and
   stop rather than writing a textbook inverse that the bytes do not show.
2. **Do not chase the `-O2`/`-Os` rolled form.** Those rows are the measured
   negative control: GCC 4.4.3 unrolls the nested loop only at `-O3`. Nothing
   about the source needs changing to "fix" them.
3. **Do not re-open the settled residuals**: `operator*`'s 6-load gap
   (`0x006e7e60`, 540 vs 564 B), `prepareCache`'s 4-byte gap,
   `android_cpuInit`'s 2648-vs-2632 B and the copy constructor's case-block order
   (identical 41/41 non-branch multiset, permuted by bb-reorder).
4. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
5. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## The copy-with-mode constructor is done; the next slice is its transpose dependency (2026-11-01)

`glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float> const&, eConstructor)`
(`0x00108c8c`, 240 B) is IMPLEMENTED + TESTED and measured **NOT MATCHED**: the
instruction content is identical (candidate 240 B, reference 240 B) and (41/41 non-branch words as a multiset) but the
reference lays the four trailing case blocks out `4,2,3,1` where this compiler
emits `1,3,4,2`, so 25 displacement words differ. The five enumerator values are
now pinned from the bytes (1 copy, 2 identity, 3 transpose, 4 inverse, 5
inverse-transpose; 0 and >= 6 write nothing) and the in-binary call sites agree:
10 calls, 8 x `r2=1`, 2 x `r2=4`.

1. **Bounded next step: recover `glitch::core::CMatrix4<float>::getTransposed(
   CMatrix4<float>&) const`** at `0x000eba84` (132 B, `.symtab` exact). It is the
   smallest dependency the just-recovered constructor calls and it is already
   declared in `collada_skin_cache_deps.h`, so recovering it turns value 3 (and
   value 5's in-place transpose) from an instrumented stand-in into real
   arithmetic. Same pattern: decode → source with observed/INFERRED labels →
   `tools/compare/collada_matrix4_transpose_pilot.py` → CTest. Read the element
   mapping out of its own load/store offsets first; if 132 B cannot pin the
   mapping, record that and stop rather than assuming a transpose convention.
2. **Do not chase the block-order residual.** Source case order was measured to
   have no effect (six permutations, identical counts) and eleven `-fno-*` /
   reorder flags were measured (`-fno-reorder-blocks` 15 EQUAL,
   `-fno-schedule-insns2` 19, `-fno-guess-branch-probability` 4,
   `-fno-crossjumping` 292 B, the rest 28). It is bb-reorder placement, not a
   missing source construct.
3. **Keep the `-fno-pic` row.** It is the negative control that proves the
   dispatcher form (`addls pc,pc,r2,lsl #2`) comes from the default
   configuration, not from a flag chosen to fit; it changes the table to
   `ldrls pc,[pc,...]` and 12 relocation words.
4. **Do not re-open the settled residuals**: `operator*`'s 6-load gap
   (`0x006e7e60`, 540 vs 564 B), `prepareCache`'s 4-byte gap and
   `android_cpuInit`'s 2648-vs-2632 B.
5. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
6. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## The shared matrix constructor is done; the next slice is its copy-construct sibling (2026-11-01)

`glitch::core::CMatrix4<float>::CMatrix4(eConstructor)` (`0x00108c54`, 56 B) is
IMPLEMENTED + TESTED and instruction-window MATCHED modulo its one `bl memset`
relocation word (56/56 B, candidate 56 B, reference 56 B, 4/4 configurations, `code_window_equal=False`).
Measured call-site distribution: 74 calls, 54 with `r1 = 2`, 6 with `r1 = 0`.

1. **Bounded next step: recover `glitch::core::CMatrix4<float>::CMatrix4(
   CMatrix4<float> const&, CMatrix4<float>::eConstructor)`** at `0x00108c8c`
   (240 B, `.symtab` exact, the immediate neighbour of the function just
   recovered). It is the only place in the reference that can pin what the
   remaining enumerator values (3, 4, 5, …) mean, because the plain constructor
   maps *everything* >= 2 to the identity. Same pattern: decode → source with
   observed/INFERRED labels → `tools/compare/collada_matrix4_copy_ctor_pilot.py`
   → CTest. Record-and-stop if 240 B cannot separate the cases; do not invent a
   "transposed"/"inverse" branch that the bytes do not show.
2. **Do not re-open the settled residuals**: `operator*`'s 6-load gap
   (`0x006e7e60`, 540 vs 564 B), `prepareCache`'s 4-byte gap and
   `android_cpuInit`'s 2648-vs-2632 B. Every semantic fact in each matches; only
   a different GCC 4.4.3 patchlevel would move them.
3. **Full oracle chain, once, when a session has the time:** the
   `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` run exceeded the foreground
   limit; `collada_matrix4_ctor_pilot.py` is wired in at
   `.cache/cpufeat/collada_ctor/check_all.json` and is unverified *inside* that
   chain until it completes.
4. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
5. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## The matrix product is recovered; the next slice is its constructor (2026-11-01)

`glitch::core::operator*<float, SMatrix>` (`0x006e7e60`, 564 B) is IMPLEMENTED +
TESTED, measured NOT MATCHED (identical instruction mix, 6 fewer `vldr`). The
Collada matrix chain now has real arithmetic in both directions: `prepareCache`
builds the destination vector and this operator computes each element.

1. **Bounded next step: recover `glitch::core::CMatrix4<float>::CMatrix4(
   eConstructor)`** at `0x00108c54` (56 B, `.symtab` exact). It is the one
   unresolved dependency shared by *both* recovered Collada units, it is small,
   and its callers already pin the two argument values used (2 in `prepareCache`,
   0 here). Same pattern: decode → source with observed/INFERRED labels → pilot
   (`tools/compare/`) → CTest. Record-and-stop if the enumerator bodies cannot be
   pinned from 56 bytes.
2. **Do not chase the 6-load gap** in `operator*` (the SMatrix's middle component
   kept live across a row) nor `prepareCache`'s 4-byte gap. Both are measured
   codegen residuals with every semantic fact matching; only a different GCC
   4.4.3 patchlevel would move them.
3. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
4. **Full oracle chain, once, when a session has the time:** the
   `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` run exceeded the foreground
   limit; the new pilot is wired in at
   `.cache/cpufeat/collada_multiply/check_all.json` and is unverified *inside* the
   chain until that completes.
5. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.


## prepareCache is done; the next slice is its SMatrix consumer (2026-11-01)

`CSoftwareSkinTechnique::prepareCache` (`0x0073f0a4`, 228 B) is IMPLEMENTED +
TESTED and measured NOT MATCHED (224 B candidate, reference 228 B, 4 words of scheduling
difference). The class layout at `+0x04`/`+0x08`/`+0x0c` is now declared, and the
three 8-byte leaves re-measured byte-equal in all four configurations afterwards.

1. **Bounded next step: recover `glitch::core::operator*<float,
   collada::SMatrix>(CMatrix4<float> const&, SMatrix const&)`** at `0x006e7e60`
   (564 B, `.symtab` exact). It is the only semantic dependency prepareCache
   calls that is small enough to finish in one heartbeat, and reconstructing it
   turns "the product is opaque" into real arithmetic. Constraints: the
   `CMatrix4`/`SMatrix` element layouts are *not* established, so read them out of
   this function's own addressing before writing any arithmetic — if the layout
   cannot be pinned from 564 bytes, record that and stop rather than inventing a
   matrix convention. Same pattern: decode → source with observed/INFERRED labels
   → pilot (`tools/compare/`, `window_diff.diff_window`) → CTest.
2. **Do not chase `prepareCache`'s 4-byte gap.** It is one `add` scheduled before
   the call versus after it; every semantic fact matches. A different GCC 4.4.3
   patchlevel would be the only lever, and that is not this queue's business.
3. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
4. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## The cpu-features surface is closed; the next slice is prepareCache (2026-11-01)

The logger question "where does `android_getCpuFeatures()` gate NEON paths" is
answered and closed: `CSoftwareSkinTechnique::init` (`0x00739630`) only *lazily
fills* a TU-local 8-byte `cpuFeatures` cache (`.bss` `0x00da2950`), and the gate
is the `(cpuFeatures & 4) != 0` test in `CSoftwareSkinTechnique::skin`
(`0x0072ad90`, at `0x72b280`/`0x72b288`/`0x72b29c`/`0x72b2a0`/`0x72b2b4`).
Both call sites live inside 1028 B and 4256 B functions, so neither is a
separable unit and neither was turned into invented source.
Reproduce with
`.venv/bin/python tools/compare/skin_cpu_cache.py --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so`.

1. **Bounded next step: recover `CSoftwareSkinTechnique::prepareCache`**
   (`_ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv`,
   ELF VA `0x0073f0a4`, 228 B). Same pattern: decode → source with
   observed/INFERRED labels → pilot → CTest. It is the smallest remaining
   function of this class that is not a 1–2 instruction leaf.
2. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
3. **Do not re-open the `init`/`skin` cpu-features slices.** Their behaviour is
   established; reconstructing 1 KB and 4 KB blind to "own" two instructions is
   the wrong trade. Revisit only with a real caller chain.
4. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## Init is closed; the Collada NEON gate was the next slice (2026-11-01)

`vox::DriverCallbackInterface::Init(void*)` (`0x009d53f4`, 48 B) is IMPLEMENTED +
TESTED and measured shape-equal modulo the two relocation words and one constant
anchor offset. The next real dependency is the other cpu-features consumer.

1. **Bounded next step: recover the NEON gate in
   `glitch::collada::detail::CSoftwareSkinTechnique::init`** (`0x00739630`,
   1028 B), bounded by the single direct `android_getCpuFeatures` call at
   `0x00739764` and the branch that consumes it — not the whole 1028 B function.
   Same pattern: decode → source with observed/INFERRED labels → pilot → CTest.
   If the gate turns out to be a one-line feature test with no separable
   behaviour, record that and stop rather than reconstructing 1 KB blind.
2. **Publish is still a credential grant, and nothing else.** Managed broker
   token absent; `gh` not logged in; the host SSH key authenticates as
   `UmbraSecurity` but the remote answers `Permission ... denied`. Either grant
   this agent GitHub capability and run `./nova3 push`, or from the maintainer's
   terminal run `GH_TOKEN=<repo-scoped token> bash tools/setup/push_branch.sh`.
   `NOVA3_PUSH_DRY_RUN=1` rehearses.
3. **Do not chase the `0x34` anchor delta.** It is explained by the original
   TU's static anchor start, measured constant on every access, and would only
   close by inventing statics that the reference does not have in this function.
4. **Early-development codebase, when supplied**: candidate source under
   `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; it upgrades no
   completion state by itself.

## Toolset: done; publish needs a credential; source work is the next step (2026-11-01)

`./nova3` (`doctor|index|decompile|status|show|search|build|test|verify|push`) is
finished and verified this session: single-writer lock on the decompile driver
(second run exits `4`, stale lock replaced, ownership-checked release), `index`
reports real per-binary counts, `docs/DECOMPILE.md` documents the unattended
`nohup ./nova3 decompile --all` recipe. Measured: CTest 16/16, tooling pytest 16
passed, `doctor` 16/0/0, a real batch advanced coverage 7 → 9 exports in 33 s,
and every CI step that can run without the private tree returns rc=0.

1. **Publish is a credential grant, and nothing else.** All three routes were
   measured from this run: managed broker token absent; `gh` not logged in; the
   machine SSH key authenticates as `UmbraSecurity` but the remote answers
   `Permission ... denied`. Either (a) grant this agent/run GitHub capability in
   Paperclip and re-run `./nova3 push`, or (b) from the maintainer's terminal
   (where `git` is not wrapped) run `GH_TOKEN=<repo-scoped token> bash
   tools/setup/push_branch.sh`. `NOVA3_PUSH_DRY_RUN=1` rehearses without
   publishing. Do not copy a personal token into an automated run's env.
2. **Resume source work** with `vox::DriverCallbackInterface::Init(void*)`
   (`0x009d53f4`, 48 B) — unchanged below. `./nova3 decompile --address
   0x9d53f4`, then `./nova3 show 0x9d53f4`.
3. **When the early-development codebase is supplied**: treat it as candidate
   source under `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; see
   "When the early-development source arrives" in `docs/DECOMPILE.md`. It does
   not upgrade any completion state by itself.

## Tooling was set up; two things remained (2026-11-01)

The decompilation toolset is finished as a usable product: `./nova3` with
`doctor|index|decompile|status|show|search|build|test|verify|push`, documented in
`docs/DECOMPILE.md`, tested by `reconstruction/tests/test_workbench.py` (10
hermetic cases) and wired into `tools/check_all.sh` + `nova-ci.yml`. Ghidra/JDK
discovery no longer hardcodes this laptop's paths.

1. **Publish the branch** — the only outstanding setup item, and it is a
   credential grant, not code. `./nova3 push` (= `bash tools/setup/push_branch.sh`)
   does the push and verifies the remote head; it exits `3` and prints the two
   supported routes when the run has no GitHub capability. Nothing else is
   missing: remote, identity and the branch are in place.
2. **Resume source work** with `vox::DriverCallbackInterface::Init(void*)`
   (`0x009d53f4`, 48 B), unchanged below. Use `./nova3 decompile --address
   0x9d53f4` then `./nova3 show 0x9d53f4` for the first pass.
3. **When the early-development codebase is supplied**: treat it as candidate
   source under `reconstruction/third_party/<name>/` with a pinned commit and a
   licence/provenance record in `docs/decisions/ARCHITECTURE.md`; see
   "When the early-development source arrives" in `docs/DECOMPILE.md`. It does
   not upgrade any completion state by itself.

## Immediate: publish the branch, then resume the caller slice (2026-10-31)

1. **Publish `automation/paperclip-worker`** once a GitHub credential source
   exists. `bash tools/setup/push_branch.sh` now does the whole action and
   verifies the remote head; without capability it exits `3` and prints the two
   supported routes (grant the GitHub connection to the run so the wrapper can
   broker a token, or run it from the maintainer's own terminal with `GH_TOKEN`).
   Nothing else about the repo is missing: remote, `user.name`, `user.email` and
   the 3-commit branch are all in place.
2. **Continue the caller slice** with `vox::DriverCallbackInterface::Init(void*)`
   (0x009d53f4, 48 B) — unchanged from the previous queue entry below.
3. **Use the new pipeline for the next slice's evidence**, bounded:
   `.venv/bin/python tools/pipeline/auto_decompile.py --max-functions 8 --batch-size 4`
   then read the exports under `generated/ghidra/exports/<sha>/`. Do not run it to
   completion: at the measured ~11 s per function warm rate the full 39,123 would
   be ~100 hours, and an export is `DECOMPILED`, never a completion state.

## Immediate: DetectNeon is closed; the probe's single caller is the next slice (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp` is
`vox::DriverCallbackSourceInterface::DetectNeon` (ELF VA `0x009dad14`, 32 B).
It is IMPLEMENTED, TESTED (`detect_neon_behaviour`, CTest) and measured
`ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION` 6/6 configs at 32/32 B with the
two `BL` words as the only differences; `code_window_equal` stays false.
The call-site search (`tools/compare/cpuquery_callsites.py`, 39,108 functions
decoded, 0 undecodable) found exactly one in-binary caller and one other
cpu-features consumer:

* `vox::DriverCallbackInterface::Init(void*)` — `0x009d53f4`, **48 B**, calls
  DetectNeon at `0x009d5414`.
* `glitch::collada::detail::CSoftwareSkinTechnique::init(...)` — `0x00739630`,
  1028 B, calls `android_getCpuFeatures` directly at `0x00739764` (no family
  gate, no helper).

1. **Next bounded step: recover `vox::DriverCallbackInterface::Init` (48 B).**
   It is the smallest remaining caller in the chain and it consumes the probe's
   boolean, so it turns DetectNeon from a leaf into a linked source path. Do it
   the same way this slice was done and in this order: decode the window with
   `tools/compare/cpuquery_callsites.py --also 0x9d53f4:DriverCallbackInterfaceInit`
   plus a small Capstone structure pass, write the source with every
   constant/field INFERRED-or-observed labelled, then add
   `tools/compare/<name>_pilot.py` and a CTest behaviour target. Do not guess the
   `void*` parameter's type: 48 B is unlikely to prove it, and an unproven cast
   is worse than an opaque parameter.
2. **Then the Collada gate**, if the 48 B function has no source-visible
   behaviour worth owning: `CSoftwareSkinTechnique::init` is where the engine
   actually branches on NEON (1028 B, one direct cpu-features read). Recovering
   the gate condition and the two paths is a real rendering decision, but it is
   a much larger slice — bound it by the branch around `0x00739764` only, not by
   the whole function.
3. **Stop chasing `android_cpuInit`'s 12-byte residual.** It is 2648 B against
   the reference 2632 B with char signedness settled (`-fsigned-char`), every
   feature-check permutation measured and every relevant flag probed and
   rejected (see the section below). Only a different GCC 4.4.3 patchlevel
   would test the remaining hypothesis; if one is never tried, record that and
   keep the function IMPLEMENTED + TESTED.
4. **Keep the rejected hypotheses visible**: `-fno-toplevel-reorder` rows in
   `tools/compare/cpufeatures_pilot.py`, the `-noToplevelReorder` objects under
   `.cache/cpufeat/order/`, and the `-fno-*` measurements in STATUS.md. Do not
   delete them.

## Previous: finish the cpu-features TU (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/cpu_features.c` is now the whole
recovered TU. All three exported getters match the reference instruction stream
modulo the two unavoidable relocation words under NDK r8e GCC 4.4.3 at `-O2`,
`-O3` and `-Os`, with the reference `.bss` layout, and the helpers have
behavioural tests (`cpu_features_behaviour`, CTest). Two bounded gaps remain.

1. **Close `android_cpuInit`'s 32-byte gap** (candidate 2600 B vs reference
   2632 B at `-O3 -fstack-protector`). Known structural differences: the
   reference sign-extends the cpuinfo-field delimiters (`LDRSB` where this
   compile uses `LDRB`) and orders the `Features` block differently. Compare
   `.cache/cpufeat/rec/cpuinit.txt` (reference listing) with the candidate
   listing function-by-function, starting from the first divergence the pilot
   reports. `.cache/cpufeat/cpuinit_refs.json` already resolves every literal.
   Do not synthesise instructions to close the gap; if the source cannot
   produce it, record the divergence and stop.
2. **Behavioural test for the initializer end-to-end.** `android_cpuInit` reads
   fixed paths, so an integration test needs either a fixture root mounted into
   the process (e.g. a test-only build with redirected paths behind a macro that
   is itself documented as not present in the original) or a host test that
   runs against a synthetic cpuinfo in a `chroot`/mount namespace. Decide and
   document which; do not silently change the recovered source's paths.
3. Keep the rejected hypotheses visible: the pilot's `-noToplevelReorder` rows
   and `.cache/cpufeat/order/` exist so the definition-order conclusion stays
   measured. Do not delete them.

## Previous: close the two measured gaps in the cpu-features TU (2026-10-31)

`reconstruction/recovered/libNOVA3_neon/cpu_features.c` now covers the whole
recovered TU and is measured by `tools/compare/cpufeatures_pilot.py`. Two
concrete, bounded gaps remain; both are measured, not speculative.

1. **Reproduce the reference `.bss` ordering.** With the local NDK r8e GCC
   4.4.3 the two int-sized objects always land `g_cpuFamily@+4`,
   `g_cpuCount@+16`, while the reference has `g_cpuCount@+4`,
   `g_cpuFamily@+16`. Declaration order, object type and symbol names were
   varied with no effect. Test, in this order: (a) another 4.4.3 build (the
   `.comment` string only proves GCC 4.4.3, not the patchlevel or NDK
   revision); (b) `-fdata-sections` plus an explicit linker script, and measure
   the *linked* result instead of the ET_REL object; (c) GCC 4.6/4.7 from the
   same r8e NDK as a bound. Do not force the layout with padding or attributes
   and call it recovered. Closing this removes the single divergent word in the
   `android_getCpuCount` and `android_getCpuFamily` windows.
2. **Close `android_cpuInit`'s 32-byte gap.** The reconstructed body is 2600 B
   at `-O3 -fstack-protector` against the reference's 2632 B. Known structural
   differences: the reference sign-extends the field delimiters (`LDRSB`
   against `LDRB`) and orders the `Features` block differently. Work from
   `.cache/cpufeat/rec/cpuinit.txt` (reference listing) and the candidate
   listing side by side; the `tools/compare/literal_map.py` output in
   `.cache/cpufeat/cpuinit_refs.json` already resolves every literal.
3. **Behavioural tests for the TU.** The helpers (`parse_decimal`,
   `cpulist_parse`, `has_list_item`, `extract_cpuinfo_field`) are pure functions
   over caller buffers. Add a host test target with fixture buffers (cpuinfo
   with/without `Features`, `vfpv3d16`-only, `(v6l)` misreport, `0-3`/`2,4-7`
   cpulists, truncated/invalid input) so the TU reaches TESTED for behaviour
   independently of the byte-match question.

## Previous: relocate the thunks, then recover the initializer (2026-10-31)

The Premium CPU-query thunks now match instruction-for-instruction under three
different compilers, so the open question is no longer "which compiler shape"
but "can the relocation and literal-pool words be resolved to the reference
values". NDK r8e GCC 4.4.3 is installed locally at
`/home/jason/.local/opt/android-ndk-r8e`; the pilot picks it up automatically
(override with `NOVA3_R8E_NDK`).

1. **Relocation/link experiment.** Compile the matching-context variant with
   r8e GCC 4.4.3 and expose the `.bss`/`.text` layout the reference implies, so
   `R_ARM_PLT32` and `R_ARM_REL32` resolve to the observed displacements. This
   is the only measured remaining difference; until it closes, no byte-match
   claim is permissible. Keep any linker script or synthetic object under
   `.cache` and out of production targets.
2. **Recover `android_cpuInit` (0x000d2f98, 2,632 B)** at least far enough to
   own its writes to the state block, which also removes the need for the
   matching-context stand-in and restores direct (non-preemptible) binding of
   the initializer.
3. **Settle the alignment inference.** The two-`LDR` feature load is modelled as
   a word-aligned aggregate. Cross-check it against other 64-bit field accesses
   in the reference: if other 64-bit fields at provable 8-byte alignment use
   `LDRD`, the word-alignment reading gains support; if not, the model is wrong
   and the candidate struct must be corrected rather than kept.
4. Keep `reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp` (context
   ABI) alongside the corrected candidate but do not present it as the
   original interface.

Existing source-engine and asset milestones remain valuable and are retained.

## Immediate evidence/worker priorities (2026-10-28)

**Pre-start checkpoint:** Jason's local six-stage audit succeeded. It
inventoried 25 old recovered C++ blocks, identified no stale source hashes,
and established no new ARM source-byte matches. A donor shortlist of 64
four-byte thunks was unrepresentative; the updated planner now stratifies
nontrivial sizes and consumes existing-source worklist hints. The dedicated
worker-presence issue is #92; it is not a queued coding issue.

Next on the private laptop: `bash tools/run_compiler_pilot.sh`. Review its
actual ARM object comparison, then assign one tightly scoped native ABI
validation task to the Codex worker. Do not queue broad decompilation until
a reproducible source C++ comparison has been demonstrated. A code-window
match is not necessarily proof of the original compiler/linker or full `.so`.

1. Run `python -m nova3_team.legacy_validation --repo . --out .cache/legacy-source-revalidation.json` against all previous decompilation work; review each historical match assertion before assigning new grades.
2. Run `nova3_team.version_compare` and `nova3_team.donor_mapping` locally on the Cyrus debug and Premium retail ELF, then stage a small `object_matching` unit. Validate its boundaries in IDA or Ghidra and retain the full ELF hashes locally.
3. Establish a reproducible GCC 4.4.3 *candidate* alongside modern cross-compilers; test one genuine C++ ARM candidate with `nova3_team.compiler_matrix` and `nova3_team.object_compare`. Record code-window/relocation and behavioural results; do not claim original compiler flags, TU objects or full `.so` SHA without evidence.
4. Investigate the saved issue #82 timeout logs and re-scope the broad architecture task before requeueing it. It was moved from queued/attention to blocked to prevent automatic repeat attempts. Worker provider logs are private; the updated runner emits elapsed/byte-count progress and stops after one provider timeout.
5. Independently confirm the Irrlicht version/fork and the JNI registration/lifecycle before sourcing and building third-party engine/JNI adapters. Preserve Ghidra as the free analysis backend and the source-only render/material vertical slice below.

Existing source-engine and asset milestones remain valuable and are retained.

1. Use recovered effect/material data to build the next genuine render dependency. SB source reader now parses4,885 entries across30 packages, decodes2.70GB with independent size/CRC agreement, and locates first mesh NV_DiffuseVC.bdae/#DiffuseVC-fx plus originalGLSL references. Native key=original archive basename, XORed26-byte ZIP local-header body. See research/assets/SCRAMBLED_GLA.md. Ignored effect fixtures .cache/assets/{main,patch}-NV_DiffuseVC*. Reconstruct own shader source from verified equations; original shaders remain offline references. Recover effect technique/defines, actual material parameter values, vertex selector semantic IDs/normalization and sampler states before claiming faithful rendering. Native material6a790c uses24-byte SNewParam records, type+8/value+20; mesh buffer+4 references material ID (firstmesh _2_-_Default). Main effect document matches patch byte-for-byte; global edition parity remains unverified.
2. Source mesh decoding works on31 sampled assets. Extend resource/scene handling using native CCDB/SCollada fields and callers; current tool extracts first geometry only. CMesh ctor0x6d7f60/createGeometry0x6d8888/addStream0x81bd20 exported. Multi-buffer minimum vertex offsets are supported and30ARM arithmetic slices confirm them. Materials, transforms, shader semantic mapping and skin/controller geometry are the next real dependencies.
3. Build actual rendering only after original data/material/shader dependencies are recovered. Primitive/type mapping is established: serialized0→runtime6→GL_TRIANGLES4; rawtype6→FLOAT,rawtype1→UNSIGNED_BYTE. Byte normalization is shader-dependent. Do not substitute diagnostic OBJ/SVG previews for game-frame fidelity.
4. Minimal own Android asset inspector can now use real named loading/mesh bytes; SDK/NDK ready, ARMv7 source core builds. No APK/UI yet. Recheck ADB environment, complete standard-runtime/DEX/source-provenance auditing; no device test assumed.
5. Resolve Premium APK/OBB naming mismatch using expected GameInstaller DownloadComponents/config, then level/native paths. No default patch-overrides-main policy is invented: recovered ZIP mount order is insertion order, earlier successful match wins; failed decode tries later mounts.
6. Source file-region ZIP subset intentionally rejects ZIP64/descriptors/gaps/unsafe names and bounds metadata/output; indexed native ownership not reconstructed. Other ELF Ghidra imports/global/vtable/fulltypes still pending. Prefer next executable dependency over mass decompilation.
7. Do not blindly retry rev.ng; reports record90/30second timeouts. Parallel-agent quota interrupted a review this session; lead continued existing authorized work and persisted state.

Reproduce:
`NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh`
`.venv/bin/python tools/extract/materialize_premium.py` #2.3GiB ignored asset-data cache, verified hashes
`.venv/bin/python tools/compare/package_resources.py`
`.venv/bin/python tools/compare/mesh_source.py`
`.venv/bin/python tools/compare/pvr_header_oracle.py`
`.venv/bin/python tools/compare/texture_corpus.py`
`.venv/bin/python tools/compare/etc1_oracle.py`
`.venv/bin/python tools/compare/etc1_corpus.py`
`.venv/bin/python tools/compare/texture_path_oracle.py`
`.venv/bin/python tools/compare/texture_path_source.py`
`.venv/bin/python tools/compare/scrambled_zip_source.py` # full30-package CRC/size comparison; extracts selected offline effects
`.venv/bin/python tools/index/record_stage2.py`
`.venv/bin/python tools/index/check_index.py`
`build/host/nova3_resource 01_room_a.bdae build/first.bres --bres .cache/assets/premium-main.obb`
`build/host/nova3_mesh .cache/assets/premium-main.obb 01_room_a.bdae build/room_a.obj`

Nested archive integration: implement a source-owned seekable/memory SB archive class using scrambled_zip.* and existing payload decoder. SDIR is not yet validated; descriptors/other custom PAK variants unsupported. Mount precedence must follow actual CCustomFileSystem::customCreateAndOpenFile0x2a3afc rather than assuming generic ZIP policy applies across archive classes. Five new Ghidra exports completed successfully; total62. No Ghidra process should remain.

Re-run the settled-residual facts check (parser + Premium `.symtab`, read-only): `python -m pytest reconstruction/tests/test_next_settled_residuals.py -q`.
