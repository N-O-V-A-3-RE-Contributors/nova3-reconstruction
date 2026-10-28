# Task #94: bounded ARM32 compiler / ABI pilot

2026-10-28. **NOT MATCHED; source equivalence NOT_VERIFIED.** One query was
investigated; the existing portable recovery was preserved. No production
integration, original executable execution, package installation, network
access or GitHub mutation occurred. PR submission is left to the supervisor
because this session explicitly prohibits GitHub changes.

The source-owned no-argument candidate compiles as ARM32 ET_REL. Five host test
processes pass. This is not an original ARM runtime/oracle test, historical
compiler identification, full-library match or working game milestone.
Machine-readable hashes, commands, return codes and independent grades are in
`coordination/evidence/compiler-pilot-000d2f10.json`.

## Reference and independent mapping

Premium `libNOVA3_neon.so` SHA-256:
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`.
ELF32 little-endian ARM ET_DYN. Both symbol tables identify
`android_getCpuCount` at ELF VA `0x000d2f10`, size 44. File-backed PT_LOAD
mapping independently gives file offset `0x000d2f10`. Window SHA-256:
`435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca`.

A fresh Ghidra 12.1.2 / PyGhidra 3.2.0 import performed no global autoanalysis.
Its image base is `0x10000`; Ghidra address `0x000e2f10` maps to the same
44 bytes, with `TMode=0` (A32). The selected function was disassembled and
decompiled; no pseudocode or instructions are published here. Ghidra's imported
language is `ARM:LE:32:v8`; target attributes separately report ARMv7.

Independent Capstone/ELF checks establish this bounded chain:

- Call at `0x000d2f28` targets PLT `0x000d2910`. Decoding that stub's address
  calculation leads to GOT `0x00d82e5c`, whose `.rel.plt` R_ARM_JUMP_SLOT names
  `pthread_once`.
- The call receives once storage `0x00d96598` and initializer `0x000d2f98`.
  ELF local symbols identify four-byte `g_once` and `android_cpuInit`.
- The return reads four bytes at `0x00d9659c` (`g_cpuCount`) after the call;
  the once return status is unused. Literal slots are `0x000d2f34` and
  `0x000d2f38`. Initializer behavior remains unreconstructed.

The new struct groups the adjacent once/count storage as an explicit
source-owned layout hypothesis. Hidden dependency declarations model the
original local binding. This does not establish an original C++ struct type,
original TU membership or general C++ ABI compatibility. Query linkage is C,
with no arguments and a 32-bit result; the candidate has no CPU-detection stub.

## Compiler experiments

NDK r29 `29.0.14206865`, Android Clang 21.0.0 (build 13989888, r563880c),
resolved executable `/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-21`:
SHA-256 `d326ed4fc4f71e5315b9ce388fc23ec792f81652ca7b29e66294de6ed2520844`.
It is an exploratory compiler, not historical GCC.

| Actual C++ input / profile | ARM size | Relocations in window | Raw comparison |
| --- | ---: | ---: | --- |
| Existing `CpuQueryContext*` negative control, pilot defaults | 36 | 0 | FUNCTION_SIZE_MISMATCH |
| New no-argument candidate, explicit softfp/NEON, default frame pointer | 48 | 3 | FUNCTION_SIZE_MISMATCH |
| Same candidate, add `-fomit-frame-pointer` | 44 | 3 | FUNCTION_BYTES_DIFFERENT |

New-candidate common flags:
`--target=armv7a-linux-androideabi21 -marm -mfloat-abi=softfp -mfpu=neon -O2 -fPIC -std=c++03`.
The matrix adds `-c -x c++ SOURCE -o OBJECT`. API 21, flags, hidden dependency
binding and data grouping are candidate choices, not recovered original flags.
Modern Clang and current Bionic headers do not establish historical pthread or
C++ runtime compatibility. No C++ runtime operations are used by this query.

The 44-byte ET_REL differs at byte offsets 24, 25, 36, 37, 38 and 40.
All fall inside its three four-byte relocation sites: R_ARM_CALL at `0x18`
(`pthread_once`), R_ARM_REL32 at `0x24` (state), R_ARM_REL32 at `0x28`
(initializer). Bytes outside these sites agree, a diagnostic observation only.
**The primary comparison excludes nothing and applies no relocations.** No
original pre-link relocations were recovered, no link/address substitution was
performed, and the full raw window is unequal. EHABI unwind relocations outside
the window are also present and unvalidated. No objdiff run is claimed.

The original `.comment` contains 968 occurrences of `GCC: (GNU) 4.4.3`.
Attributes report ARMv7, VFPv3, NEONv1, four-byte wchar/enum and eight-byte
stack alignment. Absence of an explicit VFP-argument attribute is insufficient
to establish every TU's float ABI. Dynamic dependencies include `libstdc++.so`;
its exact source/runtime version and original C++ ABI remain unknown. No GCC
4.4.3 ARM executable was found in the checked PATH/NDK locations (`/opt` has
only the modern NDK); this was not an exhaustive filesystem search. Original
compiler executable/hash, NDK revision, optimization flags, assembler/linker
and pre-link relocation history remain UNKNOWN.

## Reproduction and actual validation

Run from this checkout with `NOVA3_RETAIL_ELF` set to the read-only local
Premium path. Use existing installed tools; nothing needs downloading.

```sh
NOVA3_RETAIL_ELF="$NOVA3_RETAIL_ELF" bash tools/run_compiler_pilot.sh
python3 -m nova3_team.object_matching fingerprint "$NOVA3_RETAIL_ELF" \
  --out .cache/premium-compiler-evidence.json
```

Both exited 0. The negative control is at
`.cache/revalidation/compiler-pilot-FXQcNkia/matrix/matrix.json`.
Preflight initially found 0/4 references in the disposable checkout, then 4/4
when given the actual reference project with `--root`; both exited 0.

The private task handoff is `.cache/revalidation/task94/`. It retains
`verify_reference.py`, `check_plt.py`, metadata, compiler profiles, actual
objects, host test binary and test results. These must not be published.
The Ghidra database is `/tmp/nova3-task94-ghidra/task94.gpr`. Run the saved
verification with the reference project's existing `.venv/bin/python`.
Three setup attempts exited 1: Java-home save outside the sandbox, a Ghidra
project path containing `.cache`, and AWT requiring headless mode. The final
attempt exited 0 after explicitly selecting Java 21, placing the disposable
project under `/tmp`, setting private application settings and
`-Djava.awt.headless=true -Xmx1536m`. The maintainer's database was untouched.

To regenerate the two profiles, run `object_matching profile` with the
compiler above and one `--flag=VALUE` per common flag; write
`.cache/revalidation/task94/clang-default-frame.json` and
`clang-omit-frame.json` (add `--flag=-fomit-frame-pointer` to the latter).
Use a new/empty output directory for each matrix:

```sh
python3 -m nova3_team.compiler_matrix --reference "$NOVA3_RETAIL_ELF" \
  --source reconstruction/research/arm32_cpu_query_candidate.cpp \
  --symbol nova3_arm32_cpu_count_candidate --address 0xd2f10 \
  --profile .cache/revalidation/task94/clang-default-frame.json \
  --profile .cache/revalidation/task94/clang-omit-frame.json \
  --compile-timeout 30 --out .cache/revalidation/task94/matrix
```

Exit 0; both compiles exited 0. A separate `object_compare` invocation with
`--expected-reference-sha256` and `matrix/candidate_02.o` also exited 0 and
reported FUNCTION_BYTES_DIFFERENT. Comparator exit 0 means the check ran,
not that its windows matched.

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -pedantic -pthread \
  reconstruction/research/arm32_cpu_query_candidate.cpp \
  reconstruction/tests/research/arm32_cpu_query_candidate_test.cpp \
  -Wl,--wrap=pthread_once -o .cache/revalidation/task94/cpu-query-test
for mode in positive zero negative minimum maximum; do
  .cache/revalidation/task94/cpu-query-test "$mode" || exit
done
python3 -m unittest nova3_team.tests.test_compiler_matrix \
  nova3_team.tests.test_object_compare -v
```

Host GCC 16.2.1: compilation and all five processes exited 0. Each process
checks 1,024 concurrent queries with real pthread_once, one initializer
execution, exact dependency arguments, a later changed-cache read and injected
once-error handling. Values cover positive, zero, negative and signed limits.
The initializer is test-owned. Eight comparison-harness regression tests pass;
these are not reference behavioral tests. No ARM device execution occurred.

## Handoff / smallest next experiment

Keep `NOT MATCHED` and `NOT_VERIFIED`. This task has one source candidate with
host-tested dependency behavior and real ARM objects; zero independently
verified ARM runtime functions and zero whole-library/gameplay claims.

Next: review the three relocation records and perform one explicitly mapped
scratch link/relocation experiment using the evidenced function, PLT, state
and initializer addresses. Record its separate linked-window result without
calling it original object recovery. Select and fingerprint a locally supplied
historical GCC candidate before comparing compiler provenance; do not install
one or broaden the decompilation wave from these Clang results. Preserve the
portable context ABI and all existing recovery blocks.
