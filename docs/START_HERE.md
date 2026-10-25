# Start contributing to the N.O.V.A. 3 source reconstruction

**Repository:** https://github.com/crjase/nova3-reconstruction

Each contributor uses **their own** GitHub account and one of **Codex CLI, Claude Code, or Gemini CLI**. The ordinary ChatGPT/Claude/Gemini website chat is not a substitute for the corresponding coding CLI. No one shares AI account credentials. A VPS is not required.

## First-time setup: choose your operating system

**Get repository access first.** This repository starts private. Ask the maintainer for an invitation and accept it. On the GitHub page, select **Code → Download ZIP**, extract the archive, and open its folder. Or, if `gh` is already installed, run `gh repo clone crjase/nova3-reconstruction`.

| Platform | Start | Initial prerequisites if asked |
| --- | --- | --- |
| **Windows** | Double-click `START-WINDOWS.cmd` | Install Python, Git, GitHub CLI, CMake and Ninja using `winget` as prompted. Install Visual Studio C++ Build Tools for C++ jobs. Open the launcher again after installing. |
| **macOS** | Double-click `START-MAC.command` or run `bash START-MAC.command` | With Homebrew: `brew install git gh python cmake ninja node`; `xcode-select --install` for C++ tools. |
| **Linux** | Run `bash START-LINUX.sh` | Arch: `sudo pacman -S --needed git github-cli python cmake ninja gcc nodejs npm`. Debian/Ubuntu: `sudo apt update && sudo apt install -y git gh python3 cmake ninja-build build-essential nodejs npm`. |

The first launch will:
1. Check Git, GitHub CLI and repository permissions; guide you through GitHub login if needed.
2. Check your Git commit identity, offering a GitHub private/noreply email if it is not set.
3. Ask which AI CLI you use (Codex / Claude / Gemini), optionally install it with npm, and guide provider sign-in.
4. Start the continuous worker, which claims GitHub tasks, uses bounded AI sessions, runs checks and opens pull requests.

**Next time:** launch the same `START-*` file again. The provider choice is remembered. Keep the terminal open and computer awake while contributing. **Ctrl+C** stops it. It may consume subscription limits or billable API usage.

## Commands for anyone who prefers the terminal

```bash
python3 nova3_start.py                  # menu first time, remembered thereafter
python3 nova3_start.py --choose         # change provider
python3 nova3_start.py --once           # just one task attempt
python3 nova3_start.py --status         # queue status without AI
```

On Windows, use `py -3` instead of `python3` if needed.

## Known limitations

- **Private-fork permissions:** GitHub may prevent private forks or PRs, particularly from a personal-account private repository. GitHub repository access alone does not guarantee the automated PR flow. If you hit `fork` or `pull request` permission errors, report them to the maintainer; do not try to work around access controls. A GitHub organisation with appropriately configured fork permissions is usually a better choice for a larger team.
- **Reference binaries:** Original APKs, OBBs, native libraries, copyrighted assets and large decompiler dumps are *not* included. Contributors need their own lawfully acquired local reference files for original machine-code matching. Infrastructure and source-only testing tasks do not require them.
- **Verification:** A successful build or test does not prove source is machine-code matched. PRs must label matching evidence correctly. No auto-merge is enabled.
- **Provider subscriptions:** Headless coding CLI access and usage limits depend on each provider/account. Provider availability does not guarantee unattended operation.
- **OS and compilers:** C++ reconstruction tests can require native compilers and extra dependencies. Windows-specific toolchain compatibility is not yet validated end-to-end.

Official provider installation references: [Codex](https://github.com/openai/codex), [Claude Code](https://code.claude.com/docs/en/quickstart), [Gemini CLI](https://geminicli.com/docs/get-started/installation/).
