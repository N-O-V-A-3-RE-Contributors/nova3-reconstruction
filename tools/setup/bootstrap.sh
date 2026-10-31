#!/usr/bin/env bash
# Run as project owner. No host package installation or Arch partial upgrade.
set -euo pipefail
cd "$(dirname "$0")/../.."
for command_name in python3 cmake ninja c++ readelf llvm-objdump java; do
    command -v "$command_name" >/dev/null
done
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/setup/requirements.txt
.venv/bin/python tools/check_environment.py > .cache-environment.json
ghidra_home=$(.venv/bin/python tools/pipeline/ghidra_env.py | sed -n 's/^ghidra_install_dir=//p')
if [ -z "$ghidra_home" ] || [ "$ghidra_home" = "None" ]; then
    echo "note: no Ghidra found — './nova3 build|test' works; './nova3 decompile' needs it." >&2
    echo "      install Ghidra or export NOVA3_GHIDRA_HOME=/path/to/ghidra" >&2
else
    echo "ghidra: $ghidra_home"
fi
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
echo
echo "setup complete. Next: ./nova3 doctor | ./nova3 index | ./nova3 decompile"
