# The NOVA3 worker loop (DeepSeek Harness)

This document describes the contributor-facing automated setup and the
unattended worker loop in `tools/dsh_loop/`. It replaces the previous
PyInstaller `launcher-v6` bundles, which packaged `nova3_easy.py` and drove the
Codex/Claude/Gemini CLIs. That workflow is still available
(`./START-LINUX.sh --legacy-workflow`) but is no longer released.

Nothing here changes what the project is: a source-only reconstruction. The
loop is a *delivery* mechanism for running an agent against the rules in
`AGENTS.md`; it does not certify anything, and its commits are ordinary
contributor commits subject to the same evidence rules.

## For contributors: three ways in

| You have | Do this |
| --- | --- |
| Git access to the repository | `git clone`, then `./START-LINUX.sh` (or `START-MAC.command`, `START-WINDOWS.cmd`) |
| Only the release page | Download your platform's launcher archive, unpack, run its `START.*` |
| Neither (no private-repo access) | Download `nova3-source.tar.gz` (or `.zip`) from the release, extract, run `install.sh` / `install.cmd` |

Every route ends in the same place: a `nova3-dsh` command and a per-user
installation. The first run asks one question that cannot be defaulted --
your own model provider API key.

### You need

* A 64-bit Linux, macOS (Intel or Apple silicon) or Windows 10/11 machine.
* Python 3.10+ (Linux/macOS normally ship it; Windows: python.org).
* Node.js is **optional**: the installer uses your Node if it is current
  enough, otherwise it unpacks a checksum-verified private Node 22 into the
  install root.
* Your own DeepSeek (or OpenRouter) API key. Usage is billed to your account.
* About 1 GiB of disk. Original game files are **not** part of any download.

### What setup does, in order

`tools/dsh_loop/installer.py` runs these steps idempotently; re-running repairs
a broken step and leaves working ones alone.

1. Refuses to run as root; checks Python; reports free RAM/disk.
2. `git` present (installs it via the OS package manager with consent).
3. Node: reuse system Node >= 22, else download from `nodejs.org` to the
   install root and **verify the published SHA-256** before unpacking
   (archive extraction refuses traversal).
4. Pinned DeepSeek Harness runtime in `<install>/runtime` via npm.
5. Worker profile `nova3-worker` under `<install>/dsh-home`, composed only of
   `@deepseek-ai/dsh-base` + `@deepseek-ai/dsh-headless` with a user patch
   layer generated from `tools/dsh_loop/profile/`:
   * your model route (base URL, model id, credential *reference name*);
   * agent default model and session-title model pinned to that route;
   * approvals off, sandbox `danger-full-access`, presets defined;
   * DeepSeek web-search plugins disabled (no second provider key needed),
     HTTP fetch kept;
   * `tools` mode `native` (the experimental PTC mode is opt-in via
     `DSH_TOOLS_MODE`).
6. **Self-check**: `dsh --profile nova3-worker --dump-config` must compose and
   contain your model, credential reference and permission policy.
7. API key: if `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` is set it is reported
   as a masked fingerprint. Otherwise the wizard offers a hidden-input
   verification (one `/chat/completions` request) and explains how to keep the
   variable in your shell profile; the installer may append an empty
   `export NAME="<paste your key here>"` placeholder, never a real key.
8. Console launchers (`~/.local/bin/nova3-dsh`, `%LOCALAPPDATA%\Nova3\bin\
   nova3-dsh.cmd` + Start-menu shortcut) and a JSON settings file
   (`~/.config/nova3/dsh-loop.json`, `%APPDATA%\Nova3\dsh-loop.json`).

### Where things go

| Item | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Install root | `~/.local/share/nova3/dsh` | `~/Library/Application Support/Nova3/dsh` | `%LOCALAPPDATA%\Nova3\dsh` |
| Settings (no secrets) | `~/.config/nova3/dsh-loop.json` | same | `%APPDATA%\Nova3\dsh-loop.json` |
| Launcher | `~/.local/bin/nova3-dsh` | `~/.local/bin/nova3-dsh` | `%LOCALAPPDATA%\Nova3\bin\nova3-dsh.cmd` |

Override with `NOVA3_DSH_CONFIG`, `NOVA3_DSH_INSTALL`, or the `--config` /
`--install-root` flags. Uninstall = delete the install root, the settings
file, the launcher, and the profile line mentioning your key variable.

## How the key is handled (and what is not)

* The key is **never** written by the loop, the installer, the launcher, the
  config JSON, git, or the pass log. The pass log masks key-shaped strings.
* Verification keeps it in memory only.
* DSH reads it through a credential reference (`apiKeyEnv: DEEPSEEK_API_KEY`)
  from the process environment. That is the standard mechanism; the value lives
  wherever you put it (shell profile, `setx`, your own secret manager
  exporting it before you start `nova3-dsh`).
* The config stores the variable *name* and a sha256-prefix fingerprint so you
  can confirm which key is active (`nova3-dsh status`).
* Nothing is uploaded anywhere by the installer. The loop only talks to the
  model endpoint you configured. An optional remote status heartbeat
  (`NOVA3_LOOP_HEARTBEAT=https://...`) is off by default.

## The pass loop

```
nova3-dsh                       # setup (first run) then loop
nova3-dsh run                   # exactly one pass
nova3-dsh loop --max-minutes 180
nova3-dsh status | logs | stop | resume | doctor | update
```

Each pass is one `dsh --profile nova3-worker headless --json` process:

1. The prompt (`tools/dsh_loop/prompt.py`) lists the files to read first
   (`AGENTS.md`, `docs/NOVA_TEAM.md`, STATUS/NEXT/HANDOFF, BLOCKERS, ...),
   the current git state, the last two pass outcomes, the backlog, and the
   stop rules. The first pass seeds `.local/dsh-loop/BACKLOG.md`.
2. The model works in the checkout, runs `bash tools/check_all.sh`, commits
   small verified changes, and updates STATUS/NEXT/HANDOFF/SESSION_LOG plus
   the backlog. The loop never pushes or merges.
3. The loop independently records whether anything changed (git status digest
   and HEAD), token usage, elapsed time and the turn outcome in
   `.local/dsh-loop/passes.jsonl`, and if the model changed work without
   committing, commits a clearly-labelled checkpoint.

Stop conditions (all durably recorded):

| Condition | Why | Remedy |
| --- | --- | --- |
| Credential rejected (`AUTH`/`MISSING_CREDENTIAL`) | no pass can succeed | fix the key, relaunch |
| 3 consecutive failing passes with no change | the pass is broken, not the task | read `.local/dsh-loop/logs/` |
| 3 consecutive no-progress passes | the model is stuck on one task | edit the backlog / guidance, relaunch |
| Provider quota / rate limit | wait, don't burn attempts | automatic 15-minute cooldown |
| `--max-passes` / `--max-minutes` | explicit budget | relaunch |
| STOP file / SIGINT | human request | graceful, after the current pass |
| < 350 MiB RAM available | this host has 4 cores/8 GiB | the pass runs in conservative mode (prompt asks for light tools only) |

Guidance for the model without editing code: append to
`.local/dsh-loop/prompt-overrides.md`; it is injected into every pass.

## For maintainers: releasing

* `.github/workflows/nova-launchers.yml` runs the loop tests on Linux, macOS
  and Windows, builds PyInstaller launchers for each platform, builds the
  source-only archive with `tools/dsh_loop/packager.py`, and refuses any
  archive containing `.apk/.obb/.so/.dex/.rar/.elf` files.
* Publish by pushing a `launcher-v<version>` tag (or Actions → run workflow with
  `publish_release`). Assets: `Nova3-Linux-x64.tar.gz`,
  `Nova3-Mac-AppleSilicon.zip`, `Nova3-Mac-Intel.zip`, `Nova3-Windows.zip`,
  `Nova3-Source` (`nova3-source.tar.gz`, `.zip`, `manifest.json`).
* The archive is built from `git ls-files`, so untracked private analysis,
  reference binaries and caches can never be shipped.
* Bump the pin in `tools/dsh_loop/installer.py` (`DSH_PINNED_VERSION`,
  `NODE_PINNED_MAJOR`) deliberately, after re-running the installer against the
  new version (`--dsh-version`) and the profile self-check.

## Tests

```sh
python -m unittest discover -s tools/dsh_loop/tests -t . -v   # 29 tests, no network
```

They cover platform archive names and command construction (including the
Windows `npm.cmd` path), credential masking/fingerprinting, profile rendering
and idempotence, archive traversal refusal, launcher content, loop state and
prompt contracts, and the distribution README. The loop itself is additionally
exercised by a fake-runner test locally; CI runs the unit suite on all three
operating systems.

## Relationship to the rest of the project

* `nova3_easy.py`, `tools/observatory/`, `nova3_team/worker.py` and the
  coordinator workflows are unchanged and still work (`--legacy-workflow`).
* `AGENTS.md` and `docs/NOVA_TEAM.md` remain the governing rules for what the
  worker may do and what counts as evidence. The loop's permissions are
  deliberately wide (no approvals, full file access) because it runs
  unattended on the contributor's own machine as their own user; it never
  grants root and the prompt forbids system changes.
