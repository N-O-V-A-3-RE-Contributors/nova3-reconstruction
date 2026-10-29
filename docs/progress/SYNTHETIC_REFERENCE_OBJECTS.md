# Task #118: explicit-TU synthetic reference staging

The independent offline entry points are
`python -m nova3_team.synthetic_reference_objects` and
`python tools/stage_reference_objects.py`. They use the existing `ArmElf`
header/section/PT_LOAD reader, with additional manifest, mapping, symbol,
relocation and output validation. No changes to `object_matching`, compiler
research, TU inference or recovered C++ are needed. Only Python's standard
library and existing repository modules are required; no installation,
network access, original executable execution or APK modification occurs.

Every staged run is **SYNTHETIC_REFERENCE / ORIGINAL_O_NOT_RECOVERED**.
Source matching and whole-library matching remain **NOT MATCHED**. This is
private comparison infrastructure, with no new reconstructed game functions
or end-to-end gameplay claim.

## Explicit owner manifest, version 1

The following is a fabricated example, with fictional addresses and a SHA
placeholder. Replace it with reviewed **local** metadata before use. JSON
addresses and sizes must be integers in ELF virtual-address space. A Thumb
address is even; the entry bit is represented separately by `mode`.

```json
{
  "schema": "nova3.explicit-tu/1",
  "reference": {
    "sha256": "<64 lowercase hexadecimal characters>",
    "edition": "premium"
  },
  "groups": [
    {
      "id": "owner_tu_001",
      "functions": [
        {
          "id": "function_a",
          "address": 4096,
          "size": 8,
          "mode": "arm",
          "symbol": {"kind": "elf", "name": "fixture_function"},
          "confidence": 0.5
        },
        {
          "id": "function_a_alias",
          "address": 4096,
          "size": 8,
          "mode": "arm",
          "symbol": {"kind": "elf", "name": "fixture_alias"},
          "alias_of": "function_a"
        }
      ]
    }
  ]
}
```

Only the displayed fields are accepted. `confidence` and `alias_of` are
optional. IDs match `[A-Za-z][A-Za-z0-9_]{0,63}`; function IDs are globally
unique. Modes are exactly `arm` or `thumb`; confidence is a finite number
in `[0,1]`, retained as an owner assertion. Groups are explicit hypotheses,
never automatically proven original translation units. No class-name or
donor heuristic runs here.

`symbol.kind: elf` requires an exact defined STT_FUNC name in SYMTAB or
DYNSYM. All definitions of that name must agree with the supplied address,
size, mode and section-to-segment mapping. Duplicate consistent observations
in the two tables are allowed. `symbol.kind: owner` permits an explicitly
named stripped/unlabelled window, recorded as `OWNER_ASSERTED_ONLY` when
absent from the ELF. It cannot override a contradictory existing definition.
Original names never become assembler directives; generated safe labels are
`synthetic_<id>`. These checks establish metadata consistency, not decoded
instruction boundaries, function behaviour, or source-level equivalence.

Aliases must point directly to a non-alias function in the same group and
have identical address, size and mode. Forward references are supported.
Detached aliases, chains, cycles, unmarked overlaps and overlapping file
windows at different linked addresses are refused. Each alias receives its
own separately labelled synthetic snapshot; alias placement in an original
object is not inferred.

The explicit CLI SHA pin, manifest SHA and actual input digest must agree.
The CLI edition must agree with the manifest (`premium`, `freemium`,
`cyrus-donor`, or `synthetic`). Edition is an owner label bound to that hash,
not something inferred from an ELF header or filename. A falsely labelled,
consistently pinned manifest cannot be identified without external edition
evidence. This tool does not import donor mappings automatically.

## Offline staging

From the checkout, with a reviewed manifest under ignored `.cache`:

```sh
python tools/stage_reference_objects.py \
  --reference /local/path/to/linked-reference.so \
  --manifest .cache/owner-tu-manifest.json \
  --expected-sha256 "$REVIEWED_REFERENCE_SHA256" \
  --edition premium \
  --out .cache/synthetic-reference-run-001 \
  --group-assembly
```

The output directory must be new, including when an existing directory is
empty. It must be below this checkout's ignored `.cache/`. Output symlinks,
symlinked directory components below the checkout, parent traversal and
overwrites are refused. Created directories are 0700 and files are 0600.
Reference inputs are opened read-only; final-component input symlinks and
nonregular inputs are refused. The POSIX implementation uses `O_NOFOLLOW`
and directory descriptors; run it in an owner-controlled checkout and cache.
Do not race another process changing its scratch directories or pinned tools.

Default output:

```text
manifest.json              SHA, explicit identities, window hashes, claims
asm/<function-id>.s         mode-labelled .byte snapshots, private research
relocations/<id>.json       only observed linked REL/RELA record starts
link-layout.json           selected original linked positions and file offsets
objdiff.json               reference/<id>.o vs candidate/<id>/build.o template
README.txt                 privacy and synthetic-reference limitations
groups/<group-id>.s         optional concatenated function snapshots
```

No assembler, compiler, linker or objdiff process runs by default. Reference
and candidate `.o` paths in `objdiff.json` are placeholders until built.
Automatic objdiff builds and completion flags remain disabled. Unit names
carry `SYNTHETIC_REFERENCE`; README and manifests apply
`ORIGINAL_O_NOT_RECOVERED` to the entire run.

Supported input is little-endian ELF32 EM_ARM EABI5 ET_DYN with conventional
section headers and executable, uniquely file-backed PT_LOAD windows. ARM
windows require four-byte alignment and extents; Thumb requires two-byte
alignment and extents. BE8/LE8, extended section numbering, sectionless ELF,
packed Android relocations and RELR are unsupported. Dynamic relocation
pointers must have exact conventional REL/RELA section coverage. The ARMv7
assembler target is explicit; ELF headers alone do not prove CPU attributes.

Bounds: 64 MiB reference, 1 MiB JSON inputs, 64 groups, 128 functions,
64 KiB per function, 4 MiB total selected windows (including aliases),
250,000 symbol entries, 16 MiB aggregate symbol text, 250,000 surviving
relocation records and 8,192 selected worksheet records across all functions.
Counts above bounds are refusals, not silently truncated analyses.

The worksheet records actual r_offset VAs, table/index/file offsets,
relocation type numbers, referenced symbol metadata and observed RELA
explicit addends. REL implicit addends, prelink addends, missing original
relocations and relocation widths/effects remain UNKNOWN or uninterpreted.
An empty worksheet means no surviving record **starts** inside the window;
it does not rule out a relocation straddling a boundary. No relocation is
invented from opcodes or emitted into the synthetic assembly. The layout
worksheet records selected positions only; it emits no linker script, gap
bytes, original-object placement or relink-to-original-SHA recipe.

## Explicit optional assembly

Both `--assemble` and `--assembler-profile .cache/owner-assembler.json` are
required. A profile alone does not authorize any process. The profile is:

```json
{
  "schema": "nova3.synthetic-assembler/1",
  "kind": "clang",
  "executable": "/absolute/non-symlink/path/to/clang",
  "sha256": "<SHA-256 of the executable file>",
  "version_sha256": "<SHA-256 of raw combined --version stdout/stderr>"
}
```

Use the executable's real resolved path. Pin version output with environment
`PATH=/usr/bin:/bin`, `LC_ALL=C`, `TZ=UTC`, `SOURCE_DATE_EPOCH=0`; the tool
uses exactly these environment entries. Compute/review the two hashes locally
and create the profile privately; do not invent pins or paste tool output
containing private paths into GitHub. Version probing occurs only after
explicit enable. The executable hash is also rechecked after assembly.
Profiles pin the invoked executable, not an entire historical toolchain or
its implicit dependencies; the executable must be trusted by the owner.

`kind: clang` uses fixed flags
`--target=armv7a-none-eabi -march=armv7-a -x assembler -c`.
`kind: gnu-as` uses `-EL -march=armv7-a`. Both append the generated relative
assembly path and `-o reference/<id>.o`. Arbitrary flags, shell commands and
extra inputs are unavailable. Version probing has a five-second timeout;
each assembly has a 30-second timeout, sequentially. The resulting object
must be ARM EABI5 ET_REL with the selected function bytes/mode and no
relocation starts inside that function. Object `.comment` labels identify
the synthetic origin. Build records retain flags, commands, return codes,
ASM hashes and object hashes; local tool output stays in private logs.

Repeat the staging command with both optional arguments and a **different new
output directory**. Failures roll back that run's generated tree and any new
empty parents, leaving existing outputs and the reference untouched. Missing
tools, pin mismatches, assembler failures, timeouts and invalid objects fail
closed. CLI failures return 2 and print a sanitized message without paths,
symbol names or bytes; the Python API preserves exception details for local
diagnosis. Failed scratch logs are removed during rollback. Success returns
0 and prints only the synthetic labels and `NOT MATCHED`.

## Downstream contract and verification

1. The owner reviews exact TU grouping, window bounds, symbols and modes
   against a separately pinned local linked ELF. This tool stages only that
   manifest; future donor-catalog integration needs a separate adapter.
2. Optionally assemble synthetic references. Linked branch/literal addresses
   remain resolved bytes, not recovered prelink references. `.byte` snapshots
   may have data mapping symbols; objdiff disassembly requires separate
   instruction/data review. Even a byte-identical synthetic rebuild proves
   only byte preservation by this harness.
3. Separately prepare candidate C++ or temporary private global-asm objects at
   `candidate/<id>/build.o`. Configure objdiff symbol mapping between safe
   `synthetic_<id>` labels and candidate names as needed. This tool creates
   no C++ wrappers, candidate sources, or production build inputs.
4. Record actual candidate source SHA, compiler/assembler/linker versions,
   flags, objects and explicit relocation/link assumptions. Compile and link
   candidates independently, use objdiff on the object pair, then compare
   original linked instruction windows with independent boundary/mode,
   relocation and behaviour evidence. Existing `object_compare` requires its
   own ELF-symbol validation; it does not automatically accept owner windows.
5. Keep source present, host tested, original behaviour compared, ARM object
   built, instruction-window matched and whole-library matched as separate
   claims. No result here promotes any of those grades. Synthetic ASM/objects
   must never enter production targets, releases, commits or GitHub uploads.

Task validation (2026-10-29): 28 synthetic-only tests pass (exit 0), exercising A32/Thumb,
SYMTAB/DYNSYM, REL/RELA, aliases, malformed/unsupported ELF metadata, mapping
and hash/edition errors, privacy/permissions, rollback and deterministic runs.
An optional integration test uses the installed **Clang 23.1.3** with the
fixed flags above to build two runs' synthetic objects and check identical
bytes. The same test skips when Clang is unavailable. No proprietary fixture
or original game input was used. Objdiff is not installed in this checkout;
configuration paths/shape are fixture-tested, with GUI/CLI consumption pending.
The 33 existing object-matching, object-compare, relocation-probe and
linker-worksheet regression tests also pass (exit 0). The first new-suite run
exposed a refusal-test helper checking the wrong output directory; that
helper was fixed before the successful runs.

```sh
python -m unittest nova3_team.tests.test_synthetic_reference_objects -v
python -m unittest nova3_team.tests.test_object_matching \
  nova3_team.tests.test_object_compare nova3_team.tests.test_relocation_probe \
  nova3_team.tests.test_linker_worksheet -v
```

Handoff: all work is confined to the four task-allowed files. To resume,
run that command and review this contract before supplying any real private
manifest. GNU-as execution, real-game manifests, donor adapters, candidate
source compilation/linking and objdiff comparisons remain downstream work;
no original compiler, prelink object or machine-code equivalence is established.
