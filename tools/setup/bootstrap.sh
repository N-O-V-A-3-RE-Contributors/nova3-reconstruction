#!/usr/bin/env bash
# Run as project owner. No host package installation or Arch partial upgrade.
set -euo pipefail
cd "$(dirname "$0")/../.."
for command_name in python3 cmake ninja c++ readelf llvm-objdump java; do
    command -v "$command_name" >/dev/null
done
test -d /opt/ghidra
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/setup/requirements.txt
.venv/bin/python tools/check_environment.py > .cache-environment.json
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
