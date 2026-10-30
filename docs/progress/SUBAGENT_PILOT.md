# Manual read-only research pilot (#143)

This is an opt-in manual pilot, independent of distributed workers. The parent
is the sole writer. Three role files do not start three agents: select at most
two children at once, and delegate only independent bounded reads/reviews.
No worker, coordinator, queue, claim, service, credential, budget policy or
automerge configuration changes are part of this pilot. Default off means no
automatic dispatch or invocation; it does not change Codex's global defaults.
OpenCode PR #144 is a separate gateway pilot, not Hermes integration (#136).

The profiles use the documented standalone custom-agent fields `name`,
`description`, `developer_instructions` and disabled optional features. They
inherit the parent's model; no vendor, account, model or reasoning override is
silently selected. Review instructions are **not a separate read-only sandbox**.
Children inherit permissions and tools from the parent; instructions, budgets
and disabled optional capabilities are not an operating-system security boundary.
Use a disposable source-only checkout with no credentials or private game data
when a hard isolation boundary is needed. A TU hypothesis, synthetic object,
passing host test or model assertion does not establish an original match.

## Offline checks and honest capability states

Python 3.11+ (standard-library `tomllib`) is required for this pilot's checks:

```sh
python -B tools/agents/subagent_preflight.py
python -B -m unittest nova3_team.tests.test_subagent_profiles -v
python -B tools/agents/subagent_preflight.py --probe-codex
python -B tools/agents/subagent_preflight.py --probe-codex-loader
```

Default preflight reads only allowlisted public pilot files and executable
visibility. It never reads credentials, user/provider configuration, original
binaries or private analysis. It starts no processes, installs nothing, writes
no report, and makes no network/API calls. Its exit 0 means instruction/schema
lint passed; it does not mean a provider or agent ran successfully. Symlinked,
oversized, malformed or missing profile inputs fail validation.

`--probe-codex` explicitly permits two bounded commands: `codex --version` and
`codex exec --help`, in a disposable home with credentials and provider settings
removed from the child environment. It emits only a parsed numeric version and
fixed capability states, never raw CLI output. A missing/failed/incompatible CLI
returns nonzero for this requested probe. `CLI_FLAGS_VERIFIED` proves those flags
only. `profile_loading: UNKNOWN` remains separate: help/version is not proof
that the CLI discovered, parsed or applied a custom role. The script never
starts a model session merely to make that field green.

`--probe-codex-loader` is a separate explicit integration check. It creates a
temporary source-only project and credential-free home, then routes native
Codex exclusively to a local fixture bound to `127.0.0.1`. Fixed synthetic SSE
responses invoke only native `spawn_agent` and `close_agent`, sequentially for
each role. The check inspects the actual child request for the exact full role
instructions and the parent's native close acknowledgment. No real model is
queried and no vendor credentials or endpoint are used. All three successes
yield `profile_loading: VERIFIED_LOCAL_FIXTURE` and `children_closed: true`.
This proves native profile loading/application, not model quality, instruction
obedience, a separate sandbox or live vendor readiness. No child shell commands
are requested. Each native invocation has a ten-second timeout. A sandbox that
denies loopback sockets returns `PROBE_FAILED` with a fixed diagnostic; use the
runtime's ordinary approval mechanism, never bypass restrictions. This option
is not automatically run by the deterministic unittest suite or CI.

Hermes detection reports `UNAVAILABLE` when absent or `PRESENT_UNVERIFIED` when
an executable is visible. `config_schema: UNKNOWN` and endpoint `tool calling`
UNKNOWN remain until checked for that actual installed release. Preflight never
executes a Hermes wrapper: some wrappers perform installation/bootstrap/repair
even for help. Absence must not install Hermes or contact an API. Do not run
`hermes pm repair` as part of this task.

## Temporary Codex / ChatGPT sprint

Use a separate clean Git worktree and an issue-specific branch. Inspect existing
worktrees and `git status --short` before creating or editing anything. Do not
duplicate unfinished work. Parent reads AGENTS.md, docs/NOVA_TEAM.md and the
issue's exact allowed paths, then runs the offline checks above. No runtime
dependency on a local original APK/ELF is introduced by the pilot.

After checking support on the installed CLI, this one-shot explicitly enables
manual delegation for that invocation alone (the shell timeout is an optional
Linux/coreutils outer bound; use an equivalent process timeout elsewhere):

```sh
timeout 900s codex exec --sandbox workspace-write \
  -c agents.enabled=true \
  -c agents.max_concurrent_threads_per_session=2 \
  'Work only on the approved issue and its allowed paths. Parent is sole writer.
   If independent review is useful, assign at most two read-only children using
   nova3_tu_mapper, nova3_match_reviewer or nova3_test_reviewer. Pass the exact
   public inputs, allowed read paths, acceptance criteria and a scratch-output
   policy. Children may not spawn, edit, claim, queue, push or merge. Each has
   at most 10 minutes, 20 commands and 30 files. Stop on blockers and return a
   handoff with evidence and actual test results. No private game data in prompts.'
```

This is a manual account-usage action, not a command run by preflight or CI.
Inspect the supported CLI's role catalog/child details to confirm the selected
role instructions were actually applied (`codex agents` lists sessions on the
local app-server daemon; `codex exec resume <id>`/`fork` re-enter one). If the
standalone roles are absent, report UNSUPPORTED/UNKNOWN and stop delegation; do
not silently use a generic worker. Do not add legacy registration/global config
or guess a schema merely to conceal a capability failure. The parent
independently rechecks every child conclusion.

Do **not** add `--ephemeral` to a delegated run. On Codex CLI 0.163.0
`codex exec --help` documents it as "Run without persisting session files to
disk", so a pristine throwaway session is created and then discarded. That
directly defeats the two checks this pilot depends on: there is no session left
for `codex agents`/`resume` to inspect — so child role application and child
closing cannot be evidenced after the run — and an interrupted or timed-out
sprint cannot be resumed, only restarted. `--ephemeral` is also not an
isolation or read-only boundary: it does not make source files, worktree
changes or external side effects ephemeral. Reserve it for a single-agent
throwaway analysis where neither session inspection nor resume is needed and
no child agents are spawned; when in doubt, omit it and rely on the worktree,
`--sandbox workspace-write` and the bounded wall clock instead. The parent must
still save source changes and a concise issue-scoped handoff as it goes, because
a killed process ends the session regardless of persistence.

The handoff includes issue/head, six-path scope, existing preserved changes,
tests and exit codes, findings with lines, unresolved capability checks, and
next safe command. Keep private diagnostics out of GitHub. Commit/push only
reviewed issue-scoped source; create a separate PR; do not merge automatically.
Stop all children and wait for their bounded tests before exiting. Remove a
temporary worktree only after clean commits and PR creation, checking ignored
and untracked artifacts for unfinished work; otherwise preserve it. Never remove
another worker's worktree.

## Optional Hermes / OpenRouter verification (separate account)

Hermes does not read Codex TOML. Use its vendor-native private `config.yaml`,
outside the repository, only after reviewing the installed release's schema.
No project-local Hermes agent format or credential copy is introduced here.
Codex uses its own ChatGPT/OpenAI login; Hermes/OpenRouter is a distinct billing
and permission route. There is no automatic fallback between them.

1. Locate `hermes`, inspect its launcher for bootstrap side effects, then check
   the installed CLI's `--version`, `--help` and `chat --help` only when safe.
   Record exact release/source identity, exit codes and actual supported flags.
   If it requires repair, installation, auth or network even for diagnostics,
   record UNAVAILABLE for execution and stop; executable visibility is not readiness.
2. Compare that release's delegation docs and implementation with the following
   optional native settings. Do not write them automatically or change an active
   user's config. These are an example for an explicitly opted-in isolated
   manual session, not a new repository config file:

   ```yaml
   delegation:
     max_concurrent_children: 2
     max_spawn_depth: 1
     max_iterations: 20
     oneshot_max_children: 2
     orchestrator_enabled: false
   ```

3. Verify the `delegate_task` tool exists. A native call, after authorization,
   can pass two entries in `tasks`, each with `goal` and a complete `context`
   (public input paths, scope, no edits, no GitHub writes/claims/queue/merges,
   10-minute/20-command/30-file budget, no spawning, evidence and handoff).
   Use leaf children. Do not invent a per-task `max_iterations` argument: the
   supported native config controls it. The parent is the sole writer; budget
   instructions are cooperative, and iteration/concurrency limits are not a
   monetary spend cap or guaranteed wall-clock cutoff. Use an outer bounded
   process for a finite manual trial and verify child teardown on cancellation.
4. Before any paid smoke test, the operator separately authorizes account usage
   and a hard spend cap. Keep vendor-native credentials outside this repository.
   Verify the actual OpenRouter endpoint, pinned model
   `deepseek/deepseek-v4.1-flash-1017`, and actual endpoint tool calling support.
   Model listing or a documentation claim alone is not tool-call proof. If
   enforcement or endpoint support is unknown, do not run the paid trial; report
   UNKNOWN. No paid inference, auth flow or automatic CI smoke test is supplied.
5. A successful manual delegation must show independent results and cleanup;
   independently check them. It does not implement #136 planner/worker routing
   or satisfy #126/#127/#134/#135 distributed dispatch prerequisites.

## Observed local checkpoint and remaining acceptance

At implementation, both sprint worktrees were clean at `7d470bc` and the
existing `nova3_subagents` was inspected read-only and left untouched. Only
this issue's six allowed paths are changed in the implementation worktree.
Codex reports `codex-cli 0.163.0`; its help includes the one-shot flags above.
An initial offline prompt-inspection experiment did not expose a standalone
custom-role catalog. The subsequent reproducible loopback fixture above exited
0 on Codex 0.163.0: it discovered each role, observed each role's exact full
instructions in an actual native child request, and received each native close
acknowledgment. **All three profiles are VERIFIED_LOCAL_FIXTURE**. The model
responses were deterministic local fixtures; no paid inference was used.

The Hermes launcher exists, but both `--version` and `--help` exited 1 on an
installation-lock write blocked by the current sandbox. No repair/install or
inference followed. Read-only installed source at commit
`a938f00f0f3cdf00fdb93d9a82a9ae447e9b857f` contains native `delegate_task` and
the concurrency/depth/iteration keys above; that is source-schema evidence,
not a successful CLI/endpoint/delegation test. Runtime remains unavailable for
this session. There is no claim of Hermes backend completion or original-game
behavioural equivalence. Optional live Hermes/DeepSeek delegation remains
pending separately authorized endpoint/cost checks; it is not a prerequisite
for using the tested Codex manual pilot.

Executed checkpoint checks: the initial targeted unittest command passed 17
tests in the implementation checkout and again in an independent scratch copy.
After adding the explicit native fixture it passes 22 deterministic tests. The
main-agent regression command `python -B -m unittest discover -s nova3_team/tests -q`
initially passed 517 tests; the final fixture revision passes 522. Default
preflight, opt-in Codex help/version, and opt-in native loader fixture exited 0;
no paid calls occurred. These are synthetic/offline engineering tests.

The check commands above are the standalone resume path. Offline lint and test results must stay distinct
from simulated-model profile loading, unavailable Hermes execution and unpaid endpoint
verification. No new game functions are reconstructed by this engineering task.

## Official references (checked 2026-10-30)

- [Codex custom subagents and inherited permissions](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference)
- [Hermes native delegation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md)
- [Hermes installed-source schema comparison](https://github.com/NousResearch/hermes-agent/blob/a938f00f0f3cdf00fdb93d9a82a9ae447e9b857f/tools/delegate_tool_config.py)
