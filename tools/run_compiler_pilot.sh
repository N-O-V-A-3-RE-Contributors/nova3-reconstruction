#!/usr/bin/env bash
# Offline ARM32 source-compiler comparison against a *real* Premium code window.
# This is a machinery/ABI negative control, not historical compiler proof.
set -euo pipefail

PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

RETAIL="${NOVA3_RETAIL_ELF:-analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so}"
EXPECTED="84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
SOURCE="reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp"
COMPILER="${NOVA3_PILOT_CXX:-}"
if [[ -z "$COMPILER" ]]; then
  NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-/opt/android-ndk}}"
  COMPILER="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++"
fi
if [[ ! -f "$RETAIL" || ! -f "$SOURCE" ]]; then
  echo 'Pilot requires the unchanged local Premium ELF and recovered CPU-query C++ source.' >&2
  exit 2
fi
if [[ ! -x "$COMPILER" ]]; then
  echo "No executable Android ARM32 compiler at: $COMPILER" >&2
  echo 'Install/select an Android NDK, or set NOVA3_PILOT_CXX to its clang++ executable.' >&2
  exit 2
fi
actual="$(sha256sum -- "$RETAIL")"
if [[ "${actual%% *}" != "$EXPECTED" ]]; then
  echo 'Premium reference SHA-256 differs from the verified reference; stopping.' >&2
  exit 2
fi

mkdir -p .cache/revalidation
RUN="$(mktemp -d .cache/revalidation/compiler-pilot-XXXXXXXX)"
echo "Pilot evidence directory: $RUN"
python3 -m nova3_team.object_matching profile "$COMPILER" \
  --flag=--target=armv7a-linux-androideabi21 \
  --flag=-marm --flag=-O2 --flag=-fPIC --flag=-std=c++17 \
  --out "$RUN/compiler-profile.json"
python3 -m nova3_team.compiler_matrix \
  --reference "$RETAIL" --source "$SOURCE" \
  --symbol nova3_recovered_cpu_count --address 0xd2f10 \
  --profile "$RUN/compiler-profile.json" --out "$RUN/matrix"

python3 - "$RUN/matrix/matrix.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
result = json.loads(path.read_text(encoding='utf-8'))
for entry in result.get('experiments', []):
    print('ARM C++ compile:', entry.get('compile_exit_code', 'NO OBJECT'),
          '| comparison:', entry.get('status', 'UNKNOWN'))
if not any('comparison' in x for x in result.get('experiments', [])):
    print('BLOCKED: no ARM candidate object was compared. Inspect local toolchain.')
    print('Private report:', path)
    sys.exit(3)
print('Source matching independently verified: NO')
print('ABI caveat: this recovered context-pointer function is not the original')
print('no-argument global-state interface; a byte mismatch is expected.')
print('Private report:', path)
PY
