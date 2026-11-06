# NOVA source worker: bounded agents and acceptance-gated delivery

## Actual lifecycle

1. **Plan only on changed evidence.** The private supervisor state stores a
   fingerprint of HEAD, priorities/operator guidance, ledger and material issue/
   task state. An unchanged empty/rejected plan is not sent to a model again.
   Provider/parse failures back off. Empty plans never mean the game is complete.
2. **Approve bounded game-source work.** The enabled owner switch uses the normal
   exact-text approval gate. Source-first auto-admission excludes new standalone
   dashboard/setup/helper-test proposals. Previously approved work can still be
   reviewed and delivered. Approval hashes use the gate's lease/whitespace
   canonicalization; edits to real requirements still invalidate authorization.
3. **Collaborate selectively.** A complex source task (production source plus
   another scoped file) gets at most two controller-managed subagents: analysis
   and independent test design. These are real fresh DSH headless sessions, not
   extra model calls hidden inside one context. They may run concurrently, then
   the implementation session receives their explicitly unverified suggestions.
   Routine/trivial work skips this overhead. Notes are cached by commit/task.
4. **Implement and independently test.** Existing scope, source-only, unittest,
   CMake/ctest and task verification remain in place. Passing tests produces a
   **draft submission**, not an approved implementation or completed delivery.
5. **Review actual acceptance.** The historical immutable owner-approval comment
   must bind the full current canonical task (including Verification), authentic
   actor/repository/issue IDs and valid original posting window. Editing away
   requirements cannot obtain a new review on a weakened contract. Expired
   history is provenance under the current owner delivery switch, NEVER current
   queue authority. The branch must include the current base; an expected-head
   update triggers fresh CI before review. A fresh read-only reviewer checks the
   exact head, approved objective, every acceptance criterion, changed code and
   current CI.
   Its identity comes from the runtime, not its generated JSON. Compliance-only
   checks are additional safeguards, not semantic review. A test-only or
   comment/include-only change cannot satisfy a production-source objective.
6. **Repair within a hard limit.** A separate scoped repair session can address
   findings on the existing branch. At most two repair invocations across heads;
   failures never reset that counter. New heads require fresh independent review
   and CI. Missing evidence is blocked/escalated, never a fabricated success.
7. **Deliver, then close.** Only a substantive pass plus compliance and current
   required successful CI can make a draft ready and merge its exact head.
   Branch/base/task races fail closed. A sanitized `COMMENT` review is published
   against the exact reviewed commit (session/counts/digests, no raw model text).
   It is not a forged GitHub `APPROVE`; required human/other-account reviews and
   branch protection are never bypassed. Delivery is recorded only after GitHub
   confirms `merged: true` and linked issue closure is verified. No issue is
   closed merely because a model returned success or tests passed. PRs use
   `Related to #N`, not GitHub auto-closing keywords; legacy closing directives
   are migrated before review so an in-flight merge cannot bypass the explicit
   final task check. Controlled squash uses a fixed non-closing commit message,
   so inherited commit messages cannot auto-close tasks either. Squash must
   already be allowed by the repository; no settings change, branch deletion or
   existing-history rewrite is requested. Canceled/closed tasks are not merged
   as new work.

## Agent permissions and model routes

- Roles reuse the same owner-added model catalog/router as the planner. Cheap
  routine/test-design work prefers Flash; complex analysis, review and repair
  prefer configured Sol. Other added authenticated models remain eligible.
  Provider/account depletion follows the configured fallback/cooldown policy;
  failed code is not retried on random models.
- Collaborators/reviewers are read-only. Repairs can edit only the approved
  paths. A monotonic tool guard denies shell/network/session/permission tools
  and recursive agents; no role can approve, commit, push or merge itself.
- Read access is restricted to explicit tracked public checkout files (plus
  explicitly approved new repair files). Private/reference trees, hidden files,
  credential-shaped files, recursive root searches and symlink escapes are
  denied. This does not grant permission to upload proprietary source/bytes.
- The Python controller owns Git operations. A repair must descend from its
  original head and use an exact expected-SHA lease, so concurrent forward,
  rewind and delete changes refuse publication rather than overwrite history.
- Child sessions have separate step/tool/wall bounds; discovery and large-stdin
  startup count toward the deadline. At most two advisory children; one PR with
  model review/repair per timer pass. Existing task time is reduced by actual
  collaborator elapsed time. Delivery/collaborator usage enters the private
  spend ledger, not an unreported side channel.

These sessions are controller-managed roles correlated by task/session IDs. This
is **not a claim that the existing native Agents tab now shows a nested tree**.
No new toggle tab or replacement Web server is introduced.

## Review and retry persistence

The existing autopilot timer drives the delivery lane. A nonblocking shared
lock prevents concurrent timer/CLI/slash invocations from duplicating work.
Per-PR caches bind verdicts to head/base/task and preserve the repair budget.
Authority/guard/controller code, instruction files, dotenv configuration and
assembly payloads require manual owner review; this lane cannot rewrite its own
gates. The current owner switch, actor and admin role are checked again before
remote writes. Turning delivery off cancels its model sessions and prevents
subsequent publication/merge actions; already-dispatched GitHub requests cannot
be retracted. Missing/pending CI waits without a model; failed CI enters bounded
repair, and a repaired head still needs green CI and independent acceptance.
A cursor avoids one blocked PR starving the rest. Unchanged semantic failures
are not reviewed repeatedly; provider failures cool down rather than count as
acceptance. Diff/context limits reject giant inputs before a model call.

Private artifacts stay under the existing per-repository autopilot directory:

- `delivery/*.json`: state, hashes, verdict/budget metadata;
- `roles/*/session.log`, `last-session.json`: full private review/repair evidence;
- `merge-audit.jsonl`: delivery/refusal decisions;
- bounded repair checkouts: preserved for inspection, never reset to erase work.

`/nova status` reports delivery state counts and whether the updated worker has
actually loaded. Model completion, PR submission, acceptance pass and delivery
are distinct states. CI success is not proof of original-game equivalence.

## Activation — owner controls service restarts

The timer runs a fresh Python process, so the new delivery path can load without
restarting Web. The long-running supervisor must be restarted **by the owner**:

```sh
systemctl --user restart nova3-dsh-worker.service
```

No Web service restart is required. New automatic approvals remain gated until
the new supervisor's setup writes `acceptance-gated/1`, its PID and Linux process
start ticks into its private state. The timer checks that attestation against
procfs; merely editing files, restarting another service, or leaving an old PID
alive cannot claim that collaborators/planning suppression are active.

The owner's explicit request authorizes enabling reviewed final delivery and
source-first admission through the existing admin-checked autopilot switch.
Do not weaken gates or treat a pending restart as completed live activation.

## Verification boundary

Verified 2026-11-06: **1,509 Python tests and 8 adversarial role-guard tests pass**;
the existing 4 slash-command tests also pass. Read-only production API checks
verified historical task provenance for PRs #347/#351; actual repository policy
already allows squash into `main`. No services were restarted.

Offline tests exercise full injectable review/repair/CI/ready/merge/issue-closure
flows, malformed/stale evidence, provider failures, authorization hashing,
planning suppression and adversarial role-tool restrictions. CLI help/overlay
composition was checked using the actual installed headless runtime **without
invoking a model**. That is not an end-to-end production delivery claim.

Live validation: PR #305 ran distinct real Sol reviewer and repair sessions; the
review found an unmet golden-CLI test criterion, the scoped repair was committed
and pushed, and `source-and-tests` subsequently passed. GitHub's first post-push
REST read briefly returned the old head. The controller now pins the Git-verified
published commit and waits for API convergence without another paid repair or
premature acceptance. A moving base SHA is not a changed branch identity; fresh
base/CI/review gates still apply. Historical audit outcomes are not erased.

The complete production lane also delivered PR #306: a commit-bound independent
`COMMENT` review, successful required CI, confirmed squash merge and verified
closure/integrated labeling of issue #303. PR #305 remained blocked rather than
claiming skipped live-input verification as success.

A second live API check found that PR `base.sha` was a stale snapshot while the
default branch had advanced. The controller now validates and pins the actual
`git/ref/heads/<default>` commit in provenance, review/cache keys and ancestry
checks; moving that live ref invalidates pre-merge decisions. Expected advancement
from the controller's own confirmed merge does not invalidate resolved-task
closure. Client-side REST checks are not an atomic expected-base lease; GitHub's
server branch protections remain authoritative for later concurrent changes.

A production PR still needs its own real review and required CI. Unknown private
comparison evidence remains unknown; a reviewer must block rather than assume
behavioural or instruction-window equivalence. This orchestration does not make
an unimplemented game complete or turn reference-layout stubs into source.
