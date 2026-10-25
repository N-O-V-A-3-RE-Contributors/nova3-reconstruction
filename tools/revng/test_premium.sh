#!/usr/bin/env bash
# Research-only: decompile reference bytes, never run or package them.
set -u
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_dir"
mkdir -p .cache/revng-test
set +e
timeout 90 bash -c 'ulimit -v 1048576; ulimit -f 131072; exec .cache/revng-c865bc3/revng-public-demo/revng quick artifact emit-c analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so -o .cache/revng-test/premium.c.yml' > .cache/revng-test/premium.log 2>&1
code=$?
printf "%s\n" "$code" > tools/revng/arm_test.exit
exit "$code"
