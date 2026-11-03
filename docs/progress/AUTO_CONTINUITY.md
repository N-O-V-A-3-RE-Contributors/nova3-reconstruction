# Continuous unattended work: standing waivers and approval renewal

**The problem it fixes.** The owner gate refuses any queue approval record older
than `access_policy.MAX_APPROVAL_AGE` (one hour), by design. A one-run waiver is
clock-bound the same way. So a durable worker ran exactly one task, and within
the hour every remaining approved task was denied with
`invalid_or_unavailable_evidence`, then `owner_approval_locator_required` — the
worker stayed alive and cycled forever, approving nothing.

**What a standing waiver is.** An owner-authored comment on the holder issue
(#16) whose JSON carries `"standing": true`. It is live only while the owner's
persistent autopilot switch (`nova3_team.autopilot`) is enabled, and it pins the
exact task texts it names. Turning the switch off revokes every standing waiver
on the next tick; no GitHub edit is needed. The switch and the waiver are two
independent owner acts, and both are required.

**What it does not do.** It does not lengthen an approval record. Records stay
short-lived (1 h), the gate keeps its freshness and replay window, and every
renewal is a new comment with a new nonce bound to the *current* task text. The
waiver cannot approve a task it does not name (or, with
`"renew_autopilot": true`, one the autopilot never approved), and an edited task
text invalidates the coverage until the owner re-issues the waiver.

**Renewal loop.** `nova3-autoadmit.timer` runs every 10 minutes and calls
`python -m nova3_team.autoadmit admit --repo ... --holder 16`:

* for each covered issue, a reservation holding a live record is left alone;
* a reservation whose record has expired is replaced atomically (per-issue lock
  file, temp-file + rename) and the issue's `nova:approved:<id>` locator is
  **replaced**, not accumulated — two locators make the gate report
  `owner_approval_locator_required`, which is exactly the denial observed during
  the first live renewal;
* labels are only written when they differ, so a steady state performs no writes.

**Opting in to autopilot-approved work.** `"renew_autopilot": true` lets the
renewal cover issues recorded in the local autopilot audit log
(`~/.cache/nova3-team/<repo>/autopilot/audit.jsonl`, mode 0600, owner-owned).
The log — not a GitHub label — is the authorization, because labels are
contributor-writable. The audited `task_spec_sha256` is re-read from the live
issue, so a task edited after the autopilot approved it is refused.

**Live evidence (2026-11-03).**
* standing waiver comment `6578090965` (issues 167, 170); upgraded waiver
  `6578230028` adds `renew_autopilot`.
* `autoadmit admit` posted approvals `6578095319` (#167) and `6578096120` (#170),
  and the supervisor log shows `requested task #167; waiting for the coordinator
  lease` followed by `#167 working in …/167-b2c2db` — a real DSH session.
* issue #167 then carried two locator labels (the superseded expired record and
  the new one) and the gate refused it as ambiguous; `_queue_labels` now
  replaces the locator and the issue has exactly one.

**Revoking.** Run `python -m nova3_team.autopilot off` (or use the Mission tab).
The timer keeps running but approves nothing: `waiver_expired` for one-run
waivers and `standing_waiver_needs_autopilot` for standing ones. To revoke a
single task, edit its text (the spec digest no longer matches) or remove it from
a new waiver comment (newest comment wins).
