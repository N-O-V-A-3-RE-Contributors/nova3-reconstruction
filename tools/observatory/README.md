# NOVA3 Control Center 3.1 — contributor and owner dashboard

This is the shared, source-available replacement for the owner's private
Observatory 2.2 package (the old product name). It provides a much richer **standalone monitoring
dashboard** than the worker's original embedded session display.

It does **not** silently replace a running worker terminal or restart Codex.
Run Control Center in a second terminal while the existing NOVA3 worker runs.
Quitting Control Center leaves the worker alone. The old embedded worker dashboard
remains available as a safety fallback until a future launcher integration is
verified across Linux, macOS and Windows.

## Install without administrator privileges

Download the `Nova3-Control-Center.zip` asset from the project's GitHub Releases
page. Extract it; its top-level `observatory/` directory contains:

- Linux/macOS: `bash observatory/install.sh`
- Windows PowerShell: `powershell -ExecutionPolicy RemoteSigned -File observatory/install.ps1`
  (run from PowerShell as your normal user).

The installers run offline unit tests before installing only the Control Center
files into the current user's profile. They never download original game data,
install a background worker, create repository branches, change GitHub labels,
or automatically start systemd units. Windows may optionally prompt to install
the `windows-curses` Python dependency for the user's account.

Required: Python 3.10+, GitHub CLI `gh` authenticated to an account with
access to `N-O-V-A-3-RE-Contributors/nova3-reconstruction`. Linux/macOS
already include curses. Full interactive Control Center requires a real terminal.

Launch:

```sh
~/.local/bin/nova3-control
~/.local/bin/nova3-control --once
~/.local/bin/nova3-control --demo
```

Windows: run `%LOCALAPPDATA%\NOVA3-Control Center\nova3-control.cmd`.

If you're working from a repository checkout rather than a release, run
`python3 tools/observatory/observer.py --once` for a read-only snapshot.
The Control Center automatically reads the checkout path from the contributor
launcher's `~/.config/nova3-team/easy.json` when available.

## Contributor dashboard

Key | Page | Data
--- | --- | ---
1 | Pulse | GitHub connectivity, worker status, queue and event summaries
2 | Tasks | Queued, claimed, review, integrated and blocked issues
3 | Pull requests | CI and merge status; owner-only bulk action
4 | Actions | Workflow runs and task-feeder health
5 | Logs | User-systemd journal if a systemd worker is installed
6 | Forecast | Current queue forecasts and clearly hypothetical full-game estimates
7 | Source map | Known vs source-present vs verified ARM functions
8 | APK atlas | Locally available APK and ELF inventory metadata
9 | Roadmap | Repository-based development milestones
W | Workers | Contributor heartbeat records if enabled by the shared project
? | Help | Navigation and role-sensitive controls
0 | Owner ops | **Locked** unless this GitHub login has organization owner or repository ADMIN

Press `Q` to quit, `G` to refresh. Function counts or integrated PRs are never
represented as a percentage of playable game reconstruction.

The Logs page reads a systemd user service journal, not the foreground
`START-LINUX.sh` window. A foreground worker can be running normally while
this page says the service is unavailable. GitHub Issues, PRs and presence
signals still refresh automatically.

## Security: privilege checks in the operation path

Everyone with read access may view telemetry. The account running
`gh` must have effective **organization-owner or repository ADMIN** authority for
bulk PR merging, discovery timer operations, task-feeder triggering and owner
diagnostic exports. `WRITE` is insufficient for Control Center's bulk controls.
Both UI and backend reject unauthorized calls. Privilege is rechecked from
GitHub immediately before the action; role-query errors fail closed. Hiding
the Owner Ops page is only a visual convenience, **not** the security boundary.

GitHub still enforces branch rules, checks and actual merge permissions.
Before merging, Control Center requires an eligibility preview and an exact
`MERGE N` confirmation, then rechecks SHA/CI/mergeability. It does not
bypass branch protection.

The ordinary `S`/`X`/`R` keys manage only the current user's **local**
worker systemd service (where installed); they never operate on another
contributor's machine. These controls are separately confirmed. On Windows or
macOS the Linux systemd controls cannot operate.

For maintainers on Linux, `bash observatory/install.sh --setup-timer`
prompts for an explicit `SETUP` confirmation and checks GitHub privileges
before installing a user discovery timer. This is opt-in, not part of normal
contributor installation.

No tokens, credentials, game ELF bytes, APKs or decompiler output are bundled
or uploaded. Repository-visible worker heartbeats are limited to worker IDs,
provider, task number, state and timestamp; a stale heartbeat does not prove
that an AI job stopped.

## Verification and release

Run:

```sh
python3 -m unittest discover -s tools/observatory/tests -q
python3 tools/observatory/observer.py --demo --once
```

The `nova-launchers.yml` workflow packages Control Center as a separate release
ZIP when a trusted `launcher-v*` tag is published. It coexists with existing
Windows, macOS and Linux contributor launchers; the full UI is opt-in until
the worker's embedded dashboard can be safely replaced.

After this feature is merged to `main`, the repository maintainer can also
request a new release without manually creating a Git tag:

```sh
gh workflow run nova-launchers.yml --ref main \
  -f publish_release=true \
  -R N-O-V-A-3-RE-Contributors/nova3-reconstruction
```

The release job rejects a non-maintainer actor and manual invocations outside
`main`. The multi-platform launcher builds and Control Center tests must pass
before new versioned assets are published; a normal workflow dispatch without
`publish_release=true` only builds CI artifacts.

## Guided onboarding and live truthfulness (issue #137)

The Control Center guides two distinct roles:

- **Contributor** — status, their own provider install/login guidance, owner-approved
  work, evidence/PR submission and read-only suggestions. The Administration page is
  absent from navigation; the `0` shortcut fails with a concise reason.
- **Administration** — only an authenticated GitHub organization owner or repository
  ADMIN. MAINTAIN/WRITE/TRIAGE/READ never receive steering rights. Every mutating
  operation rechecks authority server-side and fails closed if GitHub errors or the
  role was revoked after render.

The ACTIVITY page keeps four independent signals with provenance:

1. systemd process state (`ActiveState`/`SubState`/`MainPID`/elapsed/AI-session cap);
2. GitHub claim/lease freshness plus optional presence heartbeat;
3. provider journal byte count and time since the last increase;
4. structured-event age from sanitized `round-*.activity.json`.

A 60-second structured-event gap renders as **RUNNING** with a
`structured-event quiet` sub-badge. It is never converted to stopped, idle or
failed. True failures keep the original systemd exit/result. Press `J` on
ACTIVITY (owner/admin, explicit confirmation) for a bounded, redacted local
round-journal view; there is no arbitrary shell and nothing is uploaded.

Tests remain fully offline:

```sh
python3 -B -m unittest discover -s tools/observatory/tests -q
```

The installer stages a new `nova3-control-center` directory, runs an offline
health check, then installs `nova3-control` and the compatibility alias
`nova3-observer`. `~/.local/share/nova3-observatory` and XDG shared state are
preserved, not deleted.
