#!/usr/bin/env bash
# All outputs are source-built temporary files. No downloads or installs.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../../.." && pwd)
cd -- "$root"
ndk=${NOVA69_NDK:-/opt/android-ndk}
cxx=${CXX:-g++}
python=${NOVA69_PYTHON:-python3}
scratch=$(mktemp -d /tmp/nova69-verify.XXXXXX)
trap 'rm -rf -- "$scratch"' EXIT
source_file=reconstruction/recovered/libNOVA3_neon/block_000d4dbc.cpp
test_file=reconstruction/tests/recovered/libNOVA3_neon/block_000d4dbc_test.cpp
test_dir=reconstruction/tests/recovered/libNOVA3_neon/block_000d4dbc
host_flags=(-std=c++17 -O2 -ffp-contract=off -Wall -Wextra -Wpedantic -Werror
    -idirafter "$ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include")
arm_flags=(--target=armv7a-linux-androideabi21 -std=c++17 -O2 -marm -mfpu=neon
    -mfloat-abi=softfp -ffp-contract=off -ffunction-sections -fomit-frame-pointer
    -fno-exceptions -fno-rtti -Wall -Wextra -Wpedantic -Werror)
"$cxx" --version
"$cxx" "${host_flags[@]}" -c "$source_file" -o "$scratch/source.o"
"$cxx" "${host_flags[@]}" "$test_file" -o "$scratch/test"
"$scratch/test"
"$cxx" "${host_flags[@]}" -DNDEBUG "$test_file" -o "$scratch/release-test"
"$scratch/release-test"
clang++ -std=c++17 -O1 -g -ffp-contract=off -Wall -Wextra -Wpedantic -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie -no-pie \
    -idirafter "$ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include" \
    "$test_file" -o "$scratch/sanitize-test"
ASAN_OPTIONS=detect_leaks=0 "$scratch/sanitize-test"
arm_cxx="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++"
arm_ld="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/ld.lld"
"$arm_cxx" --version
"$arm_cxx" "${arm_flags[@]}" -c "$source_file" -o "$scratch/arm.o"
"$arm_ld" -T "$test_dir/wrappers.ld" --gc-sections "$scratch/arm.o" \
    -o "$scratch/wrappers.elf"
sha256sum "$source_file" "${source_file%.cpp}/recovered.hpp" "$test_file" \
    "$test_dir/oracle.py" "$test_dir/wrappers.ld" "$scratch/arm.o" "$scratch/wrappers.elf"
if [[ $# == 0 ]]; then
    printf '%s\n' 'Reference comparison NOT RUN; NOT MATCHED / behavioural equivalence UNVERIFIED in this run.'
elif [[ $# == 2 && $1 == --reference ]]; then
    "$cxx" "${host_flags[@]}" -fPIC -shared "$test_file" -o "$scratch/test.so"
    "$python" "$test_dir/oracle.py" --reference "$2" --candidate "$scratch/test.so" \
        --arm-object "$scratch/arm.o" --linked-wrappers "$scratch/wrappers.elf"
else
    printf '%s\n' 'usage: bash verify.sh [--reference /local/path/libNOVA3_neon.so]' >&2
    exit 2
fi
