# Owner/admin-approved task queue (issue #135)

This is the integration layer that adopts the #134 authorization foundation in
the coordinator, the worker and the owner operations tool. It is a **stacked
change**: it is based on the unmerged #134 branch (owner/admin policy
foundation) and the unmerged #127 branch (verified provider runtime). Both must
be reviewed and merged into `main` before this PR; this PR was opened with both
prerequisites present and **must not be merged first**.

Status: source + offline tests. No live GitHub write, coordinator deployment or
paid provider launch was performed while developing or testing this change.
Do not claim the deployed control plane is protected until an owner/admin has
configured the pinned repository identity and observed a denied claim and an
approved claim in production.

## What changed

| Area | Enforcement |
| --- | --- |
| `nova3_team/coordinator.py` | `handle_event()` is the production entry point. A claim of an unclaimed task requires a currently verified owner/admin approval bound to the immutable task revision; a release must revalidate the approval recorded in the lease. Denials mutate nothing and never spend AI tokens. Expired/legacy claims are quarantined in `nova:attention`, not silently requeued. |
| `nova3_team/worker.py` | The queue is filtered through the same gate **before sorting and before any provider launch**; approval is revalidated immediately before push/PR. Malformed `Priority:` quarantines only that task. Deliberate exit codes are reported with their original cause. |
| `tools/observatory/operator_ops.py` | `OWNER_ROLES = {ADMIN}`; MAINTAIN/WRITE/TRIAGE/READ never steer. Organization owners are verified through a live membership read. Merge execution rechecks authorization after the preview/wait, immediately before `gh pr merge`. |

No service units, workflows or system settings are edited by this change.

## Pinned deployment identity (required, fail closed)

Name-only configuration would let a deleted/reused repository name inherit a
stale approval. The coordinator and worker refuse to start their queue loops
until both numeric IDs are pinned in the reviewed workflow/environment:

```sh
NOVA3_REPOSITORY_ID=123456789   # GET /repos/{owner}/{repo} -> .id
NOVA3_OWNER_ID=98765432         # .owner.id (organization or user numeric id)
```

Missing/invalid values make `build_gate()` raise; the coordinator exits `6` and
the worker exits `6` with the cause recorded (see exit codes below). There is no
fallback to an ungated queue and no environment-supplied role.

### Where these are configured

IDs are public repository metadata, not secrets; do not store them as secrets.

* **GitHub Actions coordinator** (`.github/workflows/nova-coordinator.yml`):
  consumed from reviewed repository variables, which must be set once by an
  owner/admin:

  ```sh
  gh variable set NOVA3_REPOSITORY_ID --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction --body 1433565792
  gh variable set NOVA3_OWNER_ID      --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction --body 347984779
  ```

  `1433565792` is `GET /repos/... .id`; `347984779` is `.owner.id` for the
  organization. The workflow passes both to the coordinator process.
* **Local/systemd worker**: put the two exports in the worker's reviewed
  environment (`~/.config/nova3-worker.env` or the systemd unit's
  `Environment=`/`EnvironmentFile=`):

  ```sh
  NOVA3_REPOSITORY_ID=1433565792
  NOVA3_OWNER_ID=347984779
  ```

  Then restart the worker. Verify the pinned identity matches live GitHub
  before enabling a queue:

  ```sh
  gh api repos/N-O-V-A-3-RE-Contributors/nova3-reconstruction --jq '.id, .owner.id'
  ```

### Startup validation before queue activation

`build_gate()` now performs `QueueApprovalGate.preflight()`: it reads the
authenticated identity and the live repository payload and compares the numeric
repository and owner IDs with the pinned values. It runs **before** any label,
claim, lease or provider action. A mismatch, unauthenticated credential or API
failure raises `EvidenceUnavailable`; coordinator and worker exit `6` without
touching the queue. Preflight is repeated in-process for the values used later
by `verify`/`revalidate`, so the queue is never activated against a name-reused
repository.

## Approval record

The approval is an ordinary GitHub issue comment written by a currently
verified repository ADMIN or an active organization owner. It is a strict JSON
object (exactly the version-3 binding from `docs/progress/CONTROL_AUTHORIZATION.md`)
with `kind = "nova3.approval"`, `action = "approve_queue"`,
`executor_user_id = null` and canonical `operation_arguments`
(`{"transition":"queue"}`). It binds:

* numeric repository id, owner id, issue id and issue number;
* `task_spec_sha256` over the exact issue `id`, `number`, `title` and `body`;
* the canonical operation arguments and their SHA256;
* a unique nonce, actor numeric id/login and issuance/expiry timestamps
  (not before issue creation, at most one hour valid).

Issue `updated_at` is deliberately **not** bound (protocol v3). GitHub moves it
for comments, labels and assignments, so binding it made any coordinator lease,
review comment or label change revoke a fresh approval. Comments and metadata no
longer invalidate approval; any title/body edit does. The coordinator still
digests the lease-stripped task text separately, so a lease cannot smuggle new
instructions into an approved body.

The **locator** tells the coordinator which comment to fetch. It is not
authorization. Exactly one of these must be present; zero or several conflict:

* label `nova:approved:<comment_id>`, or
* body marker `<!-- NOVA3_APPROVAL_COMMENT: <comment_id> -->`.

A MAINTAIN can add, remove or repoint the locator, but cannot author the owner
comment. Removing it denies; pointing it at another owner approval of the same
revision is equivalent authorization. The comment itself is re-fetched and its
author's current role is re-read on every transition; a deleted/edited comment
or a revoked ADMIN/owner role denies.

One approval admits exactly one **task revision**. It is deliberately not bound
to a worker account, so any permitted contributor may claim that revision. A
released task keeps its approval only while the task text is unchanged; any body
edit requires a fresh owner approval.

## Coordinator behavior

* `claim` on an unclaimed task: full `policy.verify_owner_approval()` with fresh
  evidence (all receipts ≤30 s old). Denial logs a fixed reason and writes
  nothing.
* `claim` while an active lease exists: no transition, so the existing lease
  idempotency handles duplicate/replayed claims; a different actor cannot steal
  the lease.
* `release`: `policy.verify_approval_record()` against the binding captured in
  the lease, the approval comment, the author's live role and a lease-time
  digest of the task text (lease envelope removed). Denial quarantines the task
  in `nova:attention` with one actionable diagnostic instead of requeueing it.
* `sweep`: an expired claim is requeued only if the recorded approval still
  revalidates; legacy claims without approval metadata are quarantined.
* `heartbeat`, `submitted`, `block`: no approval is re-requested; they cannot
  grant new work. A quarantine never marks a task complete.

## Worker behavior

* Before sorting/selection: malformed `Priority:` is quarantined per task; the
  remaining queue passes `admit_queue()` which calls the gate and records the
  approval metadata. With no gate, nothing is admitted.
* Immediately before push/PR: `revalidate_before_publish()` uses fresh evidence
  and the lease binding. A denial blocks the task (circuit breaker) and does not
  push or open a PR.
* There is **no** legacy publishing exception. A lease with no approval metadata
  is unapproved work: it is quarantined for fresh owner/admin approval and never
  pushed. Re-approval is the migration path; the gate cannot be disabled to make
  a worker run.

### Priority schema

Exactly one line, ASCII decimal integer only:

```
Priority: 50
```

* optional spaces/tabs after the colon, value `0..999`, no other characters;
* missing field → documented default `50`;
* present but empty, wrapped, duplicated, signed, fractional, suffixed with
  descriptive text/Unicode punctuation, out of range or line-injected →
  quarantined (`non_numeric_or_suffixed`, `empty_or_wrapped`, `duplicate`,
  `out_of_range`). The task is skipped with one diagnostic; the queue continues
  and no AI tokens are spent.

## Exit codes and systemd recovery (documentation only)

Deliberate worker exits now write `~/.cache/nova3-team/<owner_repo>/worker-failure.json`
with `exit_code` and the redacted original `cause`, and print
`WORKER EXIT <code>: <cause>`:

| code | meaning |
| --- | --- |
| 3 | three consecutive task failures |
| 4 | three task claims were not granted |
| 5 | repeated coordinator/API failures |
| 6 | owner/admin approval gate unavailable or repository identity unpinned |

A systemd `start-limit-hit` only means the unit restarted too often; it hides the
worker's own cause. Diagnose and reset **after fixing the cause**:

```sh
systemctl --user status nova3-worker.service
systemctl --user show -p Result -p ExecMainStatus nova3-worker.service
journalctl --user -u nova3-worker.service -n 120 --no-pager
cat ~/.cache/nova3-team/*/worker-failure.json

# Only after the reported cause is resolved:
systemctl --user reset-failed nova3-worker.service
systemctl --user restart nova3-worker.service
```

Do not reset the failure counter to "get past" an unresolved deliberate exit;
the worker will stop again by design. Do not edit active unit files for this.

## Tests (offline only)

```sh
python3 -B -m unittest nova3_team.tests.test_access_policy -q   # 47
python3 -B -m unittest nova3_team.tests.test_coordinator -q     # 15
python3 -B -m unittest nova3_team.tests.test_worker -q          # 9
python3 -B -m unittest discover -s nova3_team/tests -q          # 601, 6 skipped
python3 -B -m unittest discover -s tools/observatory/tests -q   # 154
```

Coverage includes fork/wrong-repo approvals, duplicate/concurrent claims,
tampered issue text, changed task SHA/nonce/action, spoofed comment authors and
locators, MAINTAIN/WRITE/TRIAGE/READ denial, revoked roles, GitHub failures,
permission mismatch, existing active leases, legacy-lease migration, malformed
priority values (suffix/Unicode/empty/fraction/negative/huge/duplicate/injected/
wrapped), pre-launch filtering, pre-publish revalidation and exit-cause
reporting. Every GitHub payload is synthetic; nothing is pushed, merged or
launched.

## Residual risks and required follow-up

* **Lease storage is still issue text.** A MAINTAIN who edits the body can also
  rewrite the lease, so pre-publish revalidation is tamper-evident for task text
  and approval binding but not tamper-proof against a repository MAINTAIN. Move
  leases to a coordinator-authored state comment or external store before
  opening contributor-facing management controls. The claim-time gate is
  unaffected: it re-hashes the live issue.
* **Organization membership readability.** An organization-only owner whose
  membership endpoint is unreadable for the installation token fails closed
  (repository ADMIN still works). The credential needs `members:read` for
  org-owner parity.
* **Collection/effect gap.** GitHub reads and writes are not atomic; the
  approval can be revoked between the last fetch and the PATCH. Keep the 30 s
  evidence bound, re-fetch immediately before each transition and treat any
  ambiguous result as denied.
* **No live verification.** Reviewer must observe one denied claim, one approved
  claim, one revoked-role denial and one quarantined priority task in a test
  repository before declaring the control plane protected.

## GitHub Actions installation readers

The coordinator separates the evidence transport from the human approver.
User credentials prove `/user`; installation credentials prove access to the
exact pinned repository through authenticated, bounded
`GET /installation/repositories` pages. `/app` is not an installation-token
identity endpoint and is no longer used.

An installation reader is a typed repository/owner-scoped transport context,
with a random collector channel that contains no credential. It never becomes
a user ID, executor, contributor or approver. Standing-approval verification
still re-reads the actual human author, effective permissions, task binding,
revocation and replay evidence. Generic human-action authorization rejects
installation readers. Missing or mismatched repository evidence denies access.

`python -m nova3_team.coordinator --preflight-only` exercises authenticated
reads without processing an event, claiming a task or changing a lease. CI
runs it with its repository-scoped read-only Actions token. Diagnostics expose
only fixed endpoints, HTTP status codes, reader kind and public numeric IDs.

Protocol references:
- https://docs.github.com/en/actions/concepts/security/github_token
- https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-app-installation
- https://docs.github.com/en/rest/apps/apps#get-the-authenticated-app
