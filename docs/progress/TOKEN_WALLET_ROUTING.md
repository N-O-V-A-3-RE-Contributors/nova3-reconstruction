# Token wallet and evidence routing (issue #131)

Offline, stdlib-only scheduler primitives: `nova3_team/token_wallet.py`.
No worker launch, network call, provider login, prompt text, credential file or
paid inference exists in this module or its tests.

## Ownership boundaries

* **#126 owns collision prevention**: per-slot process locks, durable machine
  UUIDs, atomic GitHub leases, isolated worktrees and crash recovery. The wallet
  *consumes* `SlotAttestation` records and refuses slots without a fresh,
  healthy, process-locked, unexpired lease. It never mints, renews or fakes an
  attestation and does not implement a process lock.
* **#135 owns GitHub queue admission.** A wallet reservation is accounting and
  assignment bookkeeping, not permission to claim an issue or start a worker.
* The router returns proposals. A future worker must still pass #126/#135 and
  its own authorization checks. Importing or calling this module grants nothing.
* N5100 default: `SchedulerPolicy.max_active_slots == 1` and same-machine
  multi-slot is disabled. More slots require explicit opt-in plus #126's
  resource checks.

## Durable wallet

`Wallet` uses one SQLite connection and stores money as integer **micro-USD**
(no binary floats). `WalletPolicy` has two mutually exclusive modes:

* `metered_api` — USD/day and USD/month ceilings are mandatory; spend is
  measured from settlements.
* `subscription` — no USD limits (provider caps cannot be pooled or converted to
  dollars). Usage is reported as **UNKNOWN**. If `cap_enforcement_required` is
  true, every reservation fails closed with `UnmeasuredCap`.

Reserve → settle lifecycle:

1. `reserve(TaskRequest, now)` opens `BEGIN IMMEDIATE`, rejects a duplicate
   issue/branch, checks actual spend + active reservations + the **worst-case**
   ceiling (per-task estimate plus max context/output tokens at the reserved
   rates), then inserts the reservation and assignment atomically. Repeated
   calls for the same task are idempotent; a released task may be retried as a
   new attempt.
2. `settle(reservation_id, Usage | None, now)` records provider token usage.
   Fresh input, cached input, output, retry tokens, retry count and the
   OpenRouter-style credit fee are stored in separate columns and charges.
   `Usage.reported_total_micros` is honoured when supplied. Reporting is
   optional; `None` means the provider did not expose usage, in which case the
   reservation amount is charged **provisionally** and `usage_state` is
   `unknown` — never treated as free. Re-settling an unknown charge with later
   usage raises `UnknownUsage` so a person must reconcile it.
3. `release(reservation_id, reason)` atomically marks a never-charged
   reservation released and deletes its assignment, freeing budget. `complete`
   closes a settled assignment without refunding spend.
4. `recover(now)` marks expired in-flight reservations `orphaned` but keeps the
   money reserved. Automatically refunding a crash could hide real spend; an
   operator releases it with a reason after checking the provider statement.

A price snapshot is stored with the reservation. Supplying different rates at
settlement raises `PriceChanged` before any mutation. Dispatch requires an exact
snapshot match, so there is no silent switch to a more expensive endpoint or a
different model. Settlements that exceed a ceiling are persisted and flagged
`over_budget`; subsequent reservations fail closed until the next window.

## Capability ledger

Capability is built only from `Evidence` objects whose `verified_by` identifies
an independent CI/verifier (not the provider/model/agent). Accepted kinds are
`accepted_ci`, `object_match`, `source_comparison`, `verified_outcome`; proof
levels are `tested` or `verified`. Merged-PR counts, self-reported percentages,
"thought" text and source-function counts have no accepted evidence kind and
cannot be recorded. Duplicate evidence IDs are rejected.

`capability(provider, model, category)` returns `UNKNOWN` at cold start, or
sample count, success count, a 95% Wilson lower bound, median wall time, billed
spend, cost per verified unit and rework rate. `proven` requires at least two
independently verified successes and a lower bound of 0.5. High-risk requests
are only routed to proven slots or with an explicit human approval token.

## Router

Order of operations per request:

1. Reject already-assigned issue/branch/task (duplicate claim or in-flight).
2. Enforce the active-slot bound and, unless opted in, one slot per machine.
3. Filter by hard prerequisites: valid fresh #126 attestation, privacy class,
   installed tools, required local binaries, approval role, context size and
   exact price snapshot. These trump any score.
4. Score eligible slots with `0.60 * success_lower + 0.25 * speed + 0.15 *
   affordability`; UNKNOWN capability is neutral 0.5. Scores within
   `strength_tolerance` form a similar-strength group.
5. Choose the least-served slot in that group (deterministic tie-break by cost
   and slot id). After `starvation_seconds` waiting, the best-scoring eligible
   slot wins instead, so a long-waiting task is not starved by fairness.
6. `canary=True` is accepted only for `risk=low` and picks the cheapest eligible
   slot for bounded exploration; high-risk canaries are refused.
7. `preferred_slot` refuses rather than substitutes unless `allow_failover` is
   explicitly true. In-flight assignments are never reprioritized.

`dispatch` then calls `wallet.reserve`, so a stale plan cannot double-book.

## Sanitized snapshot

`Router.snapshot(now)` returns `nova3.token-wallet-snapshot/1`: wallet mode and
day/month spent/reserved/remaining (or `None`/UNKNOWN for subscriptions),
assignments, and per-slot provider/model/snapshot, readiness, assignment,
proof level, cost per verified unit, fairness count and ETA confidence
(unknown/low/medium/high). Only whitelisted aggregate values are projected;
`assert_sanitized` rejects secret-looking keys (`key`, `token`, `prompt`,
`auth`, ...) and values containing private paths or credential prefixes. No
prompts, source, key names or account identifiers are inputs.

## Tests

`python3 -B -m unittest nova3_team.tests.test_token_wallet -q` — 42 offline
tests: caps (daily/monthly/mid-flight), cached/retry/fee accounting, missing
usage, price change, idempotency, release, restart durability, orphan recovery,
subscription fail-closed, evidence rejection, Wilson bounds, fair distribution,
specialization, idle slots, weak-hardware and opt-in multi-slot, privacy/tools/
binaries, high risk, stale attestations, snapshot mismatch, canary, starvation,
no mid-checkout reprioritization, duplicate assignment, explicit failover,
budget-before-dispatch, exact-once reserve, and snapshot sanitization.

## Known limitations

* Attestation semantics are a local schema expectation, not a verified #126
  implementation; integration tests must wait for that module. No cross-process
  lock is provided here.
* Fairness counters (`served`) are in memory and reset when the scheduler
  restarts; durable spend/reservations survive. Weighted history would need a
  future persisted column.
* Subscription spend, provider-native quotas and ETA are explicitly UNKNOWN;
  this module never converts them to dollars.
* Capability evidence and prices are only as trustworthy as the independent
  verifier and reviewed gateway metadata. No live OpenRouter prices, balances or
  token counts were contacted or certified. All source matching claims remain
  `NOT MATCHED`.
* Release of an orphan is manual by design. A crash between provider charge and
  settlement must be reconciled from the provider statement.
