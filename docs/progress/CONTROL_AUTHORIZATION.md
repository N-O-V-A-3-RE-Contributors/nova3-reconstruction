# Control Center authorization foundation — task #134

Implemented as an independent, standard-library-only policy in
`nova3_team/access_policy.py`. Offline synthetic tests live in
`nova3_team/tests/test_access_policy.py`. No coordinator, worker, operator,
observer, workflow or repository settings were changed.

**The existing GitHub queue and contributor-facing control plane are NOT secured
by this module.** Coordinator and worker adoption, trusted API collection,
protected review, authenticated confirmation and durable atomic replay storage
are mandatory integration work. Public/contributor onboarding must remain
blocked until every privileged execution path enforces these boundaries.
UI hiding and a role badge do not enforce authorization.

## Authority and action contract

Roles are derived from current GitHub API evidence; callers cannot select a
role. `owner` includes effective repository ADMIN and verified active owners of
the organization owning this exact repository. There is no username allowlist:
crjase and any other currently authorized ADMIN/organization owner qualify by
permission, never by handle alone. MAINTAIN, WRITE and TRIAGE are contributors;
READ is an observer. Unknown/missing permissions grant no mutation authority.

| Action | Boundary requirements |
| --- | --- |
| `read_status` | Fresh verified repository access. Anonymous access is limited to an explicitly anonymous API read of a public repository. Never expose private cached status after an authentication failure. |
| `suggest_improvement` | Authenticated repository access; outputs a proposal or inert comment only. No task creation, queue/label changes, automatic planning or command execution. |
| `run_assigned_work` | Contributor or owner; exact current owner/admin approval; authenticated coordinator App assignment to this viewer; explicit local consent; atomic one-use consumption. Only the worker's own permitted provider/billing. |
| `create_project_task`, `publish_planner_issue`, `approve_queue`, `change_priority` | Current organization owner/repository ADMIN, exact approval and explicit human confirmation. Includes generating actionable project tasks, queueing, assigning and reprioritizing. |
| `manage_agents`, `set_wallet` | Same owner/admin checks and confirmation; includes global scheduling, project-paid launches and provider/budget policy. |
| `merge_pr`, `manage_members`, `change_policy` | Same owner/admin checks and confirmation; merges also require the exact reviewed PR head SHA and repository protections. Policy deployment still requires protected review. |

All steering actions require confirmation, including money and merging; this
foundation intentionally grants no unattended steering exception. A steering
caller must approve its own exact action. Contributor execution may instead
reference a different current owner's/admin's approval. Repository access is
not itself a project steering permission. An allowed suggestion must never
be fed into an automatic mutating planner without a separate steering check.

## Trust boundary: receipts are not capabilities

`authorize(request, evidence, now=..., coordinator=...)` is deterministic and
does no I/O. It reads no tokens, environment, configuration, files, subprocesses,
network or clock. Decisions use fixed reason codes and contain no input text.
API receipt payloads are excluded from dataclass representations. Do not store
tokens, response headers, raw transport errors or secrets in policy objects.

`GitHubRead`, `ReplayState`, `Consent` and the pinned `CoordinatorIdentity` are
**trusted injected attestations**, not proofs that Python can authenticate on
its own. There is no public JSON-to-trusted-evidence endpoint. An issue, comment,
prompt, environment variable, webhook payload, local role flag, cached UI label,
or model-generated “signature” must never construct these objects or set their
trust metadata. Code controlling the trusted process can fabricate objects;
this module does not sandbox arbitrary Python. Run the guard separately from
untrusted PR/task code and keep credentials outside worker/AI execution.

A future trusted transport must authenticate the actual viewer with `GET /user`
using the same credential as the subsequent reads. A service account or owner
token is not proof of the human viewer's authority. Verify TLS and the fixed
`https://api.github.com` origin; do not follow cross-origin redirects or accept
an issue-supplied API base URL. Enterprise origins are deliberately unsupported
and denied until independently configured and reviewed by a later change.
Only successful authenticated responses become receipts; network failures,
unauthenticated responses, scope ambiguity, revoked/deleted users and rate
limits must fail closed. Never promote 403/404/429 to a successful empty grant.
Do not fall back to environment or cached permissions.

Collect these REST payloads directly, without model transformation:

* `/repos/{owner}/{repo}`: numeric repository ID, repository name, private flag,
  numeric owner ID/login/type. Pin repository and owner IDs in reviewed
  deployment configuration, preventing name reuse and cross-repository replay.
* `/user`: numeric ID, login, type `User`; this is the viewer identity.
* `/repos/{owner}/{repo}/collaborators/{login}/permission`: current effective
  `permission` and matching subject `user.id/login/type`. Only `admin` grants
  repository steering. `role_name`, body text and repository UI flags cannot
  promote a user. Missing/unknown effective permissions deny.
* Optionally `/orgs/{org}/memberships/{login}` for an organization-owned
  repository: matching organization/user IDs and logins, state `active`, role
  `admin` establishes organization ownership. `member` does not. Omit this
  optional proof when not needed; a supplied failed or ambiguous proof denies.
* For the approval author, independently read `/users/{login}` and the current
  permission/membership endpoints again. An approval does not preserve a
  revoked admin role, and the comment's author login alone proves nothing.
  Approval-author receipts must use the **same authenticated reader ID** as the
  executor's own `/user` receipt; this module deliberately offers no second
  collector/service-credential trust root. If the executor's credential cannot
  read another user's permission endpoint (GitHub commonly restricts that
  endpoint to admins), the operation fails closed. In particular a plain
  contributor's `run_assigned_work` may be unvalidatable until a separately
  reviewed collector trust root exists; never fall back to trusting the
  comment author's login or an owner token. Denial here is intentional and
  tested, not a missing feature.
* `/repos/{owner}/{repo}/issues/{number}` and
  `/repos/{owner}/{repo}/issues/comments/{comment_id}`: exact current snapshots.
  Verify endpoint identity, comment numeric ID and `issue_url`, authenticated
  comment author, timestamps and current body. Deleted or edited comments deny.

Each receipt records its exact path, origin, authenticating reader's numeric
ID, executor-generated boundary nonce and trusted fetch time. Every required
read must be fresh (at most 30 seconds old, never future dated), use the same
reader and match this boundary. This is a maximum collection duration, **not
permission to cache across operations**. Re-fetch for every operation,
including worker starts, queue writes, approval/revocation writes and retries.
Discard incomplete collections. Do not share mutable payloads with untrusted
code; defensively copy/freeze transport snapshots before evaluation.

## Exact approval protocol, version 2

Every mutating action has an open ordinary tracking issue. Creation of a new
task is approved against a separate existing proposal/tracking issue; a request
cannot authorize itself merely by creating a queued issue. PR merges use a
tracking issue and bind the PR repository/number/head SHA in operation arguments.

Construct a `Request` with the exact action, pinned repository, issue number,
authenticated executor's numeric ID, a unique cryptographically random 256-bit
nonce from the trusted executor, and the canonical `operation_arguments` JSON
object plus `operation_digest(arguments)`. The policy recomputes the digest from
the supplied arguments and denies on any mismatch, so a digest alone can never
authorize an operation: the executor cannot approve one argument set and then
perform another.
The full action-specific argument schema must be validated by its executor:
include assignee/provider, payer, currency and integer minor-unit price/limit,
new priority, target member, policy revision, or PR head SHA as appropriate.
Never omit an argument and later fill it from a prompt or mutable default.
Use exactly the arguments hashed and shown to the human when executing.
The digest helper allows exact JSON objects/lists/scalars, sorted object keys,
ASCII JSON escapes and compact separators; it rejects floats, NaN, non-string
keys and custom objects. No whitespace or Unicode normalization is performed.

`request_binding(request, issue)` constructs this exact record portion:

* `version` = 2 and `origin` = `https://api.github.com`;
* numeric `repository_id`, `owner_id`, `issue_id`, `issue_number`;
* `task_spec_sha256`: canonical SHA256 of exact issue `id`, `number`, `title`,
  `body`, `updated_at` (revision included, stable for the same API snapshot);
* `action`, `operation_sha256`, `operation_arguments`, `nonce`,
  `executor_user_id`.

`operation_arguments` is a detached canonical copy of the exact JSON argument
payload. Approval and assignment records therefore bind the precise argument
values, not only an opaque hash; comparison is canonical-JSON equality, so a
JSON boolean can never equal an integer at any nesting depth. Missing, extra or
type-mismatched fields are rejected like every other record field.

The approval is an entire, strictly parsed JSON GitHub comment, containing
exactly those fields plus `kind` = `nova3.approval`, `actor_id`, `actor_login`,
`issued_at`, `expires_at`. The actor must match the API comment author and that
author's current verified owner/admin identity. Times are UTC
`YYYY-MM-DDTHH:MM:SSZ`. Issuance must follow the task revision, precede comment
creation by no more than five minutes, and not be future dated. Expiry is
exclusive, at most one hour after issuance. Duplicate JSON keys, unknown fields,
unknown versions, appended instructions, markers and multiple candidate approval
records are rejected. The trusted executor selects one specific comment ID;
never scan arbitrary comments and take the first “approved” marker.

The API-authenticated author and the independent live permission checks provide
GitHub verification. A hash, nonce or JSON “signed” text alone grants nothing.
API timestamps do not prove a human clicked: additionally require a `Consent`
from a trusted authenticated human UI/CLI gate, bound to the SHA256 of the full
request binding, the acting user's numeric ID and a maximum five-minute window.
Show the exact operation, amount/payor or merge head before the human confirms.
Never translate an AI answer, issue sentence or ordinary comment into consent.
Local worker consent cannot permit project spending.

Task title/body changes, revision changes (including an observable edit/revert),
new issue IDs, action/argument changes, account substitution, repository
substitution and nonce reuse invalidate approval. Hashes do not attest to
semantics or safety. Issue text remains untrusted input even after approval.
Entire issue bodies are bound: the current lease-writing coordinator cannot
edit a body after approval without invalidating it. Integration must address
lease storage/re-approval explicitly; never silently exclude mutable task text.

## Coordinator assignments and revocation

For `run_assigned_work`, require both the owner's/admin's current approval of
that exact action and a separate live assignment comment. Its JSON binding is
identical, with `kind` = `nova3.assignment` and additional numeric `assignee_id`.
The API comment must come from the reviewed coordinator bot user ID **and**
`performed_via_github_app.id` must match the pinned coordinator App ID. This is
an authenticated GitHub App attestation, not a detached cryptographic signature
or a lease marker. No coordinator identity is trusted by default. An existing
unsigned lease/queued label does not meet this protocol. A later detached
signature protocol would need its own verifier and reviewed key lifecycle.
Assignment expiry/version, issue revision and operation are checked like
approval; the assignee must be the current viewer. Only the reviewed
coordinator may issue assignments, and only after the owner gate succeeds.

All mutating allows return single-use `consume_keys` for the approval comment,
repository-scoped request nonce, and (for workers) coordinator assignment
comment. `ReplayState` must come from an authoritative, durable executor store
and include consumed keys and explicitly revoked comment IDs. Reading an empty
new local store per process is not replay protection. Owner/admin revocations
must be authenticated at the same boundary; public comments cannot revoke or
restore grants. Role removal, user/comment deletion, comment edits, expiry,
policy version changes and explicit ledger revocation all deny execution.
Coordinator trust-root withdrawal must also stop assignments in the executor.

The executor must transactionally reserve **all** returned keys with unique
constraints and an authoritative current revocation check before performing any
side effect. Losing a concurrent reservation denies execution. Retain keys
across crashes/restarts and uncertain failures; reconcile rather than blindly
retry. Serialize reservation with revocation, so a stale state snapshot cannot
override a later revocation. Repeat API/permission checks immediately before
effects if collection or reservation was delayed. A saved `Decision` is not a
session capability. Never authorize from `Decision.role` without `allowed`.

GitHub reads and external side effects are not one atomic transaction. This
pure policy cannot eliminate permission/edit races after the last API read or
detect edits invisible to the API's timestamp precision. Future executors must
minimize that interval, use conditional revision/head-SHA checks where available,
cancel on changed evidence and record the exact checked revision. Do not claim
stronger edit-history or distributed atomicity guarantees from offline tests.

## Protected review and integration handoff

Before deployment, maintainers must configure protected branches/rulesets and
required independent owner/admin review covering this module, its tests, trust
roots, collectors, consent gates, replay storage and every enforcement caller.
Disallow contributor self-approval and ordinary worker edits to these paths;
contributor PR code must not run with project credentials. A `change_policy`
allow permits proposing the reviewed change, never bypassing protected review,
running PR code privileged, changing the in-process role, or auto-merging.
Repository settings were not accessed or verified in this offline task.

Integration should be a separate dependency task after the live provider/UI
work (#127/#124). Adapt coordinator/worker/operator/observer and ALL other
entry points, including non-UI paths and AI-created tasks. Collect authenticated
evidence and issue approvals through the trusted human flow; build durable
atomic consumption/revocation; enforce consent for project billing and merging;
then test denied operations produce zero effects, races/crashes cannot replay,
and revocation takes effect during an existing session. Only then assess public
control-plane readiness. Do not retrofit a local `is_owner` boolean.

## Verification and resume

Run offline, without installing dependencies or invoking existing workers:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest nova3_team.tests.test_access_policy -v
```

Policy suite: **44 policy tests passed**, exit 0. Full offline discovery suite:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s nova3_team/tests -q
```

**544 tests run, 6 skipped**, exit 0.

Tests use only
synthetic API payloads, fixed time and fake numeric IDs. Coverage includes all
steering actions/permission levels, non-owner ADMIN and organization ownership,
spoofed handles, anonymous/public/private status, maintainer denial, forged
markers/authors, prompt injection, edited tasks/approvals, exact operation,
argument and repository binding, digest/argument substitution, nested
bool-vs-int type confusion, unreadable approval-author evidence, duplicate
approvals, rate limits/unauthenticated API, revocation, expiry, explicit
consent, signed-App assignments and one-use keys.
Secret handling checks assert no filesystem/network/subprocess/environment
reads or output from decisions. No paid calls, live GitHub checks or protected
settings changes were performed. No original game inputs were accessed.

Source present and offline tests passed; live transport behavior, deployed
enforcement and race protection remain unverified. Machine-code matching is
not applicable to this new control-plane policy; no matching claim is made.
