# Maintainer: GitHub and automated contributor launcher setup

## Existing repository and safety

Current repository: `crjase/nova3-reconstruction` (private). Keep it private. Do not publish APK/OBB archives, .so game libraries, asset extractions, proprietary decompiler exports, or tracked historical commits containing them. Keep the reconstruction and research directories safe locally.

## Create organisation and transfer

1. While signed in to GitHub as `crjase`, create a new Free organisation at https://github.com/organizations/plan . You will have to choose its name and confirm the creation in the browser.
2. Open https://github.com/crjase/nova3-reconstruction/settings and scroll to **Danger Zone → Transfer**. Select the new organisation, confirm the exact repository name, and finish the transfer.
3. Visit `https://github.com/NEWORG/nova3-reconstruction` and verify the issues and Actions are present. Old URLs ordinarily redirect unless reused. Set the local remote with `git remote set-url origin https://github.com/NEWORG/nova3-reconstruction.git` if needed.
4. Under organisation **Settings → Member privileges**, set the default repository permission to **No permission**. Only invited users should get access.
5. Under organisation **Settings → Member privileges / Repository forking**, permit forks of private repositories. Exact wording changes over time; check the GitHub interface. A private fork cannot be placed under a GitHub Free organisation, but participants may fork to their personal accounts if permitted.
6. Under the repository **Settings → Collaborators and teams**, invite participant usernames as **Read** outside collaborators. They have to accept their invitations. Do not grant Write for the convenience of the worker.
7. Verify **Actions** are enabled for this repository. The `NOVA task coordinator` workflow must be enabled to make claims. Check Actions → General → Workflow permissions; the `NOVA task coordinator` file requests issues:write through its own `permissions:` block.
8. GitHub Free does **not** provide enforceable protected branches for PRIVATE repositories. Read-only contributors limit upstream push risk, but for genuinely enforced rules on private main, upgrade to an eligible GitHub Team plan. Do not enable automatic merging until you have verified CI and permissions.

See GitHub documentation: https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository ; https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches

## Produce native executables (no local cross-compilers needed)

1. Install the easy pack on the owner computer and push it to **main** (review the diff and source guard first).
2. Run `bash scripts/nova_publish_launchers.sh` from a clean main checkout. It creates and pushes a `launcher-v...` Git tag.
3. GitHub Actions → **Build contributor launchers** builds PyInstaller executables for Windows x64, Linux x64, Intel macOS and Apple Silicon macOS, runs a packaged smoke test and creates a private GitHub Release.
4. Open `https://github.com/NEWORG/nova3-reconstruction/releases`. Confirm all four archives exist and the build workflow succeeded. Do not tell people the binaries are ready before you see the Release.
5. Send invited contributors **the Releases link and the ONE_CLICK_CONTRIBUTORS.md guide**. They can unzip and double-click; no cloning required.

Release artifacts are **unsigned** and unnotarized. Windows SmartScreen and macOS Gatekeeper warnings are expected. For trustable frictionless public distribution, add Windows code signing and Apple Developer ID signing/notarization later. Native dependencies and sign-in still require permission the first time.

## Owner: launch/stop/resume

Run in your Arch Linux checkout: `bash START-LINUX.sh` or `python3 nova3_easy.py`. The launcher discovers `~/Projects/nova3_re` and uses it rather than making another project. Choose Codex at first run. Leave the terminal visible. Ctrl+C or close the window to stop; relaunch later to continue from local task state and source checkout.

Existing workers should be stopped before updating the worker code. Every contributor should use only one local worker instance per GitHub account at a time during initial deployment, until live multi-machine recovery testing has been completed.

## Actual status and limitations

- The worker can restart disposable AI sessions with local checkpoints and recover existing task checkout branches. It does **not** guarantee every in-progress AI chat transcript survives a sudden power failure.
- The initial queue is infrastructure and verification work, not the complete decompilation function backlog.
- Python code and smoke tests were verified locally. OS-native executable builds run on GitHub's Windows/macOS/Linux runners after the tag is pushed; none of those live runner jobs is pre-verified by the pack.
- Do not configure GitHub Actions to run arbitrary PR code with write tokens. Keep coordinator issue-comment inputs as data, and review any automated code changes to CI and tooling.
