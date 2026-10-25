#!/usr/bin/env bash
# Host source-only regression suite. Proprietary references are optional oracles.
set -euo pipefail
cd "$(dirname "$0")/.."
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
.venv/bin/python -m pytest -q reconstruction/tests/test_source_only.py tools/extract/test_archive_inspection.py
.venv/bin/python tools/setup/record_build_provenance.py
for artifact in nova3_crc nova3_scramble nova3_zip_header nova3_zip_index nova3_zip_extract nova3_resource nova3_bres_relocate nova3_package_probe nova3_mesh nova3_texture nova3_etc1 nova3_texture_path nova3_scrambled_zip libnova3_core.a; do
    .venv/bin/python tools/verify_source_only.py "build/host/$artifact" --manifest build/host/provenance.json
done
if test "${NOVA3_RESEARCH_ORACLES:-0}" = 1; then
    .venv/bin/python tools/compare/crc_oracle.py
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
