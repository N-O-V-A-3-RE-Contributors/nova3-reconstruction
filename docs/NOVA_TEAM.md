# NOVA reconstruction — distributed worker guide

This repository is an experimental source reconstruction, not a patched APK.
Original APK/OBB/ELF binaries and decompiler output stay on the contributor's local machine. Do not upload game content to GitHub, Git LFS, issues, pull requests or logs.

## For contributors (Windows / macOS / Linux)

Install Git, Python 3.10+, GitHub CLI (`gh`), CMake/Ninja, and one of `codex`, `claude`, or `gemini` CLI.
Authenticate with `gh auth login` and log in to the AI CLI through its own normal login command. An account with access to this GitHub repository is required; private-repository fork permissions depend on GitHub settings.

From a clone of this repository:

```bash
python -m nova3_team.worker status
python -m nova3_team.worker run --provider codex
# or: --provider claude / --provider gemini
```

Use `--once` for a single task. Local clones and work attempts live in `~/.cache/nova3-team`, separate from this repository. Keep the computer awake for continuous execution. If all tasks are claimed, the worker polls until new work arrives.

### Provider timeouts, private logs and quarantine

The worker now streams AI stdout/stderr directly to private, permission-limited
`~/.cache/nova3-team/OWNER_REPO/.nova-private/<issue-checkout>/round-N.log`
while the session runs. Journal messages include elapsed seconds and local
log byte counts, **never the AI's raw output or original game bytes**.
Inspect these files locally after a stalled attempt; do not paste unredacted
credentials, decompiler dumps or proprietary instruction excerpts into GitHub.

`NOVA3_AI_SESSION_TIMEOUT` controls the single-session time budget. The
default is **1,200 seconds (20 minutes)**, and the accepted range is 300–1,800
seconds. A timed-out provider is now `PROVIDER_TIMEOUT` rather than an
unexplained `NO_SOURCE_PROGRESS`. It immediately quarantines the task,
preserving its checkout, local changes and stream logs, instead of launching
two more identical sessions. Re-scope the issue and fix the cause before a
maintainer requeues it. The old local retry cooldown can be cleared with
`python -m nova3_team.reliability --repo OWNER/REPO --reset-issue N`
**only after** fixing and requeueing the task; do not erase the saved checkout
or blindly bypass the circuit breaker.

The historic issue #82 failed with **three 30-minute session timeouts** before
these safeguards existed. Its prior `NO_SOURCE_PROGRESS` classification
describes missing source edits, not proof that compilation or decompilation
was impossible. The maintainer intentionally moved it to `nova:blocked`
pending a smaller task and fixed worker. See
[`docs/DISCORD_IMPLEMENTATION.md`](DISCORD_IMPLEMENTATION.md) for the local
Cyrus donor and compiler-aware revalidation sequence.

## Coordination

GitHub issue labels: `nova:queued`, `nova:claimed`, `nova:review`.
Workers request a claim by commenting `/nova claim <uuid>` on an issue. The GitHub Actions coordinator serializes these requests and stores one expiring lease inside the issue body. It renews or releases leases on worker comments and sweeps expired leases every 15 minutes. Only the coordinator writes leases. Private fork issues and PRs may need membership/invitations.

The initial tasks audit independently buildable code and develop automation infrastructure. The subsequent wave must be generated from the actual ARM function ledger, not guessed from an `.so` file's byte offsets.

## Verification grades

1. `unmapped`: unknown function or incomplete discovery.
2. `hypothesis`: human/AI interpretation with no executable proof.
3. `tested`: CMake/CTest or fixture verification only.
4. `behavioural`: independent oracle on legal local data.
5. `matching`: exact target machine code with reproducible compiler and flags evidence.

No AI may upgrade a verification grade based on its own assertion. Separate compatibility source from modernised portable source once faithful behaviour is established.

## Boundaries

CI runs on untrusted pull requests without secrets or write permissions. The coordinator handles issue comments, not PR code, and has `issues:write` only. The repository does NOT auto-merge, auto-release code, distribute assets, or grant shared AI credentials.

## Running overnight

Use a terminal or tmux on Linux. Workers run fresh finite model sessions, save local handoffs, recover from failure and open a PR on a changed task. AI providers can throttle, expire sessions, request renewed login or refuse unattended permissions; the supervisor cannot bypass provider limits. Automatic PR merging is deliberately disabled until reliable source matching is proven.
