# Contributor quickstart

1. Get access to the project repo and clone it. Install Python 3.10+, Git, GitHub CLI, and your preferred AI CLI.
2. Authenticate: `gh auth login`, then use `codex login`, `claude`, or `gemini` as appropriate.
3. Run: `python -m nova3_team.worker run --provider codex` (or `claude`, `gemini`).

Private repos require owner invitations and permission to fork. Do not post original game files, copyright-protected asset dumps, private credentials, or decompiler exports. If the worker reports no queued tasks it will poll. Stop it with Ctrl+C. It uses your own AI subscription, so monitor your provider's usage limits and quotas. More details in `docs/NOVA_TEAM.md`.
