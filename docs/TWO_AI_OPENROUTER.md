# Join the NOVA3 reconstruction team with Hermes + OpenRouter

**Shared project:** <https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction>

**Model:** `deepseek/deepseek-v4.1-flash-1017` by default, change with `--model MODEL_ID`.

The two computers share **GitHub issues and PRs**, **not** AI conversations, source trees or credentials. A durable GitHub task claim/lease and an owner/admin approval gate are already implemented by `nova3_team.worker` and `nova3_team.coordinator`. Every agent uses a distinct checkout, branch and provider session. A completed change opens a PR for maintainer review; no auto-merge is enabled.

## Friend: setup from a clone

1. Create/sign into **your own GitHub account**. The maintainer can grant read-only repository permission, or you can fork the public repository. Your own GitHub CLI identity must be used for claims and PR submission; no owner GitHub token is shared.
2. Create a **dedicated OpenRouter API key** from <https://openrouter.ai/keys> and set a spending limit. The project owner may provide such a dedicated key initially. **Never share the owner's primary key**, put keys into Discord messages, commit them, or paste them into GitHub issues.
3. Download the repository ZIP from the `Code` menu and extract it, or clone with Git. You need **Python 3.10+** to start the installer. Run:
   - **Linux/macOS/WSL2:** `sh START-OPENROUTER.sh`
   - **Windows:** double-click `START-OPENROUTER-WINDOWS.cmd`. **Native Windows Hermes worker execution has not been validated**; WSL2 Ubuntu is the recommended reliable path on a Windows computer.
4. The installer offers to install Git, GitHub CLI, Python, Node, CMake, Ninja and Hermes if needed; it invokes the existing package-manager bootstrap and Hermes' official installer. Allow package-manager prompts on **your own** machine only. Sign into GitHub through the browser when prompted. Enter the OpenRouter key in the **hidden** prompt. The key is stored in `~/.hermes/.env` (mode `0600` on POSIX); it is never written into this repository.
5. One Hermes worker starts and claims **only owner/admin-approved** `nova:queued` GitHub issues. Keep the terminal running while contributing; Ctrl+C stops it. Rerun the same launcher to resume. Worker state and claims survive restart where supported.

`python3 JOIN-OPENROUTER.py --check` checks the provider and queue without claiming a task. `python3 JOIN-OPENROUTER.py --once` attempts at most one task. Run `python3 -m nova3_team.worker providers` to inspect available AI backends.

**Switching to a personal model later:** Enter a new dedicated OpenRouter key in the local Hermes settings and run `python3 JOIN-OPENROUTER.py --model NEW_MODEL`. Neither GitHub queue nor other workers need to change. Never replace the owner's local Hermes key or GitHub login.

## Owner: before leaving for work

- Merge this onboarding PR if satisfied with the diff, or send your friend its feature branch. The repository's old README/launcher notes still contain stale `crjase/nova3-reconstruction` links; use the exact organisation URL above.
- **Push the existing local reconstruction branch** after verifying it contains no proprietary binaries/secrets. The latest `automation/paperclip-worker` commits were reported as local-only; GitHub's `main` branch is behind them. A new contributor cannot receive those unpushed changes by cloning GitHub.
- Ensure the repository's **NOVA task coordinator** GitHub Actions workflow is enabled and `nova:queued` issues have explicit verified owner/admin approval records. Otherwise workers correctly skip work. Read-only contributors need GitHub Issues commenting access and permission to fork. Repo-level Write permission is not required for forks/PRs; keep it for maintainers only.
- If you run the existing worker on your own laptop *alongside* Paperclip, ensure it does **not** claim the same work as Paperclip's NOV-3 task: Paperclip issues are an independent coordination system. The GitHub worker can coordinate between computers, but **Paperclip NOV-3 does not participate in those GitHub leases** unless you explicitly bridge it.
- Owner/admin retains queue approval and PR merging. The repository's existing `planning_bridge` supports AI-generated **draft proposals**, but publishing them and approving/queueing remain separate gates. Continuous task generation + approval is **not automatic** merely by installing this launcher. Use `docs/progress/PLANNING_BRIDGE.md` and the verified owner-approved queue path. Don't grant an AI owner/admin GitHub access simply to bypass this gate.
- Prefer a separate spending-limited key for the friend. If the key is compromised, revoke **only** that key at OpenRouter.

## Permissions

Hermes uses `file,terminal,code_execution,todo` toolsets through the repo's worker adapter; these tools can run commands as **the operating-system user launching them**. No passwordless `sudo`, root session, external-account credentials, destructive filesystem operations, or automatic approval bypass is configured by the launcher. A friend can separately choose their machine's privilege model, but root is **not** required for cooperative reconstruction and it will not fix Paperclip's `spawn E2BIG` issue.

## Important limitations

- **Original references are absent:** APK, OBB, ELF and decompiler databases are intentionally not distributed. Work that requires them cannot claim binary equivalence without a local authorized reference.
- **Manual owner queue gate:** The system does not automatically convert all AI-generated tasks into authorized work; auto-approval would erase the requested control boundary.
- **No cross-machine memory:** Persist task specs, outcomes, tests, and source in GitHub commits/issues/PRs rather than in model sessions.
- **Runtime testing:** This onboarding file is validated for Python syntax and OS/CLI command construction, but has not been live-executed on your friend's machine or with their GitHub credentials. The repository's existing guarded-worker tests do not prove every OS works out of the box.

## If no work is being claimed

Check the GitHub issues for `nova:queued` and the authenticated owner/admin queue-approval record. A `nova:draft` issue is intentionally not claimable. Also check that the GitHub Actions coordinator is enabled, that your account can comment on issues and fork the repository, and that you are running **one** worker process per GitHub identity. Do not manually remove issue lease markers or re-label a task to bypass its approval gate.