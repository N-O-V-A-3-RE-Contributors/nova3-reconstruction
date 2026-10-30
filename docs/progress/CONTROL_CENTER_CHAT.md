# Plan & Chat — owner planning workspace (#140)

Status: **IMPLEMENTED / TESTED offline**. No network, no paid model call, no
worker action and no transcript in this repository. The UI lives in
`tools/observatory/observer.py`; all behavior is in
`tools/observatory/planning_chat.py` and is driven directly by tests.

## Bounded OpenRouter transport through the configured Hermes account

The production UI discovers `planner_transport.BoundedOpenRouter` for both
`hermes` and `openrouter` choices. Its label explicitly says **OpenRouter via
configured Hermes account**. A private helper executes in the installed Hermes
interpreter and resolves credentials using Hermes' native
`resolve_runtime_provider(requested="openrouter", target_model=...)` mechanism.
Only readiness metadata, model text and bounded token/cost statistics cross
stdout; credentials and native diagnostic text never do. No gateway session,
worker, global Hermes configuration or credential file is changed.

The actual native configured model is read during readiness, rather than using
the earlier placeholder/pinned model guess. `NOVA3_HERMES_PLANNER_MODEL` can
explicitly select a DeepSeek model; its identifier is validated and no silent
model fallback occurs. Legacy `hermes-planner`/`planner-default` thread labels
migrate to the resolved model. Local readiness displays `UNKNOWN` until a real
inference succeeds, then `CONNECTED`; actual failure displays `UNAVAILABLE`.
The UI never treats a local credential resolver as proof of successful inference.

Press `C`, `T` to create a private thread, then `I` to enter a message. Provider
SSE content streams into the existing redacted chat event queue and is saved in
the per-user ledger. Switching Hermes/OpenRouter rereads the same memory and uses
the **same wallet**, as does closing/reopening the application. Demo mode never
starts native providers. Both installers ship the helper and transport.

Every POST uses the exact official HTTPS OpenRouter endpoint, refuses redirects,
requests `max_tokens=512`, disables reasoning, enables no tools, sets
`provider.require_parameters=true`, `allow_fallbacks=false`, and
`max_price={prompt:1,completion:4,request:0}` in dollars per million tokens
(prompt/completion). There are no retries, fallback models, title generators or
auxiliary inference. The entire UTF-8 prompt file is capped at 8192 bytes. The
bounded envelope retains every retrieved accepted decision, verified fact and
goal before filling the remaining space with recent turns/summaries; overflowing
pins fail clearly rather than silently disappearing. Current public GitHub
issue/PR/workflow metadata is included with source/freshness and an explicit
untrusted/informational label; it never becomes queue authorization evidence.

Before every request the parent atomically reserves **$0.012** against the
**$0.05 TOTAL test budget** in the canonical OS-user wallet:
`$XDG_STATE_HOME/nova3-observatory/planning_chat/openrouter-test-wallet.json`
(default `~/.local/state/...`). It uses file locking, 0600 permissions, file and
parent-directory fsync, rejects corrupt state and never refunds uncertain errors
or replenishes itself. The canonical production wallet is shared across provider
choices, GitHub account switches, custom UI state directories and restarts. The
UI reports reserved and remaining amounts. Four reservations fit; a fifth fails
before any POST. Changing a provider, clicking retry or reopening cannot reset
the wallet. Further spending requires explicit owner budget authorization and a
reviewed implementation change; this installation does not grant unlimited use.

The helper is an internal implementation, not a standalone inference CLI: live
verification must call `BoundedOpenRouter.stream()` through `ChatWorkspace` or
the UI, preserving the wallet boundary. Native Hermes CLI mode remains an
optional transport class for offline protocol tests; it is **not** the production
default. The installed gateway CLI rejects its advertised `--run-budget` flag
in streaming mode, and killing that viewer can leave accepted gateway work
running. The bounded direct helper avoids creating such a gateway session and
retains its full reservation even after local cancellation or timeout.

The production UI keys memory by fresh authenticated GitHub **numeric user ID**
from `gh api user`, rather than `USER`/`NOVA3_VIEWER`. Account changes recheck
identity/owner authority and lock the previous account's memory. Missing identity
fails closed. Explicit offline collector seams remain available for tests.

Offline regression command (real fixture subprocesses, no paid model calls):
`python3 -m unittest discover -s tools/observatory/tests -p test_planner_transport.py -v`.
Tests cover streaming, protocol failures, timeout/cancel isolation, test-wallet
persistence/concurrency/corruption, hard request settings, no redirects/fallbacks,
context retention and restart continuity. Live inference evidence is recorded
separately by the installation verifier.

## Why this shape

The Control Center already carries fifteen pages. Adding a sixteenth tab would
have broken the compact registry contract and split planning into scattered
controls, so **Plan & Chat is a modal workspace**: `C` opens it from any page
except the PR/Administration merge context, `Esc`/`C` closes it. The underlying
page keeps refreshing; closing the workspace never stops, starts, restarts,
reassigns or changes the model of a worker.

## What the owner sees

* thread title, active provider/model and an honest `READY` / `NOT CONNECTED`
  state per provider;
* context health as a deterministic token estimate against the #139 budget,
  compaction count, summaries, stale evidence count and the last sealed
  checkpoint with its timestamp;
* autosave status (`SAVED · seq N · checkpoint …`, or `… reply NOT saved`) —
  never a success message when the checkpoint or provider failed;
* current objective, next actions and open questions from the structured
  ledger;
* explicit privacy status: local per-user ledger, nothing uploaded, no
  transcript in the public repository;
* transcript tail and redacted streaming/tool-event lines; model turns are
  labelled `[model/untrusted]`.

Layouts: a single scrollable panel at 80×24 and four cards
(`THREAD / PROVIDER`, `OBJECTIVE / LEDGER`, `CONVERSATION (redacted)`,
`TASK PROPOSALS`) at 192×54. `ui_components.Layout` still decides the
breakpoint; no page invents its own size threshold.

## Keys

| Key | Action |
| --- | --- |
| `C` | open/close Plan & Chat |
| `T` | new private thread |
| `[` / `]` | previous / next thread |
| `P` / `M` | cycle planning provider / model |
| `I` | type a message (Enter sends, Esc discards the draft) |
| `X` | cancel the streaming turn |
| `R` | retry the last owner message |
| `V` | pin a decision (human, accepted, importance 95) |
| `F` | append a correction to the latest accepted decision |
| `O` / `N` / `C` (inside workspace) | objective / next action / open question |
| `K` | inspect the last checkpoint (id, mode, seq, tokens, checksum) |
| `E` | private continuation export (0600, refused inside the checkout) |
| `G` | accept a task proposal **only** from full proposal JSON |
| `B` | publish the selected local draft (typed `PUBLISH <12 hex>` required) |
| `D` | remove the thread from local history (sealed checkpoints retained) |
| `↑`/`↓`, PgUp/PgDn | scroll |

In-progress text lives in `self.plan_buffer`, outside curses, so resize,
terminal repaint or a provider crash cannot erase it. Only `Esc` (explicit
discard) or a successful send clears it.

## Authorization

`ChatWorkspace` receives a `role_provider` callable. The production CLI injects
`operator_ops.owner_access(repo)`, so GitHub is re-queried on workspace entry
and on every privileged action (`guard()`), with failures failing closed.
`allowed` requires repository `ADMIN` **or** an authenticated organization
owner; READ/TRIAGE/WRITE/MAINTAIN get a separate help surface and the store is
never opened (no directory is created). The UI never trusts `snap.repo_role`,
a cached role or local metadata: without an injected operator seam it denies.

Provider selection, model selection and compaction do not cross this boundary
into worker management — the module imports neither `worker` nor `coordinator`
and exposes no endpoint that could. A test asserts worker actions are never
called from the page.

## Durable memory

* Store: `<state_dir>/planning_chat/users/<sha256(user)[:16]>/memory`, using
  `nova3_team.planning_memory` unchanged (#139). Each signed-in user has a
  separate directory; a thread from user A is invisible to user B.
* Thread titles, provider/model choice, pins and archive state live in
  `threads.json` in the same private directory (0600, atomic replace).
  The ledger remains authoritative; losing the sidecar loses only cosmetics.
* Before every prompt the provider receives the #139 continuation envelope:
  recent window + bounded summaries + retrieved accepted decisions / verified
  facts / goals / next actions / stale items with provenance. The transcript is
  never re-sent whole.
* Compaction happens inside `append_turn` before the checkpoint is sealed.
  When it fires the page shows `CONTEXT REFRESHED`.
* The envelope checksum excludes `provider_hint`, so Hermes → OpenCode →
  Codex → Claude → Gemini rereads the same memory identity (asserted in tests
  with a fixed clock).

## Task proposals

`G` parses the last model reply as JSON; prose never becomes a task. Valid
proposals go through `planning_bridge.validate_proposal` and are written to
`drafts/<fingerprint>.json` (0600) as `LOCAL DRAFT — not published, not queued`.
`B` requires a typed `PUBLISH <fingerprint[:12]>` phrase **and** a fresh
owner/admin check, then calls `planning_bridge.publish_draft(..., apply=True)`
which creates a `nova:draft` issue only. Queueing remains the separate #135
gate; this page reports that explicitly. Offline transports surface
`github-offline` and leave the local draft untouched.

## Privacy and deletion

* Model output and user text pass the #139 sanitiser; credentials and absolute
  home paths become `[redacted]`, private repo references `[private-ref]`, and
  reserved approval markers are stripped.
* Only `delta`, `tool` and `status` channels are accepted. A `reasoning` event
  is dropped, so the UI never invents or displays chain-of-thought.
* Exports and drafts are 0600 and refused inside the repository checkout; the
  store itself is refused inside the repo by `PlanningMemory`.
* `D` hides a thread from local history but does not pretend to destroy sealed,
  append-only checkpoints; the notice says so.

## Offline test evidence

`tools/observatory/tests/test_planning_chat.py` uses `tempfile` stores,
`ScriptedProvider`, fake terminals and injected role providers:

* 120 turns with repeated pre-limit compactions, then retrieval of a pinned
  decision and objective that are far outside the recent window;
* restart/resume with a new `ChatWorkspace`, two provider switches and an
  identical continuation checksum;
* contributor denied (store never created), role revoked mid-session,
  provider crash + retry, cancelled stream with partial turn kept, unavailable
  provider with no shell/API fallback;
* corrupt ledger recovery (or explicit `ledger` error), stale evidence count,
  cross-user isolation;
* offline GitHub publish (no issue created), typed-confirmation refusal,
  publish blocked after revocation, prose-to-task refusal;
* redaction of credentials/home paths and rejection of a `reasoning` channel;
* 80×24 and 192×54 rendering, resize while typing, keyboard-only cancel,
  export/archive controls, and assertions that no worker action is called.

## Limitations / follow-ups

The standalone installers include the source-owned planning ledger, bridge
and authorization modules. An isolated installed-bundle regression checks
decision persistence, role revocation and the unavailable-provider result
without a checkout, network access or model invocation. The install directory
is distinct from the private state directory.

* Providers are injected adapters. This PR does not implement vendor CLI
  transports; without an adapter the page says `NOT CONNECTED` rather than
  shelling out. Wiring the real adapters (and #136 Hermes planner) is follow-up.
* Token accounting is the #139 deterministic estimate, not a vendor tokenizer.
* The workspace is owner-only by design; there is no contributor chat history,
  only the static help surface.
* Archive hides a thread; it is not cryptographic erasure of local SQLite
  pages or backups.
* Checkpoint count is cached per thread to keep repaint cheap; an external
  writer appending to the same store is not reflected until the thread is
  reselected.
