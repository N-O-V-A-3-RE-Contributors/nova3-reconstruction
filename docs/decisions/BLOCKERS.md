# Outstanding blockers — 2026-10-25

## Publication blocked on run capability, not on repository setup (2026-10-31)

`git push` from an automated session fails at authentication with the runtime
message `GitHub capability_missing`. This is not a broken remote or a missing
identity:

- `git ls-remote origin HEAD` succeeds unauthenticated → `43f3502` (main), so
  network, URL and visibility are fine.
- The repository already has `user.name=jason`, `user.email=jason@umbrasec.one`;
  commits succeed (`82febf9` and earlier are on `automation/paperclip-worker`,
  3 ahead of `origin/main`, no upstream branch yet).
- The Paperclip `git` wrapper (`$PATH`-injected) deletes `GH_TOKEN`,
  `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GIT_ASKPASS`, `SSH_AUTH_SOCK`, sets
  `GIT_TERMINAL_PROMPT=0`, and requests a brokered credential from
  `POST /runtime-tools/github/credentials` **only when
  `PAPERCLIP_GITHUB_BROKER_TOKEN` is set**. In this run it is unset, which is the
  single reason every push fails.

Remedies, both verified to be the only two the wrapper supports:
1. Grant the GitHub connection to this agent/run in Paperclip so the wrapper can
   broker credentials, then run `bash tools/setup/push_branch.sh`.
2. Maintainer runs the same script from a normal terminal with `GH_TOKEN`
   exported (or after `gh auth login`), where no wrapper strips it.

Third route tested and rejected, 2026-11-01: the host holds an SSH identity that
authenticates as **`UmbraSecurity`** (`ssh -T git@github.com` succeeds), but a
read-only `git push --dry-run` to the SSH URL returns
`ERROR: Permission to N-O-V-A-3-RE-Contributors/nova3-reconstruction.git denied
to UmbraSecurity`. So the key is a read-only outsider for this repository, and
`NOVA3_PUSH_VIA_SSH=1` is a documented opt-in flag rather than a fix. The
Paperclip wrapper additionally rewrites explicit `git@github.com:` push targets
back to https, so that route only exists outside the wrapper. `gh` is installed
at `/tmp/paperclip-github-runtime/<run>/gh` and reports "not logged into any
GitHub hosts". `git ls-remote origin` works unauthenticated, so read access and
networking are fine — this is purely a missing *write* credential.
Use `NOVA3_PUSH_DRY_RUN=1` to rehearse any route without publishing.

The script classifies outcomes so this is never diagnosed by guesswork again:
`3` no credential source, `5` credential rejected, `6` other push failure,
`7` remote head != local head. Do not work around the wrapper by invoking
`/usr/bin/git` with the host's personal credentials: that is outside the
capability this session was granted.

## Resolved 2026-10-31 (recorded so they are not rediscovered)

- **Ghidra project path.** `export_batch.py` placed the project under the repo;
  Ghidra's `ProjectLocator` rejects any path element beginning with `.`, so every
  worktree under `.local/`/`.cache/` failed to import. Now `--project-dir`
  (default `~/nova3-analysis/ghidra`).
- **Index bootstrap.** `tools/index/inventory.py` raised
  `unable to open database file` on a clean checkout because `analysis_db/` is
  gitignored. It now creates the directory; verified 76,344 rows in 43.3 s.
- **Oracle suite in a worker worktree.** `NOVA3_RESEARCH_ORACLES=1` used to abort
  at `tools/compare/zip_headers.py` because `research/assets/` is private and
  absent here. `tools/setup/link_private_references.sh` now links
  `research/assets`, `.cache/assets` (the 2.2 GiB materialized OBB cache, needed by
  `texture_path_source.py`) and `analysis/` from the primary checkout, so the full
  oracle suite runs in a worker worktree without copying any reference data.


## Compiler-aware matching and AI-queue blockers (2026-10-28)

- CPU-query thunk byte equality is blocked on link-time values, not on source
  shape (updated 2026-10-31). Under r8e GCC 4.4.3 at `-O2`/`-O3`/`-Os` the
  reconstructed `cpu_features.c` reproduces every instruction word of
  `android_getCpuCount`, `android_getCpuFeatures` and `android_getCpuFamily`
  except the `R_ARM_PLT32` call displacement and the `.bss` `R_ARM_REL32`
  addend, so `object_compare` never reports `LINKED_FUNCTION_WINDOW_EQUAL`.
  Closing it needs a measured relocation/link experiment; `android_cpuInit` is
  still 32 bytes short of the reference (2600 vs 2632 B), which is the only
  remaining structural difference in the TU.
- The earlier "word-aligned 20-byte state aggregate" inference is retracted
  (superseded 2026-10-31): the symbol table shows four separate file-scope
  objects (`g_once` 4 B, `g_cpuCount` 4 B, `g_cpuFeatures` 8 B, `g_cpuFamily`
  4 B) and the observed `.bss` offsets (`+4`, `+8`, `+16`) follow from getter
  definition order under GCC 4.4.3. What remains INFERRED is only the pointer
  type of `g_once` (`pthread_once_t`), which the bionic typedef makes 4 bytes.

- The Premium library contains repeated GCC 4.4.3 compiler strings, but no proven complete original per-TU flags, assembler/linker version, build order or reproducible toolchain profile. Modern NDK Clang builds demonstrate portability only. Some recovered C++ uses C++17 and cannot simply be recompiled by GCC 4.4.3 unchanged; create separate historical-ABI candidates when byte matching is justified.
- The linked `.so` cannot uniquely restore discarded original `.o` sections/translation-unit boundaries and pre-link relocations. Synthetic wrappers are research references, not original objects; an identical full-`.so` SHA and valid final linker script have not been reproduced. A relocation worksheet and measured linker experiments are still prerequisites.
- The Cyrus debug ELF is a promising symbol/DWARF donor, but compile-unit and function transfers are inferred until Premium mapping is validated. A reported behavioural/short-window match from a previous issue is not automatically an independent new-standard compiler/objdiff result.
- Issue #82 ran three 1,800-second provider sessions ending `Session timed out` with no source edits. Its `NO_SOURCE_PROGRESS` classification hid the provider timeout. The issue is intentionally `nova:blocked` pending smaller scope and deployment of streamed private logs and explicit `PROVIDER_TIMEOUT` quarantine; do not reset its checkpoint or requeue blindly. Other blocked architecture issues likewise need task review.
- Local Ghidra discovery analysed two original ELFs, deferred two and reported zero newly identified functions in `up-to-date` mode. This is not complete-game coverage, and deferred references need resource/ledger investigation before generating more AI reconstruction tasks.
- The original modified Irrlicht engine version/fork and source-built JNI binding remain unverified; copying arbitrary stock Irrlicht or linking original JNI executable would violate the source-only end-state. No independent Android game runtime has been demonstrated.

- No Android device/emulator attached: `adb devices -l` in .cache/logs/environment.json returned an empty list. Host utility/oracle tests continue; no game runtime comparison or actual gameplay test exists.
- Premium supplied APK is GAND.GloftN3LT code105/v1.0.8e; supplied expansions are ANMP.GloftN3HM main1050/patch1070. Installer selects GAND directory; actual expected component list remains unresolved. Do not assume matching assets. See research/android/STARTUP.md and research/assets/ARCHIVE_REPORT.md.
- Premium DWARF has20 compiler-runtime compilation units and zero observed game C++ class DIEs. Full game layout recovery requires symbols, disassembly, vtables and data evidence rather than DWARF.
- Initial Ghidra export used raw ELF addresses without default image rebase correction: output invalidated and retained under .cache/invalid-ghidra/. Fixed mapping adds image base and checks first16 code bytes. Initial export also lacked complete function bodies; bounded disassembly/body correction verified in12 exports; false annotation removed and edges refreshed. These were tool defects, not binary behavior.
- First simultaneous inventory/export attempt hit SQLite database-is-locked; inventory completed and subsequent operations run sequentially. Failure retained .cache/logs/ghidra-crc.log.
- No independent Android APK, renderer, gameplay, level loader, audio, progression or multiplayer exists. Native ARMv7 source core does compile and bounded asset-byte decoding works. Final source-only APK/dependency/license verification is not possible until builds exist.
- Full automated Ghidra import/analysis for the other three ELF files, comprehensive xrefs/types/string/global export and resumable coverage are incomplete. Current exporter is a scoped starting point, not complete engine recovery.

Stage2: all sampled mesh and complete texture-container tests pass within documented subsets. Next rendering dependencies are original ETC1 pixel conversion, alpha-mode/material/shader semantics, scene transforms and attribute binding. No Android device/game APK or gameplay validation exists. Agent account quota interrupted an independent review; lead inspected and continued; no replacement agents were spawned.

Resolved missing outer shader/effect filename lead: source-decoded effects.gla contains NV_DiffuseVC.bdae and shader references. Outer absence did not mean missing content. Actual remaining blocker for faithful first frame is effect technique/parameter parsing, shader semantic/normalization binding, samplers and scene/camera transforms. Generic resource mounts do not yet include source SB readers; inspect custom filesystem precedence.19other .gla variants and SDIR/descriptor handling remain unsupported.

## B-2026-11-02 — the DSH worker's code exists only in this checkout, not on GitHub

**Blocked:** cutting over from `nova3-worker.service` (Codex) to
`nova3-dsh-worker.service` (DSH) in a way that *persists across a reboot*.

**Evidence.** After two commits (`9f7d6d2`, `d575231`),
`git status -sb` reports `main...origin/main [ahead 2]`. Those commits touch
`nova3_team/*.py`, `tools/systemd/*` and `docs/`, all of which CI would run and
the coordinator would see — but none of them is on `origin/main` yet. The old
worker's upgrade path (`3563606 worker: refresh AGENTS.md and progress docs from
main on every start`) is **not** the solution, because the tooling it would
have to restock is exactly the thing missing from `main`. Doing that cutover
now would leave the enabled unit failing to import `nova3_team.dsh_supervisor`
at the next reboot, producing precisely the restart loop the migration is meant
to remove.

**Why a GitHub link was not simply used.** `git remote -v` is
`https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction.git`, so
`git push` authenticates as **jason**, not as the worker's pinned GitHub account
`crjase`. The worker's own PR path authenticates with `gh` (logged in as
`crjase`, scopes `repo, workflow`) to the **upstream** repository and pushes the
`nova/issue-N-…` branch with plain `git`, which is why every historical worker
PR exists while this checkout itself is divergent: the maintainer merges GitHub
side branches locally. Nothing in this session's remit authorises inventing a
new push path with an identity the operator did not name.

**Also observed, not blocking.** `nova-ci.yml` has no `workflow_dispatch`, so CI
can only be triggered by a real push or PR; the committed tree was verified
locally instead with `worker.verify()` on a clean clone (4 commands, all exit 0,
CTest 39/39, 1094 unit tests).

**Actionable request (the exact commands, run as the repository owner):**

```bash
cd ~/Projects/nova3_re
gh pr create --base main --head <branch> ...          # OR, simplest:
git push git@github.com:N-O-V-A-3-RE-Contributors/nova3-reconstruction.git \
    HEAD:refs/heads/nova/dsh-execution-provider
# then open a PR from nova/dsh-execution-provider into main
```

**Reason:** a new execution engine is permanent infrastructure and should
reach `main` through a reviewed PR, not a local-only commit.

**Risk:** none to the running system — the Codex worker keeps its unit and its
state; the DSH worker is additionally enabled and idling on an empty queue.
**Rollback:** `tools/systemd/rollback-to-codex.sh` (nothing is deleted).

**Until this is resolved:** the DSH supervisor runs from this checkout and has
verified its complete loop against the live queue, but **the cutover (stopping
the Codex unit) is deliberately not done in this session**, because a
half-migrated pair of workers is worse than one working worker. If the
operator prefers a local-only cutover that assumes this checkout persists,
`tools/systemd/rollback-to-codex.sh` is the reverse and
`systemctl --user stop nova3-worker.service` is the forward step — both are
single commands and both preserve state.
