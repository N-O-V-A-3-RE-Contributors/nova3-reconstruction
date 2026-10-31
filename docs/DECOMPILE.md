# Decompiling NOVA 3 with this repository

Everything below runs locally. Reference APKs/ELFs/OBBs are read-only inputs and
are never modified, uploaded or committed; decompiler output stays in the
gitignored `generated/` tree.

## 0. The one command

```sh
./nova3
```

That prints the command list. There is no other entry point you need to know:
`./nova3` finds `.venv/bin/python`, `tools/workbench.py` does the work, and every
subcommand is a thin, tested wrapper around an existing script.

```sh
./nova3 doctor                  # can this machine decompile right now?
./nova3 index                   # build analysis_db/index.sqlite (once, ~45 s)
./nova3 decompile               # bounded Ghidra batch (default 8 functions)
./nova3 status                  # indexed / exported / remaining
./nova3 show 0x009dad14         # one function: facts + decompiled body
./nova3 search DetectNeon       # symbol search across 39,123 functions
./nova3 build | test | verify   # host build, CTest, full source-only audit
./nova3 push                    # publish the branch (needs GitHub credentials)
```

If `.venv` does not exist yet:

```sh
bash tools/setup/bootstrap.sh      # creates .venv, installs tools/setup/requirements.txt
bash tools/setup/link_private_references.sh   # links the private analysis/ trees into a worktree
```

## 1. `doctor` first, always

`./nova3 doctor` prints one line per dependency with `PASS` / `WARN` / `FAIL`
and, underneath any non-passing line, the exact command that fixes it. Exit code
is 1 when anything is `FAIL`, so it is safe to use in scripts.

What it checks, and why each one matters:

| check | why it can fail |
|---|---|
| reference: Premium engine ELF | the file must exist **and** hash to `84075223…baaf`; every export is keyed on that SHA, so a different build silently invalidates all previous work |
| reference: Freemium engine ELF | optional, comparative target only |
| python: project venv | `./nova3` runs `.venv/bin/python`; running the scripts with the system interpreter loses pyghidra |
| python: pyelftools + capstone | used by the indexer and every compare pilot |
| ghidra: installation / JDK 17+ / pyghidra | required for `decompile`; discovery is in `tools/pipeline/ghidra_env.py` (`NOVA3_GHIDRA_HOME`, `GHIDRA_INSTALL_DIR`, `/opt/ghidra`, `~/ghidra_*`) |
| ghidra: project directory | Ghidra's `ProjectLocator` refuses any path element starting with `.`, so the project cannot live under `.cache/` or `.local/`; default `~/nova3-analysis/ghidra` |
| disk: free space | a Ghidra project for the 18.7 MB ELF is GB-scale |
| index: function rows | tells you whether `./nova3 index` has been run |
| build / optional tools | cmake+ninja for the host tests; jadx/apktool/7z only for Android and archive work |

## 2. `index`, then `decompile`

The index (`analysis_db/index.sqlite`, gitignored) holds every symbol, DWARF DIE,
function row and call edge per binary. It is built by
`tools/index/inventory.py` and is what `search`/`show`/`decompile` read.

`decompile` is bounded and resumable by design:

```sh
./nova3 decompile                            # 8 functions, batch size 4
./nova3 decompile --max-functions 40         # longer bounded run
./nova3 decompile --all                      # until no progress (~100 h, Ctrl-C safe)
./nova3 decompile --address 0x009dad14       # just this function
```

Each batch is a separate process. A timeout or Ctrl-C costs at most one batch;
already-written exports are skipped, so re-running continues where the last run
stopped. Progress is checkpointed after every batch in
`.cache/pipeline/auto_decompile_state.json` and logged to
`.cache/logs/auto_decompile.log`.

Measured on the maintainer's N5100 (4 cores / 8 GiB): cold batch ~69 s (ELF
import dominates), warm batch ~22 s per 2 functions, i.e. roughly 11 s per
function. That is why the default is bounded and the full ELF is not a
"just run it" operation.

Output per function:
`generated/ghidra/exports/<sha256>/<address>.json` — symbol, size, ABI,
instructions, basic blocks, call edges, and the decompiled C.

## 3. Reading one function

```sh
./nova3 show 0x009dad14
./nova3 show _ZN3vox29DriverCallbackSourceInterface10DetectNeonEv
./nova3 show 0x009dad14 --c-lines 0     # facts only, no body
```

`show` prints the index facts (address, size, A32/Thumb, recorded state), the
Ghidra metadata, the call edges with their symbol names resolved through the
index, and the decompiled body. Addresses inside an export are Ghidra addresses
(`ELF VA + 0x10000`); `show` subtracts the image base before looking names up.

`search` is a plain substring match over the real symbol table — the Premium
engine keeps 107,418 symbols, so mangled C++ names are usually findable:

```sh
./nova3 search SkinTechnique
./nova3 search android_getCpu
```

## 4. What a decompiler export is — and is not

An export is at most `DECOMPILED` in this project's evidence scale. It is not
`UNDERSTOOD`, not `IMPLEMENTED`, and it never establishes behavioural
equivalence. The states are:

```
INVENTORIED -> DISASSEMBLED -> DECOMPILED -> UNDERSTOOD -> IMPLEMENTED -> TESTED -> VERIFIED
```

Turning an export into reconstruction means: read the window, write named C/C++
with explicit uncertainty for every inferred type, add a pilot that re-derives
the claim from the reference bytes, add a CTest behaviour test, and record what
remains unverified. `tools/compare/detect_neon_pilot.py` and
`reconstruction/recovered/libNOVA3_neon/` are the worked examples.

The import runs with `analyze=False` (ELF import + selected disassembly, no
global autoanalysis), so decompiler output for functions that need whole-program
type/flow analysis is incomplete. "Ugly but sound" beats "pretty but invented":
do not fill gaps with plausible-looking code.

## 5. When the early-development source arrives

An early public development codebase may be supplied. Treat it as **candidate
source**, not as the answer, and keep this project's evidence rules:

1. Put it under `reconstruction/third_party/<name>/` (or fetch it through
   `tools/setup/bootstrap.sh`) with the upstream commit pinned and its licence
   recorded in `docs/decisions/ARCHITECTURE.md`.
2. Run the licence audit before any of it reaches a production target; assets
   and code from the shipped game may not be imported on the strength of being
   "old source".
3. Diff its naming and structure against the reference ELF (symbol table, DWARF,
   call graph) — agreement is evidence, disagreement means our reading or theirs
   is wrong and must be resolved, not averaged.
4. It does not change any completion state on its own: `IMPLEMENTED`/`TESTED`
   still require our own compile and test run, and byte match still requires the
   recorded toolchain, flags and instruction-window comparison.

## 6. Publishing the branch (the one thing this harness cannot do)

The repository, remote, identity and workflows are already configured
(`nova-ci.yml`, `nova-autofeed.yml`, `nova-status.yml`, `nova-coordinator.yml`
all run on `main`). What is missing in an automated run is a **credential
source**: the runner's `git` wrapper strips `GH_TOKEN`/`GITHUB_TOKEN`/
`SSH_AUTH_SOCK`, so `git push` fails with `could not read Username`.

```sh
./nova3 push          # = bash tools/setup/push_branch.sh
```

It pushes `automation/paperclip-worker`, verifies the remote head equals local
HEAD, and classifies failure precisely: exit `3` = no credential source,
`5` = credential rejected, `7` = remote SHA mismatch. To publish from your own
terminal:

```sh
export GH_TOKEN=<your token with repo scope>   # or authenticate git over SSH
bash tools/setup/push_branch.sh
```

Do not route a personal token into an automated run's environment as a
workaround; granting the run the GitHub capability is the supportable route.

## 7. Troubleshooting

| symptom | cause | action |
|---|---|---|
| `nova3: no project virtual environment` | `.venv` missing | `bash tools/setup/bootstrap.sh` |
| `Path element starting with '.' is not permitted` | Ghidra project set under `.cache/`/`.local/` | unset `NOVA3_GHIDRA_PROJECT_DIR`, use `~/nova3-analysis/ghidra` |
| `no Ghidra installation found` | not in a searched location | `export NOVA3_GHIDRA_HOME=/path/to/ghidra` |
| `no JDK found` | `JAVA_HOME` unset and no JDK under `/usr/lib/jvm` | install `jdk-openjdk`, or set `JAVA_HOME` |
| `reference SHA-256 ... != expected` | wrong/different engine build | restore the original file; do not re-extract a different version |
| batch reports `stalled` with 0 new exports | exporter failed on that address | read the tail it prints and `.cache/logs/auto_decompile.log` |
| `ORA`/oracle tests fail in a worktree | private `research/assets` not linked | `bash tools/setup/link_private_references.sh` |

Every command is safe to re-run. Nothing here deletes an existing export unless
you pass `--retry`.
