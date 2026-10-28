# Task #99: historical ARM GCC provenance gate

2026-10-28. **MISSING_HISTORICAL_COMPILER. Historical GCC NOT MATCHED.**
The reusable metadata/profile tool and source-only tests are implemented. No
historical compiler was executed, no ARM profile was built, and no new window
match was established. The #97 modern Clang result remains separate and intact.

## Evidence and independent claims

| Claim | Task #99 status / scope |
| --- | --- |
| GCC_4_4_3_STRING_PRESENT | true; read-only `.comment` query reconfirmed 968 occurrences |
| GCC_4_4_3_CANDIDATE_AVAILABLE | false; no candidate in the bounded search |
| PROFILE_REPRODUCIBLE | false; flag plans are generated, no historical build replay |
| LINKED_WINDOW_MATCH | NOT_TESTED for historical GCC; prior #97 Clang scratch match retained |
| EXACT_ORIGINAL_COMPILER_PROVEN | false |
| EXACT_HISTORICAL_TOOLCHAIN_VERIFIED | false |
| SOURCE_EQUIVALENCE_VERIFIED | false; not inferred from host tests or code windows |
| WHOLE_SO_HASH_MATCH | false |

Premium ELF SHA-256 was independently rechecked as
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`.
The existing ELF ledger selected `android_getCpuCount`, A32, VA/file offset
`0x000d2f10`, size 44, window SHA-256
`435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca`.
This is reference identification, not a new compiled comparison. ARM attributes
reconfirm ARMv7, VFPv3 and NEONv1. They do not establish every TU's flags or ABI.
The source-owned candidate SHA-256 remains
`ea7f1ae3cf95610d2f22ca566e9756d8591f16f99126634020b85cd6bc2dd46b`;
its source/header and all earlier evidence files are unchanged.

The preflight API reported four accessible references, four logical CPUs and
7,924,896 KiB RAM. It and the fingerprint API were called in memory without
writing private reference reports into the checkout. No heavy analyzer ran.

## Historical documentation: offline research boundary

The task explicitly prohibits network access. Neither supplied historical page
was fetched, and no local historical changelog was available in the checked
repository/current NDK documentation. **Authoritative document contents,
release dates and historical package checksums remain unverified here.** The
following are research leads supplied with the task, not newly authenticated
citations or evidence that a package produced the Premium game:

| Authoritative source to inspect in a separately authorized session | Version / date scope | Current evidence |
| --- | --- | --- |
| [AOSP NDK CHANGES at revision 81dd1b4](https://android.googlesource.com/platform/ndk/+/81dd1b4/docs/CHANGES.html) | Historical changelog snapshot; revision-to-release/date mapping NOT_VERIFIED | Task identifies ARM GCC 4.4.3 discussion; text NOT_ACCESSED_OFFLINE; short revision is not an authenticated archive hash |
| [Android NDK revision history](https://developer.android.com/ndk/downloads/revision_history) | Rolling official release history; page date/content NOT_VERIFIED this session | NOT_ACCESSED_OFFLINE; consult historical entries and any checksum links before selecting a package |

The bounded candidate set covers **r7, r7b, r7c, r8, r8b, r8c, r8d, r8e** as
hypotheses. Inclusion does not assert that each ships the same GCC build, uses
it by default, or has a particular binutils/sysroot revision. A future source
review must distinguish those claims per release and record the exact official
release date, document revision/content digest, host package name and published
checksum. No r7/r8 release date, archive digest, GCC binary digest or GNU linker
version has been invented. **Independently authenticated historical binary or
archive hashes: none.** The Premium hash authenticates reference identity only;
it does not authenticate an NDK. A locally computed SHA pin alone likewise
does not establish distribution provenance or historical use.

The 968 `.comment` records can be contributed/retained by different linked
inputs. They neither assign a compiler to this function/TU nor uniquely reveal
NDK release, sysroot, optimization, compiler patches, GNU binutils, linker
script, C++ runtime or link order. Matching one small C-linkage function cannot
identify the original C++ ABI or validate earlier reconstruction en masse.

## Bounded local search and blocker

`NOVA3_HISTORICAL_TOOLCHAIN_ROOT` was unset. The tool checked 55 fixed candidate
paths across 11 roots: `/opt/android-ndk`, `/opt/android-ndk-r7`, `-r7b`, `-r7c`,
`-r8`, `-r8b`, `-r8c`, `-r8d`, `-r8e`, `/opt/android-sdk/ndk-bundle`, and the
current user's `Android/Sdk/ndk-bundle`. Zero C++ compiler candidates were
present. Only `/opt/android-ndk` exists among these roots; `source.properties`
identifies **r29 / 29.0.14206865**, not a historical candidate.

For each root, only `bin/arm-linux-androideabi-g++`,
`prebuilt/{linux-x86,linux-x86_64}/bin/arm-linux-androideabi-g++`, and
`toolchains/arm-linux-androideabi-4.4.3/prebuilt/{linux-x86,linux-x86_64}/bin/arm-linux-androideabi-g++`
are tested. No recursive traversal, general home scan, PATH execution,
compiler download, package installation or host configuration change occurs.
Missing `g++` means this C++ candidate is unavailable even if a C-only compiler
could exist elsewhere. This is deliberately **not** an exhaustive machine scan.

The owner must supply a separately authenticated, trusted historical candidate
and its complete supporting sysroot/toolchain in an isolated local directory.
Record exact official origin, release/host package, archive digest and checksum
source/authentication method before execution; preserve the archive/manifest
privately. Inspect host ELF class/interpreter/dependencies without executing it
before deciding whether a compatible disposable environment is required. Old
32-bit host tools may need dependencies; this task did not install any or test
host compatibility. Select a root explicitly with `--root` or
`NOVA3_HISTORICAL_TOOLCHAIN_ROOT`; no old NDK download/install is authorized by
this document. Do not run a compiler obtained from an untrusted third party.

## Tool contract and nonexecuting owner handoff

`python3 -m nova3_team.historical_toolchain` is read-only with JSON on stdout.
Redirect private reports to ignored `.cache` only in a subsequent owner-run
session. Never commit its raw local paths, GCC specs or generated profiles.

```sh
python3 -m nova3_team.historical_toolchain discover
python3 -m nova3_team.historical_toolchain discover --root "$NOVA3_HISTORICAL_TOOLCHAIN_ROOT"
python3 -m nova3_team.historical_toolchain profiles --count 8
```

Those commands do not execute a compiler. Missing-tool flag plans have a
separate `/plan` schema and no `compiler` field; they cannot be mistaken for
valid compiler-matrix profiles. At most eight variants are returned: `-O2`,
`-Os`, `-O1`, `-O3`, each with default or omitted frame pointer. Common flags
are `-marm -march=armv7-a -mfpu=neon -mfloat-abi=softfp -fPIC -std=c++03`.
They are hypotheses, including the default frame-pointer behavior. Explicit
flags use repeated `--flag=VALUE`, require `--count 1`, and pass a small exact
allowlist. Response files, plugins, specs overrides, `-B`, linker directives,
output paths, shell strings and contradictory options are rejected.

After independent owner trust review, the following is a **nonexecuted
recipe**, not evidence that the tools are present. The variables must identify
approved paths and independently reviewed local executable hashes. A SHA pin
is an explicit execution gate, not proof that an archive is genuine:

```sh
python3 -m nova3_team.historical_toolchain fingerprint "$NOVA3_HISTORICAL_CXX" \
  --trusted-sha256 "$NOVA3_HISTORICAL_CXX_SHA256" \
  --linker "$NOVA3_HISTORICAL_LD" --linker-sha256 "$NOVA3_HISTORICAL_LD_SHA256" \
  --assembler "$NOVA3_HISTORICAL_AS" --assembler-sha256 "$NOVA3_HISTORICAL_AS_SHA256" \
  > .cache/historical-fingerprint.json
python3 -m nova3_team.historical_toolchain profiles \
  --fingerprint .cache/historical-fingerprint.json --count 4 \
  > .cache/historical-profile-list.json
```

Metadata queries collect compiler executable SHA, `--version`, `-dumpversion`,
`-dumpmachine`, `-dumpspecs`, `-print-sysroot` and reported ld/as/cc1plus paths.
Every metadata subprocess has a ten-second timeout, a sanitized environment,
no stdin and no shell. Each queried executable is rehashed before/after; all
explicit binutils pins are checked before any query. SHA and version are
separate checks. Only GCC 4.4.3 with exact target `arm-linux-androideabi`,
consistent version metadata and available specs passes the candidate gate.
An empty or unsupported sysroot query is retained; it is not a guessed path.
Reported GCC program paths never cause an implicit tool execution.

The separately supplied linker/assembler are fingerprinted as candidates;
**compiler-selected binutils are not automatically verified**. Compare their
resolved paths to GCC's reported selections, including specs overrides. Review
and pin cc1plus, compiler libraries, as/ld, startup objects, Android headers,
sysroot and libc/C++ runtime before a build. Metadata alone deliberately leaves
`PROFILE_REPRODUCIBLE=false`; clean build replay with those inputs pinned is a
separate requirement. The current matrix checks version output only and does
not replace this gate or verify the full package closure.

Split the JSON list into one private JSON file per profile. Before passing each
to the existing matrix, revalidate it with independent owner pins:

```sh
python3 -m nova3_team.historical_toolchain validate .cache/historical-01.json \
  --trusted-sha256 "$NOVA3_HISTORICAL_CXX_SHA256" \
  --trusted-tool-sha256 "$NOVA3_HISTORICAL_LD_SHA256" \
  --trusted-tool-sha256 "$NOVA3_HISTORICAL_AS_SHA256"
```

Revalidation rejects changed executable bytes even with identical version
text, changed specs/search results, changed tool hashes and changed version
output. It still does not mark a build reproducible. Serialized reports and
profiles are local trusted configuration; do not accept third-party profile
files as provenance. Optional `fingerprint --arm-object SOURCE_BUILT_OBJECT
--readelf APPROVED_READELF --readelf-sha256 PIN` checks ELF32 ARM EABI5 ET_REL
and records `readelf -A` metadata and object SHA. The tool cannot itself prove
that the provided object came from source; its producer must be recorded.
No object means `arm_attributes=NOT_BUILT`, never inferred from flags.

**Historical sysroot integration is pending.** This source includes pthread,
stdint and stddef headers. If GCC's configured sysroot/search paths do not
provide the correct Android headers, the generated flags cannot compile it.
The existing matrix allowlist does not accept `--sysroot`/`-I`. Do not weaken
it, use a shell wrapper, copy modern headers, or edit GCC specs to force this
through. Have a separately scoped change record and pin an explicit sysroot
before retrying. An unset sysroot, successful modern compile or arbitrary API
level is not evidence of historical headers/ABI.

## One-function continuation, only after the gate

Use a new ignored output directory, unchanged source candidate, at most eight
profiles total, **sequentially**. For each approved profile:

```sh
python3 -m nova3_team.compiler_matrix --reference "$NOVA3_RETAIL_ELF" \
  --source reconstruction/research/arm32_cpu_query_candidate.cpp \
  --symbol nova3_arm32_cpu_count_candidate --address 0xd2f10 \
  --profile .cache/historical-01.json --compile-timeout 30 \
  --out .cache/historical-01-matrix
```

Record compiler/assembler/linker and source/header/sysroot hashes, exact flags,
exit codes and object ARM attributes. Inspect raw comparison even on mismatch.
For an appropriate 44-byte A32 candidate with supported relocations, repeat the
[explicit scratch-link and relocation probe](ARM_COMPILER_PILOT.md#reproduce-97-offline)
in a new private directory, using the **approved historical linker** under
`timeout 30s` and its verified actual CLI. The prior LLD command is not proof
of GNU ld compatibility; record `--version` and validate supported options
before linking. Use a 30-second bound for any direct compile too. Do not run
the scratch ELF. If that linker is absent, record the blocker rather than
silently substituting LLD for an original-toolchain claim.

Keep explicit assumptions: function `0x000d2f10`, pthread_once PLT destination
`0x000d2910`, state `0x00d96598`, initializer `0x000d2f98`, size 44, A32, and
both reference/window hashes above. Probe with `--linked` and those three
`--target` mappings; preserve raw, resolved and actual linked comparison
results independently. Unsupported relocation or size/mode mismatch is a
recorded failure, not a reason to mask bytes or expand scope. No new linker
script or original bytes are shipped with this task. A linked-window equality
would still leave exact historical compiler, source equivalence and whole-.so
claims unproven. No bulk decompilation/regeneration is queued.

## Validation and handoff

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  nova3_team.tests.test_historical_toolchain \
  nova3_team.tests.test_compiler_matrix \
  nova3_team.tests.test_relocation_probe -v
```

Final run: **44 tests passed, exit 0** (25 new metadata/gate tests, 3 matrix
and 16 relocation regressions). Fixtures are inert source-owned bytes and
mocked subprocess output, not copied game code or an installed compiler.
The first run failed two new tests: an incorrect existing `_read` signature
and directory rejection after `fdopen`; bounded regular-file readers now
handle those cases, including nonblocking FIFO rejection. A preliminary
read-only query also attempted a nonexistent indexer import; rerunning with
actual APIs succeeded. No failures imply a historical compiler was tested.

CLI discovery and eight-plan generation both exited 0; their JSON reported
MISSING_HISTORICAL_COMPILER and eight nonexecuting plans. The machine-readable
scrubbed record is `coordination/evidence/historical-toolchain-research.json`.
Only the five task-allowed paths changed. No network/GitHub state, installs,
original executable writes/execution, prior Clang evidence, production source
or game functionality changed. PR creation is left to the supervisor under the
explicit no-GitHub-state instruction.

Resume: review official historical documents in a separately authorized
session, authenticate and supply a candidate, rerun bounded discovery, then
query trusted metadata. Resolve sysroot/host dependency blockers before the
one-function sequential experiment. Stop at eight attempts and retain each
failure rather than launching retries or broad reconstruction.
