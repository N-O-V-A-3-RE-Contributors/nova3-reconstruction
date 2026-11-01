
<<<<<<< HEAD
<!-- NOVA3-WORKER-LAUNCHER -->
## Run or contribute with the worker (DeepSeek Harness)
=======
<!-- NOVA3-WORKBENCH -->
## Decompile NOVA 3 — one command

```sh
./nova3 doctor        # PASS/WARN/FAIL per dependency, with the fix for each
./nova3 index         # build analysis_db/index.sqlite (once)
./nova3 decompile     # bounded, resumable Ghidra batch
./nova3 show 0x009dad14
./nova3 status
```

Full guide: [docs/DECOMPILE.md](docs/DECOMPILE.md) (workflow, evidence states,
troubleshooting, and how incoming third-party source will be audited).
The workbench only orchestrates existing scripts; it never modifies the
reference inputs and never commits decompiler output.

<!-- NOVA3-EASY-LAUNCHER -->
## Join or start the reconstruction worker
>>>>>>> c840ee2

**One-click setup for Linux, macOS (Intel/Apple silicon) and Windows:**
[download the worker release](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/releases),
unpack it and run `START.sh` / `START.command` / `START.cmd`. From a Git
checkout, run `./START-LINUX.sh` (or the matching `START-*` launcher).

The first run installs everything for your own user account, then asks for
**your own** DeepSeek (or OpenRouter) API key — it is never stored by the
installer, and nothing is billed to anyone else. Afterwards `nova3-dsh`
continues the work unattended and `nova3-dsh status` shows what is happening.

Contributors without repository Git access can use the source-only
`nova3-source.tar.gz` asset (`./install.sh` / `install.cmd`).

Full details: [docs/WORKER_LOOP.md](docs/WORKER_LOOP.md). The previous
Codex/Claude/Gemini launcher still works via `./START-LINUX.sh --legacy-workflow`.

<!-- NOVA-CONTRIBUTOR-QUICKSTART -->
## Contribute with your own AI

**[Windows, macOS and Linux: contributor quickstart](docs/START_HERE.md)**

Download the repository ZIP and run the `START-*` launcher for your operating system, or use `python3 nova3_start.py`. GitHub access and a Codex/Claude/Gemini coding CLI are required. Original game binaries and assets are not included.

# N.O.V.A. 3 independent source reconstruction

This is an early reconstruction, not a playable game. It contains source-owned CRC32, repeating-XOR and ZIP local-header/index traversal and bounded payload decoder modules, with pinned source-built zlib. Original executable code is used only in isolated offline research oracles and is never a production build input.

Read [status](docs/progress/STATUS.md), [handoff](docs/progress/HANDOFF.md), and [next tasks](docs/progress/NEXT.md) before continuing. Exact evidence is under research/. Proprietary reference inputs and generated decompiler/project databases are excluded from Git.


### Where your game files go (contributors and owner)

Game files are never shipped. Put the two APKs you own in `materials/apk/` and
the expansion archives or extracted `.obb` files in `materials/obb/` (owner
checkouts may keep using `originals/` and `Archive/`), then run:

```sh
python tools/extract/find_materials.py     # what it found + the exact next commands
```

A pre-released source tree dropped in `source-release/` next to the checkout is
reported there too.

Build without any supplied game files:

```sh
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
```

Prepared-host setup and extended verification:

```sh
bash tools/setup/bootstrap.sh
bash tools/check_all.sh
# Optional, offline proprietary-reference tests, on this workspace only:
NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh
.venv/bin/python tools/compare/zip_index.py
.venv/bin/python tools/compare/zip_payload.py
```

See [toolchain](docs/toolchain.md) for Android ARMv7 compilation, sanitizer commands, tool versions and setup limits. No Android APK or gameplay has been implemented. All source mappings carry limited verified scopes; unsupported ZIP descriptors/ZIP64 and flags are rejected.


## Compiler-matched object research (experimental)

See [offline object-matching and IDA/Ghidra workflow](docs/OBJECT_MATCHING.md) for ELF compiler fingerprinting, tentative translation units, private per-function assembly, global-asm C++ scaffolding and objdiff. This is a research-only loop; original `.o` files and a whole-`.so` hash match have **not** been recovered. The existing source-only CMake targets remain authoritative.


For the fastest local-first route, see [Fast-track operator plan](docs/FAST_TRACK.md), including IDA Pro 9.4 headless analysis, hardware-aware batching, compiler preflight and source-built first-frame milestones. Run `python -m nova3_team.preflight` after synchronizing that workflow branch.
