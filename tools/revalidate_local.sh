#!/usr/bin/env bash
# Private, sequential Premium/Cyrus research checkpoint for Jason's Arch box.
# No GitHub mutation, original-code redistribution, tool installation or
# implicit whole-.so/ABI/compiler-match claim.
set -euo pipefail

PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

RETAIL="${NOVA3_RETAIL_ELF:-analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so}"
CYRUS="${NOVA3_CYRUS_ELF:-.cache/cyrus-debug/libNOVA3_neon.so}"
OUT="${NOVA3_REVALIDATION_OUT:-.cache/revalidation}"
PREMIUM_SHA="84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
CYRUS_SHA="d6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167"

if [[ "$OUT" != .cache/* || "$OUT" == .cache/ || "$OUT" == *'..'* ]]; then
  echo 'Output must be a relative subdirectory under ignored .cache/' >&2
  exit 2
fi
for path in "$RETAIL" "$CYRUS"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing private ELF: $path" >&2
    echo 'Set NOVA3_RETAIL_ELF / NOVA3_CYRUS_ELF if using different local paths.' >&2
    exit 2
  fi
done

check_sha() {
  local actual
  actual="$(sha256sum -- "$1")"
  actual="${actual%% *}"
  if [[ "$actual" != "$2" ]]; then
    echo "Reference checksum mismatch: $1" >&2
    echo "Expected: $2" >&2
    echo "Observed: $actual" >&2
    exit 2
  fi
}

check_sha "$RETAIL" "$PREMIUM_SHA"
check_sha "$CYRUS" "$CYRUS_SHA"
mkdir -p -- "$OUT"

echo '[1/6] Revalidating the existing C++ source inventory (metadata only)'
python3 -m nova3_team.legacy_validation --repo . \
  --out "$OUT/legacy-inventory.json" \
  --plan-out "$OUT/legacy-worklist.json"

echo '[2/6] Comparing Premium and Cyrus debug ELF metadata'
python3 -m nova3_team.version_compare --retail "$RETAIL" \
  --debug "$CYRUS" --out "$OUT/cyrus-vs-premium.json"

echo '[3/6] Ranking donor function/TU hypotheses'
python3 -m nova3_team.donor_mapping \
  --report "$OUT/cyrus-vs-premium.json" \
  --out "$OUT/donor-plan.json" --limit 64

echo '[4/6] Recording linked relocations and experimental layout'
python3 -m nova3_team.linker_worksheet --reference "$RETAIL" \
  --out "$OUT/premium-linker-worksheet.json" --experimental-ld

echo '[5/6] Fingerprinting original compiler/ARM attributes'
python3 -m nova3_team.object_matching fingerprint "$RETAIL" \
  --out "$OUT/premium-compiler-evidence.json"

echo '[6/6] Staging a bounded donor-guided research unit (synthetic only)'
python3 -m nova3_team.object_matching stage "$RETAIL" \
  --out "$OUT/objmatch" --donor-plan "$OUT/donor-plan.json" \
  --limit 16

echo
echo "Local evidence ready under: $OUT"
echo 'No real C++ ARM instruction match, historical compiler or original'
echo 'object/relocation/full-ELF hash equivalence has been verified by this setup.'
echo 'Next: profile a candidate compiler; compile actual C++ and compare one'
echo 'source function through nova3_team.compiler_matrix / object_compare.'
