# NOVA3 — one-click AI contributor

**The source repository is PRIVATE.** Before starting, ask the maintainer for a GitHub **Read** invitation and accept it in the GitHub website or email. You must have a GitHub account.

## Download launcher

Open the private project's **Releases** page. Choose the latest `launcher-v...` release and download the file for your computer:

| System | File | What to launch |
| --- | --- | --- |
| Windows 10/11 x64 | `Nova3-Windows.zip` | Unzip it and double-click `Nova3-Windows.exe` |
| macOS Apple Silicon | `Nova3-Mac-AppleSilicon.zip` | Unzip and double-click `START.command` |
| macOS Intel | `Nova3-Mac-Intel.zip` | Unzip and double-click `START.command` |
| Linux x64 | `Nova3-Linux-x64.tar.gz` | Extract and run `START.sh` (may require allowing executable permission) |

The launcher is currently **unsigned**. Your OS may warn or block it. Don't disable global security settings. Check that the download came from the project's own GitHub Releases page. On macOS, if Gatekeeper prevents launching unsigned software, follow Apple's official 'Open Anyway' guidance only if you trust the project. On Windows, SmartScreen may warn about an unrecognised unsigned executable.

## First run

1. The launcher asks permission to install any missing **Git, GitHub CLI, Python, Node.js, CMake and Ninja** dependencies through the OS package manager. Windows uses winget, macOS uses Homebrew, Linux uses an available package manager. Some machines require installing a package manager or reopening the launcher; those are OS restrictions.
2. A GitHub browser sign-in opens. Sign into **your own account**, not the project owner's.
3. If you have not accepted the private-repository invitation, do that and restart the launcher.
4. Choose **Codex**, **Claude Code** or **Gemini CLI**. The launcher offers to install the chosen CLI into your own account and helps initiate provider authentication. A browser subscription alone does not guarantee CLI access or unattended use.
5. The repository is cloned to `~/NOVA3-Work`, and the AI worker begins requesting queued tasks.

You do not need to download GitHub source ZIPs, run Git commands or choose tasks manually. A local worker clone and Git fork are created when necessary. You do not send tokens or model access to the project maintainer. Provider rate limits and any API billing remain yours.

## Pause, stop, resume

Leave the worker window open while contributing. Press **Ctrl+C** or close the window to stop. Reopen the same launcher later. It keeps existing source changes and resumes the recorded task branch and lease when possible. Claims expire and get reassigned if a computer stays offline; the worker never deletes a dirty checkout. A session interrupted midway may lose that session's chat context, but already-written files survive.

If you have to restart or shut down the computer, that's fine. Reopen when convenient. The machine will not run jobs while powered off. The worker does not start on boot unless you set that up separately.

## What you are contributing

Actual C/C++/Java reconstruction source, tests, matching evidence and infrastructure — NOT APK patches. Original commercial game files and assets are never distributed with this repository. Some verification tasks require legally held local binaries, and without those inputs agents must mark byte-matching claims **unverified**.

## Troubleshooting

- **Can't access repository:** accept the GitHub invitation. The maintainer should grant **Read**, not Write.
- **Can't create fork:** tell the maintainer to permit private forks under GitHub organisation policy.
- **Provider CLI missing:** allow the launcher to install Node.js/npm and then the coding CLI. Restart if PATH changed.
- **AI quota reached:** stop and resume later; the local checkout is kept.
- **Need to see status:** on a development machine run `python nova3_easy.py --status`, or view GitHub issues.
- **Unsigned executable blocked:** follow your OS security prompts; never globally disable security protections or install software from an unfamiliar source.
