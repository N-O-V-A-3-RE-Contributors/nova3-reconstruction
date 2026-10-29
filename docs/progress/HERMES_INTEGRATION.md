# Optional Hermes Agent backend (#136)

Status: **IMPLEMENTED / TESTED offline** for detection, capability gating, argv
construction, role separation, output parsing and failure handling. A live
paid model completion was deliberately **not** run; see *Verified* below.

## Scope

Hermes Agent is an *optional* planner or coding worker for NOVA3. It is not
required to build the game, not required for the coordinator, and not a second
subscription: it uses the operator's existing Hermes-native OpenRouter (or
other) authentication. Nothing in this module installs Hermes, performs vendor
login, copies credentials or starts a daemon.

The adapter is `nova3_team/hermes_runtime.py`. `worker.py` and
`provider_runtime.py` are deliberately untouched: wiring Hermes into the
provider registry is a later narrow PR after the #127 registry is stable.

## Verified CLI surface (2026-10-30)

`hermes chat --help` on the locally installed build documents:

```text
--query-file PATH   read the single query from a file ('-' = stdin), no shell interpretation
--oneshot           answer the query and exit
-Q, --quiet         suppress banner/spinner/tool previews
--format {text,stream-json}
-m, --model MODEL
--provider PROVIDER
-t, --toolsets TOOLSETS
--max-turns N
--run-budget SECONDS
--in DIR
--source SOURCE
--safe-mode         disable user config, AGENTS.md/memory injection, plugins, MCP
--yolo              (never used by this adapter)
```

Toolset names were read from `hermes tools list` (not guessed): `web`,
`browser`, `terminal`, `file`, `code_execution`, `vision`, `image_gen`, `tts`,
`skills`, `todo`, `memory`, `session_search`, `connections`, `clarify`,
`delegation`, `cronjob`, `computer_use`, plus MCP `server:tool` entries.

`hermes --version` does **not** print a semantic version on this build (it
prints Python/SDK lines and an update notice). The adapter therefore derives
`:version` as UNKNOWN and gates on **capability tokens parsed from help text**,
not on a guessed minimum version.

## Generated command

Worker role:

```text
hermes chat --query-file <prompt.md> --oneshot --quiet --format stream-json \
  --provider openrouter --model deepseek/deepseek-v4.1-flash-1017 \
  --toolsets file,terminal,code_execution,todo \
  --max-turns 40 --run-budget 900 --in <worktree> --source tool
```

Planner role adds `--safe-mode --ignore-user-config` and forces the read-only
toolset list `file,todo,skills,session_search`.

Guarantees enforced in code and tests:

* `--yolo`, `--accept-hooks` and `--api-key` never appear in any generated argv.
* Prompts are passed via `--query-file`, never inline: quotes, `$(...)` and
  backticks are preserved verbatim as one argv element.
* A planner invocation may not request `terminal`, `code_execution`, `browser`,
  `computer_use`, `delegation`, `cronjob`, `connections`, `memory`,
  `image_gen` or `video_gen`; unknown toolset names are rejected.
* Provider/model must match bounded identifiers; the model defaults to the
  pinned DeepSeek Flash 1017 slot and any override is explicit.
* Budgets (`max_turns`, `run_budget_seconds`, `max_cost_micro_usd`) are
  validated and bounded; a planner cannot exceed the configured cap.
* Diagnostics never echo vendor stderr, env vars or secrets; model output is
  redacted before it is returned.
* Interactive TTY detection refuses to start a session that would hang an
  unattended worker.

## Planner workflow (with #132)

```text
planning_bridge context  ->  snapshot.json (sanitised GitHub metadata)
hermes_runtime.planner_prompt(snapshot, question)
        -> hermes chat --safe-mode ... -> stream-json
        -> extract_proposal() -> validated proposal dict (or fixed reason)
        -> planning_bridge preview / publish-draft   (owner decides)
        -> access_policy approve-queue              (owner/admin only)
```

`extract_proposal` only returns a schema-validated dict. It cannot publish, label
or queue; a model reply containing prose like "approved, queue it" has no
effect. The Hermes gateway/ACP/TUI JSON-RPC surfaces are intentionally unused
until a separate approved task verifies them.

## Readiness reporting

`hermes_runtime.readiness(status)` returns a compact provider record
(`provider_id`, display name, state, version, model, provider, roles, install
and docs URLs, actionable reason) intended for the Control Center provider
registry after #125.

## Operator first run

1. Install Hermes from the official docs (<https://hermes-agent.nousresearch.com/docs>).
2. Authenticate with the vendor-native flow (`hermes model`, or an OpenRouter
   key in Hermes' own secure storage). Do not put keys in this repository.
3. `python3 -m nova3_team.hermes_runtime status` — expect `"state": "available"`.
4. Keep `gh` authenticated separately; the planner reads GitHub through `gh`,
   never through the model.
5. Run planner and worker with **separate** provider/session configuration and
   never enable `--yolo` for either.

## Verified

Run locally on 2026-10-30:

* `probe()` → `state=available`, executable
  `/home/jason/.hermes/hermes-agent/.hermes/bin/hermes`, all 8 required
  capability tokens present, version UNKNOWN (not advertised).
* Generated planner argv executed for real with a deliberately invalid
  provider and `max_turns=1`, `run_budget=45`: Hermes accepted every flag,
  started headless, and exited 2. The adapter classified it as
  `RunState.FAILED` with the fixed reason `Hermes exited 2`, empty output and
  two parsed stream-json events — i.e. argv shape and failure classification
  are real, not synthetic.
* `python3 -B -m unittest nova3_team.tests.test_hermes_runtime` → 40 passed.
* Full `nova3_team` suite green in this worktree.

## Not verified

* No live model completion or paid OpenRouter call was made, so planner output
  quality, tool-use correctness and real token cost are UNKNOWN.
* Cost accounting is a pre-flight cap (`max_cost_micro_usd`), not a measured
  spend; Hermes' own usage reporting is not parsed here.
* `--safe-mode` isolation of planner runs is a documented Hermes behaviour, not
  independently audited by this project.
* Native-child tool permission enforcement inside Hermes is vendor-owned; the
  adapter guarantees only what it passes on the command line.
* No Control Center page, provider-registry entry or worker integration yet
  (#125 / later narrow PR). No multi-worker concurrency (#126).
