# Provider-neutral planning bridge (#132)

Status: **IMPLEMENTED / TESTED offline**. Live `publish-draft` and
`approve-queue` remain gated on #134 and #135 being merged and independently
reviewed. Until then they are dry-run-first and fail closed.

## Why

Planning must not live in one proprietary chat session. Any planner — Hermes,
OpenCode Plan, Codex, Claude, Gemini, or a human — emits the same validated
JSON proposal. GitHub issues remain the durable source of truth, and the
existing coordinator/worker claim path is unchanged: workers only ever claim
`nova:queued` issues through the coordinator. The bridge never claims, never
merges and never starts a model.

## Pipeline

```text
AI / human -> proposal.json -> validate -> preview -> publish-draft (non-queued)
                                                          |
                            owner/admin approval comment <-+
                                                          |
                                        approve-queue (access policy + #135 gate)
                                                          |
                                                   nova:queued -> coordinator
```

Preview is the default. `publish-draft` creates an issue only with `--apply`,
and it is labelled `nova:draft` (plus `nova:owner-review` when required) —
never `nova:queued`.

## Proposal schema (version 1)

| Field | Meaning |
| --- | --- |
| `key` | lowercase slug, stable identity for humans |
| `objective`, `title`, `body` | task text, bounded and marker-checked |
| `allowed_paths` | repository-relative scope; private/generated trees rejected |
| `prerequisites` | issue numbers; cycles rejected |
| `evidence`, `acceptance`, `test_commands` | evidence refs, criteria, argv-only commands |
| `priority` | integer `0..100` |
| `risk`, `privacy`, `requires_owner_review` | classification; high risk forces review |
| `source_standard` | `compiler_abi`, `tu_object_first`, `original_match`, `evidence` |
| `estimate` | integer minor-unit cost, runtime minutes, confidence |

`fingerprint = sha256(canonical JSON of the validated spec)`. Publishing is
idempotent on the fingerprint (embedded as `<!-- NOVA3_PLAN_FINGERPRINT: … -->`)
and on the exact title, so two planners produce one issue.

Epistemic rules are enforced, not documented only:

* `original_match: verified_byte_match` **requires** non-empty reproducible
  evidence; otherwise validation fails. No fabricated equivalence claims.
* Unknown extra keys are rejected, so a proposal cannot smuggle `auto_approve`,
  `yolo` or `skip_review` fields.
* Text containing reserved authorization markers (`nova3.approval`,
  `NOVA3_LEASE_START`, `NOVA3_APPROVED`) is rejected — task text can never act
  as an approval record.
* Embedded credentials are rejected on every text field.

## Commands

```bash
python3 -m nova3_team.planning_bridge validate proposal.json
python3 -m nova3_team.planning_bridge preview proposal.json
python3 -m nova3_team.planning_bridge publish-draft proposal.json \
    --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction          # dry run
python3 -m nova3_team.planning_bridge publish-draft proposal.json \
    --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction --apply
python3 -m nova3_team.planning_bridge context \
    --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction --out snapshot.json
python3 -m nova3_team.planning_bridge approval-request \
    --repo … --issue 135 --task-spec-sha256 <sha> --nonce <64-hex>
python3 -m nova3_team.planning_bridge approve-queue \
    --repo … --issue 135 --task-spec-sha256 <sha> --nonce <64-hex> \
    --state-dir ~/.local/state/nova3/replay --apply
```

Exit codes: `0` success, `2` validation/IO/GitHub error, `3` authorization
denied. Diagnostics are fixed strings; argv, prompts, vendor stderr and tokens
are never echoed.

## Authorization binding

`approval-request` prints the exact JSON an owner/admin must post as a comment.
`approve-queue` then:

1. collects fresh `/user`, repository, issue, comment and permission reads at
   the action boundary (no cached label, env var, TUI role or model text);
2. asks `nova3_team.access_policy` (PR #142) for a decision with
   `Action.APPROVE_QUEUE`;
3. requires exactly one owner-written comment whose JSON equals the binding —
   the issue number, repository id, task-spec SHA256, operation arguments,
   nonce, actor id and expiry are all bound;
4. requires explicit bound human consent for the exact displayed operation;
5. atomically reserves the returned `consume_keys` (O_EXCL, cross-process)
   before applying anything, so two planners racing cannot both succeed;
6. applies `nova:queued` **additively** and idempotently.

An edited issue body changes `updated_at` and the task digest, invalidating any
prior approval. MAINTAIN/WRITE/TRIAGE/READ never confer approval.

## Context export

`context` writes a versioned, sanitised snapshot: issue/PR numbers, titles,
states, labels, prerequisite references, blockers, and scalar metrics. It
excludes binaries, assets, decompiler output, credentials, absolute local
paths and private trees; `assert_sanitized` fails the command if any leak
through. This snapshot is safe to pass to any planner.

## Verified vs unverified

Verified locally (offline, no network, no paid calls):

* 47 bridge tests plus the full `nova3_team` suite (591 tests) pass.
* Schema, fingerprint determinism, cycle/overlap detection, idempotent publish,
  dry-run default, argv-only test commands, secret/marker rejection.
* Authorization paths: ADMIN allowed; MAINTAIN/WRITE/TRIAGE/READ denied;
  plain-prose "approved" ignored; forged contributor approval denied; edited
  issue, wrong repo, expired approval, no consent and replay all denied;
  denied transitions reserve nothing; GitHub failures surface as clean errors.

Not verified / deliberately out of scope:

* No live GitHub writes were performed by tests; `gh` transport is exercised
  only through injected fakes.
* The real `GhTransport` code path (argv construction, `gh api` behaviour,
  pagination beyond one page) has not been run against the live API.
* `approve-queue --apply` must not be used until #134 and #135 are merged; the
  queue gate (`PolicyQueueGate`) is written against the #134 API and expects
  the #135 worker/coordinator integration to consume the same decisions.
* No Observatory page is added here (tracked separately after #124/#125).

## Dependencies

* `nova3_team/access_policy.py` from #134 (PR #142) — required import.
* #135 queue enforcement supplies the coordinator/worker side of the same gate.
* #136 Hermes backend consumes the sanitised context snapshot; it must not
  auto-publish or auto-queue anything.
