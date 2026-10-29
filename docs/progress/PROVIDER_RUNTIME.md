# Optional solo-worker provider runtime — task #127

This slice provides an immutable typed provider registry, bounded local probes,
registry-backed worker selection and synthetic/offline tests. Default provider:
`codex`. Its display name is **ChatGPT (Codex CLI)**. There is no separately
assumed official ChatGPT coding executable. This does not add concurrent workers,
change launchers/TUIs, manage vendor accounts or change coordinator claims.

## Per-provider verification status

Official vendor documentation was retrieved on 2026-10-30. No vendor CLI is
installed in the development or CI environment, and **no model call, login,
session or paid request was made**. "VERIFIED" below means the documented
non-interactive invocation and approval mode were checked against the vendor's
own documentation and the argv is enforced by tests; it does not mean a live
session was executed here.

| ID | Status | Documented invocation used | Official evidence |
| --- | --- | --- | --- |
| `codex` | VERIFIED (pre-existing argv, unchanged) | `codex exec --ephemeral --sandbox workspace-write [--json] PROMPT` | <https://developers.openai.com/codex/cli/reference/> |
| `claude` | VERIFIED (pre-existing argv, unchanged) | `claude -p PROMPT --max-turns 12 --allowedTools Read Edit Write Glob Grep --output-format text` | <https://code.claude.com/docs/en/cli-reference> |
| `gemini` | VERIFIED (pre-existing argv, unchanged) | `gemini -p PROMPT --approval-mode auto_edit --output-format text` | <https://geminicli.com/docs/cli/headless/> |
| `kimi` | **UNSUPPORTED** (documented, no bounded profile) | none built | <https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html> · <https://moonshotai.github.io/kimi-cli/en/reference/kimi-command.html> |
| `qwen` | **VERIFIED** | `qwen -p PROMPT --approval-mode auto-edit --output-format text` | <https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/> · <https://qwenlm.github.io/qwen-code-docs/en/users/features/approval-mode/> |
| `opencode` | **UNSUPPORTED** (documented, permission profile is config-only) | none built | <https://opencode.ai/docs/cli/> · <https://opencode.ai/docs/permissions/> |
| `copilot` | **VERIFIED** | `copilot -p PROMPT --allow-tool write --deny-tool shell` | <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference> · <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference> |

Exact flags relied on, from those pages:

- **Qwen Code**: `--prompt` / `-p` "invokes Qwen Code in a non-interactive
  mode"; `--approval-mode` values `plan, default, auto-edit, auto, yolo`, where
  `auto-edit` "automatically approve[s] edit tools (`edit`, `write_file`,
  `notebook_edit`) while prompting for others"; `--output-format` / `-o`
  accepts `text, json, stream-json`. `auto-edit` is therefore a bounded edit
  profile, not blanket approval; `yolo` is never used.
- **GitHub Copilot CLI**: `-p PROMPT` "execute a prompt in non-interactive
  mode"; `--allow-tool=TOOL` and `--deny-tool=TOOL` accept the documented tool
  kinds `shell`, `write`, `read`, `url`, `memory`. `write` is allowed and
  `shell` denied; `--allow-all`, `--allow-all-tools`, `--allow-all-paths`,
  `--allow-all-urls` and `--yolo` are never emitted.
- **Kimi Code CLI**: print mode is entered with `--print`, and the official docs
  state that `--print` "implicitly enables `--afk`" and that in YOLO/AFK mode
  "all tool calls are auto-approved", including file modifications and shell
  commands. `--afk`, `--yolo`, `--yes` and `--auto-approve` are all blanket
  approvals. `kimi-cli` (the project whose executable is `kimi`) documents no
  per-tool permission rule for this path, so no bounded headless edit session
  exists within this worker's rules. The successor project `kimi-code` adds
  `permission.rules`, but it is a different, separately installed product and
  is not assumed here.
- **OpenCode**: `opencode run` is documented as non-interactive, but bounded
  restrictions are expressed only as `opencode.json` `permission` rules
  (`allow`/`ask`/`deny`). The only per-run switch, `--auto`, "automatically
  approve[s] permission requests that are not explicitly denied", i.e. a
  blanket approval. This adapter does not write vendor configuration files, so
  it deliberately builds no command rather than guessing one or substituting a
  read-only session.

`kimi` and `opencode` remain registered so `worker providers` reports the
executable, version if present, and this cited reason. They can never produce a
worker command: `build_command` rejects them even with a synthetic `AVAILABLE`
status. Their `required_help` tuple is empty because no help-token gate can
authorise a profile that does not exist.

## Capability gating (replaces the blanket `safety_block`)

Each supported provider declares the complete help tokens it depends on.
`probe_provider` runs `--version`, parses only a numeric version, then runs the
provider's documented help command and requires every token as a **whole
token**. `--allow-toolbox` therefore does not satisfy `--allow-tool`, and
Gemini's `auto_edit` does not authorise Qwen's `auto-edit`. For `qwen` and
`copilot` (the adapters added by this change) `build_command` additionally
refuses an `AVAILABLE` status whose `capabilities` do not contain all required
tokens, so a caller cannot bypass the probe by constructing a status object.
`codex`, `claude` and `gemini` keep their existing argv byte-for-byte and are
not subject to the new capability-evidence check.

Probing is bounded and safe: resolved absolute path, closed `stdin`, no shell,
three-second deadline, 64 KiB retained-output cap with process-group kill, and
fixed diagnostics. No prompt, login, install or network operation is part of a
probe. Vendor stdout/stderr is never surfaced in reports. A `.cmd`/`.bat` shim
is rejected before any process is started.

## Official documentation and operator installation links

Install only providers you independently choose, outside the worker. These are
reference links, not automatic installers:

| Provider | Official install / source instructions | CLI / permission reference |
| --- | --- | --- |
| ChatGPT (Codex CLI) | <https://developers.openai.com/codex/cli/> | <https://developers.openai.com/codex/cli/reference/> |
| Claude Code | <https://code.claude.com/docs/en/setup> | <https://code.claude.com/docs/en/cli-reference> |
| Gemini CLI | <https://geminicli.com/docs/get-started/installation/> | <https://geminicli.com/docs/cli/headless/> |
| Kimi Code CLI | <https://github.com/MoonshotAI/kimi-cli#installation> | <https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html> |
| Qwen Code | <https://github.com/QwenLM/qwen-code#installation> | <https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/> |
| OpenCode | <https://opencode.ai/docs/> | <https://opencode.ai/docs/permissions/> |
| GitHub Copilot CLI | <https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli> | <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference> |
| GitHub CLI (separate required repository identity) | <https://cli.github.com/> | <https://cli.github.com/manual/gh_auth_status> |

`ProviderSpec` carries `docs_url`, `install_url`, `permission_docs_url` (where
the vendor publishes approvals separately), `permission_profile` and, only for
documented-unsupported vendors, `unsupported_reason`. `worker providers` prints
`docs`, `permission_profile` and `state` per provider.

Every AI provider is OPTIONAL per contributor. Each contributor obtains and pays
for their own applicable subscription or API account and checks vendor pricing,
quotas and eligibility. No shared credentials, pooled billing, bundled accounts,
subscription equivalence or included API credits are promised here.

## Platform boundary

| Worker host | All registered providers | Verification |
| --- | --- | --- |
| Linux | Direct argv to a locally installed executable on PATH | Synthetic Python child-process tests executed here; no provider session tested |
| macOS | Same argv contract, subject to vendor installation support | Not exercised on macOS in this slice |
| Windows | Native `.exe` paths, including spaces, retained as one argv element | Synthetic path tests only; no Windows process execution tested |
| Windows `.cmd` / `.bat` wrappers | Rejected before probing or sending prompts | No `cmd.exe`, `shell=True` or implicit batch interpolation fallback |

This is the worker's invocation matrix, not a claim that every vendor ships
native builds for every OS. Check each vendor's installation/platform guidance.
An npm installation exposing only a Windows batch shim is unavailable to this
adapter until a vendor-supported non-shell entrypoint is implemented and tested.

## Operator-local first run

1. Read the selected vendor's official CLI and permission documentation. Install
   the chosen CLI yourself using its official instructions. Verify its version.
   For Qwen/Copilot, confirm the documented approval flags against your local
   version; Kimi/OpenCode are unsupported by this adapter, with reasons above.
2. Log in through the vendor's own normal interactive flow: `codex login`, launch
   `claude`, `gemini` or `qwen` and follow official instructions, or use
   `opencode auth login` / Copilot's interactive `/login`. The worker never runs
   these operations, prompts for credentials or reads credential files.
3. Independently install GitHub CLI and use `gh auth login` for the repository
   identity. This does not authenticate an AI provider, including Copilot.
4. Inspect local capabilities without a task claim:

   ```sh
   python -m nova3_team.worker providers
   python -m nova3_team.worker providers --check-auth
   ```

   The second command invokes status operations only: Codex `login status` and
   GitHub CLI `auth status --hostname github.com`. A vendor status operation may
   contact that vendor; it is never a model completion or login operation. Other
   providers return UNKNOWN because a safe status command is not verified here.
   Missing API environment variables do not disprove an existing OAuth login.
   Status success does not prove model entitlement, available credits or quotas.
5. After local verification, explicitly opt into the solo worker, for example
   `python -m nova3_team.worker run --provider qwen --once`. Existing providers
   retain their prior command arguments. Missing/incompatible runtimes fail with
   a diagnostic before coordinator interaction; no network install is attempted.

## Registry API and security constraints

`provider_ids()`, `get_provider(id)` and the immutable `PROVIDERS` mapping expose
`ProviderSpec` metadata. `probe_provider(id)` returns `ProviderStatus` with a
state, numeric version, resolved local executable path, capability evidence and
fixed diagnostic reason. The worker's listing omits executable paths and raw
probe output. `build_command(id, prompt, status=...)` returns an argv list;
new runnable providers require successful probe evidence. Unknown providers,
NUL/empty prompts, dash-prefixed prompts and incompatible status are rejected
without echoing their contents. Generated worker prompts always start with task
text, so the dash-prefix guard does not change normal session prompts.
`worker --provider` argparse choices come from `provider_ids()`, so an unknown ID
exits with code 2 before any GitHub call.

`check_auth(id)` and `check_github_identity()` return `AuthEvidence`, never keys,
tokens, environment dumps or credential paths. Unsupported status checks are
UNKNOWN, not authenticated and not an automatic login. Vendor account isolation
and login storage remain vendor-owned; this issue adds no account manager.

Provider execution keeps the existing bounded timeout, quarantine/recovery,
coordinator flow and post-session path/source guards. Provider stdout/stderr
flows through a bounded line relay before private 0600 logs are written. Known
environment secret values, recognizable token prefixes and Bearer credentials
are redacted; oversized lines are omitted, partial final lines are retained.
This preserves incremental diagnostics but logs are no longer byte-for-byte raw
provider output. Redaction is defence in depth, not a guarantee of detecting
arbitrary credentials or copyrighted content: logs and handoffs remain private
and must never be uploaded. Telemetry still contains only bounded activity
metadata. No blanket approval bypass or unrestricted shell flag is added.

The provider's edit approval policy is not an OS filesystem sandbox or a precise
issue allowlist. Existing scope checks still reject unapproved edits before
staging, and source guards still reject original executables/assets. No binary
matching or game-reconstruction equivalence is claimed by these runtime tests.

## Tests and continuation handoff

Run without any provider installation or paid calls:

```sh
python -m unittest nova3_team.tests.test_provider_runtime nova3_team.tests.test_worker_timeout -v
python -m unittest discover -s nova3_team/tests -v
git diff --check
```

At this commit the focused modules ran **38 tests** and the full collaboration
suite ran **530 tests with 6 skipped**, both exit 0. Tests use only synthetic
help/version/status responses and local Python emitters. No original game files
are needed. Covered: exact argv per provider (including unchanged Codex/Claude/
Gemini), Kimi/OpenCode refusal with cited docs, missing executables, unsupported
versions, failed probes, whole-token capability mismatch (`--allow-toolbox` vs
`--allow-tool`), capability-evidence bypass attempts, prompt injection kept in
one argv element, `.cmd`/`.bat` refusal, auth uncertainty, redaction, bounded
stdout/timeout, worker `command_for` regressions and the unknown-provider path.
No live provider or cross-platform runtime success is claimed.

Remaining genuinely unverified: no vendor CLI is installed in the development or
CI environment, so Qwen/Copilot argv have not been exercised against a live
vendor binary or model; their documented flags could change in future releases,
which the installed-help token gate is designed to catch. Kimi/OpenCode would
need a vendor-documented bounded per-run approval profile (or an owned,
validated config file with deny-by-default rules) before an adapter could be
added. Add status-only auth commands only when officially documented. Do not
resolve a capability failure with `--yolo`, global approvals, a read-only
substitute or a model readiness prompt.
