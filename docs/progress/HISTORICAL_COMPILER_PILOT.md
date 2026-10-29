# Historical GCC compile pilot (#104)

This is an offline owner tool awaiting a private local run after review/merge.
No historical compiler or Premium comparison was executed by this worker.
Synthetic tests exercise the machinery; original source equivalence remains
**NOT MATCHED**, and the original compiler/version/flags remain **UNKNOWN**.
The historical Google package checksum identifies a distribution candidate,
not Gameloft's build. Local executable SHA pins authorize metadata queries and
compilation; they do not authenticate that distribution or sandbox malicious
executables. Review the NDK installation and independently approve all pins.

The CLI fixes the Premium reference identity, A32 address `0x000d2f10`,
44-byte extent, window hash and existing `block_000d2f10.cpp` candidate.
It never modifies the source. It reuses `historical_toolchain`'s flag and
metadata gates, `compiler_matrix`'s source bound, `object_compare` and
`relocation_probe`; the separate runner accommodates explicit GCC include
paths without changing those modules or the existing Clang pilot.

Owner command, from the reviewed repository root (Bash). Use the SHA values
from your independently reviewed local toolchain fingerprint, not values
blindly computed and automatically trusted by this command. The three tool
paths must match GCC's selected assembler/frontend. Set `NOVA3_NDK_ROOT` to
the installed r8e directory and `NOVA3_GCC_PREBUILT` to its GCC 4.4.3 prebuilt
host directory; neither variable points to a downloaded installer.
Set `NOVA3_SYSROOT` to the chosen existing ARM platform sysroot. Header search
order below is an explicit hypothesis; adjust paths only to actual reviewed
r8e directories. No path or tool is discovered or executed via PATH by the
pilot itself (the shell launcher uses the owner's Python).

```bash
bash tools/run_historical_pilot.sh \
  --ndk-root "${NOVA3_NDK_ROOT:?set reviewed local NDK directory}" \
  --compiler "${NOVA3_GCC_PREBUILT:?set reviewed GCC prebuilt directory}/bin/arm-linux-androideabi-g++" \
  --compiler-sha256 "${NOVA3_GXX_SHA256:?set approved executable SHA256}" \
  --assembler "$NOVA3_GCC_PREBUILT/bin/arm-linux-androideabi-as" \
  --assembler-sha256 "${NOVA3_AS_SHA256:?set approved executable SHA256}" \
  --cc1plus "$NOVA3_GCC_PREBUILT/libexec/gcc/arm-linux-androideabi/4.4.3/cc1plus" \
  --cc1plus-sha256 "${NOVA3_CC1PLUS_SHA256:?set approved executable SHA256}" \
  --sysroot "${NOVA3_SYSROOT:?set reviewed ARM platform sysroot}" \
  --include "$NOVA3_NDK_ROOT/sources/cxx-stl/gnu-libstdc++/4.4.3/include" \
  --include "$NOVA3_NDK_ROOT/sources/cxx-stl/gnu-libstdc++/4.4.3/libs/armeabi-v7a/include" \
  --include "$NOVA3_GCC_PREBUILT/lib/gcc/arm-linux-androideabi/4.4.3/include" \
  --reference "${NOVA3_RETAIL_ELF:?set local Premium reference}" \
  --source reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp \
  --profiles 8 --timeout 30 \
  --out ".cache/historical-pilot-$(date +%Y%m%d-%H%M%S)"
```

The expected caveat is material: this candidate uses `<cstdint>` and C++17
nested namespace syntax, plus a context-pointer ABI instead of the original
no-argument global-state ABI. The runner preserves the existing historical
`-std=c++03` guard. **A compile failure is expected to be possible**; no shim,
rewritten candidate, C++17 flag, removed ABI flag or Clang fallback conceals
it. Inspect each private `stderr.log` for the actual diagnostic before
asserting a specific local failure. A separately scoped source change would
be needed for a historical-language/ABI candidate.

Four to eight sequential hypotheses cover `-O2`, `-Os`, `-O1`, `-O3`, first
with frame-pointer omission and then retention. All keep A32, ARMv7-A,
NEON, softfp and PIC. Only the explicit pinned compiler, assembler and
`cc1plus` are authorized; GCC's selected support paths and banners must agree.
`-B` selects an isolated helper directory. `-nostdinc/-nostdinc++` and explicit
includes/sysroot suppress implicit header searches. Search trees are hashed
with entry/byte limits, reject symlinks/special files, and must stay inside the
explicit NDK root. Source/header/tool changes block the run. Each compile has
a 1–30 second wall timeout, CPU/address-space/output limits and process-group
cleanup. A compile timeout stops further profiles. No original code is run.

`--out` must be a new directory under the repository's ignored `.cache`.
It holds private metadata, local paths, logs and candidate `.o` files, never
production inputs. Reports checkpoint before metadata execution and each
compile, including failures. Earlier input rejection returns a sanitized
message without creating a report. Do not publish these files or unredacted
logs. No extracted reference object, original assembly, `.ld`, full `.so`, or
original-binary copy is generated. Supporting dynamic host libraries/specs remain properties of
the owner's reviewed installation, not independently authenticated inputs.

Raw reports compare function mode, size, bytes and relocations. With a
same-size A32 candidate, optional repeated `--target SYMBOL=0xVALUE` arguments
supply **explicit candidate link assumptions**, never inferred original
relocations. Only supported external A32 CALL/REL32 relocations are applied
to scratch bytes in memory; missing/unused mappings and unsupported types
are reported. Raw and resolved hashes/counts remain separate. A resolved
window equality is **not** a linked-ELF comparison: no linker executes and
`linked_window_match` stays `NOT_TESTED`. Actual linked-window validation
requires a separately reviewed experiment. No ABI, behavioural, historical
compiler, original `.o` or full-library claim is promoted even on equality.

Exit `0` means all requested candidate objects were compared, including
mismatches; `1` means a compile failed/timed out or object comparison was
rejected; `2` means input/preflight rejection or a blocked run. The top-level
completed status is `NOT_MATCHED`; read per-profile statuses to distinguish
compile failure, comparison rejection, mismatch, unresolved relocation and
narrow equality observations. It is never a match gate.

Worker verification (offline; no NDK/Premium required):

```sh
python -m unittest nova3_team.tests.test_historical_compile_pilot \
  nova3_team.tests.test_historical_toolchain nova3_team.tests.test_object_compare \
  nova3_team.tests.test_relocation_probe nova3_team.tests.test_compiler_matrix -q
bash -n tools/run_historical_pilot.sh
```

Result: all 66 tests passed (17 pilot tests plus related component tests),
and shell syntax / runner `--help` checks exited zero. Compiler subprocesses
in the comparison tests are mocked; the real timeout/logging test executes
only source-owned Python snippets, with no game or NDK executable.

Resume: run these tests, review/merge the four allowlisted files, then have the
owner run the command above privately. Review actual compiler diagnostics
first; retain `NOT_MATCHED`/`UNKNOWN` unless independent evidence establishes
a narrower claim. This slice adds comparison tooling, no reconstructed game
functions or end-to-end game functionality.
