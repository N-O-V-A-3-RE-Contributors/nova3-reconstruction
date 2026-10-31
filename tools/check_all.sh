#!/usr/bin/env bash
# Host source-only regression suite. Proprietary references are optional oracles.
set -euo pipefail
cd "$(dirname "$0")/.."
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
.venv/bin/python -m pytest -q reconstruction/tests/test_source_only.py tools/extract/test_archive_inspection.py
.venv/bin/python tools/setup/record_build_provenance.py
for artifact in nova3_crc nova3_scramble nova3_zip_header nova3_zip_index nova3_zip_extract nova3_resource nova3_bres_relocate nova3_package_probe nova3_mesh nova3_texture nova3_etc1 nova3_texture_path nova3_scrambled_zip libnova3_core.a cpu_features_behaviour_test cpu_features_init_test detect_neon_behaviour_test; do
    .venv/bin/python tools/verify_source_only.py "build/host/$artifact" --manifest build/host/provenance.json
done
if test "${NOVA3_RESEARCH_ORACLES:-0}" = 1; then
    .venv/bin/python tools/pipeline/auto_decompile.py --status
    .venv/bin/python tools/compare/crc_oracle.py
    .venv/bin/python tools/compare/cpufeatures_pilot.py --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so --out .cache/cpufeat/pilot/check_all.json
    .venv/bin/python tools/compare/detect_neon_pilot.py --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so --out .cache/cpufeat/detectneon/check_all.json
    .venv/bin/python tools/compare/cpuquery_callsites.py --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so --also 0x9dad14:DetectNeon --out .cache/cpufeat/cpuquery_callsites/check_all.json
    .venv/bin/python tools/compare/cpuquery_pilot.py --out .cache/revalidation/cpuquery-pilot/check_all.json
    .venv/bin/python tools/compare/scramble_oracle.py
    .venv/bin/python tools/compare/zip_headers.py
    .venv/bin/python tools/compare/zip_payload.py
    .venv/bin/python tools/compare/bres_source.py
    .venv/bin/python tools/compare/lookup_source.py
    .venv/bin/python tools/compare/etc1_oracle.py
    .venv/bin/python tools/compare/texture_path_oracle.py
    .venv/bin/python tools/compare/texture_path_source.py
    .venv/bin/python tools/index/check_index.py
fi
