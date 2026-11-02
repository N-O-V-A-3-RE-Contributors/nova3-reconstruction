# Owner control plane, contributors, and the current hold

Status: **automation setup complete; reconstruction is deliberately on hold**
until the owner supplies the pre-release source and says go. This page is the
operator's map. It does not claim any reconstruction milestone.

## Roles and authority

| Role | How it is verified | May do |
| --- | --- | --- |
| **Owner / admin** | live GitHub `ADMIN` permission, an active org owner, or a login in `coordination/admin_team.json` (the DSH team allowlist, mergeable only by an owner) | approve/queue tasks, approve waivers, merge, change policy, run owner commands |
| **Contributor (slave)** | any other authenticated account with repository access | claim a scoped task, edit only its `Allowed paths`, run its **own** AI/provider, test, push a branch, open a PR |
| **Anonymous / unverified** | none | nothing |

`nova3_team/authority.py` implements the classification and fails closed: an
unverified or missing role is never admin. Contributors keep full freedom over
their local model, key and prompts — that is local configuration, not project
authority. They never approve or merge their own work.

The owner/admin review is two-layered:

1. **Machine check** — `python -m nova3_team.review --repo OWNER/REPO --pr N`
   compares the PR against the issue's `Allowed paths` and flags protected
   trees, binaries, credentials and oversized changes. CI runs the same check
   for contributor PRs (`.github/workflows/nova-pr-review.yml`).
2. **Owner decision** — merge authority stays with the owner/admin. The worker
   and the review gate never merge.

## Owner commands

```bash
python -m nova3_team.dsh_status            # phone-readable worker status
python -m nova3_team.dsh_status --publish  # also post it to the status issue
python -m nova3_team.analysis              # inventory, coverage, ETA scenarios (--json)
python -m nova3_team.review --repo OWNER/REPO --pr N   # contributor PR audit
python -m nova3_team.autoadmit admit --repo OWNER/REPO --holder 16   # use an owner waiver
python -m nova3_team.fleet --task recon --cpu 4 --ram-gb 8           # model/host routing
python -m tools.dsh_loop.toolchain --tier project --plan             # per-OS toolchain
```

Worker lifecycle: `systemctl --user {status,restart,stop} nova3-dsh-worker`;
rollback to the legacy worker with `tools/systemd/rollback-to-codex.sh`.

## Release and contributor setup

`launcher-v7-*` is the current distribution generation. It ships:

* per-platform launchers (Linux x64, macOS Apple silicon/Intel, Windows x64)
  that install a per-user DSH runtime and ask for the contributor's **own**
  provider key;
* `nova3-source.tar.gz` for contributors without Git access (extract, run
  `install.sh` / `install.cmd`);
* the tracked source only — no APK/OBB/ELF data and no credentials.

A contributor needs their own GitHub account with repository access and their
own AI key. Nothing is sent to the owner, and the installer never writes their
key into a file — it prepares a shell/profile placeholder they fill in.

### Toolchain tiers (elevation is explicit)

`tools/dsh_loop/toolchain.py` declares, for every supported OS, what each tier
needs and the exact argv to install it:

* **core** — python, git, GitHub CLI (the worker itself);
* **project** — CMake, Ninja, a C/C++ compiler, JDK for Ghidra, archive tools;
* **full** — the optional analysis stack (JADX, Apktool, LLVM/binutils, Ghidra,
  Android NDK) plus manual links for tools with no reliable package source
  (IDA Pro, rev.ng).

Linux/macOS installs use `sudo`/Homebrew, Windows uses `winget`; nothing
escalates unless the user passes `--install`. Every command is an argv list, so
a package name cannot inject shell syntax.

## Model and host routing

`nova3_team/fleet.py` picks a configured model by task strength (code,
long-context, review, docs) and host capacity (cores, RAM, heavy-analysis
floor), and caps heavyweight jobs to one per small machine. It is a policy
layer and never contacts a provider; the operator supplies the configured model
names. Unknown models are eligible but lose ties rather than silently winning.

## ETA: read it as a scenario, not a promise

`python -m nova3_team.analysis` reports:

* **inventory**: 1,165 recorded function boundaries of 39,092 ELF functions
  (3.0%), `inventory_complete=false`;
* **recorded progress**: 18 mappings (7 matched ARM, 8 behavioural, 3
  utilities), sourced in `coordination/progress_state.json`;
* **rate**: measured over the recorded window, with a range and low confidence;
* **scenarios**: recorded-inventory burn-down, and a naive full-ELF floor that
  is explicitly *not* a completion claim.

The pre-release source the owner is supplying will change these inputs. Re-run
the analysis after it is indexed; the file says so, and the ETA is marked
`provisional` until then. Do not quote a completion date from it.

## Current hold

No reconstruction task will start until the owner gives the go-ahead and the
pre-release source is available. The queue is empty, planning is paused while
ten proposals await an owner decision, and the worker is idle. When the source
arrives:

1. index it, update `coordination/progress_state.json` and the ledger;
2. re-run `nova3_team.analysis` for a new baseline;
3. review the pending proposals and queue the ones the new evidence supports;
4. remove the hold and let the supervisor claim them.

Nothing in this hold affects the contributor release: the worker loop, the
owner gate and the review gate are ready to use.

## DSH plugin: `/nova` in any chat

The repository ships a real DSH/cordis plugin at
`dsh-plugins/nova3-control`. It registers one slash command in a DSH profile:

| Command | Runs |
| --- | --- |
| `/nova` or `/nova status` | `nova3_team.dsh_status` (worker, queue, open requests) |
| `/nova eta` | `nova3_team.analysis` (inventory, coverage, ETA scenarios) |
| `/nova review <pr>` | `nova3_team.review` (read-only compliance verdict) |
| `/nova fleet [task]` | `nova3_team.fleet` (model/host routing) |
| `/nova toolchain [tier]` | `dsh_loop.toolchain --plan` (per-OS tool plan) |

It is read-only: every subcommand is one of the audited CLIs above, and it never
merges, labels or reads credentials.

Install into a profile (a local checkout works; a published package is not
required):

```bash
# 1) make the package resolvable in the profile
ln -s "$PWD/dsh-plugins/nova3-control" \
  "${DSH_HOME:-$HOME/.local/share/nova3-dsh/data}/profiles/web/node_modules/@nova3/dsh-control"
# 2) add the loader row to that profile's cordis.patch.yml
#    - insert:
#        - id: nova3-control
#          name: '@nova3/dsh-control'
#          config: { repo: /absolute/path/to/nova3_re }
```

`dsh --profile web --dump-config` must show `id: nova3-control`. The plugin loads
when that profile next boots; restarting the live web profile ends the current
chat session, so do it when convenient (a reboot does it too).
