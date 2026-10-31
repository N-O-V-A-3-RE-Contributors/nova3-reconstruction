# Unattended decompilation pipeline

Status: working and exercised on the Premium engine ELF (2026-10-31).
Owner path: `tools/pipeline/auto_decompile.py`.

## Why it exists

Three pieces already existed but nothing joined them, so "start decompilation and
walk away" was not actually possible:

- `tools/index/inventory.py` builds `analysis_db/index.sqlite` (symbols, DWARF
  compilation units, function rows, edges) — but it crashed on a fresh worktree
  with `sqlite3.OperationalError: unable to open database file`, because
  `analysis_db/` is gitignored and therefore absent from every clean checkout.
- `tools/ghidra/export_batch.py` exports one bounded batch of disassembly,
  decompiled C, calls, references and basic blocks — but it wrote its Ghidra
  project under the repository, which Ghidra refuses when any path element
  starts with `.` (`Path element starting with '.' is not permitted`), so every
  isolated worktree under `.local/` or `.cache/` failed on import.
- Nothing checkpointed between batches, so a timeout lost the run's position and
  the operator had no coverage number to resume from.

All three are fixed. Both defects were reproduced, then fixed, then re-run.

## One command

```bash
.venv/bin/python tools/pipeline/auto_decompile.py --status        # coverage only
.venv/bin/python tools/pipeline/auto_decompile.py                 # until no progress
.venv/bin/python tools/pipeline/auto_decompile.py --max-functions 8 --batch-size 4
```

Defaults: Premium ARMv7 engine ELF, batch size 8, 900 s per batch, checkpoint at
`.cache/pipeline/auto_decompile_state.json`, log at `.cache/logs/auto_decompile.log`.
Both paths are gitignored; the exports under `generated/ghidra/exports/<sha256>/`
are private and never committed.

## What one run does

1. Hashes the reference ELF and aborts (exit 2) if `--expected-sha256` disagrees,
   because every export is keyed on that hash.
2. Bootstraps the index by running `tools/index/inventory.py` when the database
   is missing or has no rows for that binary (~43 s for the 18.7 MB Premium ELF:
   4 binaries, 76,344 function rows total, 39,123 for the Premium engine).
3. Runs `tools/ghidra/export_batch.py --all-symbols --start 0 --limit <batch>`
   repeatedly. Each batch is a separate process, so a crash or timeout costs at
   most one batch; already-written exports are skipped, which makes the run
   idempotent and resumable.
4. Writes the checkpoint JSON after every batch and prints a coverage summary:
   `indexed`, `exported`, `remaining`, `new_this_run`.

## Measured run (2026-10-31, this worktree)

```
start: sha256=84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf indexed=39123 exported=0
batch: +2 exports in 68.7s (rc=0)      # cold: Ghidra ELF import dominates
batch: +2 exports in 22.9s (rc=0)      # warm project, next two by symbol address
summary: indexed=39123 exported=4 remaining=39119 new_this_run=2
```

The four exports are `0x000de2cc` (`_Z10ComputeCRCPhi`, 84 B, 20 instructions,
5 basic blocks), `0x008d890c` (`_ZN3glf10CrcChecker5CRC32EPKvij`, 24 B), and the
two recovered cpu-features getters `0x000d2f10` / `0x000d2f3c` — i.e. the
pipeline reproduces functions that were previously recovered by hand, and
`byte_mapping_validated` is true for each (ELF VA → Ghidra byte check).

Projected full-ELF cost at the measured warm rate is ~22 s per 2 functions for
this hardware, i.e. on the order of 100 hours of wall clock for all 39,123
functions — which is why batches are bounded, checkpointed, and driven by
priority slices rather than run to completion blindly.

## Ghidra project location

The project must live on a path with no `.`-prefixed element. Default is
`~/nova3-analysis/ghidra` (override with `--project-dir` or
`NOVA3_GHIDRA_PROJECT_DIR`). Do not move it under `.cache/` or `.local/`: Ghidra's
`ProjectLocator` rejects such paths before any import happens.

## Limits (measured, not assumed)

- Import runs with `analyze=False`; exports are "ELF import plus selected
  disassembly, no global autoanalysis". Decompiler output is therefore
  incomplete for functions that depend on whole-program type/flow analysis.
- One heavyweight Ghidra process at a time on the maintainer's N5100 (8 GiB).
  The driver is serial by design.
- An export is evidence, never a completion state: it is at most `DECOMPILED`.
  `UNDERSTOOD`/`IMPLEMENTED`/`TESTED` still require the per-function work the
  rest of this repository does.
