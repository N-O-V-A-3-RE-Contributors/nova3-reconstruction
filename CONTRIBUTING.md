
<!-- NOVA3-EASY-LAUNCHER -->
## Join or start the reconstruction worker

[Contributor one-click guide](docs/ONE_CLICK_CONTRIBUTORS.md) · [Maintainer GitHub setup](docs/MAINTAINER_GITHUB_SETUP.md)

On the maintainer's Linux checkout, run `bash START-LINUX.sh` to start or resume. Windows, macOS and Linux downloadable launchers are generated through the GitHub Actions **Build contributor launchers** workflow and appear under **Releases** after the maintainer creates a launcher tag. Private repository contributors must accept an invitation before downloading.


<!-- NOVA-CONTRIBUTOR-QUICKSTART -->
## Contribute with your own AI

**[Windows, macOS and Linux: contributor quickstart](docs/START_HERE.md)**

Download the repository ZIP and run the `START-*` launcher for your operating system, or use `python3 nova3_start.py`. GitHub access and a Codex/Claude/Gemini coding CLI are required. Original game binaries and assets are not included.

# Contributor quickstart

1. Get access to the project repo and clone it. Install Python 3.10+, Git, GitHub CLI, and your preferred AI CLI.
2. Authenticate: `gh auth login`, then use `codex login`, `claude`, or `gemini` as appropriate.
3. Run: `python -m nova3_team.worker run --provider codex` (or `claude`, `gemini`).

Private repos require owner invitations and permission to fork. Do not post original game files, copyright-protected asset dumps, private credentials, or decompiler exports. If the worker reports no queued tasks it will poll. Stop it with Ctrl+C. It uses your own AI subscription, so monitor your provider's usage limits and quotas. More details in `docs/NOVA_TEAM.md`.
