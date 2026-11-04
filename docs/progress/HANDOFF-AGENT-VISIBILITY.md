# Handoff — agent visibility, /toggle, real progress metrics

Written 2026-11-03 after the user's explicit requirements. Read this before
changing the Web UI plugin or the autonomy loop. Do not re-litigate what is
marked DECIDED.

## The user's requirements (DECIDED — do not reinterpret)

1. **The autonomous agent must be visible in the DSH dashboard as a session,
   clearly separate from normal user chats**, so it is never mistaken for a chat.
2. **A dedicated tab just to toggle the agent is stupid.** Management must be a
   **slash command** (`/…`) in chat.
3. **The Mission tab is nearly useless.** Replace the ETA/stats with something
   that works: an *honest* view with useful statistics. A third-party plugin that
   does this well is acceptable and preferred over continuing to hand-roll it.
4. **The agent must generate AND complete its own tasks** — full autonomy, no
   owner intervention per task. Approving/merging PRs alone is NOT the job.
5. The user has **$1 of OpenRouter credit until payday**. Prefer local work; do
   not propose experiments that burn model credit.

## Verified update — 2026-11-05 (supersedes historical claims below)

- **Draft-verification root cause established**, not network loss or relay timing:
  `provider_runtime.redact_diagnostic` treated the NONSECRET budget variable
  `NOVA3_DSH_TOKENS_PER_DAY=0` as a credential because its name contains TOKEN.
  Replacing every `0` corrupted structured JSON (`exit_code: 0`), so event parsing
  reported `tests_completed=0`. Reproduction before fix:
  `NOVA3_DSH_TOKENS_PER_DAY=0 /usr/bin/python3 -m unittest nova3_team.tests.test_worker_timeout.ProviderExecutionTests.test_structured_stream_and_independently_observed_file_count -v`
  failed with exactly the production assertion. Exempting this exact budget
  setting fixes it without weakening redaction of real API keys. After fix:
  1,331 unittest tests pass under that same budget setting. Added dedicated
  regression and secret-preservation tests. Existing draft branches still need
  the fix incorporated and independent re-verification; never bulk mark ready.
- `/nova` had three concrete contract errors: handler used `input` rather than
  DSH's `rawInput`, did not declare its free-form input hint, and `on` called the
  nonexistent CLI action `autopilot on`. Patched to use the real invocation
  contract and a new `agent_control` CLI: off writes supervisor STOP then
  disables approvals; on authorizes autopilot enable FIRST, then removes STOP
  and writes RESUME. Checkpoints are never removed. Pure/mocked tests do not
  spend credits or alter live switches.
- Worker view distinguishes MODEL EXECUTING, NO ACTIVE MODEL SESSION and STATUS
  UNAVAILABLE. A real process check still determines live execution. Historical
  counters are explicitly labelled; another task's logs cannot be attributed to
  the current task. Session identity comes from the actual recorded event.
- Removed BOTH mapping-calendar projections from the Mission card. Project-end
  ETA remains unavailable, not fabricated. Existing inventory/implementation/
  behavioural/matching counters and queue statistics remain separate.
- Evaluated https://dsh-plugin.org/plugins/omokom/dsh-progress-eta : it estimates
  individual turns, not whole-game completion, and has optional extra LLM calls.
  Not installed: it does not satisfy this project's end-goal ETA requirement.
- Current autopilot observed DISABLED before changes. Do not silently enable a
  disabled switch or interpret a live supervisor PID as paid model execution.
  No paid probe sessions are needed to verify these fixes.
- Historical assertions that Host sessions *require* an attached browser were
  not established by inspecting the Host transcript format. Keep native-session
  integration unproven; do not repeat that claim as an architectural fact.

## Historical observations (2026-11-03; not current status)

- Worker: `nova3-dsh-worker.service`, headless DSH child per task. It **does**
  generate tasks (planner filed #331–#336) and **does** complete them
  (tasks 336, 334 completed at 21:53 and 21:59). Autopilot approved them
  itself. So requirement 4 is *partly* real; see the blocker below for why it
  looks like "only approves and merges".
- The loop's visible failure is **draft PRs**: 14+ open, none mergeable. Each
  one's body names the same failing test:
  `test_structured_stream_and_independently_observed_file_count`
  `AssertionError: 0 != 1`. It fails **inside the worker's process** and passes
  standalone, 8/8, and inside `worker.verify()` standalone. Cause **NOT
  ESTABLISHED**. Fixes already applied that did not resolve it: relay drain
  before activity publish, 2s→20s child bound.
  Consequence: tasks move to `nova:review`, stay unmerged, the planner hits its
  in-flight cap, and the visible behaviour becomes "approve and merge only".
- Native Agents tab is the **squad registry** (`@linxin666/dsh-agents`): it lists
  Host-owned sessions/runs. It currently shows the squad "NOVA3 Reconstruction"
  with 2 members and **1 stale run** (`session-23e38197…`, marked running since
  16:15). The systemd worker appears **nowhere** there and cannot: DSH cannot
  list a process it did not create.
- A bridge plugin exists (`dsh-plugins/dsh-runner`) that creates real Host
  sessions with an explicit `cwd`; it returns 200 and creates sessions, but
  **queued prompts never execute without an attached browser client** (two
  sessions: 0 steps, 0 tools, no terminal turn). That is why the worker runs
  headless. This is the central obstacle for requirement 1.

## Mission ETA — correct the record

An earlier statement in this session claimed the band ordering was inverted.
**That was wrong.** 37,927 unrecorded functions ÷ 2.25 mappings/day = 46.2
years; the 30.8–92.3 year band is consistent. The problem is presentation: a
naive floor was shown as the headline ETA. Changed in `nova3_team/analysis.py`:
it now says "Throughput … a rate, not an ETA", and the unrecorded extrapolation
is labelled `NOT AN ESTIMATE`. The recorded-inventory scenario (16.7 months) is
shown first.

## Concrete next steps, in order

1. **Fix the draft-PR blocker.** Reproduce
   `test_worker_timeout.ProviderExecutionTests.test_structured_stream_and_independently_observed_file_count`
   under the *worker's* interpreter and env (`/usr/bin/python3`, checkout cwd,
   worker env) rather than the venv. Read
   `~/.cache/nova3-team/<repo>/.nova-slots/dsh/state/<task>/.nova-private/round-1.log`
   for the activity JSON and event stream. Until this is green in that context,
   requirement 4 cannot be met.
2. **Requirement 1 (visibility).** Options, in order of honesty:
   a. Ask DSH upstream (or its config) whether a headless child can register a
      session the Web host lists; this is the only path to a real "session".
   b. Otherwise build our own **"Runs" view inside the control plugin** that
      reads supervisor state and shows one row per task round, styled as a
      session card and labelled `autonomous · not a chat`. Do not pretend it is
      a Host session.
3. **Requirement 2 (slash command).** The control plugin
   (`dsh-plugins/nova3-control`) currently registers **no** command. Use the
   DSH command service (see `dsh-web` / `@deepseek-ai/dsh-tool-*` command
   plugins) to add `/nova on|off|status`, backed by
   `python -m nova3_team.autopilot enable/off`.
4. **Requirement 3 (stats plugin).** Evaluate an existing marketplace plugin
   for project dashboards before writing another one. If none fits, delete the
   ETA card entirely and show: throughput, coverage of recorded inventory,
   verified counts, open tasks by state, merged PRs today. No years figure.

## Do not do

- Do not add a dedicated tab for toggling.
- Do not present a mapping extrapolation as a project ETA.
- Do not claim the Agents tab shows the worker.
- Do not merge PRs whose verification failed; fix the test or close the ticket.
