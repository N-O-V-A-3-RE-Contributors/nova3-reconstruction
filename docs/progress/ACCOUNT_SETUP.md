# Accounts & GitHub user management (Control Center page U)

Status: **IMPLEMENTED / TESTED locally**. No live vendor login, GitHub mutation
or paid model call is performed by this screen.

## What the page is for

The Accounts page makes optional AI-provider and GitHub setup legible without
turning the Control Center into a credential collector:

* per-provider **install / login readiness**, sourced from the verified
  `nova3_team/provider_runtime.py` registry (never re-declared here);
* an explicit, bounded **Test connection** action;
* local **enable/disable** preference per provider;
* official **login guidance** and a deliberately activated vendor documentation
  link (never an automatic interactive login);
* a separate, **read-only** GitHub identity and collaborator view;
* a single **official GitHub settings URL** for invitation/removal/role work —
  the screen does not perform those mutations.

## Navigation

| Key | Action |
| --- | --- |
| `U` from any page | open ACCOUNTS (Team group, page 14) |
| `Up`/`Down` or `J`/`K` | select a provider on a compact terminal |
| `R` | probe installed provider CLIs and `gh auth status` (deliberate, bounded) |
| `T` | Test connection for the selected provider (local probe only) |
| `E` | enable/disable the selected provider (persisted locally) |
| `L` | show a confirmation modal; `Y` opens the **official vendor docs page** |
| `M` | load the read-only GitHub collaborator list |
| `O` / `Enter` | owner-only: open the official GitHub access-settings page |
| `PgUp`/`PgDn` | scroll the GitHub/member card |
| `Q`, `?`, Tab, digits, `{`/`}` | unchanged global navigation |

The first render performs **no probing**. Provider and GitHub state starts as
`UNKNOWN` / `NOT LOADED`; pressing `R` is the deliberate activation. This keeps
opening the TUI cheap and prevents surprise subprocesses on a slow laptop.

## Honest state model

Provider rows combine two independent states:

* install: `INSTALLED`, `NOT INSTALLED`, `INCOMPATIBLE`, `PROBE FAILED`, `UNKNOWN`;
* account: `LOGGED IN`, `LOGGED OUT`, `LOGIN UNKNOWN`, `NOT APPLICABLE`, `NOT LOADED`.

`READY` is only shown when the runtime probe says the CLI is installed **and**
the vendor status command positively reports authentication. Missing/unknown
status is never converted into “logged in”. Only Codex currently exposes a
verified offline login-status command in the runtime; other vendors honestly
remain `LOGIN UNKNOWN` and show their official guidance.

GitHub identity is separate from every AI provider. The screen reports only the
state, the public account login (when gh prints one), and the token **scopes**.
Token values are never parsed, displayed, written or passed to another process.

## Security boundaries

* **No secret collection**: there is no password/token field anywhere. The
  module never reads `~/.config/gh`, keyrings, API-key environment variables or
  OAuth files.
* **No arbitrary shell**: provider IDs are validated against the registry before
  any command is built. All `gh` calls use a fixed argv with `shell=False`,
  closed stdin, a finite timeout, capped output and no pager. Shell-injection-
  shaped IDs are rejected without echoing them into a command or the UI.
* **Redaction**: probe details pass through the runtime redactor plus token/
  password/API-key assignment masking and control-character stripping. Fixed
  failure strings are used instead of raw vendor stderr.
* **Bounded actions**: `R`/`T` use the runtime's 3-second probes; gh status,
  role and collaborator calls use a 4-second timeout and 64 KiB cap.
* **Login is deliberate**: `L` opens a modal; only `Y` opens a browser. An
  aborted modal (`N`/`Esc`) opens nothing. The default `login_launcher` returns
  no command and never starts an interactive CLI.
* **Mutations stay on GitHub**: collaborator listing is read-only. Invitations,
  removals, role changes and merges are never sent by this screen; owners get a
  link to `https://github.com/<owner>/<repo>/settings/access`.
* **Role checks fail closed and are re-derived**: rendering may use the last
  cached role, but every owner action calls the resolver again (`gh repo view
  --json viewerPermission` via the existing `operator_ops.repo_permission`).
  A revocation between render and keypress denies the action. Exceptions,
  malformed responses and values outside `ADMIN|MAINTAIN|WRITE|TRIAGE|READ`
  resolve to `UNKNOWN` and are denied.
* **Curses is suspended around a browser launch** (`def_prog_mode` / `endwin`,
  then `reset_prog_mode` / refresh) so the TUI is restored even if the browser
  fails to open.

## Layout

Both breakpoints were exercised by tests:

* **80x24 (compact)**: two stacked cards. Top card lists provider rows
  (`> [x] Name  INSTALL  ACCOUNT`) with a short key legend and scrolls to keep
  the selected row visible. Bottom card shows GitHub identity, owner lock state,
  member-view state, the official settings URL, and account/scopes.
* **192x54 (panoramic)**: the same two cards side-by-side, with login guidance,
  member rows and the last test report visible without scrolling.
* Below 72 columns the existing tiny resize notice is kept; Accounts never
  renders a partial privileged modal.

## Dependency seam for #134 / #135

The issue #134/#135 readiness/authentication gate is not merged here. The
adapter seam is:

```python
AccountsController(
    ...,
    login_launcher=callable(provider_id) -> str | None,  # whitelisted HTTPS URL
    role_resolver=callable() -> str,                     # fresh server-derived role
)
```

`login_launcher` is the only place a future verified native-login adapter may
return a vendor-controlled URL. It must remain bounded, must not expose a
password/token prompt to the TUI, and its return value is rejected unless it is
an official HTTPS destination. Until #134/#135 lands, the default launcher
returns `None` and `L` opens the registry's documented vendor page only.

Provider metadata and readiness remain owned by `nova3_team/provider_runtime.py`.
This screen adds no CLI flags, auth subcommands or vendor-specific internals; if
the runtime gains a verified status command, the projection updates automatically.

## Verification

```text
python3 -B -m unittest discover -s tools/observatory/tests -q
Ran 193 tests ... OK

python3 -B -m unittest discover -s nova3_team/tests -q
Ran 537 tests ... OK (skipped=6)
```

All Accounts tests inject or mock provider probes, `gh`, the role resolver and
the browser. No network, real authentication, paid model call or server mutation
is performed.
