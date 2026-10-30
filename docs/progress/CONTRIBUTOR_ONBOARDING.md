# Contributor onboarding — optional local browser GUI (issue #128)

`nova3_easy.py --gui` starts an **optional, local-only** browser wizard. The
terminal flow (`python3 nova3_easy.py`) and the Control Center TUI
(`python3 -m tools.observatory`) remain the primary interfaces and need no
browser. Nothing in the GUI changes the reconstruction pipeline, the owner/admin
approval gate (#135/#149) or any worker policy.

Implementation: `tools/setup/contributor_desktop.py` (Python standard library
only). It is a frontend over existing backends, not a second implementation:

| Concern | Backend reused |
| --- | --- |
| Provider detection/readiness | `nova3_team.worker.provider_candidates` → `nova3_team.provider_runtime` |
| Bounded worker slots | `nova3_team.worker.select_slot_providers` / `launch_slots` |
| Owner/admin gate + pinned identity | `nova3_team.worker.build_gate` / `authorize_launch` |
| Slot pause/resume/stop state | `nova3_team.worker.SlotScheduler` |
| GitHub identity | the user's own `gh auth login` (never proxied) |
| CLI installs | the same official npm packages as `nova3_easy.install_provider` |

## Security model

* **Loopback only.** The server binds `127.0.0.1` with `port=0` (ephemeral).
  `create_server()` raises `DesktopSecurityError` for every other host,
  including `0.0.0.0`, `::`, `localhost` and LAN addresses. There is no public
  binding, no port forwarding support and no remote login.
* **Per-run CSRF secret.** `secrets.token_urlsafe(32)` generated per session,
  embedded in the page `<meta name="nova3-csrf">`, required in the
  `X-NOVA3-CSRF` header of every `POST`, compared with
  `secrets.compare_digest`. It never appears in a URL, query string or log.
* **Origin and Host validation.** Every request must carry
  `Host: 127.0.0.1:<actual port>`; every mutating request must carry
  `Origin: http://127.0.0.1:<actual port>`. Missing or different values are
  rejected with 403 (DNS-rebinding / cross-site defence). Loopback client
  addresses are required as defence in depth.
* **No credential collection.** Payload keys such as `token`, `password`,
  `secret`, `api_key`, `authorization` and `cookie` are rejected with 400
  before dispatch. The GUI never reads, stores, echoes or copies a token, never
  switches provider accounts and never performs a login itself.
* **Bounded, redacted diagnostics.** Request bodies are capped at 64 KiB,
  responses are JSON with `Cache-Control: no-store`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a
  restrictive CSP. Logs contain only `METHOD /path -> status`; token-shaped
  text is redacted by `redact_text()` before it reaches a response or log.
* **No privilege escalation.** The GUI never runs `sudo`, never invokes a
  package manager and never pipes a remote script to a shell. The optional CLI
  install is a fixed argv list with `shell=False` into
  `~/.nova3-team/npm`, from the allow-listed official packages
  (`@openai/codex`, `@anthropic-ai/claude-code`, `@google/gemini-cli`).
* **Gate before launch.** `WorkerController.start()` builds the real gate and
  calls `worker.launch_slots`; a missing gate, a failed pinned preflight or a
  rejected queue admission returns a refusal and no provider process is
  spawned. The GUI has no bypass path.

## Wizard contents

1. **Welcome/privacy** — local-only explanation plus CLI/TUI fallback.
2. **Repository** — `OWNER/REPO` validation, `gh auth status` guidance
   (`gh auth login --web` is run by the user, never by the server), access check.
3. **Provider** — installed/signed-in detection, optional install of one
   allow-listed official CLI after an explicit consent checkbox. Offline or
   failed probes are reported as `UNKNOWN`, never as `READY`.
4. **Worker** — one active AI process by default; 2–4 only with an explicit
   consent checkbox; Start/Pause/Resume/Stop through the shared controller.
5. **Control Center and source match** — the `python3 -m tools.observatory`
   command and an explanation of `source match UNKNOWN`.
6. **Finish** — clean shutdown button.

Every step has next/back buttons, skippable steps have Skip, controls are
native `<label for>`/`<input>`/`<button>` elements and status regions use
`role="status" aria-live="polite"`.

## Known limitations

* **Active worker controls are unavailable in this GUI.** The in-memory
  scheduler has no command channel to the separate worker's claim loop.
  Pause/Resume/Stop therefore return an explicit failure while a launched
  worker exists, leaving its state and process untouched. Use the confirmed
  local worker controls instead. No successful pause or shutdown is claimed.
  All explicitly selected slots are retained in the controller's scheduler.
* **No login inside the GUI.** `gh auth login` and vendor CLI logins are
  interactive terminal flows; the wizard only re-checks their status. This is
  deliberate — the server must never receive a token or drive a browser login.
* **One local operator per run.** Multiple tabs share the single per-run
  session and CSRF secret; requests are serialised by a lock. There is no
  multi-user account model, and the server exits with the launcher.
* **Local processes are not an adversary here.** Any process already running as
  your user can read the page and its CSRF token. The loopback/Origin/Host model
  defends against remote pages, DNS rebinding and other users on the network,
  not against a compromised local account.
* **No packaging changes.** `--gui` works from a source checkout. Signed
  installers/release artifacts (issue step 3) are not part of this change and
  `.github/workflows/nova-release.yml` is untouched until an explicit
  packaging task requires it. PyInstaller builds must ship
  `tools/setup/contributor_desktop.py` and the `tools` package.
* **Server/headless machines** should not use `--gui`; use the terminal flow.

## Recovery paths

| Situation | Behaviour |
| --- | --- |
| No browser / `webbrowser.open` fails | URL printed; server stays usable; CLI/TUI instructions printed |
| Offline | GitHub/provider checks report `UNKNOWN`/`SIGNED_OUT`; no fabricated readiness |
| Unsupported platform | Detection/install errors are reported in-page; nothing is installed with elevated rights |
| GUI refused or crashes | `python3 nova3_easy.py` and `python3 -m tools.observatory` are unaffected |

## CLI/TUI fallback

```bash
python3 nova3_easy.py                 # guided terminal setup + worker
python3 nova3_easy.py --status        # worker status
python3 nova3_easy.py --dry-run --providers codex,claude   # slot preview
python3 -m tools.observatory          # Control Center TUI
python3 tools/setup/contributor_desktop.py --repo OWNER/REPO --no-browser
```

`NOVA3_GUI_NO_BROWSER=1` keeps the server-only mode for users who want to open
the URL in their own browser.

## Tests (offline)

```bash
python3 -B -m unittest tools.setup.tests.test_contributor_desktop -q
python3 -B -m unittest discover -s nova3_team/tests -q
python3 -B -m unittest discover -s tools/observatory/tests -q
```

The GUI tests use real ephemeral loopback sockets (to prove binding, Host,
Origin and CSRF enforcement) and mocked subprocesses/probes/browsers. They make
no internet request, start no provider and perform no paid call. Covered:
CSRF rejection, wrong Origin/Host, non-loopback bind refusal, ephemeral port,
no secrets in responses/logs, skipped/aborted install, offline provider status,
one-worker default, start/pause/resume calls, no launch without the gate,
browser-launch failure and clean shutdown.
