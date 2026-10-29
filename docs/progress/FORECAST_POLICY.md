# Control Center forecast policy and integration contract (#105)

`tools/observatory/forecast_model.py` is a Python 3.10+ standard-library-only,
pure deterministic model. `forecast(snapshot: Snapshot, policy: Policy =
Policy()) -> dict` accepts the frozen dataclasses exported by that module.
It returns JSON-compatible values with `schema_version: 1`. It performs no
file, clock, network, GitHub, binary, process, scheduler or service operations.
The adapter owns collection, UTC timestamp conversion and evidence review.

**Current project whole-game ETA: UNKNOWN.** Source blocks, merged PRs,
successful provider exits, passing host tests and infrastructure closures
are not verified ARM source matches. This module makes no original-machine
matching claim: **NOT MATCHED** applies to this source-only engineering task.
It does not inspect or upgrade any existing reconstruction evidence.

## Exact input contract

Times are finite nonnegative UTC epoch seconds, never ISO strings, durations
in days, or values obtained from an internal clock. IDs and evidence strings
are bounded metadata (maximum 500 characters), never executable bytes,
assembly, assets or decompiler output. Collections are tuples of the records
below. Required arguments precede fields with defaults in the dataclasses;
keyword arguments are recommended. Unknown information stays absent/default.

| Record | Fields and meaning |
| --- | --- |
| `Snapshot` | Required `now`, `observed_at`, `scope`. `queue_complete=False` asserts whether all tasks **within the named scope** are present. `tasks=()`, `prs=()`, `ci=()`, `workers=()`, `sessions=()`, `history=()`, `milestones=()`, `matching=MatchingEvidence()`. |
| `Task` | Required `id`, `cohort`. `kind='source'` (`source`, `infrastructure`, `matching`); `stage='queued'` (`queued`, `active`, `review`, `blocked`, `done`); `stage_started_at=None`; `blockers=()` of explicit reason strings. `active` requires a known stage start. Issue closure alone must not assign `done` without the workflow's accepted completion evidence. |
| `PullRequest` | Required `task_id`, `observed_at`. `state='open'` (`open`, `merged`, `closed`); `draft=False`. At most one current PR record per task; the adapter reconciles multiple PRs first. |
| `CI` | Required `task_id`, `observed_at`. `state='unknown'` (`passed`, `pending`, `failed`, `unknown`). This is the aggregate of required checks for the current PR head, not any passing historic run. |
| `Worker` | Required `id`, `task_id` (may be `None`), `observed_at`. `state='active'` (`active`, `idle`, `blocked`, `offline`). Configured worker counts and a running systemd service are insufficient evidence of active work. |
| `Session` | Required `id`, `task_id`, `started_at`, `ceiling_seconds`. `ended_at=None`; `outcome='running'` (`running`, `completed`, `timeout`, `failed`, `cancelled`). Provider-session completion is separate from task-stage completion. |
| `Attempt` | Required `id`, `task_id`, `cohort`, `kind`, `phase`, `started_at`, `ended_at`. `kind` uses the Task values. `phase` is `execution`, `review` or `matching`. `outcome='completed'` (`completed`, `timeout`, `failed`, `cancelled`); `number=1`; `matched_units=()`; `match_kind=None` (`object`, `linked_window`); `source_owned=False`; `evidence=''`. Details below. |
| `Milestone` | Required `id`. `state='unknown'` (`unknown`, `pending`, `blocked`, `in-progress`, `complete`); `evidence=()` of reviewed check/report IDs. Supply the complete set of required setup milestones; the model cannot discover omitted prerequisites. |
| `MatchingEvidence` | `verified_original_matches=None`, `verified_count_evidence=''`, `inventory_functions=None`, `inventory_confidence='unknown'` (`unknown`, `partial`, `verified`), `inventory_evidence=''`, `cohort=''`, `throughput_calibrated=False`, `calibration_evidence=''`. Count and denominator refer to the same explicitly selected original reference/function scope. |

`cohort` is an explicit comparability key chosen by the evidence adapter:
include work size/difficulty, provider/toolchain, workflow and throughput
regime. Changing these requires a new key. Never mix onboarding, compiler
research, scalar math, rendering and full linked-window matching just because
all are issues. `kind` and `phase` must also match exactly. Work observations
must represent one worker per task; multi-worker joint work needs a separately
calibrated future model and must not be fed as single-worker samples.

`Attempt.phase='execution'` measures task execution through review readiness;
`review` measures review through accepted closure. These are stage closures,
not successful AI session exits or arbitrary issue-close timestamps. A PR
merge may establish a workflow review completion, never an original match.
`matching` is a **separate observation** covering the entire source-owned
matching attempt through independently accepted original comparison. It is
eligible for matching throughput only with `kind='matching'`,
`source_owned=True`, nonempty `evidence`, an approved `match_kind` and distinct
`matched_units` (stable function-ledger IDs). Synthetic ASM wrapper matches
are ineligible. The adapter must review compiler version/flags, source and
reference hashes, ARM/Thumb mode and full claimed instruction windows before
setting those evidence fields. The model trusts supplied metadata; it cannot
authenticate evidence strings or prove that samples represent the inventory.

For each `(task_id, phase)`, supply the **complete** numbered attempt history,
starting at 1, including failed/time-out attempts and backoff gaps. Observed
duration runs from the first start to final completed end, including retry
and waiting time. There must be no overlapping attempts, intermediate
successes or cohort/kind changes within that history. These are elapsed
durations under the observed single-worker workflow, not CPU time or a
guarantee of continuous staffing. Do not manufacture a completed Attempt for
an unfinished task. Current running sessions use `Session`; their stage time
is conditionally modeled from `Task.stage_started_at`.

The defaults in `Policy` are explicit safeguards, **not task-duration priors**:

| Field | Default |
| --- | --- |
| `recent_seconds` | 14 × 86400 |
| `stale_seconds` | 900 |
| `min_samples`, `max_samples` | 5, 20 (per kind/cohort/phase) |
| `max_records` | 2000 across all seven record collections |
| `max_queue_tasks` | 50 runnable tasks |
| `max_forecast_seconds` | 7 × 86400 (stage-work horizon; longer ranges stay unknown) |
| `worker_cap`, `worker_exponent` | 4, 0.5 |

Policy requires at least 3 samples, a maximum sample count no smaller than
the minimum, worker cap at most 32 and exponent in `(0, 0.75]`. Time windows
are positive and at most 365 days; integer policy limits are at most 10000.
IDs must be unique within collections (PR/CI use task ID). Blockers, milestone
evidence and matched units are bounded to 100 items per record. Invalid
numerics, bounds, enums, duplicate IDs and malformed strings raise
`ValueError`; an adapter should show unavailable data and retain the reason,
never fall back to fabricated days. Invalid chronology in history instead
excludes/censors that group; invalid session chronology returns unknown
elapsed time. A fresh timestamp may equal `now`; future observations are
never fresh. Invalid/future/reversed/zero completed durations never count.

## Output and interpretation

Top-level keys are `schema_version`, `as_of`, `observed_at`,
`sample_window_start`, `setup`, `sessions`, `queue`, `source_matching`,
`whole_game`, `excluded_history`. Result objects share `state`, `confidence`,
`sample_size`, `reasons`, `as_of`, `observed_at`. Reasons are machine-readable
strings (queue sampling reasons also identify cohort/phase). `None` means
unknown. Zero means a measured empty workload, never missing information.

* `setup`: aggregate state plus `milestones`, each with `id`, `state`,
  `evidence`, `reasons`. Missing evidence or a stale/future snapshot makes a
  milestone unknown. Aggregate precedence is blocked, unknown, in-progress,
  pending, complete; no milestones means unknown. This is readiness evidence,
  with no readiness time prediction.
* `sessions`: one result per Session, with `id`, `task_id`, `outcome`,
  `started_at`, `ended_at`, `elapsed_seconds`, `ceiling_seconds`,
  `remaining_budget_seconds`, `ceiling_exceeded`,
  `completion_eta_seconds=None`. A time ceiling is an execution limit. It
  does not predict completion. Budget remaining is clamped at zero after
  expiry; elapsed can exceed the ceiling. Stale running sessions are not
  extrapolated. A latest failed/cancelled/timed-out session on an active or
  queued task requires reconciliation or a newer retry before capacity counts.
* `queue`: `scope`, `stage_counts`, `runnable_task_ids`, `blocked_tasks`
  (task-ID to reason list), `active_workers`, `effective_workers`, `per_task`,
  `runnable_seconds`, `all_supplied_queue_seconds`,
  `all_supplied_queue_reasons`, `horizon_seconds`, `completion_boundary`, `assumption`.
  **The numeric forecast covers current stage work only**, queued/active
  through review readiness and already-reviewing tasks through closure.
  Future review/integration stages, blocked work, new arrivals and future
  shifts are excluded. It is not an issue-closure or calendar-date forecast.
  A range whose p80 exceeds the configured short-range horizon stays unknown;
  it is never silently clipped to that horizon.
  Any blocked task leaves `all_supplied_queue_seconds=None`; a calibrated
  runnable subset can still be shown with the excluded IDs beside it.
  All-blocked is unknown; an explicitly complete empty scope is `empty` with
  zero work. Missing queue completeness or stale snapshot means unknown.
* `queue.per_task`: `task_id`, `phase`, `cohort`, `kind`, `elapsed_seconds`,
  `sample_size`, `censored_tasks`, `newest_sample_at`, `seconds`, `reasons`.
  Completed samples are counted once per cohort/phase (minimum qualifying
  residual count across tasks in that cohort), not once per queued issue.
* `source_matching`: `verified_original_matches`, `units`,
  `per_worker_throughput`, `newest_sample_at`, `target_remaining_seconds`,
  `target_reasons`, `target_units`. Observed throughput uses distinct,
  independently evidenced matching completions only, in verified original
  functions per worker elapsed second. Infrastructure and source-present
  closures cannot contribute. Unknown/zero verified counts, missing count
  evidence, repeated unit IDs, sampled units exceeding the cumulative count,
  unresolved matching failures or too few real samples gate throughput.
  A numeric target remainder additionally requires an independently verified
  denominator and explicit representative calibration evidence. It is
  **single-worker elapsed seconds for the matching scope**, not full-game
  days. Contradictory counts leave that remainder unknown.
* `whole_game`: `state='unknown'`, `label='UNKNOWN'`, `eta_seconds=None`,
  reasons for matching prerequisites plus
  `whole_game_integration_and_gameplay_uncalibrated`. Even a calibrated native
  matching target does not establish gameplay/integration/QA effort. This
  bounded core deliberately has no full-game calendar extrapolator.

All numeric distributions use `{'p20': number, 'median': number, 'p80':
number}`. These are linearly interpolated empirical percentiles, **not
confidence intervals or guaranteed bounds**. Queue samples must finish in
the recent window and include their first attempt within it; only the latest
`max_samples` per cohort are retained. Unresolved failures, cancelled work,
timeouts and incomplete retry histories suppress forecasts for the affected
cohort rather than silently select only successes. Unrelated cohorts remain
usable. `excluded_history` reports the excluded task/phase group counts.

Active/review residuals use only historical durations longer than the known
elapsed time, subtracting that elapsed time **before** computing percentiles.
Fewer than `min_samples` conditional survivors means unknown, including an
active task outliving all observed completions. Queue capacity counts only
fresh, active workers assigned to distinct unblocked active/review tasks.
Idle workers, configured slots, stale heartbeats, queued claims and duplicate
assignments add no capacity. Each phase divides summed percentile work by
`min(actual_active_workers, worker_cap) ** worker_exponent`, with a floor at
its longest indivisible task; execution and review phase workloads are added
serially. This is an explicit conservative scaling assumption, not fitted
parallel speedup. `low` confidence starts at the minimum sample count;
`medium` needs at least `max(10, 2*min_samples)`. No result claims high
confidence or calibrated probability coverage.

## Executable integration vectors

The test module exports `queue_vector()`, `matching_vector()`, and `history()`
as synthetic, offline fixtures. Minimal queue construction, also asserted by
`test_queue_fixture_exact_output_and_distinct_samples`:

```python
from tools.observatory.forecast_model import (
    Attempt, Snapshot, Task, Worker, forecast,
)

now = 2_000_000.0
history = tuple(
    Attempt(id=f"h{i}", task_id=f"old{i}", cohort="small-v1",
            kind="source", phase="execution",
            started_at=now-1100-i*1000, ended_at=now-1000-i*1000)
    for i in range(5)
)
snapshot = Snapshot(
    now=now, observed_at=now, scope="two small source tasks",
    queue_complete=True,
    tasks=(Task("active", "small-v1", stage="active",
                stage_started_at=now-20), Task("queued", "small-v1")),
    workers=(Worker("worker", "active", now),), history=history,
)
result = forecast(snapshot)
assert result["queue"]["runnable_seconds"] == {
    "p20": 180.0, "median": 180.0, "p80": 180.0,
}
assert result["queue"]["sample_size"] == 5
assert result["source_matching"]["per_worker_throughput"] is None
assert result["whole_game"]["eta_seconds"] is None
```

Additional fixture expectations: a blocked third task leaves the runnable
180-second stage-work estimate but clears the total; idle workers clear that
estimate; five reviewed matching samples of one unit per 100 seconds produce
0.01 units/worker-second. With five cumulative verified units and an
independently verified/calibrated 100-function target, the synthetic matching
remainder is 9500 single-worker seconds and the game ETA remains UNKNOWN.
These numbers are synthetic test vectors, not project measurements.

## Bounded follow-up after #103 and #105 merge

No observer, progress or installer integration is included in this issue.
The current dashboard still uses its legacy forecasts until a follow-up:

1. Normalize already-collected issues, current PR-head CI, fresh worker
   assignments, sessions and explicit setup checks into this API in
   `observer.py`. Map blocked labels and pending/failing review CI explicitly.
   Keep session histories separate from actual stage closures. If the retained
   journal cannot establish complete retries or comparable stage completions,
   leave history unavailable and render unknown.
2. Supply reviewed matching counts/inventory/calibration from the provenance
   ledger; default to unknown/zero evidence as appropriate. Never translate
   source file count, status labels, merged PR count or task addresses into
   verified-match evidence. Show sample size, freshness, exclusions and the
   exact stage-work boundary alongside any empirical range.
3. Route all game forecast displays and real-time/legacy summaries in
   `observer.py` and `progress.py` to `whole_game.label`. Remove fabricated
   whole-game totals, fixed day counts and speculative fractions from those
   display paths. Keep setup/session clocks in their separate units.
4. Include the model in both standalone installer/package layouts and test
   imports there. Add adapter/render regression tests showing UNKNOWN with
   25 source blocks, zero verified ARM matches, onboarding/toolchain blockers,
   stale or unavailable data and an empty scoped queue. No scheduler changes.

## Verification and task handoff

Offline focused command:

```sh
python -m unittest discover -s tools/observatory/tests -p test_forecast_model.py -v
```

The focused tests use synthetic metadata only and exercise evidence gates,
timeouts/retries, percentile calculations, cohort changes, blocked subsets,
review CI, worker scaling, stale/future observations, invalid durations,
readiness, deterministic output and JSON serialization. Full Control Center
discovery also runs existing tests that invoke `systemd-analyze verify`; this
sandbox rejects its socket options, causing two unrelated discovery-test
failures. Those files and system configuration are outside #105's allowlist.
There are no binary fixtures, toolchain/matching claims, new dependencies or
GitHub changes. Resume with the focused tests and the integration steps above.

Recorded on 2026-10-29 with Python 3.14.8: focused tests **35 passed**, exit 0;
Control Center discovery with exactly the following two previously failing
sandbox-dependent tests excluded: **107 passed**, exit 0.

* `test_discovery_fix.Tests.test_known_bug_working_directory_quoted`
* `test_discovery_fix.Tests.test_owner_repairs_exact_installer_output`

Unfiltered discovery reproduced one failure and one error in those tests
(`SO_PASSRIGHTS` / `SO_PASSCRED`: Operation not permitted). No approval or
host repair was attempted. The documented Python vector executed successfully,
both Python files passed Python 3.10 grammar parsing, and all three allowed
files passed trailing-whitespace/newline checks. Python 3.10 runtime execution
was not available in this session. The supervisor's next step is the bounded
adapter/display/packaging follow-up after #103 and #105 merge; this slice is
ready for review without changes to `observer.py`, `progress.py` or installers.
