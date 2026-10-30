# Historical ARM compiler candidate onboarding (#103)

Optional setup is now offered on the first worker run through `nova3_easy.py`
and `START-LINUX.sh`. Declining or skipping it permits ordinary host-only tasks.
The Control Center installer advertises its **H / TOOLCHAIN** page and bundles the
same offline helper; monitoring requires no compiler, worker, or NDK download.
The default flow never downloads, extracts, installs or executes GCC.

```sh
# Offline guidance, before any dependency installation or account login:
python3 nova3_easy.py --historical-toolchain instructions
python3 -m nova3_team.historical_toolchain onboard --instructions
nova3-observer --toolchain-setup
# Explicitly skip the optional step while starting an ordinary worker:
bash START-LINUX.sh --historical-toolchain skip
```

The separate `nova3_start.py` entry point and direct
`python -m nova3_team.worker run` bypass `nova3_easy.py` onboarding. They are
outside #103's file allowlist and unchanged. Users of those modes should open
the offline guidance above; integrating a first-run notice there requires a
separate narrow change to `nova3_start.py`/`nova3_team/worker.py`.

| Onboarding status | What it establishes |
| --- | --- |
| `MISSING` | No compiler found in bounded discovery. Observer/host tasks remain usable. |
| `AVAILABLE_BUT_UNTRUSTED` | A candidate file was hashed; no execution or version assertion. |
| `PINNED_VERSION_TARGET_VERIFIED` | Explicit owner-pinned executable returned consistent GCC 4.4.3, ARM target and specs metadata. No build or match. |
| `COMPILER_REJECTED` | Queried metadata contradicts the candidate version/target or is incomplete. |
| `COMPILER_UNUSABLE` | Missing/nonexecutable file, changed pin, timeout, or host execution/dependency failure. |
| `UNSUPPORTED_HOST` | Explicit metadata execution refused outside Linux x86_64. Monitoring still works. |

Every status retains `source_matching: NOT_PROVEN` and
`match_to_original: NOT_MATCHED`. The legacy inventory has **zero independently
verified original object/source matches**. `.comment` GCC 4.4.3 strings narrow
a hypothesis; they do not prove that NDK r8e produced N.O.V.A. 3. No source
acceptance or #101 evidence-gate policy changed.

## Official archive and manual opt-in (Linux x86_64, no sudo)

Task-supplied upstream provenance, **not fetched in this offline session**:

- [Original Google archive](https://dl.google.com/android/ndk/android-ndk-r8e-linux-x86_64.tar.bz2):
  `android-ndk-r8e-linux-x86_64.tar.bz2`, **466853553 compressed bytes**.
- [Historical AOSP publisher record](https://android.googlesource.com/platform/frameworks/base/+/603dbd7^!/),
  `ndk.linux64_*` fields: publisher MD5 **fa812352956067e7a9eefc0274675e9a**.
- [Official release history](https://developer.android.com/ndk/downloads/revision_history):
  r8e, March 2013, Linux 64-bit host package; default GCC **4.6**.
  Select **arm-linux-androideabi-4.4.3 explicitly**, never default `ndk-build`.

Historical publisher MD5 is **not a modern cryptographic integrity or signature
guarantee**. Combine the pinned official HTTPS origin, byte count and digest;
record a local SHA-256 without inventing an authenticated upstream SHA-256.
The reported uncompressed `.tar` SHA-256 is a different artifact and cannot
be compared with the compressed original's MD5.

After choosing to obtain the candidate, in a **new private empty directory**:

```sh
umask 077
curl --fail --proto '=https' --tlsv1.2 --max-redirs 0 \
  --output android-ndk-r8e-linux-x86_64.tar.bz2 \
  'https://dl.google.com/android/ndk/android-ndk-r8e-linux-x86_64.tar.bz2'
file -- android-ndk-r8e-linux-x86_64.tar.bz2
wc -c < android-ndk-r8e-linux-x86_64.tar.bz2
md5sum -- android-ndk-r8e-linux-x86_64.tar.bz2
sha256sum -- android-ndk-r8e-linux-x86_64.tar.bz2
```

No redirects, third-party mirrors, `--compressed`, or preexisting download
destination. Stop on an HTTP error, transformed `.tar`, unexpected size or
digest. Do not extract a partial download. From the repository, verify the
absolute archive path and retain stdout privately:

```sh
export NOVA3_NDK_ARCHIVE=/absolute/path/android-ndk-r8e-linux-x86_64.tar.bz2
python3 -m nova3_team.historical_toolchain verify-archive "$NOVA3_NDK_ARCHIVE"
tar -tjf "$NOVA3_NDK_ARCHIVE"
tar -tvjf "$NOVA3_NDK_ARCHIVE"
df -h "${XDG_DATA_HOME:-$HOME/.local/share}"
```

The verifier reads a bounded regular file, checks bzip2 signature, compressed
size and publisher MD5, and records local SHA-256. It never extracts or runs
code. It does not authenticate the network origin of a previously supplied
file or validate the decompressed tar. Review the listing: one
`android-ndk-r8e/` tree; reject absolute/traversing paths, device entries or
escaping links. Plan disk space for the archive and unpacked tree, and keep
the archive until provenance review is complete.

For guarded extraction use updated Python with `tarfile.data_filter`
(Python 3.12+), from the repository. This owner-run recipe first repeats archive
validation, refuses an existing final install, extracts into fresh staging with
the data filter (rejecting escaping links/special files), and only then moves
the completed tree to the XDG user location. Failure removes staging; the
download is retained. It does not execute any extracted tool:

```sh
python3 - <<'PY'
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
from nova3_team.historical_toolchain import user_local_root, verify_archive

archive = Path(os.environ['NOVA3_NDK_ARCHIVE']).resolve(strict=True)
print(verify_archive(archive))
if not hasattr(tarfile, 'data_filter'):
    raise SystemExit('Updated Python with tarfile.data_filter required; do not extract unfiltered')
target = user_local_root()
if target.exists() or target.is_symlink():
    raise SystemExit('Existing NDK target: inspect it; do not overwrite')
target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
with tempfile.TemporaryDirectory(prefix='.r8e-staging-', dir=target.parent) as scratch:
    with tarfile.open(archive, 'r:bz2') as bundle:
        def ndk_filter(member, destination):
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != 'android-ndk-r8e'
                    or '..' in parts or PurePosixPath(member.name).is_absolute()):
                raise tarfile.FilterError('Unexpected NDK archive path')
            filtered = tarfile.data_filter(member, destination)
            if filtered.issym() or filtered.islnk():
                base = ((Path(destination) / filtered.name).parent if filtered.issym()
                        else Path(destination))
                link = (base / filtered.linkname).resolve()
                try:
                    link.relative_to((Path(destination) / 'android-ndk-r8e').resolve())
                except ValueError:
                    raise tarfile.FilterError('Link escapes the final NDK tree')
            return filtered
        bundle.extractall(scratch, filter=ndk_filter)
    tree = Path(scratch) / 'android-ndk-r8e'
    if tree.is_symlink() or not tree.is_dir():
        raise SystemExit('Expected real android-ndk-r8e directory')
    if target.exists() or target.is_symlink():
        raise SystemExit('Target appeared during extraction; stop')
    tree.rename(target)
print('Installed candidate data only:', target)
PY
```

No automatic cleanup deletes the archive; remove it manually only after review.
Keep the historical SDK out of production artifacts and commits. Onboarding
does not select it as the general CMake compiler or replace any modern NDK.

## Select and validate the candidate before a bounded pilot

Default discovery includes
`${XDG_DATA_HOME:-$HOME/.local/share}/nova3-team/toolchains/android-ndk-r8e`,
`/opt/android-ndk-r8e` and existing bounded `/opt` roots. A relative
`XDG_DATA_HOME` is ignored per XDG rules. `NOVA3_HISTORICAL_TOOLCHAIN_ROOT` has
first priority; repeated explicit `--root` options restrict discovery. No
recursive home scan or PATH execution occurs.

```sh
export NOVA3_HISTORICAL_TOOLCHAIN_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/nova3-team/toolchains/android-ndk-r8e"
python3 -m nova3_team.historical_toolchain onboard
export NOVA3_HISTORICAL_CXX="$NOVA3_HISTORICAL_TOOLCHAIN_ROOT/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++"
file -- "$NOVA3_HISTORICAL_CXX"
readelf -h -l -d "$NOVA3_HISTORICAL_CXX"
sha256sum -- "$NOVA3_HISTORICAL_CXX"
```

Some old packages use `prebuilt/linux-x86` instead: inspect the actual tree
and host ELF class/loader before selecting it. Discovery recognizes both host
directories but does not guess that either runs on current Arch. Review
compiler, cc1plus, assembler and linker dependencies with `file`/`readelf`,
without running `ldd` on untrusted code. Missing old loaders/libraries call for
a separately reviewed disposable environment, not arbitrary host packages or
system changes. A successful driver metadata query does not prove cc1plus or
the linker works. Missing/nonexecutable drivers, bad pins, execution failures
and metadata contradictions produce operator-visible statuses/warnings.

After provenance review, explicitly supply the **reviewed compiler SHA** to
authorize its metadata execution; a freshly computed local hash alone does
not establish trust. An optional source-owned candidate must also match a
separately reviewed SHA before any metadata execution:

```sh
python3 -m nova3_team.historical_toolchain onboard \
  --compiler "$NOVA3_HISTORICAL_CXX" --trusted-sha256 REVIEWED_COMPILER_SHA256 \
  --source reconstruction/research/arm32_cpu_query_candidate.cpp \
  --source-sha256 REVIEWED_SOURCE_SHA256
```

The compiler gate reuses `fingerprint` and `assess_metadata`: exact GCC 4.4.3
banner/dumpversion, `arm-linux-androideabi` target, nonempty specs, eight bounded
metadata queries, sanitized environment and before/after executable SHA checks.
The optional source identity check is hash-only, not behavior validation.
`onboard` exits 0 for missing/untrusted optional candidates, 2 for explicit
compiler rejection/unusable/unsupported-host checks or invalid arguments.
No trust pin is persisted by the launcher or TUI. Custom roots must be exported
in the actual worker/service environment; changing another terminal's
environment does not reconfigure a running service.

Immediate next step: use the existing `fingerprint`/`profiles` flow below,
review/pin assembler/linker and historical sysroot/header dependencies, and run
one bounded source-owned compiler pilot. Retain source/reference/compiler and
object hashes, exact flags, ARM/Thumb identity, errors and mismatches. Compare
the object and linked instruction window separately, with explicit relocation
mapping. Keep all original ELF/APK and machine bytes private. **NOT MATCHED**;
installation and green host tests establish no original match.

## #103 validation / handoff

Validated offline on Python 3.14.8, 2026-10-29:

| Check | Actual result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s nova3_team/tests -q` | Exit 0, **394 tests passed**, including 39 historical-toolchain tests and the unchanged #101 gate tests. |
| Control Center `test_toolchain.py`, `test_render.py`, `test_operator_ui.py` via `unittest discover -p` | Exit 0, **4 + 3 + 8 tests passed**; offline CLI, standalone bundle, no compiler/network calls, navigation/scroll/rescan and rendering. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/observatory/tests -q` | Exit 1, **78 tests: 76 passed, one failure and one error** in the preexisting `test_discovery_fix` tests; `systemd-analyze verify` is denied sandbox `SO_PASSRIGHTS` / `SO_PASSCRED` socket operations. Same blocker recorded by #101; no unrelated test/implementation repair. |
| Actual `historical_toolchain onboard`, launcher `--historical-toolchain instructions`, observer `--toolchain-setup` | Exit 0; discovery reported `AVAILABLE_BUT_UNTRUSTED`, `NOT_PROVEN` across 13 roots, without executing a candidate. Both guidance routes work offline. |
| Launcher `--self-test`, observer `--demo --once`, installer `bash -n`, `git diff --check` | Exit 0. |
| `python3 -m nova3_team.guard --repo . --all` and explicit guard of the new untracked test | Exit 0, 393 tracked files checked and new test passed. |

New archive tests use tiny inert signature fixtures, never an NDK download.
Metadata subprocesses are mocked. The documented extraction recipe itself is
executed only against generated source-owned tar fixtures, with its publisher
verification mocked: safe extraction succeeds, existing targets are preserved,
and traversal/escaping links (including links inside staging but outside the
final NDK root) are rejected with staging cleanup. This does not validate a real
NDK archive layout. An initial new launcher integration fixture omitted mock
stdout for familiar-checkout discovery; explicitly selecting its fixture repo
corrected the test. Final full repository suite passes.

Private local diagnostics:
`/tmp/nova103-team-tests.log`, `/tmp/nova103-observatory-tests.log`,
`/tmp/nova103-onboard.json`, `/tmp/nova103-launcher-guide.txt`,
`/tmp/nova103-observer-guide.txt`. Do not attach raw local discovery paths to
a PR. No original binaries/assets were needed or modified. No network access,
packages, sudo, compiler execution, real NDK extraction, GitHub changes or
source-match promotions occurred. The actual installer was not run because it
would write outside this checkout and its full suite hits the blocker above.

Handoff: review the ten allowed changed/new files, run the normal Control Center
suite/installer on a host permitting systemd validation, then deliberately
follow the manual candidate provenance/install steps above. Independently
review compiler/binutils pins and sysroot/source hashes before the bounded
compiler pilot and separate object/linked-window comparisons. Integrating the
separate launchers requires the narrow follow-up described above. PR creation
is left to the supervisor under this session's no-GitHub-state constraint.
Historical compiler and source matching remain **NOT MATCHED**.

---

# Task #99: historical ARM GCC provenance gate (prior offline checkpoint)

The counts and unavailable-checksum statements below describe the earlier
#99 session, before the task-supplied publisher checksum update above.

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
