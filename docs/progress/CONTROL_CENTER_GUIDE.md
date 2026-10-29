# NOVA3 Control Center — rebrand, onboarding and truthful live status (#137)

The user-facing product is now **NOVA3 Control Center**. Module names
(`tools/observatory/observer.py`), the `nova3-observer` executable alias, the
`nova3-observatory` install/state directories and existing shortcut keys are
kept so upgrades do not break launchers, systemd drop-ins or saved state.

## Entry points

| Entry point | Status |
| --- | --- |
| `nova3-control` | New preferred executable |
| `nova3-observer` | Compatibility alias (same program) |
| `START-LINUX.sh --control` | New launcher flag |
| `START-LINUX.sh --observer` / `--observatory` | Compatibility aliases |
| `python3 tools/observatory/observer.py` | Checkout entry point, unchanged |

Installation stages `~/.local/share/nova3-control-center`, runs an offline
`--demo --once` health check, then installs both wrappers. The previous
`~/.local/share/nova3-observatory` directory and XDG state are preserved. The
installer remains no-sudo, offline-test-first and never starts a worker.

## Contributor onboarding

1. `gh auth login` locally (no token entry in the UI).
2. Confirm repository membership from GitHub metadata.
3. Open **U ACCOUNTS**, press `R` to probe local provider CLIs, `T` to test the
   selected provider and `L` for official vendor login guidance. Credentials
   never leave the vendor CLI.
4. Read only owner-approved work from **2 TASKS**; planner drafts are
   suggestions, never silently queued.
5. Submit a PR with separate evidence claims (source present, host test,
   reference comparison, byte match) and keep `UNKNOWN`/`NOT MATCHED` visible.
6. Suggestions go through issues/discussions; contributors cannot approve,
   reprioritise, merge or inspect project-owner memory.

The HELP page renders the live checklist with `OBSERVED`, `NEEDS ACTION` or
`UNKNOWN`; no offline probe is fabricated as ready.

## Administration onboarding

Only an authenticated **GitHub organization owner** or **repository ADMIN**
sees the Administration group and the `0` shortcut. `MAINTAIN`, `WRITE`,
`TRIAGE`, `READ`, revoked and unknown roles fail closed. Hiding the page is a
convenience only: every mutation rechecks authority at the operation boundary
(`operator_ops.owner_access`) and again inside the operation itself. A GitHub
API failure denies, never grants.

Administration covers planner/queue review, priorities and budgets, permitted
slot pause/resume, PR evidence review and merge after the separate #135
approval gate. No active worker is interrupted by viewing or planning.

## Four independent live signals

| Signal | Source | Never implies |
| --- | --- | --- |
| Process | `systemctl --user show` `ActiveState`, `SubState`, `MainPID`, `ActiveEnterTimestamp`, `ExecMainStatus`, `Result` | feed health |
| Lease | GitHub `nova:claimed` label/`updatedAt` + optional presence comment | progress |
| Provider log | local systemd journal byte count and last increase | useful work |
| Structured feed | sanitized `round-*.activity.json` age | process failure |

A **60-second structured-event gap** is `RUNNING` with the
`structured-event quiet` sub-badge: “No new structured event for 2m — process
still running.” A true systemd failure keeps its original exit/result and shows
a safe next step before any confirmed restart. `POSSIBLE STALL` requires no log
growth **and** a quiet feed **and** a stale claim; it remains a warning, not a
verdict.

`J` on ACTIVITY (owner/admin only) requires an explicit `Y` confirmation and
opens one validated `round-N.log` inside the protected per-repository state
directory. Traversal, absolute paths, symlinks, hardlinks, FIFOs and oversized
files are rejected; output is redacted. There is no arbitrary shell and nothing
is uploaded or published.

## Tests

```sh
python3 -B -m unittest discover -s tools/observatory/tests -q
python3 -B -m unittest discover -s nova3_team/tests -q
```

Coverage includes the 8/8-command two-minute-quiet case, true failures, expired
leases, missing logs, contributor denial of every owner action, revocation
between render and action, resolution changes, 80x24/192x54 rendering and
bounded journal redaction. No network, paid calls or real systemd mutation.

## Remaining dependencies

- #124 responsive layout and #125 Accounts page are the base (this work keeps
  both).
- #134/#135 own the shared owner/admin policy and approval gate. This branch
  consumes `operator_ops.owner_access`/`require_owner_access` as the seam; it
  does not edit `nova3_team/access_policy.py`, `coordinator.py` or `worker.py`.
- #130 OpenRouter/DeepSeek caps, the optional Hermes adapter and #131 wallet
  remain honestly `UNKNOWN` until their own PRs are merged.
- Optional release packaging stays out of scope until the UI is on `main`.
