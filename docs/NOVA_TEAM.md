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
