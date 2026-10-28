# NOVA3 Observatory 3.0 — contributor and maintainer dashboard

This is the shared, source-available replacement for the owner's private
Observatory 2.2 package. It provides a much richer **standalone monitoring
dashboard** than the worker's original embedded session display.

It does **not** silently replace a running worker terminal or restart Codex.
Run Observatory in a second terminal while the existing NOVA3 worker runs.
Quitting Observatory leaves the worker alone. The old embedded worker dashboard
remains available as a safety fallback until a future launcher integration is
verified across Linux, macOS and Windows.

## Install without administrator privileges

Download the `Nova3-Observatory.zip` asset from the project's GitHub Releases
page. Extract it; its top-level `observatory/` directory contains:

- Linux/macOS: `bash observatory/install.sh`
- Windows PowerShell: `powershell -ExecutionPolicy RemoteSigned -File observatory/install.ps1`
  (run from PowerShell as your normal user).

The installers run offline unit tests before installing only the Observatory
files into the current user's profile. They never download original game data,
install a background worker, create repository branches, change GitHub labels,
or automatically start systemd units. Windows may optionally prompt to install
the `windows-curses` Python dependency for the user's account.

Required: Python 3.10+, GitHub CLI `gh` authenticated to an account with
access to `N-O-V-A-3-RE-Contributors/nova3-reconstruction`. Linux/macOS
already include curses. Full interactive Observatory requires a real terminal.

Launch:

```sh
~/.local/bin/nova3-observer
~/.local/bin/nova3-observer --once
~/.local/bin/nova3-observer --demo
```

Windows: run `%LOCALAPPDATA%\NOVA3-Observatory\nova3-observer.cmd`.

If you're working from a repository checkout rather than a release, run
`python3 tools/observatory/observer.py --once` for a read-only snapshot.
The Observatory automatically reads the checkout path from the contributor
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
0 | Owner ops | **Locked** unless this GitHub login has ADMIN or MAINTAIN

Press `Q` to quit, `G` to refresh. Function counts or integrated PRs are never
represented as a percentage of playable game reconstruction.

The Logs page reads a systemd user service journal, not the foreground
`START-LINUX.sh` window. A foreground worker can be running normally while
this page says the service is unavailable. GitHub Issues, PRs and presence
signals still refresh automatically.

## Security: privilege checks in the operation path

Everyone with read access may view telemetry. The account running
`gh` must have effective repository **ADMIN or MAINTAIN** permission for
bulk PR merging, discovery timer operations, task-feeder triggering and owner
diagnostic exports. `WRITE` is insufficient for Observatory's bulk controls.
Both UI and backend reject unauthorized calls. Privilege is rechecked from
GitHub immediately before the action; role-query errors fail closed. Hiding
the Owner Ops page is only a visual convenience, **not** the security boundary.

GitHub still enforces branch rules, checks and actual merge permissions.
Before merging, Observatory requires an eligibility preview and an exact
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

The `nova-launchers.yml` workflow packages Observatory as a separate release
ZIP when a trusted `launcher-v*` tag is published. It coexists with existing
Windows, macOS and Linux contributor launchers; the full UI is opt-in until
the worker's embedded dashboard can be safely replaced.
