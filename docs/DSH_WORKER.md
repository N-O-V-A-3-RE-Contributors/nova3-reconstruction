# The DSH autonomous worker (DeepSeek Harness execution)

This document replaces the Codex-backed worker as the project's *local
execution engine*. The GitHub queue, the coordinator leases and the task
lifecycle are unchanged: DSH is an **execution provider inside the existing
`nova3_team` worker architecture**, not a second queue and not a second worker.

```
GitHub issues  ──  coordinator (Actions)  ──  leases: /nova claim|heartbeat|release|submitted
                                   │
                     nova3-dsh-worker.service  (durable supervisor, survives everything)
                                   │
        ┌──────────────────────────┼───────────────────────────────┐
   claim next task           prepare_checkout              validate_scope + verify()
   (worker.py)               (isolated worktree)           (independent commands)
                                   │
                     dsh_runtime.run_session()  ──  dsh --profile headless --json
                                   │                 (prompt on stdin, key by REFERENCE)
                     dsh_policy.decide()  ── RUNNING | BACKOFF | WAITING_CREDITS |
                                   │            WAITING_APPROVAL | BLOCKED | COMPLETE
                     commit → push → PR (never merge) → /nova submitted
```

## Components

| Module | Responsibility |
| --- | --- |
| `nova3_team/dsh_runtime.py` | Launch one bounded DSH headless session, parse its JSON event stream, classify the outcome, generate the non-secret profile overlay. |
| `nova3_team/dsh_policy.py` | The durable state machine: budgets, bounded exponential backoff, per-task attempt caps, and the explicit states. Pure and unit-tested. |
| `nova3_team/dsh_planner.py` | Evidence assessment (queue, ledger, checkpoints, last outcome) and proposal validation/deduplication. |
| `nova3_team/dsh_supervisor.py` | The long-lived loop that ties queue → task → DSH → policy → checkpoint. |
| `nova3_team/dsh_status.py` | Read-only status page for the phone/terminal; optional GitHub publication. |
| `nova3_team/worker.py` | Unchanged claim/checkout/scope/verify/commit/PR path, plus the `dsh` provider branch (`run_dsh`, `dsh_session_prompt`). |

## Why the headless profile needs an overlay

DSH's shipped `headless` profile **disables the managed credential store**
(`credentials`, `authorization`, `deepseek-account` are in the bundle's patch),
so a key saved in the Web UI is invisible to it. The supervisor therefore writes
a small patch overlay at

```
~/.cache/nova3-team/<OWNER_REPO>/.nova-slots/dsh/state/dsh-overlay.yml
```

which re-enables the store and declares the route:

```yaml
- id: credentials
  disabled: false
- id: llm-pi-ai
  config:
    providers:
      openrouter:
        baseURL: https://openrouter.ai/api/v1
        apiKeyEnv: OPENROUTER_API_KEY   # a NAME; the value stays in DSH's store
- id: agent-default-model
  config:
    provider: openrouter
    model: deepseek/deepseek-v4.1-flash-1017
```

No secret is written by the project: the key lives only in
`$DSH_HOME/.credentials.yaml` (DSH-owned, 0600). DSH's documented resolution
order then applies: stored file → launch environment → `./.env` → `$DSH_HOME/.env`.

## Session contract

```
dsh --profile headless --patch <overlay> headless --json [--session-id ID] -
```

* The prompt travels on **stdin** (`-`), never in argv or the environment.
* `--json` emits `session`, `status` (turn/step boundaries + usage), `text`,
  `thinking`, `tool_call`, `tool_result`, `error`, `final`. A failed turn still
  emits `final`, so **the only success signal is `turn_end.reason.kind ==
  "completed"`**. Exit 0 alone is not trusted.
* `--session-id` is persisted in the task checkpoint (`dsh_session`), so a
  session can be continued after a crash; a missing id fails loudly instead of
  silently starting empty.
* Bounds: wall-clock (`NOVA3_AI_SESSION_TIMEOUT`, 300–1800 s) **and** model
  steps (`NOVA3_DSH_MAX_STEPS`, default 120). Exceeding either kills the whole
  process group — no orphaned tool processes.
* Timeout → `SESSION_TIMEOUT` (quarantine, never an identical retry storm).
  Cancellation → `CANCELLED`. Step limit → `STEP_LIMIT` (fresh session).

## Failure taxonomy and states

| Objective signal | State | Behaviour |
| --- | --- | --- |
| `turn_end` completed + verification passes | `RUNNING` | commit, PR, next task |
| `RATE_LIMIT`, 429 | `BACKOFF` | 120 s → 240 s → … capped at 1 h |
| `PROVIDER_OUTAGE`, 5xx, network | `BACKOFF` → `WAITING_APPROVAL` after 5 | hourly recheck |
| `QUOTA`, `MISSING_CREDENTIAL` | `WAITING_CREDITS` | recheck every 30 min **in process**, resumes automatically |
| `AUTH`, `INVALID_CREDENTIAL` | `BACKOFF` → `WAITING_APPROVAL` after 3 | asks for a key refresh |
| `EXECUTION_ERROR` ×3 identical | `BLOCKED` (task) | worker moves to different work |
| per-task session cap (3) without closing | `BLOCKED` (task) | forces a replan instead of repeating |
| hourly/daily session or token budget | `BACKOFF` / `WAITING_APPROVAL` | hard spend stop |
| operator `STOP` file | `BACKOFF` | honoured before any spend |
| queue empty and planner finds nothing | `COMPLETE` | **idles** (30-min checks), never exits; cleared by `RESUME`, by approved work, or by new evidence |

**The old restart trap.** `nova3-worker.service` carried
`RestartPreventExitStatus=3`: a deliberate "exhausted" exit made systemd stop
restarting the worker, so one credit pause permanently disabled autonomy until a
human ran `systemctl start`. The DSH unit has **no** `RestartPreventExitStatus`;
a pause is a state *inside* the process. The process only exits for genuine
configuration faults, and those are recorded in `worker-failure.json`.

`Restart=always` was tried during the migration and is wrong: `COMPLETE` is a
legitimate exit 0 and systemd respawned it every 30 s. The unit now uses
`Restart=on-failure`, and `COMPLETE` no longer exits at all — it idles and polls,
so a human approving a proposal is picked up within 30 minutes with no service
action. `RestartPreventExitStatus=""` (empty) is the explicit opposite of the
old unit's trap.

## Planning

`nova3_team/dsh_planner.assess()` runs every cycle with **no model call**: it
counts runnable/claimed/review/blocked tasks, open PRs, duplicate task keys, the
function ledger (1165 discovered boundaries, unassigned count), checkpointed
tasks and the last session outcome. When runnable work drops to the low-water
mark (2), one bounded planner session runs; its answer must be a JSON array of
small tasks, each validated for allowed paths (never `originals/`, `analysis/`,
`research/`, `generated/`, `.github/`), acceptance criteria, evidence links and
secret-shaped content, then deduplicated against open issues, closed history and
previously rejected proposals.

Proposals are filed as **ordinary issues without queue labels**. They are
approved exactly like any other task (owner approval label + `nova:queued`) and
claimed by the normal coordinator lease path. An AI-created task therefore goes
through the same gate as a human-created one; nothing local can inject work into
the queue directly.

## Operating it

```sh
tools/systemd/install-units.sh --check      # verify installation
tools/systemd/install-units.sh --now        # install, enable, start

systemctl --user status nova3-dsh-worker.service
journalctl --user -u nova3-dsh-worker.service -f
python3 -m nova3_team.dsh_status            # this machine, readable page
python3 -m nova3_team.dsh_status --json
```

Control files (slot-scoped, 0700):

```sh
S=~/.cache/nova3-team/N-O-V-A-3-RE-Contributors_nova3-reconstruction/.nova-slots/dsh/state
touch $S/control/STOP        # finish the current step, then idle (no spend)
touch $S/control/RESUME      # recheck immediately
cat $S/supervisor.json       # durable state, last outcome, next check
cat $S/spend.jsonl           # append-only session/token ledger
cat $S/requests.jsonl        # outstanding actionable human requests
```

## Configuration (environment)

| Variable | Default | Meaning |
| --- | --- | --- |
| `NOVA3_DSH_BIN` | discovered | driver override (must be executable) |
| `DSH_HOME` | `~/.local/share/nova3-dsh/data` | credential store + sessions |
| `NOVA3_DSH_MODEL` / `_ROUTE` / `_BASE_URL` / `_KEY_REF` | OpenRouter DeepSeek V4.1 Flash | model routing; failover = point these at another approved route |
| `NOVA3_DSH_MAX_STEPS` | 120 | model steps per session |
| `NOVA3_AI_SESSION_TIMEOUT` | 1200 | wall clock per session (300–1800) |
| `NOVA3_DSH_SESSIONS_PER_HOUR` / `_PER_DAY` | 6 / 48 | spend caps |
| `NOVA3_DSH_TOKENS_PER_DAY` | 6,000,000 | token cap |
| `NOVA3_DSH_SESSIONS_PER_TASK` | 3 | anti-loop cap |
| `NOVA3_DSH_PLAN_TIMEOUT` | 900 | planner session bound |
| `NOVA3_REFERENCE_ROOT` | `<repo>/analysis` | local read-only oracles |

## Independent review fixes (2026-11-02 15:12 snapshot)

Every finding was confirmed against the code and fixed, with a regression test
that failed on the earlier snapshot (`nova3_team/tests/test_dsh_review_fixes.py`).

| Finding | Fix |
| --- | --- |
| P0 claim race: supervisor claimed, then `work_one` claimed again and skipped the task it had won | claiming is split out: `work_one` claims then calls `work_claimed()`; the supervisor calls `work_claimed()` with the lease it holds |
| P0 silent process: deadline checked only while output flowed | watchdog thread kills the process group on the deadline; the reader loop only decides *why* it stopped |
| P0 spend: 3 sessions, 1 ledger row | every session is recorded when it ends, by the component that spent it; the supervisor writes a token-free summary row; terminal credit/auth stops the round loop |
| P1 planner only proposes | unchanged by design — it files proposals a human approves through the coordinator gate; approval remains a human act |
| P1 completion | `COMPLETE` now requires the project acceptance check; an empty queue becomes `WAITING_APPROVAL: evidence needed` |
| P1 request handling | requests are resolved one at a time, each when its own condition holds |
| P1 `--plan-only` | plans and never claims or executes |
| P1 two loops | `tools/dsh_loop` refuses to start while a systemd worker is enabled or running |
| P1 provider fallback | `NOVA3_DSH_FALLBACK=route[:model][,route...]` declares approved pi-ai routes over the same credential; failover only for provider-level failures |
| Dry run | a session that completes without changing anything is `EXECUTION_ERROR/NO_EFFECT`, never `RUNNING` |

## Operator controls and budget consent

`control/RESUME` is the general "act now" button. It wakes any wait, and it
waives the **hourly** session cap for exactly one session (the daily session cap
and the token cap still apply, so consent cannot spend a day's budget at once).
The supervisor consumes the consent at the moment it admits a session, so a
cycle that could not spend does not throw the request away.

The **planner** is exempt from the per-task session cap: its sessions are meant
to end without closing a task (they file proposals for a human). It is still
bounded by the hourly/daily/token budgets and by the de-duplication of its own
unanswered proposals.

A budget guard refusing a session writes `BACKOFF` with the budget as its
reason; it never writes `BLOCKED`, because `BLOCKED` means "this *task* needs
repair" and drops the task link.

## Recovery

| Symptom | Action |
| --- | --- |
| Unit dead after reboot | `systemctl --user enable --now nova3-dsh-worker.service`; check `loginctl show-user $USER -p Linger` |
| `WAITING_CREDITS` | top up the OpenRouter balance, or set `NOVA3_DSH_KEY_REF`/`NOVA3_DSH_MODEL` to an approved alternative; **no restart needed** |
| `WAITING_APPROVAL` | read `requests.jsonl`; the entry names the exact action and risk |
| task `BLOCKED` | re-scope the issue and requeue it (`nova:blocked` → `nova:queued` after fixing the cause) |
| stale local checkout | preserved automatically at `~/.cache/nova3-team/<repo>/<issue>-<id>`; never deleted by the worker |
| duplicate worker suspicion | `systemctl --user status nova3-worker.service nova3-dsh-worker.service`; the slot lock refuses a second supervisor for the same slot |
| rollback to Codex | `tools/systemd/rollback-to-codex.sh` (keeps all DSH state) |
| unit hit the start limit | `systemctl --user reset-failed nova3-dsh-worker.service` then `start` — a limit hit also blocks a manual start, so recovery always needs this step |
| a task is `BLOCKED` locally | read `supervisor.json` (`last_decision`); the reason names the remedy (`replan_task`, `repair_lease`, `failure`) |

## What this system does **not** claim

A session succeeding means *the model returned and the deterministic checks
passed* — `COMPILES`/`TESTED`. It never upgrades a reconstruction grade on its
own. `BEHAVIOURALLY COMPARED`, `INSTRUCTION-WINDOW MATCHED` and `VERIFIED` still
require the evidence rules in `AGENTS.md`, and the PR body states only what the
local checks showed.
