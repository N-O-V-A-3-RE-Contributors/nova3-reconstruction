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
