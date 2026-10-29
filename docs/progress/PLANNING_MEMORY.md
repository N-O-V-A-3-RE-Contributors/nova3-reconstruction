# Persistent planning memory (#139)

Status: **IMPLEMENTED / TESTED offline**. No network, no paid model calls, no
credentials and no raw transcript enters the repository. The planning bridge
from #132 is consumed read-only; `observer.py`, `worker.py`, `coordinator.py`,
`provider_runtime.py` and `planning_bridge.py` are untouched.

## Why

Planning state previously lived in one chat session. A provider switch, a
restart, a context-window overflow or a truncated export destroyed the thread of
work and forced a manual handoff. #139 adds a local, versioned, provider-neutral
memory that:

1. keeps an ordered turn log **and** a separate structured ledger, so prose can
   compact without losing accepted decisions or verified facts;
2. checkpoints incrementally after every turn, before the provider window is
   exhausted;
3. compacts older turns into bounded summaries while retaining the recent
   window and retrieving only relevant ledger entries;
4. exports a deterministic, checksummed continuation envelope that Hermes,
   OpenCode, Codex, Claude, Gemini or a human can import;
5. treats model output as untrusted: it can propose, but cannot accept a
   decision, verify a fact, or replace one.

## Storage and privacy

Default store: `$NOVA3_PLANNING_MEMORY_DIR`, else
`$XDG_STATE_HOME/nova3/planning_memory`, else
`~/.local/state/nova3/planning_memory`. Files are private (`0700` directory,
`0600` snapshots):

```text
<store>/memory.sqlite3     SQLite (WAL, foreign keys, busy timeout)
<store>/continuation.json  atomic full export (tmp + fsync + os.replace)
<store>/memory.sqlite3.corrupt-<epoch>  preserved when recovery ran
```

A store inside the repository is **refused** (`store-in-repo`) unless the
relative path matches an explicit `.gitignore` entry and the caller passes
`allow_repo_path=True` (CLI: no opt-in flag, so the CLI always refuses).

Every text field passes the shared bridge sanitiser and an additional filter:

* credential patterns and absolute home paths → `[redacted]`;
* `originals/`, `analysis/`, `generated/`, `research/` references →
  `[private-ref]`;
* reserved authorization markers (`NOVA3_APPROVED`, `nova3.approval`, …) are
  removed and flagged, so text can never masquerade as an approval record;
* redaction reasons and prompt-injection heuristics are stored as audit flags,
  never silently applied.

## Data model (schema version 1)

| Table | Purpose |
| --- | --- |
| `meta` | `schema_version`, forward-incompatible stores fail closed |
| `sessions` | durable `session_id`, `thread_id`, project, repo revision, origin provider |
| `turns` | monotonic per-session `seq`, role, provider, writer kind, digest, compaction/checkpoint link |
| `checkpoints` | append-only sealed envelopes; SQL triggers reject `UPDATE`/`DELETE` |
| `ledger` | goals, plans, decisions, facts, hypotheses, evidence, dependencies, blocked questions, next actions |
| `compactions` | bounded extractive summaries covering `from_seq..to_seq` |
| `events` | audit trail: creation, downgrade, correction, verification, compaction, recovery, import |
| `imports` | idempotency key for portable envelopes |

Ledger entries carry `status`, `rationale`, `importance`, `confidence`,
`writer`, `writer_kind`, `trust`, `provenance`, `evidence[{locator,digest}]`,
`created_at/updated_at`, `stale_after`, `flags`, `supersedes`,
`superseded_by` and `verified_by/verified_at`.

### Epistemic rules (enforced)

| Attempt | Result |
| --- | --- |
| model sets `fact: verified` | stored as `unverified`, flag `downgraded:model-cannot-assert-verified` |
| model sets `decision: accepted` | stored as `proposed`, same downgrade flag |
| model verifies any entry | `model-cannot-verify` error |
| model corrects an accepted decision or verified fact | `model-cannot-supersede` error |
| human verifies a fact | requires at least one 64-hex SHA-256 evidence digest; recorded in `verified_by` and an audit event |
| anyone corrects an entry | new row with `supersedes`, old row keeps its text and gains `superseded_by` + `superseded` status |

Staleness is computed at read time from `updated_at + stale_after`; stale facts
move to `stale_items` and out of `verified_facts` without deleting anything.

## Automatic budgeting

`Budget(max_tokens, reserve_tokens, recent_turns, max_summaries,
summary_tokens, max_ledger_items)` defaults to 4000 / 800 / 10 / 6 / 240 / 24.
`available = max_tokens - reserve_tokens` is the hard ceiling for the
serialised envelope (~4 chars per token, deterministic).

Per turn, inside one `BEGIN IMMEDIATE` transaction:

1. append the turn and bump `last_seq`;
2. render the level-0 envelope and compare with `available`;
3. also compare the **uncompacted backlog** beyond the recent window with
   `recent_turns` (so long, low-token sessions still compact instead of silently
   replaying);
4. if over, compact the oldest up-to-40 uncompacted older turns into one
   summary, mark them `compacted=1`, and log the event;
5. seal a checkpoint whose `parent_id` is the previous checkpoint;
6. atomically rewrite `continuation.json`.

Rendering degrades through four deterministic levels (text truncation and
bucket caps), then a final `fallback` mode that keeps the last three turns and
the highest-signal ledger entries. Building context reads only the recent
window, the ≤`max_summaries` newest summaries and ledger rows — never the full
transcript. `summaries_omitted` is a count, not content, so older prose cannot
grow the envelope without bound.

Retrieval = pinned (importance ≥ 80 and accepted/verified) → keyword overlap
with the recent window → importance → recency. That is why an important decision
made before the last 50 turns still appears in a resumed context; it is loaded
from the ledger, not from the compacted transcript.

## Continuation envelope

`tools/planning_memory.py resume --session …` prints concise text plus
`stats`/`checksum`. The JSON envelope (`nova3.planning.context/v1`) contains:

```text
schema, schema_version, session_id, thread_id, project, repo_revision
session_provider, provider_hint          # hint is excluded from the checksum
generated_at, last_turn_seq, checkpoint, budget, mode/fallback
recent_turns[...], summaries[...], summaries_omitted
ledger{goals, accepted_decisions, rejected_decisions, verified_facts,
       hypotheses, unverified_claims, plans, dependencies,
       blocked_questions, next_actions, evidence, stale_items}
retrieval{strategy, query_digest, counts}, turns{total, in_context,
       compacted, omitted}, excluded[]
checksum = sha256(canonical JSON without checksum/provider_hint)
```

`sha256` is stable for a fixed clock and state, and **identical across
providers**; only `provider_hint` changes, so switching Hermes → OpenCode →
Codex → Claude → Gemini cannot alter the memory identity.

Portable export (`nova3.planning.continuation/v1`) adds full turns, ledger
(with supersession links), compactions and checkpoint history, sealed with the
same checksum rule. Import verifies the checksum, remaps ids on collision,
preserves trust levels, and is idempotent on the envelope digest. A tampered or
truncated envelope is rejected (`envelope-invalid`) and leaves the target store
unchanged.

## CLI

```bash
python3 tools/planning_memory.py --store ~/.local/state/nova3/planning_memory \
    init --project nova3-reconstruction --provider hermes --repo-revision a4d1a91
python3 tools/planning_memory.py --store … turn --session s_… --role assistant \
    --text "context from this turn"
python3 tools/planning_memory.py --store … decision --session s_… \
    --text "Keep Premium retail as the primary target" --rationale "debug info"
python3 tools/planning_memory.py --store … remember --session s_… --kind fact \
    --text "Premium ELF is 32-bit ARM EABI5" --writer-kind human --status verified
python3 tools/planning_memory.py --store … resume --session s_… --provider gemini
python3 tools/planning_memory.py --store … context --session s_… --out ctx.json
python3 tools/planning_memory.py --store … export --session s_… --out handoff.json
python3 tools/planning_memory.py --store other import handoff.json --provider codex
python3 tools/planning_memory.py --store … status
python3 tools/planning_memory.py --store … doctor
```

Exit codes: `0` success, `2` memory/privacy/schema/IO failure. Diagnostics are
fixed codes and never echo secrets.

## Reproducible resumption demonstration

The 100+ turn case is an executable test, not a transcript:

```bash
python3 -B -m unittest \
  nova3_team.tests.test_planning_memory.ResumeTests.test_three_provider_switch_and_two_restarts -v
```

It creates one session, records an important accepted decision and a verified
fact, writes 120 turns across three mock providers (codex/claude/gemini) in two
60-turn blocks, closes and reopens the store between blocks (two restarts),
resumes with a fourth provider (gemini) and asserts:

* 120 turns and ≥ 120 incremental checkpoints survived;
* ≥ 5 compactions ran and the recent window stays ≤ `recent_turns`;
* the continuation contains the old decision (never present in the recent 50
  turns) and its rationale, plus the verified fact;
* the provider switch line is `hermes -> gemini`.

Related: `test_old_important_decision_survives_over_50_recent_turns` (130 turns),
`test_hundred_turn_run_compacts_repeatedly_and_keeps_window`,
`test_corrupt_database_is_recovered_from_snapshot`,
`test_truncated_snapshot_is_rebuilt_from_sqlite`,
`test_concurrent_append_from_two_instances_keeps_unique_sequence`.

## Test evidence

```text
python3 -B -m unittest nova3_team.tests.test_planning_memory -q
Ran 62 tests … OK

python3 -B -m unittest discover -s nova3_team/tests -q
Ran 659 tests … OK (skipped=6)
```

All tests are offline: tempfile stores, fixed clocks, `threading` for locking,
no subprocess, no model, no network. The CLI tests call `main(argv)` in-process.

## Limitations / open work

* Token accounting is a deterministic ~4-chars/token estimate, not each
  vendor's tokenizer; it deliberately over-reserves via `reserve_tokens`.
* Summaries are deterministic extractive snippets, not semantic model
  summaries. Ledger retrieval is lexical (stopword-filtered keyword overlap) +
  importance/recency, not embeddings.
* `stale_after` is per-entry and coarse; there is no automatic re-verification
  job or dependency-driven invalidation yet.
* Encryption at rest is not implemented; privacy relies on filesystem
  permissions and the containment/redaction rules. No cross-machine sync.
* Concurrent snapshot writers are safe (unique temp + atomic replace) but the
  last writer wins; SQLite is authoritative and `load_snapshot` rebuilds.
* Import preserves original trust levels and checkpoint bytes; it does not
  re-derive verification. Checkpoint ids are remapped on import, so old ids in
  external notes no longer resolve.
* `observer.py` / `worker.py` / `coordinator.py` are not wired to this module
  yet; connecting live worker handoffs is follow-up work, kept out of this PR to
  preserve issue #139 scope.
