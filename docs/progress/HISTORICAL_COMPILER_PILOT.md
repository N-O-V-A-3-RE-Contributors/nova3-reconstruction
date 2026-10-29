# Historical GCC compile pilot (#104 / #115 / #117)

The pilot now compiles a separate source-owned C++03, no-argument hypothesis
for `android_getCpuCount`. **NOT MATCHED**: the local r8e GCC 4.4.3 run on
2026-10-29 compiled four profiles successfully; each produced a 52-byte A32
function against the fixed 44-byte Premium window. This removes the known
language/ABI blocker without establishing the original compiler or source.
The legacy C++17 context-pointer source is unchanged.

Only two source paths are recognized:

| Source | Selected symbol | Sanity result |
| --- | --- | --- |
| `reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp` | `nova3_recovered_cpu_count` | `BLOCKED`: incompatible C++17/context-pointer candidate; no compiler invocation |
| `reconstruction/research/arm32_cpu_query_gcc443.cpp` | `android_getCpuCount` | Explicit C++03/no-argument source hypothesis |

The new file uses the selected platform's `<pthread.h>`, external `g_once`,
`g_cpuCount`, `android_cpuInit`, and `pthread_once`. It defines only the query;
no globals, initializer, host CPU detector or once-service stub are supplied.
Compile-time checks require four-byte `int` and once storage. Global binding,
types and source organization remain hypotheses. Published source comments
supply the count function's VA/size/mode and the two global addresses; these
are metadata, not instructions or recovered relocation records. The features
and family queries and the initializer's implementation are outside scope.

Source sanity uses a narrow token contract for this one hypothesis. It rejects
changed includes, arguments, C linkage, external declarations or body before
running tools. It is not a general C++ parser. Both paths have distinct ABI
claim labels; `abi_verified` stays false. Other source paths are rejected.
A new candidate/body requires a separately reviewed contract change.

The CLI fixes the Premium reference SHA-256
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`,
A32 VA `0x000d2f10`, size 44 and window SHA-256
`435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca`.
It checks independent symbol/section/PT_LOAD mapping and never executes or
copies the reference library.

GCC 4.4.3 requires two details that the previous mocked pilot missed:

- Its frontend `cc1plus --version` exits zero with no banner. The pilot uses
  `-quiet -version -fsyntax-only` with empty standard input, captures both
  streams and validates the actual `GNU C++ (GCC) version 4.4.3` banner.
- The installed r8e compiler rejects `-std=c++03` as an unrecognized option.
  Its pre-C++11 language mode, incorporating C++03 corrections, is named
  `-std=c++98`. The shared historical flag gate still validates the requested
  `-std=c++03` policy. Only this runner translates that exact spelling;
  reports distinguish `requested_flags` from effective `flags` and retain
  the complete command. `-pedantic-errors` enforces the selected language.

All profiles use A32, ARMv7-A, NEON, softfp and PIC. Four to eight sequential
hypotheses cover `-O2`, `-Os`, `-O1`, `-O3`, first omitting and then retaining
frame pointers. No Clang switches or fallback compiler are used.
`-nostdinc/-nostdinc++` plus explicit GCC/platform header trees avoid implicit
C++17 or host headers. This source needs no C++ standard-library headers.
The selected sysroot and ordered header trees are hashed and bounded;
symlinks/special files inside those trees are rejected.

From the reviewed repository root, use this one owner-local Bash command.
**Inspect first:** the already installed r8e root, `linux-x86_64` host build,
`android-9/arch-arm` sysroot, internal include trees, host ELF loaders and
libraries, and GCC's selected helper paths must exist and be suitable locally.
The selected assembler is `arm-linux-androideabi/bin/as` beneath the prebuilt
root, not the other `bin/arm-linux-androideabi-as` path. Independently review
and set the three approved executable SHA variables before running this block;
a locally computed hash alone does not authenticate an NDK distribution.
No downloads, installs, tool discovery via PATH or compatibility repairs are
performed by the pilot (the wrapper uses the owner's Bash/Python).

```bash
NOVA3_LOCAL_NDK="$HOME/.local/opt/android-ndk-r8e"
NOVA3_LOCAL_GCC="$NOVA3_LOCAL_NDK/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64"
NOVA3_LOCAL_SYSROOT="$NOVA3_LOCAL_NDK/platforms/android-9/arch-arm"
NOVA3_LOCAL_PREMIUM="$HOME/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
bash tools/run_historical_pilot.sh \
  --ndk-root "$NOVA3_LOCAL_NDK" \
  --compiler "$NOVA3_LOCAL_GCC/bin/arm-linux-androideabi-g++" \
  --compiler-sha256 "${NOVA3_GXX_SHA256:?set independently approved compiler SHA256}" \
  --assembler "$NOVA3_LOCAL_GCC/arm-linux-androideabi/bin/as" \
  --assembler-sha256 "${NOVA3_AS_SHA256:?set independently approved selected assembler SHA256}" \
  --cc1plus "$NOVA3_LOCAL_GCC/libexec/gcc/arm-linux-androideabi/4.4.3/cc1plus" \
  --cc1plus-sha256 "${NOVA3_CC1PLUS_SHA256:?set independently approved frontend SHA256}" \
  --sysroot "$NOVA3_LOCAL_SYSROOT" \
  --include "$NOVA3_LOCAL_GCC/lib/gcc/arm-linux-androideabi/4.4.3/include" \
  --include "$NOVA3_LOCAL_GCC/lib/gcc/arm-linux-androideabi/4.4.3/include-fixed" \
  --reference "$NOVA3_LOCAL_PREMIUM" \
  --source reconstruction/research/arm32_cpu_query_gcc443.cpp \
  --profiles 4 --timeout 30 \
  --out ".cache/historical-pilot-$(date +%Y%m%d-%H%M%S)-$$"
```

Compiler, assembler and frontend paths/hashes must match the pinned NDK and
compiler-selected tools. `-B` selects an isolated helper directory. Sources,
headers and pins are rechecked around each compile. Each compile has a maximum
30-second wall timeout, CPU/memory/output limits and process-group cleanup;
a timeout stops further profiles. Owner pins authorize execution; neither
pins nor the Google distribution candidate prove Gameloft's original build.

`--out` must be a new ignored repository `.cache` directory. Reports and logs
are explicitly mode 0600, directories 0700, and compiler-created objects use
umask 077 even when the Python module is invoked directly. Reports checkpoint
before metadata execution and each profile. Each profile records its symbol,
source/compiler/version/reference hashes, requested/effective flags, command,
compile status and object hash when available. No proprietary bytes are
included in reports. Keep all reports, logs and candidate objects private.
No original `.o`, assembly snapshot, linker script or full library is emitted.

Raw comparisons include mode, size, all bytes and relocation presence. For
same-size A32 candidates only, repeated optional `--target SYMBOL=0xVALUE`
arguments provide explicit candidate link assumptions. Names are exact and
case-sensitive; maps are validated and sorted deterministically. There are
no default global, initializer or pthread/PLT targets. Missing/unused maps,
unsupported relocations and unequal bytes fail closed. Supported external
A32 CALL/REL32 relocations are applied only to scratch bytes in memory, with
no masking. Equality is not a linked-ELF result: no linker executes, and
`linked_window_match` stays `NOT_TESTED`. Original `.o`, historical toolchain,
ABI/data layout, behavior, source equivalence and whole-library SHA remain
separate unverified claims, including on compile success or window equality.

Exit 0 means all requested objects were compared, including mismatches;
exit 1 means compile failure/timeout or comparison rejection; exit 2 means
input rejection or `BLOCKED`. A completed run remains `NOT_MATCHED`.
A language/ABI sanity blocker is not negative evidence about GCC. If archived
tools cannot execute, retain `BLOCKED`, inspect the private error, and supply
locally reviewed executable hashes, version/target/helper metadata and host
loader/dependency diagnostics. Do not install arbitrary compatibility packages
or retry an identical blocked run. Early path/hash/header rejection produces
a sanitized CLI error before tools execute, with no report directory.

Verification and handoff for #115 (2026-10-29):

```sh
python -m unittest nova3_team.tests.test_historical_compile_pilot \
  nova3_team.tests.test_historical_toolchain nova3_team.tests.test_object_compare \
  nova3_team.tests.test_relocation_probe nova3_team.tests.test_compiler_matrix -q
bash -n tools/run_historical_pilot.sh
bash tools/run_historical_pilot.sh --help
```

86 tests passed (23 pilot tests); shell syntax/help checks exited zero.
All reference/comparison fixtures are synthetic and require no NDK or original
ELF. An optional installed host g++/nm check compiles source-owned C++03,
checks the no-argument function-pointer type, rejects a pointer-argument type,
and confirms all four dependencies are undefined. It does not link stubs or
run the query. Compiler fixture processes are mocked; the timeout/log test
executes source-owned Python only.

The actual local run used `arm-linux-androideabi-g++ (GCC) 4.4.3`, compiler
SHA-256 `cb1a930727715eec0565830462d10f8cf1a190d220505ae481df9148bcd93288`,
and source SHA-256
`ba1d258b34b4742333a9a4382fb76f8aeda47091c9a67a5ce90a049afd292a42`.
The checked-in reference hashes above were confirmed. After correcting the
frontend gate and flag spelling, four optimization profiles with frame-pointer
omission compiled with exit 0; all selected `android_getCpuCount`, A32, size 52,
four relocation sites, `FUNCTION_SIZE_MISMATCH`. Relocation resolution was
`NOT_ATTEMPTED`; no targets were supplied. Full flags, header/tool pins, object
hashes and logs remain in ignored `.cache/task115-r8e-cxx03-noarg/report.json`
**relative to the retained #115 worker checkout**, not the main project or a
new worker checkout. Preserve that directory. Only a bounded, read-only listing
of `115-*` directories under the repository's worker-cache parent is needed
to locate it; never search the entire home directory.
The earlier frontend blocker and four rejected `-std=c++03` attempts are also
retained privately. No original-library execution or behavior comparison ran.

## Structural diagnostic checkpoint #117 (2026-10-29)

The retained owner-local #115 report and four cached candidate objects were
available and independently checked against the pinned source and Premium
ELF/window above. No compiler or linker executed during this diagnostic.
The prior report SHA-256 was
`0fe82cceedbf3fbe32202c375c053cff93676b9c4582d78c24b6fb1af7bbaecd`.
Actual structural results for **each of the four profiles**:

| Measurement | Candidate ET_REL | Premium linked window |
| --- | --- | --- |
| Function bytes / mode | 52 / A32 | 44 / A32 |
| Mapping-symbol code extent | 40 bytes | 36 bytes |
| Mapping-symbol data extent | 12 bytes | 8 bytes |
| Function relocation sites | 3 `R_ARM_GOT_PREL`, 1 `R_ARM_PLT32` | Original pre-link records UNKNOWN |
| Expected state/initializer symbol binding | GLOBAL, DEFAULT visibility, undefined | LOCAL, DEFAULT visibility, defined |
| Expected once-service symbol binding | GLOBAL, DEFAULT visibility, undefined | GLOBAL, DEFAULT visibility, undefined |

The eight-byte gap consists of four extra mapped code bytes and four extra
mapped data bytes. These are mapping-symbol extents, not decoded instruction
semantics or proof that all data words serve the same purpose. Exact-name
symbol metadata does not independently establish the reference's call/data
edges. Four distinct candidate external symbols remain unresolved. Both ELF
headers have zero floating-point ABI flag bits; this alone cannot establish
the complete ABI. The recorded invocations confirm PIC, A32, ARMv7-A, NEON,
softfp, frame-pointer omission and the four optimization levels described
above. `-O2`, `-Os`, `-O3` have identical candidate function-code hashes;
`-O1` differs but has the same size, extents and relocation counts.

**INFERRED hypothesis:** PIC access to externally bound dependencies may
contribute to the extra code/data compared with local binding. The GOT
relocations and binding difference support investigating this; they do not
prove causation or recover the original source declarations. All four
relocations are outside the existing CALL/REL32 resolver's supported set.
Applying an invented target map or ignoring those sites would be invalid.
Root cause remains **UNKNOWN**, source **NOT MATCHED**, relocation resolution
**NOT_ATTEMPTED**, linked comparison **NOT_PERFORMED**. No flag/visibility
experiment or change to either source candidate was made in #117.

`python -m nova3_team.gcc_layout_diagnostics` diagnoses one explicitly selected
cached object at a time. It requires explicit reference/object/source/prior
report paths and a prior-report SHA pin. It checks the fixed reference
SHA/window, symbol/section/PT_LOAD mapping, A32/EABI5, source/object pins,
recorded compiler target/version hashes, complete effective invocation,
header-manifest consistency and a freshly recomputed raw comparison. Report
paths are never followed as input paths. Inputs have byte and aggregate
metadata budgets; malformed relocations, overlapping sites, symlinks and
special files fail closed. It reuses the existing ELF/comparison readers;
there is no new disassembler or compiler runner.

Output contains hashes, allowlisted flags, normalized dependency roles,
structural counts and difference categories. It never serializes raw code,
assembly, literal values, arbitrary symbol names, private input paths or
unfiltered prior-report fields. Only a fresh ignored checkout-local `.cache`
directory is accepted; reports use mode 0600 and their directories 0700.
Even synthetic equal windows retain the independent NOT MATCHED claims.
Exit 0 means the diagnostic completed; exit 2 means input rejection.

Actual private reports in the #117 worker checkout are
`.cache/task117-layout-checked/profile-00/report.json` through
`.cache/task117-layout-checked/profile-03/report.json`.
Exact retained input paths are recorded **only locally** in
`.cache/task117-layout-checked/handoff.json`; private `preflight.json` and
`fingerprint.json` are alongside it. Do not upload these files. Initial
diagnostic reports under `.cache/task117-layout-profile-00/` through `-03/`
were preserved as well.

Owner follow-up from the checkout containing this tool, after setting the
exact retained #115 directory and Premium path locally:

```bash
: "${NOVA3_115_CHECKOUT:?set exact retained saved #115 checkout}"
: "${NOVA3_LOCAL_PREMIUM:?set owner-local Premium ELF path}"
python -m nova3_team.gcc_layout_diagnostics \
  --reference "$NOVA3_LOCAL_PREMIUM" \
  --source reconstruction/research/arm32_cpu_query_gcc443.cpp \
  --prior-report "$NOVA3_115_CHECKOUT/.cache/task115-r8e-cxx03-noarg/report.json" \
  --prior-report-sha256 0fe82cceedbf3fbe32202c375c053cff93676b9c4582d78c24b6fb1af7bbaecd \
  --object "$NOVA3_115_CHECKOUT/.cache/task115-r8e-cxx03-noarg/profile-00/candidate.o" \
  --profile-index 0 \
  --out ".cache/task117-layout-followup-$(date +%Y%m%d-%H%M%S)-$$"
```

Select index 1, 2 or 3 together with its matching object for another profile.
The next bounded experiment should test one independently reviewed
dependency-binding hypothesis with explicit source/header/tool pins, then a
controlled candidate link and relocation worksheet at the original VA.
Local symbols and hidden external declarations are different hypotheses;
neither source declarations nor original link transformations are recovered
by this inventory. Preserve the reference and do not force size equality.

Verification for #117:

```sh
python -m unittest nova3_team.tests.test_gcc_layout_diagnostics \
  nova3_team.tests.test_historical_compile_pilot \
  nova3_team.tests.test_historical_toolchain nova3_team.tests.test_object_compare \
  nova3_team.tests.test_relocation_probe nova3_team.tests.test_compiler_matrix -q
python -m nova3_team.gcc_layout_diagnostics --help
git diff --check
```

102 tests passed, including 16 diagnostic tests; commands exited zero.
Synthetic fixtures cover the size gap, mapped extents, GOT/PLT sites,
unresolved externals, visibility/header differences, stale reports,
hash/machine/mode/mapping failures, aggregate budgets, private output,
sanitized errors and false-match prevention. They require no original ELF
or historical NDK. The four actual local structural runs above are separate
evidence. Neither test group demonstrates original behavior, instruction
equality, a linked candidate, full-library equality or gameplay.
