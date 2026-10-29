
<!-- NOVA3-EASY-LAUNCHER -->
## Join or start the reconstruction worker

[Contributor one-click guide](docs/ONE_CLICK_CONTRIBUTORS.md) · [Maintainer GitHub setup](docs/MAINTAINER_GITHUB_SETUP.md)

On the maintainer's Linux checkout, run `bash START-LINUX.sh` to start or resume. Windows, macOS and Linux downloadable launchers are generated through the GitHub Actions **Build contributor launchers** workflow and appear under **Releases** after the maintainer creates a launcher tag. Private repository contributors must accept an invitation before downloading.

First-run `START-LINUX.sh` / `nova3_easy.py` setup offers optional historical
**ARM GCC 4.4.3 candidate** guidance. Use
`python3 nova3_easy.py --historical-toolchain instructions` to view it offline,
or `--historical-toolchain skip` to continue ordinary host-only worker tasks.
The NOVA3 Control Center installer (`bash tools/observatory/install.sh`) advertises its
**H / TOOLCHAIN** page and `nova3-control --toolchain-setup`
(`nova3-observer` remains a compatibility alias); watching tasks
requires no compiler. Nothing downloads or executes GCC automatically.
See [no-sudo XDG installation and publisher archive checks](docs/progress/HISTORICAL_TOOLCHAIN.md).
NDK r8e is a hypothesis: select GCC 4.4.3 explicitly rather than its GCC 4.6
default. Source matching is **NOT PROVEN**, with zero independently verified
original object/source matches. The separate `nova3_start.py` and direct worker
CLI do not show this new step; open the same offline guidance when using them.


<!-- NOVA-CONTRIBUTOR-QUICKSTART -->
## Contribute with your own AI

**[Windows, macOS and Linux: contributor quickstart](docs/START_HERE.md)**

Download the repository ZIP and run the `START-*` launcher for your operating system, or use `python3 nova3_start.py`. GitHub access and a Codex/Claude/Gemini coding CLI are required. Original game binaries and assets are not included.

# N.O.V.A. 3 independent source reconstruction

This is an early reconstruction, not a playable game. It contains source-owned CRC32, repeating-XOR and ZIP local-header/index traversal and bounded payload decoder modules, with pinned source-built zlib. Original executable code is used only in isolated offline research oracles and is never a production build input.

Read [status](docs/progress/STATUS.md), [handoff](docs/progress/HANDOFF.md), and [next tasks](docs/progress/NEXT.md) before continuing. Exact evidence is under research/. Proprietary reference inputs and generated decompiler/project databases are excluded from Git.

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
