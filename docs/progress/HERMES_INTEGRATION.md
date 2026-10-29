# Optional Hermes Agent backend (#136)

Status: **IMPLEMENTED / TESTED offline, wired into the permanent worker**. The
registry entry, file-based worker execution path, fail-closed configuration and
readiness surface exist and are covered by offline tests. A live **paid** model
completion was deliberately **not** run; see *Verified* below.

## Scope

Hermes Agent is an *optional* planner or coding worker for NOVA3. It is not
required to build the game, not required for the coordinator, and not a second
subscription: it uses the operator's existing Hermes-native OpenRouter (or
other) authentication. Nothing in this module installs Hermes, performs vendor
login, copies credentials or starts a daemon.

Two modules own the integration:

* `nova3_team/hermes_runtime.py` — probe, argv construction, execution, config
  and readiness for the Hermes CLI.
* `nova3_team/provider_runtime.py` — the shared provider registry, which now
  contains a `hermes` entry built from the adapter's own capability constants,
  so `worker.py` gains selection/discovery without duplicating any CLI flags.

`worker.py` routes a session through `hermes_command()` when the operator
selects `--provider hermes`. No other provider's argv changes.

## Verified CLI surface (2026-10-30)

`hermes chat --help` on the locally installed build documents:

```text
--query-file PATH   read the single query from a file ('-' = stdin), no shell interpretation
--oneshot           answer the query and exit
-Q, --quiet         suppress banner/spinner/tool previews
--format {text,stream-json}
-m, --model MODEL
--provider PROVIDER (default: auto; built-in or user-defined in config.yaml)
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

The installed build prints a version banner but the registry treats it as
UNKNOWN: readiness is gated on **capability tokens parsed from help text**, not
on a guessed minimum version. `hermes chat --help` is the only probe that may
run; `hermes --version` is not invoked for Hermes.

`hermes chat --help` exposes no static provider enumeration, so the adapter
trusts only the pinned default provider (`openrouter`) implicitly. Any other
provider must be named explicitly in `NOVA3_HERMES_ALLOWED_PROVIDERS`; a typo
is denied rather than guessed.

## Generated command (worker role)

```text
hermes chat --query-file <private>/hermes-prompt.md --oneshot --quiet \
  --format stream-json --provider openrouter \
  --model deepseek/deepseek-v4.1-flash-1017 \
  --toolsets file,terminal,code_execution,todo \
  --max-turns 40 --run-budget 1200 --in <worktree> --source tool
```

Planner role adds `--safe-mode --ignore-user-config`, forces the read-only
toolset list `file,todo,skills,session_search`, and is never launched by the
worker. In the worker path the role is fixed to `worker`; setting
`NOVA3_HERMES_ROLE=planner` makes the worker refuse to start.

Guarantees enforced in code and tests:

* `--yolo`, `--accept-hooks` and `--api-key` never appear in any generated argv.
* Task text is written to a `0600` file inside the private round directory and
  passed as `--query-file`; it never enters argv or a shell. Quotes, `$(...)`
  and backticks are preserved verbatim.
* A planner invocation may not request `terminal`, `code_execution`, `browser`,
  `computer_use`, `delegation`, `cronjob`, `connections`, `memory`,
  `image_gen` or `video_gen`; unknown toolset names are rejected.
* Provider/model must match bounded identifiers; the model defaults to the
  pinned DeepSeek Flash 1017 slot and any override is explicit.
* Budgets (`max_turns`, `run_budget_seconds`, `max_cost_micro_usd`) and the
  wall-clock timeout are validated and bounded; Hermes' own `--run-budget`
  equals the worker timeout, so the child cannot outlive its supervisor.
* Diagnostics never echo vendor stderr, env vars or secrets; model output is
  redacted before it is returned or persisted.
* Launch is headless by construction: the provider child gets `stdin=DEVNULL`,
  and `run_oneshot(still_tty=...)` refuses interactive seeding at the library
  boundary. A terminal on the parent worker is irrelevant and does not disable
  the backend.
* Readiness is re-probed at the launch boundary when the record is older than
  five minutes; an `available` string captured at worker start is not proof
  that the binary and flags still exist.
* The `hermes` registry entry is registered only when `hermes_runtime.py` is
  bundled. A standalone install without it still exposes every other provider
  and reports `hermes` as unknown rather than failing the whole registry.

## Configuration (fail-closed)

| Variable | Default | Bound / meaning |
| --- | --- | --- |
| `NOVA3_HERMES_ROLE` | `worker` (fixed by caller) | must match the calling role or deny |
| `NOVA3_HERMES_PROVIDER` | `openrouter` | registry provider ID; must be allowlisted |
| `NOVA3_HERMES_ALLOWED_PROVIDERS` | `openrouter` | comma-separated extra provider IDs |
| `NOVA3_HERMES_MODEL` | `deepseek/deepseek-v4.1-flash-1017` | bounded identifier |
| `NOVA3_HERMES_MAX_TURNS` | `40` | 1..200 |
| `NOVA3_AI_SESSION_TIMEOUT` | `1200` | 300..1800 seconds (also the Hermes run budget) |
| `NOVA3_HERMES_EXECUTABLE` | `hermes` | executable name, not a shell fragment |

Planner configuration is read from a separate `NOVA3_HERMES_PLANNER_*` prefix;
worker and planner settings cannot bleed into each other, and no session state
is shared. Every failure raises a fixed diagnostic code — `unverified`,
`capabilities`, `provider`, `model`, `role`, `config`, `prompt` — and
never falls back to a default or a partially configured launch. A readiness
record that is `missing`, `probe_failed` or `incompatible` denies execution.

## Worker execution path

```text
worker --provider hermes run
  -> provider_runtime.probe_provider('hermes')          # registry readiness
  -> hermes_runtime.load_config(Role.WORKER)            # fail-closed config
  -> claim/lease gate, Allowed paths gate               # unchanged authorization
  -> private per-round directory
     -> hermes_runtime.materialise_prompt()             # 0600 prompt file
     -> hermes_runtime.build_invocation()               # file-based argv
     -> worker.run_provider()                           # streaming, redacted log
  -> validate_scope -> guard -> verify -> commit/PR     # unchanged
```

Queue authorization, owner/admin approval, path scope, guard, independent
verification and no-auto-merge behaviour are untouched. The worker refuses to
launch Hermes before a claim is granted or when `Allowed paths:` is missing.

Readiness is surfaced through `provider_runtime.readiness('hermes', status=…)`
and included in `worker providers` output (`readiness` key), so the Control
Center and operator diagnostics read one registry record instead of calling the
adapter directly.

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

## Operator first run

1. Install Hermes from the official docs (<https://hermes-agent.nousresearch.com/docs>).
2. Authenticate with the vendor-native flow (`hermes model`, or an OpenRouter
   key in Hermes' own secure storage). Do not put keys in this repository.
3. `python3 -m nova3_team.worker providers` — the `hermes` entry should report
   `available` with a `readiness` block.
4. Keep `gh` authenticated separately; the planner reads GitHub through `gh`,
   never through the model.
5. Run the worker with `--provider hermes`; never enable `--yolo` for either
   role. Planner and worker processes stay separate.

## Verified

Run locally on 2026-10-30 in the integration worktree:

* `provider_runtime.probe_provider('hermes')` → `state=available`, executable
  `/home/jason/.hermes/hermes-agent/.hermes/bin/hermes`, all 8 required
  capability tokens present, version UNKNOWN (not probed).
* `provider_runtime.readiness('hermes')` returns the registry record with
  `roles=[planner, worker]`, pinned model/provider and install/docs URLs.
* A generated worker argv was executed for real with a deliberately invalid
  provider (`nova3-nonexistent-provider`, `none/none`, `max_turns=1`,
  `run_budget=30`, timeout 90 s). Hermes accepted every flag, started headless,
  and exited 2; the adapter classified it as `RunState.FAILED` with the fixed
  reason `Hermes exited 2`, empty output and two parsed stream-json events.
  No model provider was reachable, so no spend occurred.
* `python3 -B -m unittest discover -s nova3_team/tests -q` → 705 tests OK
  (6 pre-existing skips).
* `python3 -B -m unittest discover -s tools/observatory/tests -q` → 150 OK.

## Not verified

* No live model completion or paid OpenRouter call was made, so planner output
  quality, tool-use correctness and real token cost are UNKNOWN.
* Cost accounting is a pre-flight cap (`max_cost_micro_usd`), not a measured
  spend; Hermes' own usage reporting is not parsed here.
* `--safe-mode` isolation of planner runs is a documented Hermes behaviour, not
  independently audited by this project.
* Native-child tool permission enforcement inside Hermes is vendor-owned; the
  adapter guarantees only what it passes on the command line.
* The Hermes adapter is wired only into the solo worker. No Control Center page
  (#125) or multi-worker concurrency (#126) is included.
* Provider allowlisting is necessarily explicit because the installed CLI does
  not enumerate providers offline; operators adding a provider must set
  `NOVA3_HERMES_ALLOWED_PROVIDERS`.
