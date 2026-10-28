# Tasks #94 / #97: bounded ARM32 compiler / ABI pilot

**Current #97 result: RAW_MISMATCH; separate RELOCATED_LINKED_WINDOW_MATCH
over all 44 bytes.** Source equivalence, historical compiler, original object,
whole-library matching and original behavior remain unverified. The #94
baseline below is retained; the #97 experiment and reproduction follow it.

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

## Task #94 handoff (relocation experiment completed by #97 below)

Keep `NOT MATCHED` and `NOT_VERIFIED`. This task has one source candidate with
host-tested dependency behavior and real ARM objects; zero independently
verified ARM runtime functions and zero whole-library/gameplay claims.

At the #94 checkpoint, the next experiment was to review the three relocation records and perform one explicitly mapped
scratch link/relocation experiment using the evidenced function, PLT, state
and initializer addresses. Record its separate linked-window result without
calling it original object recovery. Select and fingerprint a locally supplied
historical GCC candidate before comparing compiler provenance; do not install
one or broaden the decompilation wave from these Clang results. Preserve the
portable context ABI and all existing recovery blocks.

## Task #97: relocation-aware linked-window experiment

2026-10-28. One function only. Both C++ candidate files, the host test and the
portable `CpuQueryContext*` implementation remain unchanged. Modern NDK r29
Clang again produced the same 44-byte A32 ET_REL function and object hash as
#94. Its **primary, unmodified raw comparison is still RAW_MISMATCH**, with
six differing bytes at offsets 24, 25, 36, 37, 38 and 40. No bytes are masked.

The new `python3 -m nova3_team.relocation_probe` independently reads actual
ELF REL records and their implicit addends. It rejects unsupported function
relocations, unresolved/unused symbol mappings, bad offsets, duplicate sites,
branch overflow, BLX/Thumb calls, malformed/non-ARM/non-ELF inputs and ambiguous
file mappings. Its supported function scope is aligned A32, external symbols,
R_ARM_CALL and R_ARM_REL32; it does not reconstruct a general linker. ELF32
EABI5 is required. The linked/reference inputs must have a unique executable
file-backed PT_LOAD mapping agreeing with their symbol/section mapping.

The read-only private reference check independently reconfirmed the full ELF
and window hashes above, symbol extent, section and segment mappings, and the
nearest `$a` mapping symbol. Capstone independently decoded the bounded call
and literal-address calculations. PLT `0x000d2910` resolves through GOT
`0x00d82e5c` to the retained R_ARM_JUMP_SLOT for `pthread_once`. No retained
dynamic relocation targets the 44-byte query window; the reference's original
pre-link relocations remain unavailable.

| Candidate site | Actual REL type | Implicit addend A | Explicit scratch symbol S | Place P |
| --- | --- | ---: | --- | --- |
| +0x18 | R_ARM_CALL | -8 | pthread_once = 0x000d2910 | 0x000d2f28 |
| +0x24 | R_ARM_REL32 | 16 | nova3_arm32_cpu_state = 0x00d96598 | 0x000d2f34 |
| +0x28 | R_ARM_REL32 | 16 | nova3_arm32_cpu_initialize = 0x000d2f98 | 0x000d2f38 |

For CALL, sign-extend the instruction's imm24 shifted left two bits, then
encode `S + A - P`. The implicit -8 already accounts for the A32 PC pipeline;
subtracting another eight would be wrong. Direct ARM BL reach/alignment are
checked, and overflow/interworking requiring a veneer is rejected. REL32 uses
`((S + A) | T) - P` modulo 2^32 without an overflow check; T is the STT_FUNC
Thumb bit removed from S before addition. All three actual mapped targets are
even/A32. The two +16 addends bridge each literal's place to the PC base of its
consumer: `0x000d2f24` and `0x000d2f28`, respectively. Those bases and literal
values were checked independently against the original instructions locally.

LLD 21.0.0 linked **only the source-compiled candidate object**, using explicit
absolute symbols at these addresses. This models the query's destination at
the observed PLT entry; it does **not** regenerate the original PLT, GOT,
dynamic binding, initializer or data implementation. The scratch ELF is an
unexecuted research ET_EXEC, not a functioning program or production input.
No original ELF bytes were inserted, and no reference bytes were patched.

The independent linker mapped its function to file offset `0x2f10`, while the
reference maps to `0xd2f10`. Comparing the full independently mapped windows
gives 44/44 equal bytes. The diagnostic's relocation result also agrees with
all 44 linker bytes. The resolved and actual linked windows have SHA-256
`435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca`.
The scratch ELF hash is
`a5fcece51104226edf780de56f7feabbe3009e971a7c509a4a26003516942d28`;
this is **not** the original shared-library hash.

No veneer symbols or `.plt` section were emitted; the linked branch reaches
the prescribed address directly. No relocations remain in the scratch ELF.
Separately, the candidate has R_ARM_PREL31 at `.ARM.exidx+0` referencing the
`.text` section symbol, with implicit addend zero. LLD places `.ARM.exidx` at
`0x000d2f3c`; independently decoding its first signed PREL31 gives the query
address `0x000d2f10`. It expands the eight-byte input index to 16 bytes with
an end-of-text CantUnwind sentinel. LLVM readelf successfully decodes it.
This checks the scratch link's EHABI mechanics only; original unwind-table
equivalence is **NOT_CHECKED**. The diagnostic inventories outside-window
relocations and never applies them to the function comparison.

| Independent observation | Result |
| --- | --- |
| Raw ET_REL vs reference | RAW_MISMATCH, 6/44 differing bytes |
| Diagnostic resolved window vs reference | RESOLVED_WINDOW_EQUAL, 44/44 bytes |
| Actual scratch linked window vs reference | RELOCATED_LINKED_WINDOW_MATCH, 44/44 bytes |
| ORIGINAL_OBJECT_MATCH | NOT_AVAILABLE; no original ET_REL object |
| HISTORICAL_COMPILER_VERIFIED | false; GCC 4.4.3 executable/profile unknown |
| WHOLE_SO_SHA_MATCH | false |
| BEHAVIOR_VERIFIED | false; no original/ARM runtime execution |

### Reproduce #97 offline

Set `NOVA3_RETAIL_ELF` to the maintainer's read-only Premium ELF. Run from the
checkout. All generated artifacts remain in a disposable private directory;
never commit the ELF, object, linker output or private verification script.
The existing local scratch handoff for this session is `/tmp/nova3-task97/`;
it is temporary, so the commands below are the durable reproduction path.

```sh
NOVA3_PILOT_SCRATCH="$(mktemp -d /tmp/nova3-task97.XXXXXXXX)"
NOVA3_PILOT_CXX=/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++
NOVA3_PILOT_LD=/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/ld.lld
"$NOVA3_PILOT_CXX" --target=armv7a-linux-androideabi21 -marm \
  -mfloat-abi=softfp -mfpu=neon -O2 -fPIC -std=c++03 \
  -fomit-frame-pointer -c -x c++ \
  reconstruction/research/arm32_cpu_query_candidate.cpp \
  -o "$NOVA3_PILOT_SCRATCH/candidate.o"
cat > "$NOVA3_PILOT_SCRATCH/layout.ld" <<'LD'
ENTRY(nova3_arm32_cpu_count_candidate)
SECTIONS {
 . = 0x000d2f10;
 .text : { *(.text) }
 .ARM.exidx : { *(.ARM.exidx*) }
 .ARM.extab : { *(.ARM.extab*) }
 pthread_once = 0x000d2910;
 nova3_arm32_cpu_state = 0x00d96598;
 nova3_arm32_cpu_initialize = 0x000d2f98;
}
LD
"$NOVA3_PILOT_LD" -m armelf_linux_eabi --build-id=none \
  -T "$NOVA3_PILOT_SCRATCH/layout.ld" "$NOVA3_PILOT_SCRATCH/candidate.o" \
  -o "$NOVA3_PILOT_SCRATCH/linked.elf"
python3 -m nova3_team.relocation_probe --reference "$NOVA3_RETAIL_ELF" \
  --object "$NOVA3_PILOT_SCRATCH/candidate.o" \
  --linked "$NOVA3_PILOT_SCRATCH/linked.elf" --address 0xd2f10 \
  --symbol nova3_arm32_cpu_count_candidate \
  --target pthread_once=0xd2910 --target nova3_arm32_cpu_state=0xd96598 \
  --target nova3_arm32_cpu_initialize=0xd2f98 \
  --expected-reference-sha256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf \
  --expected-window-sha256 435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca \
  > "$NOVA3_PILOT_SCRATCH/probe.json"
python3 -m unittest nova3_team.tests.test_relocation_probe \
  nova3_team.tests.test_compiler_matrix nova3_team.tests.test_object_compare -v
```

Compiler, linker and diagnostic commands exited 0. The diagnostic's exit 0
means it completed, including when comparisons mismatch; inspect each status.
Invalid input exits 2. Omitting `--linked` never awards the linked-window grade.
24 Python tests pass (16 new fixture tests, eight existing regressions), with
no original files or ARM toolchain required for the fixtures. The unchanged
source-only host command documented above was rerun: compile and all five
test modes exited 0. These check modeled pthread dependency behavior, not
original ARM behavior. No source-owned CPU initializer was added.

The initial new test run had one overbroad assertion rejecting the metadata
key `compared_bytes`; it now recursively rejects binary values/code fields
while allowing byte counts. The private PLT check initially omitted Capstone's
separate immediate-rotation operand; corrected A32 rotation arithmetic passes.
Invoking the resolved multicall `lld --version` exited 1; invoking the actual
`ld.lld --version` driver succeeded. These failures and fixes are recorded in
the JSON, without proprietary instruction output.

### Bounded handoff

This completes the one-function relocation experiment. The JSON preserves the
#94 raw matrix and adds #97 tool/profile/source/object/linker hashes, exact
commands, return codes, relocation worksheet and separate comparison grades.
No GitHub state was modified and no PR was submitted; the supervisor can use
this evidence for human review. No package installation, network access,
heavy analyzer, original executable execution or production integration ran.

Next independent gate: identify a locally available historical ARM GCC
executable and its provenance/profile before making any compiler claim.
Initializer/data and original pthread behavior require a separate bounded
runtime/oracle task. No bulk follow-up, original-object recovery, whole-library
match or game functionality is established by this pilot.

## Task #99: historical provenance gate (2026-10-28)

**MISSING_HISTORICAL_COMPILER; historical GCC NOT MATCHED.** A fresh bounded
read-only check reconfirmed the Premium ELF/window hashes, A32 extent and 968
GCC 4.4.3 `.comment` strings. Discovery checked 55 fixed C++ compiler paths in
11 candidate roots; none was present. The configured historical-root variable
was unset; the installed NDK remains r29. Zero historical profiles were built.

[Historical toolchain handoff](HISTORICAL_TOOLCHAIN.md) documents the new
read-only fingerprint/profile gate, explicit SHA trust pins, official source
URLs pending authorized network review, missing compiler/sysroot requirements
and the one-function continuation. 44 source-only tests pass, including the
existing compiler-matrix/relocation regressions. Machine-readable metadata:
`coordination/evidence/historical-toolchain-research.json`.

The #97 Clang raw mismatch and separately scratch-linked 44-byte equality
above remain unchanged. Neither identifies historical GCC/NDK nor verifies
source equivalence, original C++ ABI, original behavior or whole-library SHA.
No compiler download, package installation, GitHub change, production source
regeneration or additional matching claim occurred.
