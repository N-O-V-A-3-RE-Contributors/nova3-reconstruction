# Handoff — agent visibility, /toggle, real progress metrics

## 2026-11-06 — owner-requested orchestration upgrade

Implemented bounded controller-managed DSH collaborators, task-aware routing,
unchanged-plan suppression, independent acceptance review, capped repair and
reviewed/green exact-head PR delivery. See `docs/AUTONOMOUS_DELIVERY.md` for the
actual lifecycle, authority checks, private audit artifacts and activation.
Passing tests/model completion is not delivery; test-only source objectives and
unknown matching claims cannot be promoted. No native nested-Agents UI claim.

The existing worker process still needs an **owner-controlled** restart into the
new code; Web does not. New task auto-approval is held until its version/PID/start
ticks prove the updated supervisor ran setup. Do not restart either service from
an agent or schedule a restart. Preserve the owner's dirty matrix-multiply file.
Source-first priority below/`NEXT.md` remains: do not generate more setup jobs.

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

## Follow-up verification — 2026-11-05 05:46

- Confirmed GitHub main at `98103e1`, containing donor integration `1469692`,
  audit, importer/tests and source-first priority changes. Re-ran the donor and
  source-reference pytest suites: **21 passed**. The supplied 2,027 layout stubs
  still represent zero imported game implementations; detailed hints stay local.
- The worker did take #349: selected GPT-6.1 Sol at 05:29:43 and submitted draft
  PR #351 at 05:44:08. However, the actual PR diff changes **only the recovered
  C++ test** (+65 lines), not the getInverse implementation. Source-and-tests CI
  succeeded; contributor-review was skipped. The source correction is not proven
  complete despite the supervisor's COMPLETED execution status.
- Posted that finding on PR #351 and left it draft/unmerged. Updated live
  guidance and NEXT.md to distinguish partial test evidence from implemented
  source, and to choose another concrete source target when this one is blocked.
- Do not inflate implementation/matching counts or approve PR #351 as the
  completed arithmetic-order correction solely because its CI is green.
- No service restart, paid probe, merge approval or original-code upload was
  performed in this follow-up verification.

## Owner-approved transition to reconstruction — 2026-11-05 05:00 request

- Approved pending setup request #348 through the normal hash-bound comment and
  queue-label gate. Worker actually claimed it and selected the configured
  DeepSeek Flash route for routine work. The provider session failed at step 1
  with PI_AI_ERROR, zero tools/tokens, before edits; #348 was quarantined. Do not
  describe setup as completed or keep retrying it as a condition for game work.
- Narrowed the existing live planner operator override to source reconstruction
  only after #348. The next real planner pass produced **#349**, an actual
  getInverse cofactor-order source task at Premium 0x000ebb08, not another helper
  or dashboard task. No worker restart was required for that guidance.
- Checked the task's local compiler/interpreter dependencies and host C++ test;
  the existing independent inverse test passes. Corrected #349's verification
  paths to owner-installed tools/reference locations before approval, since
  isolated task checkouts do not contain private toolchains.
- Approved #349 with the normal owner/admin comment and queue labels. Its first
  approval was rejected because the task body had a trailing newline but the
  gate hashes lease-stripped/rstripped text. Canonicalized that whitespace only,
  replaced the stale locator with a fresh approval, and verified the ACTUAL
  worker-credential gate returns `allowed=True` (`6638668176`). No gate code or
  security rule was bypassed. Closed never-started duplicate #350 (same target)
  to prevent competing source edits. Autopilot's persistent switch and review/
  merge gates were not changed.
- `NEXT.md` now puts actual decompilation/source recovery ahead of old setup
  instructions. Keep #349's acceptance criteria: meaningful source change,
  independently passing behaviour tests, measured before/after comparisons,
  private full outputs, and no invented byte-matching claim.

## Latest continuation — 2026-11-05 04:17

- Owner restarted Web at 03:53:21 and worker at 03:53:26 (systemd timestamps
  verified). Do not restart them again. The prior restart requests below are
  historical, not outstanding owner actions.
- REvanguard3 integration published on main as `1469692`, together with the
  earlier verification/routing/status fixes. See `REVANGUARD3_IMPORT.md`.
  No decompiled function bodies existed in the supplied layout; only audited
  metadata tooling was integrated. Never count 2,027 stubs as implementation.
- Took the next draft-verification task: PR #347 / issue #294, isolated worktree,
  merged current main into its branch without changing the task implementation.
  Full unittest discovery under the actual restarted worker `/usr/bin/python3.14`
  and service environment (`NOVA3_DSH_TOKENS_PER_DAY=0`) passed **1,349 tests**,
  exit 0. Required pytest passed **9 tests / 19 subtests**, exit 0.
- Published branch update `d4f6187` and evidence comment on PR #347. Kept DRAFT;
  no review approval or merge is implied. This establishes the fix in a real
  task checkout, beyond the previous standalone regression reproduction.
- Observed 21 open PRs, all drafts, before updating #347. The other drafts still
  require individually checked base integration, task verification and review.
  Do not bulk mark ready or auto-merge. Native Agents integration remains open.
- Supervisor still WAITING_APPROVAL for #348; no automation enable, paid model
  session, or service restart was performed in this continuation.

## Owner restart and routing rule — 2026-11-05 03:21 request

- **Only the owner restarts DSH services.** Never schedule another reload or
  self-prompt/restart script. No pending reload timers were present when checked.
- Reuse the planner's existing `dsh_model_router` pipeline; do not spawn a new
  setup agent. Router now reads the actual added-model catalog (8 entries at
  verification), rather than only Web default and remembered Codex routes.
- Cheap bounded test/docs work, including tests of ARM/compiler code, prefers
  added DeepSeek V4.1 Flash 1017. Planning and difficult implementation/review
  prefer added GPT-6.1 Sol. This is explicit policy, not a capability benchmark.
- Other added models remain eligible. Live catalog removals take effect on the
  next session; when Web is offline, existing configured/remembered routes are
  retained rather than guessing a new model.
- Account quota/auth failures cool down sibling models on that provider and
  switch providers, rather than retrying many names against exhausted credit.
  Task/code failures still never trigger paid model roulette.
- Local-only validation: 21 routing tests and the full 1,339-test unittest suite
  passed with `NOVA3_DSH_TOKENS_PER_DAY=0`; metadata-only discovery showed
  planner -> gpt-6.1-sol, tests/docs -> DeepSeek Flash, native implementation ->
  gpt-6.1-sol. No paid model sessions or service restarts were launched.
- **Activation pending owner action:** `nova3-dsh-worker.service` needs a restart
  at an idle checkpoint to load the new Python router. Do not restart the Web
  host for this routing-only change. Existing UI changes were loaded at 00:07.

## Live verification — 2026-11-05 03:30–03:42

- Authenticated Host `commands/list` returned `/nova` with the required input
  hint. `commands/execute` with `line: /nova status` and no attachments returned
  `kind: success`, not a model turn. The actual transport is a client-request
  envelope with `payload.args`; plain JSON requests are not that protocol.
- Browser on the existing GUI: selected an existing conversation, then Worker.
  The live card rendered NO ACTIVE MODEL SESSION, supervisor WAITING_APPROVAL,
  actual recorded session identity and `latest recorded session`. This correctly
  distinguishes idle supervisor from a model executing. No paid probe required.
- Mission was viewed: project ETA unavailable, throughput 2.25 mappings/day
  explicitly measured over 2026-10-25..2026-11-02, inventory separate from source
  mappings. No 46-year projection remains in the card.
- `/nova status` still rendered an old recorded-inventory ETA. Removed that
  forecast from `dsh_status.render` too and added a regression test. The command
  launches a fresh Python process, so this text change needs no host restart.
- Screenshot exposed a light-theme contrast issue (dark badge/background).
  Theme-inheriting root/badge styles and plain row labels are prepared; this
  last client-only polish awaits the OWNER's Web service restart. No restart
  was performed in this verification round.
- Current autopilot is unset, execution waits for approval #348. Do not describe
  this as active autonomous reconstruction, and do not silently enable spending.
- Native Agents integration remains unimplemented. The verified Worker view is
  the handoff's honest fallback, not a forged Host run or ordinary user chat.

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
