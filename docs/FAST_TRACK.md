# Fast-track operator path — October 2026

**Objective:** reach a source-built, testable N.O.V.A. 3 vertical slice quickly, then expand evidence-backed features. There is no honest guarantee of a playable full campaign in weeks: a commercial FPS includes rendering, levels, AI, UI, audio, persistence, JNI, and Android lifecycle. A fast plan must avoid claiming pseudocode count or byte-copying as game progress.

## Two parallel research tracks, one source product

1. **Function-accurate track:** keep exact ELF/compiler evidence, ARM/Thumb boundaries, optional IDA/Ghidra disassembly, proposed translation-unit groups and objdiff byte windows. Use it for hot paths, ABI-sensitive functions and disputed semantics. Genuine original `.o` boundaries/relocations are not recoverable merely by splitting a linked `.so`. A synthetic `.o` comparison is not proof of a source reconstruction.
2. **Behavioural vertical-slice track (critical path):** extend existing source-only CMake modules: resource mount → named mesh/texture/effect parsing → material/shader parameter semantics → scene/camera transforms → actual rendered frame. Then implement JNI lifecycle and interaction. Verify with independent golden data and devices when available. This track can advance while the compiler is unknown.

The primary source is the Premium ARMv7 library. JNI `libnativeinterface.so` and two Freemium libraries are additional references; do not silently equate their versions. The old Ghidra pseudocode remains useful evidence even before the historical compiler is identified.

## On Jason's Arch Linux laptop

Known hardware: Intel Celeron N5100, four cores, around 8 GiB RAM, Intel UHD GPU, and IDA Pro 9.4 executable at `/home/jason/ida-pro-9.4/ida`. **One heavy IDA/Ghidra analysis process at a time** is the recommended local concurrency. Prefer persistent projects, short function batches, caching, and targeted exports; do not run four full auto-analyses or large rev.ng/angr jobs simultaneously.

Do not switch branches over dirty uncommitted work. Use a clean clone/worktree or the project's normal GitHub PR flow first. Once the offline-object-matching PR is integrated, in the project directory:

```sh
python -m nova3_team.preflight \
  --ida /home/jason/ida-pro-9.4/ida \
  --out .cache/object-preflight.json

python -m nova3_team.object_matching fingerprint \
  analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --out .cache/premium-compiler-evidence.json
```

Preflight checks the four expected local ELF references, SHA-256 digests, tool visibility, RAM/CPU and whether `idapro` is importable; it **does not upload anything**, install packages, or prove the original compiler. These two local JSON outputs are private. A worker may publish a scrubbed summary such as “missing compiler evidence / ARMv7 / analysis pending” but not raw disassembly, game bytes or private filesystem paths.

### IDA Pro 9.4, with Ghidra still available

Hex-Rays documents `idalib` for headless scripting; 9.4 installers can activate it automatically. First check locally whether `python -c "import idapro"` works in the chosen Python environment. When necessary, use the official activation script supplied with IDA (verify it exists) and the official `idapro` Python package for the exact installed version. Set `IDADIR=/home/jason/ida-pro-9.4` for a single session, not globally.

The optional `tools/ida/export_headless.py` uses `idapro` and standard IDAPython on a **disposable ELF copy under .cache** or an existing `.i64`, and writes bounded function/chunk metadata only. Example after making a private reference copy:

```sh
mkdir -p .cache/ida-input
cp -n analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  .cache/ida-input/libNOVA3_neon.so

IDADIR=/home/jason/ida-pro-9.4 python tools/ida/export_headless.py \
  .cache/ida-input/libNOVA3_neon.so \
  --out .cache/ida-premium-functions.json --limit 256
```

`--analyze` explicitly requests full IDA autoanalysis and may consume time/memory; use a persisted analysis database instead when available. Exported image-relative addresses are not trusted ELF virtual addresses until an independent byte-mapping check. The existing Ghidra exporter stays the free default for contributors.

Do **not** install or route proprietary IDA licenses to community agents, and do not expose local analysis over an unauthenticated network server. An IDA MCP bridge may be an optional personal convenience, not a requirement for any contributor.

### Tool decisions

- **Keep:** existing Ghidra/PyGhidra, IDA 9.4/idalib, JADX/Apktool, CMake/Ninja, binutils/LLVM, NDK, source-only tests, local SQLite function ledger and GitHub worker coordination.
- **Add only if missing:** objdiff CLI/GUI for actual candidate-vs-reference objects; a compiler cache (e.g. ccache) if repeated compilation shows it helps. Prefer pinned versions and local build artifacts.
- **Delay:** mass installation of additional decompilers, angr, unconstrained symbolic execution, and repeat rev.ng experiments—prior real ARM rev.ng probes timed out without useful output. Test any new tool on a representative ARM/Thumb/NEON function first, with a time budget.
- **Historical compiler:** NDK r29 can compile **our new** ARMv7 source, but it is not evidence of Gameloft's historical GCC/NDK. Build a small candidate matrix from `.comment`, `.ARM.attributes`, dynamic symbols and instruction evidence. Use an isolated toolchain/container for older compilers, not global Arch upgrades.

### Irrlicht source: identify before vendoring

The official [Irrlicht downloads](https://irrlicht.sourceforge.io/?page_id=10) include source-containing SDKs 1.8.5, 1.7.3 and older versions. The engine's [zlib-style license](https://irrlicht.sourceforge.io/?page_id=294) permits modification with source notice preservation, but bundled JPEG and other dependencies have separate notices. Do not assume the newest tree—or even stock Irrlicht—is the exact modified code used by N.O.V.A. 3. First compare original class/symbol/RTTI names, vtable shapes, strings, API methods and old release history. Then pin the identified source archive/commit, audit its licenses, and import only relevant source-owned components under `reconstruction/third_party` while preserving game-specific glue and JNI work.

## AI-agent division of labour

On the four-core laptop, run **one analysis owner** and at most one lightweight independent coding/test worker at a time. Across remote contributors, divide by non-overlapping files and evidence boundaries:

| Role | Offline evidence | Deliverable allowed in GitHub |
| --- | --- | --- |
| Compiler/ABI investigator | ELF attributes, link layout, toolchain candidate experiments | Sanitized provenance matrix, synthetic test code |
| Native analyst | IDA or Ghidra scoped disassembly/decompilation | Verified metadata and source-owned C++ implementation |
| JNI/Android analyst | Local JADX/Apktool/JNI exports | Source-owned Android/JNI interfaces with tests |
| Engine/resource implementer | Validated asset data and existing CMake modules | Real decoder/material/render code with tests |
| Independent verifier | Local original oracle outputs | Bounded pass/fail evidence, regressions, mismatch reports |

GitHub issues/PRs remain the task handoff hub. Each issue needs exact `Allowed paths:`, small acceptance tests and no proprietary uploads. Workers should only propose PRs, never auto-merge. Run the existing release launcher when you are ready to give the local worker access to private inputs; downloading the launcher alone does not keep an agent running.

## Next measurable milestones

1. **Now:** local preflight + historical compiler evidence for all four references; record unknowns rather than guessing.
2. **First bounded objdiff:** select a genuine independently understood ARM function and verify a new C++ candidate against the original address window. No “match” grade based only on copied `.byte` assembly.
3. **First source-built frame:** decode actual first-mesh effect/material bindings, reconstruct shader semantics and render first tested geometry/texture, with capture/oracle comparison.
4. **JNI bootstrap:** recover actual registration/lifecycle and load the source-owned engine on an Android test device/emulator when available.
5. **Expand playability:** controls, scene transitions, game loop, entities, campaign and audio, each with reproducible tests.

If a task cannot be completed due to missing original binary access, CPU/memory or license restrictions, mark it blocked with a precise reproduction command and switch to the independent source vertical slice. Optimise the time to **verified working game features**, not the number of auto-generated `.cpp` files.
