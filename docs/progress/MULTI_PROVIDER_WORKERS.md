# Bounded multi-provider AI workers (issue #126)

Optional execution capacity for one contributor: several **separately
authenticated** coding CLIs, each in its own local slot, while the GitHub
coordinator stays the single cross-machine authority. The conservative default
on the maintainer's Intel Celeron N5100 (4 cores / ~8 GiB) is **one active AI
process in total**. Nothing here changes the queue authorization model.

## 1. Stable per-worker identity

One worker identity is:

```
GitHub account  +  per-machine persisted UUID  +  explicit slot id  +  provider
```

* `~/.cache/nova3-team/<OWNER_REPO>/identity/machine.json` — one UUID per
  installation, created with `O_EXCL` and durable replace.
* `…/.nova-slots/<slot>/identity/worker.json` — account, machine UUID, slot,
  provider for that slot, written atomically (`fsync` + `os.replace`, `0600`).

Identity files are bound: reusing a slot file with a different account or a
different provider fails closed with `IdentityError`; use a new slot id. This
keeps recovery checkpoints attached to exactly one acknowledged worker rather
than silently reassigned.

### Non-destructive migration

Pre-slot recovery state lived directly under the repository state root. On the
first start of `slot-1`, `migrate_legacy_state()` **copies** (never moves)
`.nova-state/*.json` and `worker-failure.json` into the slot state directory
only when the destination file does not already exist. The originals remain in
place, so a downgrade or an interrupted migration still works. Later slots do
not inherit a second copy. Resources that are not files (a live provider
process, a GitHub lease) are never copied.

## 2. Local layout and collision-proof checkouts

```
<cache>/nova3-team/<repo>/
  identity/machine.json            # per machine
  .nova-locks/slot-<slot>.lock     # one process per slot
  .nova-locks/work-<sha256>.lock   # one slot per work id
  .nova-slots/<slot>/
    state/    .nova-state/, worker-failure.json, scheduler-slot.json
    worktrees/                     # reserved per-slot checkout area
    cache/    logs/    identity/    presence/
```

Every mutable path is slot-scoped and `0700`; `assert_slot_isolation()` refuses
two slots that would share state, worktree, cache, log, identity or presence
paths, and a slot cache may never be the shared repository-level `.cache`.

### Locks

* POSIX (Linux **and macOS**): `fcntl.flock(LOCK_EX | LOCK_NB)`.
* Windows: `msvcrt.locking(LK_NBLCK, 1)` on a one-byte region.
* Locks are held by the OS until release or process death, so a crash leaves a
  stale *file* but no stale *owner*: the next start acquires it normally.

Before any provider launch the worker takes its per-slot process lock
(`acquire_slot_lock`) and, per task, a `CheckoutGuard` claim on the work id.
Two slots on the same machine can therefore never work the same issue or
branch. The GitHub lease (`/nova claim`) remains the cross-machine authority;
the local lock is additional, never a replacement, and does not change the
double-claim behaviour of `process_comment`.

## 3. Guided launcher

`nova3_easy.py` and `python3 -m nova3_team.worker slots` expose:

```
--providers codex,claude     installed/authenticated providers to consider
--slots N                    total active AI processes to plan (default 1)
--allow-multiple             explicit consent required when N > 1
--allow-duplicate-provider   explicit consent for one provider in 2 slots
--dry-run                    print commands, launch nothing
```

Selection rules (all fail closed):

1. only providers whose bounded probe reports `available` **and** whose vendor
   auth status is `authenticated` are eligible — login is never initiated and
   no readiness prompt is sent;
2. `N > 1` requires `--allow-multiple`; `N` is bounded by `MAX_SLOTS = 4`, the
   detected core count, the configured total and ~2 GiB of RAM per session;
3. the same provider twice needs `--allow-duplicate-provider`;
4. each plan slot is `slot-1 … slot-N` with one provider, and a preview of the
   exact argv list (`shell=False`) is printed before anything starts.

No shell interpolation, no password/API-key/token on the command line
(`assert_argv_safe` rejects credential-shaped args), no credential copies.
Example preview:

```
slot-1 [codex]: /usr/bin/python3 -m nova3_team.worker run --provider codex --slot slot-1 --repo OWNER/REPO
slot-2 [claude]: /usr/bin/python3 -m nova3_team.worker run --provider claude --slot slot-2 --repo OWNER/REPO
```

## 4. Scheduling, failure isolation, telemetry

`SlotScheduler` (one scheduler per launcher/worker process) provides:

* fair round-robin among idle slots (least-recently-started first);
* per-slot timeout (`--slot` session timeout, 300–1800 s) and cooldown;
* `record_failure()` maps to `reliability.failure_decision()`: one bounded retry
  then `blocked`; per-slot launch budget (6 starts/hour) prevents retry storms;
* quota/429/rate-limit responses use bounded exponential backoff
  (300 s → 3600 s) instead of a tight loop;
* `pause()` / `resume()` / `stop(slot, terminate=…)` affect exactly one slot;
  other slots keep their processes;
* `save()` / `load()` persist sanitized per-slot state; a record saved while
  `running` is restored as `idle` (crash recovery), cooldowns are preserved;
* telemetry is fixed metadata only (`slot`, `provider`, `state`, failure code,
  cooldown seconds) — no prompt, provider log, path or credential.

`presence.Reporter` is per slot (`presence-<repo>-<provider>-<slot>.json`, body
`v:2` with `slot`/`machine`), so concurrent slots no longer overwrite each
other's worker id/comment id. `presence.aggregate()` returns the sanitized
status rows for the Observatory; it never contains prompts or provider logs.

## 5. Preserved security invariants (#149 / #135)

* `launch_slots()` calls the owner/admin approval gate and pinned deployment
  preflight (`NOVA3_REPOSITORY_ID` / `NOVA3_OWNER_ID`) **before** it probes
  local locks, creates identity files or spawns any provider. Gate `None`,
  failed preflight, denied admission, or zero approved tasks means no launch.
* The order is fixed and tested: budget → preview → gate preflight → queue
  admission → local activity probe → spawn.
* `work_one()` still revalidates the owner approval immediately before push/PR;
  there is no legacy publish bypass, and the gate is consulted again inside
  each slot process (each worker calls `build_gate()` and fails closed with
  exit code 6).
* No auto-merge, no issue creation, no queue steering, no cross-machine
  scheduling. Slots are local execution capacity only; project-wide scheduling
  and spending remain owner/ADMIN authority.

## 6. Known limitations

* Slots are separate worker processes; work is not stolen between them. Fair
  sharing applies to a slot's own issue loop and the launcher plan.
* The launcher probes slot locks before spawning and releases immediately; two
  launchers racing on the same slot converge on fail-closed (the second child
  cannot acquire the slot lock), not on two writers.
* Provider auth for vendors without a documented offline status command stays
  `unknown` and is therefore not selectable until it can be verified; this is
  intentional.
* `worker-failure.json` for local setup refusals is written to the shared
  repository root (an exit marker, not recovery state); per-slot `.nova-state`
  records are never shared.
* Multi-process use still requires each provider to be installed and logged in
  by the contributor; it is never required for ordinary solo work.
