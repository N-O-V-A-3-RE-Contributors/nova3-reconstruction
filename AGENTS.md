# N.O.V.A. 3 — Independent Source Reconstruction

## Mission: build the game from source, not from its old executable

You are the autonomous principal engineer for a long-running reverse-engineering and source-reconstruction project. Recover and reimplement the Android game **N.O.V.A. 3** from the supplied APKs and game-data archives. The required end product is **real, working, independently compilable source code** that reproduces the original game's behavior, appearance, levels, audio, UI, controls, single-player campaign, and, where recoverable, multiplayer functionality. A complete project may require many sessions; relentlessly advance verifiable milestones rather than claiming premature completion.

**Non-negotiable end-state:** the resulting APK is built from this project's source and approved source-available dependencies. No original Gameloft `.so`, `.dex`, executable code, encrypted executable payload, bundled emulator, binary translation wrapper, or copied executable blob may be linked, loaded, executed, or required at runtime. New `.so` files **compiled from our source** are expected and permitted. Original assets may be read as *data* for local testing/reproduction, but do not conflate asset decoding with executable-code reuse. Third-party libraries used in the final product must have acceptable licenses and reproducible source builds; list and audit them. The project must never need an original APK to *run* once built. Reference binaries remain available offline only for analysis and comparative testing.

**Priority order:** (1) executable behavior and correctness, (2) comprehensive coverage, (3) reproducibility and verification, (4) readable maintainable organization, (5) optimization and enhancements. Use ugly but sound intermediate code if necessary to obtain a demonstrably correct source-compiled milestone; subsequently refactor with regression tests. Do not replace evidence with attractive invented implementations. Recovering exact original source is generally impossible; target observable equivalence and explicitly record gaps. Do not substitute patched APKs, original-library launchers, lifted-binary execution engines, or compatibility layers for the final implementation.

## Actual workspace (verify, do not assume later changes)

Work from `~/Projects/nova3_re`, resolving the absolute path from the real user account and current filesystem. Do not infer the project user's home from an elevated/root shell. Preserve these read-only reference inputs exactly:

```text
originals/N.O.V.A.3_premium.apk
originals/N.O.V.A.3_freemium.apk
analysis/N.O.V.A.3_premium/lib/armeabi/libnativeinterface.so
analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libgenerator.so
analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libGloftNAHM.so
Archive/Nova3_premium_only_OBB.rar
Archive/Nova3_freemium_only_OBB.rar
```

Known observations (recheck with tools): the Premium engine `libNOVA3_neon.so` is 32-bit ARM EABI5, **not stripped and reported to contain debug information**; Premium `libnativeinterface.so` and both Freemium libraries are stripped. The extent of actual DWARF/type/symbol information is unknown. APK hashes supplied earlier:

```text
Premium   e2a4e30546c5a82270c3f867e814d8759cc3ea96bf22ab4e5eae173083b163b2
Freemium  e3f5a609ce952bdf46ae78521c6ac31b35fcdea8ee94eb44c738b0431d21cdf8
```

**Premium is the primary reconstruction target** because of possible surviving debug metadata. Freemium is an independent comparative reference, not assumed to be ABI- or data-compatible. The two RAR archives are purported edition-specific; verify each archive's contents, naming, signatures, versions, and compatibility with the corresponding APK. Do not mutate input files. Do not fabricate file names or contents. Do not change the mission to merely stabilizing an original binary.

## Autonomy, execution permissions, and guardrails

The operator explicitly allows unsandboxed Codex, administrative/root use, installation of tools, internet access needed for tools/documentation, multi-agent work, and local system configuration **in service of this project**. Use your actual session's permissions; project instructions do not elevate permissions. Prefer running ordinary analysis and build jobs as the unprivileged project owner; use root only for operations requiring it, e.g., package management. On Arch, avoid unsupported partial system upgrades; handle package upgrades/installs consistently. Detect and log all changes affecting the host. Ask no routine questions if answers can be established from evidence.

Permission is **not** a reason to be careless: never overwrite originals; never wipe or repartition disks, change unrelated security settings, destroy the operator's files, modify unrelated repositories, expose credentials, or deliberately break host networking/boot/login to accomplish an install. Do not upload APKs, OBBs, proprietary extracted code, credentials, or private filesystem content to third parties. Tool downloads and official documentation lookups are fine, but inspect remote installers before execution and avoid untrusted binary releases. Respect installation prompts or blockers from the actual Codex runtime; do not bypass them. Any risky experiment should occur in a disposable temp directory, container, VM, or Android test device, not on the host itself.

Unattended work: do not stop after a plan, summary, tool installation, partial decompilation, or one tested component if productive work remains and execution resources permit. When an avenue stalls, write the blocker and switch to the next independent task. Do not fabricate success or promise work after the current process ends. Do not rely on context retention: leave durable on-disk state continuously.

## Toolchain: install and operate it yourself

You own tool discovery, compatible installations, setup, and testing; no separate user-led setup step should be required unless a real privilege/authorization/hardware barrier occurs. Check actual versions, available disk/RAM, executable paths, and help outputs. Consult current official documentation for changing CLIs. Use the installed versions' real APIs, not guessed flags.

- **Codex CLI**: orchestrator, coding agent, review/test coordinator. Use high reasoning for semantic reconstruction and architecture; use scripts rather than model tokens for mass extraction. Available account/model/context/usage limits are finite (ChatGPT Plus); do not assume unlimited concurrency or token budgets.
- **Ghidra + PyGhidra**: authoritative persistent ELF, ARM/Thumb, DWARF, JNI, references, call graphs, and decompiler analysis. Prefer headless, restartable batch scripts and saved Ghidra project databases.
- **rev.ng**: secondary lifter/decompiler, LLVM/QEMU-based machine-code lifting and recompilable C generation. Install from verified official distribution if needed. Test against representative actual functions/binaries before using broadly. The output is evidence/intermediate source, **not** automatic completion.
- **JADX + Apktool**: recover Java/Smali/manifests/resources, identify Android lifecycle, JNI registration, library load sequence, input, permissions, packaging.
- **LLVM, binutils, readelf, nm, objdump, dwarfdump, pyelftools, LIEF, Capstone**: ELF/debug/relocations, symbols, disassembly cross-checks. Optional tools only if justified by concrete analysis problems: angr, RetDec, decompiler alternatives, emulators, symbolic execution, BSim.
- **Python virtual environment**, SQLite, pytest, structured JSON/JSONL and optional graph analysis: automation, evidence database, comparisons, reproducible testing.
- **C/C++17 or newer, CMake, Ninja, Android SDK command-line tools, Android NDK, Gradle/Android plugin as justified**: host-native tests and eventual independently built Android APK. Do not defer basic compile testing until a giant codebase has been generated.
- **Archive inspection**: 7zip/unrar-compatible tools, file signature detection, carefully bounded resource extraction, safe path handling.
- **ADB / Android device / emulator**: inspect availability; use only if working and authorized. Never assume an Android test environment exists. If unavailable, do static/oracle-independent tests and document pending integration verification.

Make `tools/setup/` contain a reproducible version-pinned bootstrap/install procedure (plus records of host package changes), `tools/check_environment.*` a repeatable health check, and `docs/toolchain.md` with exact verified paths, versions, and commands. Use a project-local `.venv` and `.cache` when possible. Be mindful of Ghidra-Java requirements and correct PyGhidra startup paths. If rev.ng has a restricted/limited distribution or unsupported feature, verify and report rather than hallucinating a CLI/API.

## Multi-agent working method

**Use parallel subagents when supported, but only on independent bounded assignments.** A good initial division: (A) Premium DWARF/symbol/native analysis, (B) Android JADX/manifest/JNI, (C) archive/asset format and edition comparisons. A later independent reviewer should audit reconstructed source and tests against binary evidence. Do not spin up agents merely to create activity; on a Plus plan, keep concurrency moderate (normally 2–3) and prefer higher-quality completion to redundant work. Follow the Codex version's actual agent/subagent capabilities; do not invent tool names or modify global config unnecessarily. The main agent owns overall integration, task priorities, final correctness, and merges. Every subagent receives: bounded inputs, designated output path, acceptance tests, time/resource budget, and a prohibition on editing another agent's owned files. Avoid parallel Ghidra imports of the same project database, parallel edits to the same files, and competing package installs. Subagent findings are **untrusted until checked**, just like generated decompiler output.

## Persisted project structure

Create as needed, not as empty theater:

```text
originals/                      # untouched APKs
Archive/                        # untouched RARs
analysis/                       # existing native extracts, plus controlled reference work
research/elf/                   # DWARF, symbol, import/export inventory
research/android/               # lifecycle, JNI, manifests
research/assets/                # OBB indices, format specimens, parser notes
research/architecture/          # verified diagrams and hypotheses
analysis_db/                    # SQLite index; schema/version/migrations
generated/ghidra/               # Ghidra databases and exports
generated/revng/                # lifting and generated C, intermediates
generated/jadx/                 # Java decompiler output
generated/apktool/              # decoded resources/Smali
tools/setup/                    # automated dependency bootstrap
tools/extract/                  # safe input indexing and materialization
tools/ghidra/                   # headless analysis/export scripts
tools/revng/                    # reproducible lift recipes
tools/index/                    # function/type/symbol indexing
tools/compare/                  # differential/behavioral verification
reconstruction/engine/          # source-owned native engine modules
reconstruction/android/         # source-owned Android application
reconstruction/tests/           # unit/property/integration/regression tests
reconstruction/third_party/     # source-controlled or reproducibly fetched OSS dependencies
docs/progress/                  # STATUS.md, NEXT.md, HANDOFF.md, SESSION_LOG.md
docs/decisions/                 # architecture decisions and blockers
build/                          # generated build artifacts
```

Maintain `.gitignore` to exclude original/proprietary blobs, enormous analysis caches, secrets, outputs and `.venv`. Initialize/maintain Git without destroying existing history. Commit small verified milestones if Git identity/config allows; otherwise leave the changes and report why. Use separate working trees or nonoverlapping paths for parallel agents.

## Evidence, provenance, and completion states

Build a local SQLite function and type index. Every item records binary hash, architecture, offset/virtual address (including ARM Thumb semantics), extraction/decompiler version, caller/callee/cross-references, imported or recovered symbols, type evidence, file paths, unresolved dependencies, provenance, and confidence. Stable IDs should survive repeated analysis. Capture failure/timeouts, not only successes.

Use these **strictly distinct** states:

1. `INVENTORIED`: bytes/metadata recorded; no functional recovery implied.
2. `DISASSEMBLED`: instructions/control flow found.
3. `DECOMPILED`: readable or generated C-like form obtained, not yet verified.
4. `UNDERSTOOD`: evidence-backed behavior, invariants, interfaces and side effects established.
5. `IMPLEMENTED`: new independently compiled source exists with mapped dependencies.
6. `TESTED`: relevant automated tests actually executed with results and fixtures.
7. `VERIFIED`: tested against original reference outputs, recorded behavior, validated structural invariants or other meaningful independent evidence; document scope of equivalence.

No inferred names/types/semantics may be reported as fact without evidence. Mark uncertain findings `INFERRED` and cite addresses/log excerpts. A function counting as decompiled never counts as a completed reconstructed module. Generated C with QEMU/rev.ng helpers and machine-address jump tables is interim evidence unless all helpers and semantics are owned and tested. Avoid calling smoke tests equivalence tests.

## Reconstruction process — keep executing, in order where dependencies require

### Stage 0 — baseline, input safety, actual architecture

1. Inspect tree and validate the SHA256 hashes of both APKs and native ELF extracts; list archive members **without extracting unchecked paths**, detect traversal/zip-bomb risks, record metadata.
2. Read Android package IDs/versionName/versionCode, min/target SDK, permissions, Activities, native library load orders and ABI selections. Verify each archive is matched to the correct edition. Identify missing OBBs/other data without guessing.
3. Examine ELF sections, `.symtab`, `.dynsym`, `.debug_*`, `.ARM.exidx`, relocations, linked libraries, compiler metadata, ARM/Thumb mapping symbols, possible symbol versioning and C++ mangled names. **Premium's debug information is a major opportunity:** inspect actual DWARF compilation units, source path names, line tables, class/struct types, globals, signatures, symbol counts. Do not assume reported `with debug_info` means full type data.
4. Baseline size, CPU time and disk requirements; choose scoped analysis settings. Output `research/architecture/BASELINE.md` with factual counts and command reproducibility.

### Stage 1 — fully automated and durable binary analysis

1. Install/validate Ghidra/PyGhidra and use persistent headless project(s). Analyze Premium's `libNOVA3_neon.so`, then JNI bridge and comparative Freemium binaries. Verify ARM/Thumb modes, relocations, pointers, import stubs and JNI thunk behavior.
2. Make reusable scripts to export **all discoverable functions in resumable batches**: addresses, sizes, disassembly, decompiler output, signatures/calling conventions, references, strings, globals, basic blocks/call graph, decompilation errors and evidence paths.
3. Populate SQLite function/type/class index. Query/test it. Make extraction incremental and idempotent; checkpoint against memory/timeouts and never re-decompile everything on every session.
4. Produce an actual engine startup/call sequence from `JNI_OnLoad`, `System.loadLibrary`, native registration, Android Activity, filesystem setup, render context creation, game-loop entry, asset managers. Distinguish observed from inferred edges.
5. Independently sample rev.ng lifting on relevant ARMv7 code, including Thumb/NEON, indirect calls, structures and loops; inspect generated dependencies and try compiling small recovered units. Retain it only if it demonstrably adds value. No lift system is permitted as the final runtime substitute for reconstructed source.
6. Find ABI signatures, structures, method tables/vtables, resource-loader dispatch, controller handling, network calls, audio/renderer entry points. Diagram evidence-backed subsystems.

### Stage 2 — reconstruct an end-to-end vertical slice EARLY

Do **not** spend the entire effort exporting millions of lines of decompilation without building anything. Choose the smallest genuine function chain that can compile and be tested independently: e.g., a real asset container header/index reader, checksum/decompressor, file-path mapping, or native math/serialization primitive. Choose only based on observed binaries/assets. Recover real structs, constants, byte ordering, allocation semantics, errors and meaningful edge cases. Implement it in C/C++ source, make an independent CMake target, add tests against original data/known outputs. Document exact function addresses and what remains unverified. Continue extending that slice into asset loading and engine initialization. Prioritize **working source** over beautiful placeholder architecture.

### Stage 3 — coverage expansion by real engine dependencies

Expand subsystem by subsystem, guided by the call/dependency graph and practical ability to run tests:

- Native startup, platform abstraction and JNI; C++ runtime behavior and ownership.
- Configuration, memory, logging, filesystem, package/OBB/resource access.
- Binary asset formats: archives, levels, textures, shaders, models, animation, fonts, audio, scripts/configuration; safe and validated readers.
- Engine scheduler, game loop, event dispatch, saved state and scene/campaign transitions.
- Input and controller handling, including touchscreen and modern controller adapters.
- Math, collision, physics, entities, navigation, weapons, damage, AI and scripted behavior.
- Rendering: graphics APIs, shaders, material semantics, lighting/bloom, frame timing, coordinate systems, UI/HUD and observable visuals.
- Audio, menus, persistence, progression, loading and other platform services.
- Multiplayer/network protocols and self-hostable server-side elements, if identified; do not invent interfaces or claim interoperability before validation.

For complex native code, use recovered structure layouts, callers, callee semantics, strings, test cases, disassembly and multiple decompilers. Rewrite modules into logically equivalent source, not superficial assembly annotations. It is acceptable to temporarily preserve awkward reconstructed control flow or original-looking variable names in **working source** until behavior is stabilized. Every substitute must have real, tested semantics. No empty success stubs, hardcoded fake game screens, prerecorded footage masquerading as rendering, or unrelated game-engine swap offered as reconstruction.

### Stage 4 — fully independent Android application and feature parity

Produce a reproducible Android build using our Java/Kotlin and C/C++ sources; regenerate all Android-executable code, JNI glue and native libraries from source. Assets are data and may be referenced from local licensed copies. Implement lifecycle, filesystem/scoped storage, audio, display/surface/input, graceful errors and packaging. Establish an emulator/device testing strategy where available. Target ARMv7 first if that yields the fastest verified parity, then port to ARM64 with deliberate pointer-width, alignment, ABI and NEON reviews. Attempt x86_64 host-native harnesses for portable units; don't assume Android UI can run there unchanged.

### Stage 5 — fidelity, stability and proof

Validate increasingly ambitious reproducible scenarios: binary parsing and deterministic algorithms, load a real level, render a real frame, input movement, combat, enemies, campaign progression, UI, sound, saves, networking. Record original-vs-new reference observations where legally and technically feasible: game-state snapshots, deterministic input traces, frame captures, hashes of uncompressed assets, audio output and timing ranges. Pixel-identical output across devices/drivers may be unattainable; measure deviations rather than inventing perfection. Characterize crashes with backtraces and minimized test cases, fix in source, add regressions. Don't label "the game works" until actual gameplay end-to-end tests exist.

## Build, test, and source-only enforcement

- Begin with working host-native CMake targets for recovered modules; wire Android builds when meaningful source exists.
- Provide a single documented reproduction path (`tools/setup/...`, `cmake`/`ctest`, later Gradle/NDK build) and CI-like scripts for repeatable local checks.
- Create `tools/verify_source_only.py` or equivalent to inspect the final APK, linker inputs, DEX/classes, imported libraries, source provenance, and bundled binary hashes. Its test must fail if any original proprietary game executable is linked, embedded, copied, or loaded. Android platform libraries and permitted source-built third-party libraries are not original game executables; do not erroneously reject them.
- Ensure each build from a clean checkout plus permitted, reproducibly fetched source and supplied local assets succeeds without requiring input APKs/native libraries as build or runtime dependencies. Reference binaries are permitted as **test oracles in separate research fixtures**, not production inputs.
- Test actual behavior, with adversarial/truncated/corrupt file inputs for parsers and fuzzing when it helps. Report test commands, exit codes, and what they actually demonstrate.

## Progress, time/resource discipline, and continuability

Keep these **always current**:

- `docs/progress/STATUS.md`: current milestone, verified facts, tangible results, builds/tests, incomplete work.
- `docs/progress/NEXT.md`: prioritized concrete executable task queue with exact commands/paths.
- `docs/progress/HANDOFF.md`: concise standalone resume directions and saved state; must work with no chat history.
- `docs/progress/SESSION_LOG.md`: append significant actual commands/results, failures, resources and decisions; link bulky machine logs rather than pasting them.
- `docs/decisions/BLOCKERS.md`: outstanding problems with evidence and attempted remedies.
- `docs/decisions/ARCHITECTURE.md`: substantial engineering decisions, tradeoffs, third-party source/license provenance.
- `docs/toolchain.md`: installed tools/versions and reproducibility.

After each milestone: run verification, save analysis indexes, update status/handoff, then move to the next task. Do not wait for operator guidance. With time, token, GPU, RAM, or disk constraints, favor meaningful working vertical slices and durable artifacts over endless analysis. If compaction/usage limits approach, checkpoint before interruption. Independent subagents must produce inspectable reports and verify their own assigned results. Avoid redundant tool installations, full-binary rereads, giant context dumps, or other wasteful work.

## Immediate autonomous assignment — do this on the first invocation

Do not merely repeat these instructions or write a plan. **Start executing**:

1. Verify the real project tree and input hashes, check storage/RAM/tool availability, preserve originals, initialize `.gitignore`/Git carefully.
2. Prepare verified tools and a reproducible bootstrap; if unavailable, continue work possible with present tools. Inspect both edition-specific RAR archives safely.
3. Prioritize Premium's debug symbols/DWARF: record actual surviving source information and possible C++ type recovery; produce evidence-backed counts.
4. In parallel **when supported and helpful**, delegate Android/JNI analysis and archive-format inventory to narrowly scoped subagents, keeping DWARF and main Ghidra work with the lead agent.
5. Implement and **run** Ghidra headless/PyGhidra import and batched export; create/test SQLite function and type index. Keep logs and failures.
6. Map JNI and engine startup and classify first recoverable dependency chain.
7. Test rev.ng on at least one realistic native example, but don't block source development waiting for it if Ghidra is already productive.
8. **Write, compile, and meaningfully test at least one authentic reconstructed native function/module** as soon as evidence permits, then continue expanding the first executable vertical slice.
9. Reassess next most valuable task from evidence and continue autonomously until a genuine execution barrier or session limit.

**Acceptance for the first meaningful checkpoint:** binary/asset inventory with matching hashes; actual DWARF quality assessment; reproducible Ghidra script successfully run; searchable real function index with provenance; JNI/startup map; first source-compiled recovered unit or precise evidence why not feasible yet; passing/failing test logs; and an accurate HANDOFF.md. These are checkpoint goals, not permission to stop while further work remains.

**When reporting:** separate inventory totals, analyzed/decompiled function totals, source-implemented totals, verified totals, and end-to-end game functionality. Always distinguish actual test evidence from forecasts. The measure of progress is **independent working game source**, not quantity of automatically generated pseudocode.


## NOVA3 TEAMKIT: distributed verified reconstruction
- Goal: source-based recovery of Android native and Java game behaviour, then separate modernization; NEVER APK patching or binary redistribution.
- Existing source, tests, history and analysis are valuable. Do not delete, reset, overwrite, rewrite or move them without clear evidence.
- Work ONLY within `Allowed paths:` in the assigned GitHub issue. Never change infrastructure, CI, credentials or unallocated files.
- The `analysis/`, `originals/`, `Archive/`, `research/` and `generated/` trees are PRIVATE, excluded from Git; reference them locally only. Never put binary excerpts, game assets, JADX dumps or private account data in commits, PR descriptions or AI prompts.
- Unknown function -> NOT RECONSTRUCTED. Decompiled pseudocode -> HYPOTHESIS. Compile and tests -> COMPILES/TESTED. Only independent evidence can establish behavioural equivalence or byte match. Original ABI/compiler/optimisation/link transformations matter.
- Use tests and build evidence. Prefer small well-scoped C++ with named structures and explicit uncertainty. Maintain checks for bounds, ownership, endian and alignment. Preserve APIs unless the issue requires change.
- All processes must support session expiry: write concise handoff to local private state, and resume in a new session. Stop repetitive failures.
- Do not launch child AI agents, install packages, run sudo, touch network accounts or merge PRs from an AI session.
- Provider execution permissions must remain scoped; never configure unrestricted root access for community workers.
- Further workflow details: docs/NOVA_TEAM.md.

## Object-matching research track (Discord workflow update — 2026-10-28)

Read `docs/OBJECT_MATCHING.md` before attempting translation-unit segmentation, recovered-reference objects, global-asm wrappers, relocations, linker scripts, objdiff, IDA Pro, or Irrlicht/JNI imports. The offline command `python -m nova3_team.object_matching` is an experimental bounded scaffold, NOT a verified original compiler pipeline. First fingerprint the target ELF and record actual compiler/ABI evidence. Group functions into explicitly hypothetical translation units; a linked `.so` cannot uniquely recover original `.o` files, their removed relocations or linker inputs. Per-function `.s`, synthesized objects, compiler profiles and objdiff reports must remain under ignored private local cache paths. A byte-identical synthetic-ASM build is not a source match or full-`.so` SHA match. Source C++, JNI and licensed/pinned Irrlicht components belong in independent source-only production targets only after their provenance and functionality are established.

IDA Pro is an optional local metadata producer. Preserve Ghidra as the free default, cross-check image rebasing, ELF bytes, ARM/Thumb state and function bounds before using either backend. Workers must not upload proprietary executable bytes, assembly, pseudocode or local game files to GitHub; published progress should contain only reviewed metadata and source-owned code/tests. Do not auto-merge. Never replace or downgrade existing independently tested reconstruction to global assembly as the final product.

## Fast-track resource and backend policy — 2026-10-28

The maintainer's Arch laptop is an Intel Celeron N5100 (4 cores, approximately 8 GiB RAM); IDA Pro 9.4 is installed at `/home/jason/ida-pro-9.4/ida`. Read `docs/FAST_TRACK.md` and `docs/OBJECT_MATCHING.md`. On local startup run the bounded read-only `python -m nova3_team.preflight` and fingerprint the Premium ELF offline before assuming a compiler. Use one heavyweight Ghidra **or** IDA idalib analysis process at a time and persistent databases; split AI work into independent bounded GitHub issues, not simultaneous local full-ELF analyses. IDA's optional headless exporter is `tools/ida/export_headless.py`; preserve Ghidra for contributors. Continue source-owned resource/material/first-frame reconstruction in parallel with compiler research. Do not promote a synthetic ASM object, an unknown compiler guess or a staged `.s` into production source. Irrlicht is not yet version-verified; official historical source SDKs require evidence-based selection and license audit before import. A GitHub worker cannot use private files until the maintainer starts it locally.

## Retrospective validation and donor-assisted matching mandate — 2026-10-28

The original compiler and Discord object-reconstruction protocol were researched *after* much of `reconstruction/recovered/` was written. Treat all earlier reconstruction as useful **candidate source** and revalidate it; do not bulk-delete it, silently award machine-code equivalence, or infer a correct compiler from successful host-only tests. Read `docs/LEGACY_REVALIDATION.md` and `docs/DISCORD_IMPLEMENTATION.md` before claiming source-level equivalence or staging a donor mapping. The Premium `.comment` GCC 4.4.3 strings are a compiler clue, not proven compiler flags, NDK version or linker. The Cyrus debug ELF is a local supporting donor; keep Premium retail as the primary target, and independently confirm function name/address/size/mode/bytes before transferring any hypothesis.

For each reviewed function, preserve separate claims for **source present**, **host tests passed**, **original behaviour compared**, **ARM object built with identified toolchain**, and **actual original instruction-window match**. A source-only regression or synthetic `.o` build never implies a byte match. Compile/compare repeatably with objdiff and an explicit relocation/mapping worksheet where evidence permits; preserve `NOT MATCHED` or `UNKNOWN` when it does not. ARM/Thumb state, all instruction bytes, exact reference SHA, and toolchain version/flags must be recorded for genuine byte-match claims. Whole-library SHA-256 equality is a separate final verification goal, not a prerequisite for behavioural source reconstruction and not presently established.

Worker tasks must have narrow file allowlists, bounded resource budgets, independently run tests, retained local handoffs and concrete acceptance criteria. Repeated AI session timeouts are **provider execution failures**, not proof that decompilation was impossible; preserve source edits and private diagnostic logs, stop retry storms and have a maintainer repair task scope before requeueing. An unattended worker must not erase checkpoint histories merely to bypass cooldowns. Prefer one local heavy analyzer at a time on the N5100. Keep all original APKs/ELFs/assembly/decompiler databases and proprietary instruction bytes out of GitHub commits, issues, PRs, action logs and shipped products.
