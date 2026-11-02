# Security policy

## Reporting a vulnerability

Please use GitHub's **private security advisory** on this repository. Do not open a public issue for anything involving credentials, permissions, the approval gate, the installer or a possible exploit.

Include: what you did, what happened, the version/commit, and the smallest reproduction you can. We will acknowledge it privately.

## What is in scope

* The worker installer and launchers (`tools/dsh_loop`, release archives).
* The approval/lease gate (`nova3_team/coordinator.py`, `access_policy.py`).
* The DSH supervisor, autoadmission and review gate (`nova3_team/*`).
* The `/nova` DSH plugin (`dsh-plugins/nova3-control`).
* GitHub Actions workflows in this repository.

## What is not

* The original game or its assets; this project does not distribute them.
* Your own AI provider account, billing or rate limits.
* A modified local checkout, or a worker run with credentials you shared with a third party.

## How credentials are handled

* The installer never stores your API key; it prepares a shell/profile placeholder that you fill in yourself.
* The worker refers to a key by **name** in its generated profile overlay; the value lives in your environment or DSH credential store.
* Logs are redacted before they are written or published. If you find a secret in a log or issue, report it privately and rotate the credential.

## Supported versions

Security fixes land on `main` and in the latest `launcher-v*` release. Older launcher generations are kept for reference and do not receive fixes.
