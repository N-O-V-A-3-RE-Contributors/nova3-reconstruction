# Optional solo-worker provider runtime — task #127

This slice provides an immutable typed provider registry, bounded local probes,
registry-backed worker selection and synthetic/offline tests. Default provider:
`codex`. Its display name is **ChatGPT (Codex CLI)**. There is no separately
assumed official ChatGPT coding executable: OpenAI documents the Codex CLI as the
account-backed coding client, so "ChatGPT" here always means that CLI. This does
not add concurrent workers, change launchers/TUIs, manage vendor accounts or
change coordinator claims.

Every one of the seven providers has a typed `ProviderSpec` (ID, display name,
executable, official docs URL, official install URL, login guidance, help-probe
argv, required help tokens, permission profile) and a safe argv builder that
never uses `shell=True` and always passes the prompt as exactly one argv element.

## Per-provider verification status

Official vendor documentation was retrieved on **2026-10-30**. None of `kimi`,
`qwen`, `opencode` or `copilot` is installed in this development or CI
environment, and **no model call, login, session or paid request was made**.
"DOC-VERIFIED" means the documented non-interactive invocation and bounded
approval/allowlist mechanism were checked against the vendor's own pages and the
argv is enforced by offline tests. It does **not** mean a live session was run
here. Local-only facts: `codex 0.163.0`, `claude 2.1.126` and `gemini 0.38.1` are
installed on the maintainer host; `kimi`, `qwen`, `opencode` and `copilot` are
not.

| ID | Status | Documented invocation used | Official evidence |
| --- | --- | --- | --- |
| `codex` | VERIFIED (pre-existing argv, unchanged) | `codex exec --ephemeral --sandbox workspace-write [--json] PROMPT` | <https://developers.openai.com/codex/cli/reference/> |
| `claude` | argv PRE-EXISTING and unchanged; **local 2.1.126 probe reports INCOMPATIBLE** (its help no longer lists `--max-turns`, which this PR was not allowed to change) | `claude -p PROMPT --max-turns 12 --allowedTools Read Edit Write Glob Grep --output-format text` | <https://code.claude.com/docs/en/cli-reference> |
| `gemini` | VERIFIED (pre-existing argv, unchanged) | `gemini -p PROMPT --approval-mode auto_edit --output-format text` | <https://geminicli.com/docs/cli/headless/> |
| `kimi` | DOC-VERIFIED (agent-file tool allowlist) | `kimi --print --agent-file <ReadFile+WriteFile> --output-format text -p PROMPT` | <https://www.kimi-cli.com/en/customization/print-mode.html> · <https://www.kimi-cli.com/en/customization/agents.html> · <https://www.kimi-cli.com/en/reference/kimi-command.html> |
| `qwen` | DOC-VERIFIED | `qwen -p PROMPT --approval-mode auto-edit --output-format text` | <https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/> · <https://qwenlm.github.io/qwen-code-docs/en/users/features/approval-mode/> |
| `opencode` | DOC-VERIFIED (inline deny-by-default config) | `opencode run --auto --agent nova3-bounded PROMPT` + `OPENCODE_CONFIG_CONTENT` | <https://opencode.ai/docs/cli/#run> · <https://opencode.ai/docs/permissions/> · <https://opencode.ai/docs/agents/> · <https://opencode.ai/docs/config/> |
| `copilot` | DOC-VERIFIED | `copilot -p PROMPT --allow-tool write --deny-tool shell` | <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference> |

Exact documented flags relied on:

- **Kimi Code CLI**: `--print` runs non-interactively; the docs state it
  "implicitly enables `--afk` mode, all tool calls are auto-approved". Because
  there is no per-tool approval flag, the bound is structural: `--agent-file`
  loads a YAML agent whose `tools:` list contains **only**
  `kimi_cli.tools.file:ReadFile` and `kimi_cli.tools.file:WriteFile` (both paths
  are taken verbatim from the agents page). `Shell`, `SearchWeb` and `FetchURL`
  are therefore absent, so print-mode auto-approval can only authorise reads and
  writes. The worker materialises that YAML (mode 0600) in
  `.nova-private/<task>/provider-aux/` and passes its absolute path. `--yolo`,
  `--yes`, `-y`, `--auto-approve` and an implicit-afk-only argv are never
  emitted; `--afk` itself is never passed. Residual risk: if a future `kimi`
  release renames those tool module paths, the session has no tools and fails,
  which is fail-closed rather than a permission expansion.
- **Claude Code**: the adapter's argv and required-token list are unchanged by
  this task, per the issue's regression constraint. On the maintainer host,
  `claude --help` (2.1.126) advertises `-p/--print`, `--allowedTools` and
  `--output-format` but **not** `--max-turns`, so the probe reports
  `INCOMPATIBLE`. Because `codex`/`claude`/`gemini` predate the registry and
  their argv is frozen by #127, the probe is **advisory** for them: the worker
  prints an explicit warning and still launches the unchanged documented
  invocation, while new adapters (Kimi/Qwen/OpenCode/Copilot) fail closed
  without positive help-token evidence. Refusing only happens when the
  executable is genuinely absent. This preserves the pre-registry behaviour the
  issue requires instead of silently disabling an existing provider.
- **Qwen Code**: `--prompt` / `-p` "Run in headless mode"; `--approval-mode`
  value `auto-edit` auto-approves edit tools while other tools still require
  approval; `--output-format` / `-o` accepts `text, json, stream-json`. `yolo`
  is never used.
- **OpenCode**: `opencode run [message..]` is the documented non-interactive
  command; `--agent` selects an agent and `--format` selects output.
  `--auto` "auto-approve[s] permissions that are not explicitly denied" — with
  a deny-by-default rule set this only prevents an interactive "ask" from
  hanging. The rules are supplied per run through `OPENCODE_CONFIG_CONTENT`
  (documented inline runtime config, precedence tier 6) and define both a global
  `permission` table and a `nova3-bounded` primary agent whose rules are
  `{"*": "deny", "read": "allow", "edit": "allow", "glob": "allow",
  "grep": "allow"}`. `bash`, `webfetch`, `websearch`, `question` and
  `external_directory` therefore resolve to deny. No project or user config file
  is written. Residual risk: documented *managed* config
  (`/etc/opencode/`, MDM) has higher precedence than inline config, so an
  operator-installed managed policy can override these rules; that is an
  administrator-controlled trust boundary, not something this adapter can
  defeat.
- **GitHub Copilot CLI**: `-p PROMPT` "Execute a prompt in non-interactive
  mode"; `--allow-tool=TOOL` and `--deny-tool=TOOL` accept documented tool kinds
  `shell`, `write`, `read`, `url`, `memory`. `write` is allowed and `shell`
  denied. `--allow-all`, `--allow-all-tools`, `--allow-all-paths`,
  `--allow-all-urls` and `--yolo` are never emitted.

The registry keeps an `unsupported_reason` field for a future vendor whose
official docs genuinely show no bounded non-interactive profile. Such a spec is
reported `INCOMPATIBLE` after the version probe, never builds a command even
with a synthetic `AVAILABLE` status, and never falls back to read-only, `--yolo`
or a model readiness prompt. `kimi` and `opencode` no longer use that field.

## Capability gating

Each provider declares the complete help tokens it depends on.
`probe_provider` resolves the executable with `shutil.which`, rejects
`.cmd`/`.bat` shims before any process starts, runs a bounded `--version` parse,
then runs the provider's documented help command and requires every token as a
**whole token**. `--allow-toolbox` does not satisfy `--allow-tool`, and Gemini's
`auto_edit` does not authorise Qwen's `auto-edit`. Missing binary → `MISSING`
with the install URL; absent tokens or an unrecognised version → `INCOMPATIBLE`
with an actionable reason and the docs URL; timeout/oversized/failed probe →
`PROBE_FAILED`. No path crashes.

For the four newer adapters, `build_command` also refuses an `AVAILABLE` status
whose `capabilities` do not contain every required token, so a caller cannot
bypass the probe by constructing a status object. `codex`, `claude` and `gemini`
keep their existing argv byte-for-byte and are not subject to the new
capability-evidence check.

Probing is bounded and safe: resolved absolute path, closed `stdin`, no shell,
three-second deadline, 64 KiB retained-output cap with process-group kill, and
fixed diagnostics. No prompt, login, install or network operation is part of a
probe. Vendor stdout/stderr is never surfaced in reports or exception text.

## Official documentation and operator installation links

Install only providers you independently choose, outside the worker. These are
reference links, not automatic installers:

| Provider | Official install / source instructions | CLI / permission reference |
| --- | --- | --- |
| ChatGPT (Codex CLI) | <https://developers.openai.com/codex/cli/> | <https://developers.openai.com/codex/cli/reference/> |
| Claude Code | <https://code.claude.com/docs/en/setup> | <https://code.claude.com/docs/en/cli-reference> |
| Gemini CLI | <https://geminicli.com/docs/get-started/installation/> | <https://geminicli.com/docs/cli/headless/> |
| Kimi Code CLI | <https://github.com/MoonshotAI/kimi-cli#installation> | <https://www.kimi-cli.com/en/customization/print-mode.html> · <https://www.kimi-cli.com/en/customization/agents.html> |
| Qwen Code | <https://github.com/QwenLM/qwen-code#installation> | <https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/> |
| OpenCode | <https://opencode.ai/docs/> | <https://opencode.ai/docs/permissions/> |
| GitHub Copilot CLI | <https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli> | <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference> |
| GitHub CLI (separate required repository identity) | <https://cli.github.com/> | <https://cli.github.com/manual/gh_auth_status> |

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
native builds for every OS. An npm installation exposing only a Windows batch
shim is unavailable to this adapter until a vendor-supported non-shell
entrypoint is implemented and tested.

## Operator-local first run

1. Read the selected vendor's official CLI and permission documentation. Install
   the chosen CLI yourself using its official instructions and verify its
   version. The worker's help-token gate will report `INCOMPATIBLE` (it never
   degrades to read-only or `--yolo`) if your installed release lacks a required
   flag.
2. Log in through the vendor's own normal interactive flow: `codex login`,
   `claude`, `gemini`, `kimi login`, `qwen`, `opencode auth login`, or Copilot's
   interactive `/login`. The worker never runs these operations, prompts for
   credentials or reads credential files.
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
   `python -m nova3_team.worker run --provider opencode --once`. Existing
   providers retain their prior command arguments. Missing/incompatible runtimes
   fail with a diagnostic before coordinator interaction; no network install is
   attempted.

## Registry API and security constraints

`provider_ids()`, `get_provider(id)` and the immutable `PROVIDERS` mapping expose
`ProviderSpec` metadata. `probe_provider(id)` returns `ProviderStatus` with a
state, numeric version, resolved local executable path, capability evidence and
fixed diagnostic reason. The worker's listing omits executable paths and raw
probe output and includes the install URL and whether the spec is unsupported.

`build_command(id, prompt, status=..., aux_dir=...)` returns an argv list. New
runnable providers require successful probe evidence; `kimi` additionally
requires `aux_dir` containing the materialised agent file
(`prepare_aux_files`, 0600 inside a 0700 directory). `build_environment(id)`
returns only vendor-documented, non-secret child environment entries (currently
OpenCode's inline config); the worker merges them over its own environment for
the child process only. Unknown providers, NUL/empty prompts, dash-prefixed
prompts and incompatible status are rejected without echoing their contents.
Generated worker prompts always start with task text, so the dash-prefix guard
does not change normal session prompts. `worker --provider` argparse choices
come from `provider_ids()`, so an unknown ID exits with code 2 before any GitHub
call.

`check_auth(id)` and `check_github_identity()` return `AuthEvidence`, never keys,
tokens, environment dumps or credential paths. Unsupported status checks are
UNKNOWN, not authenticated and not an automatic login. Vendor account isolation
and login storage remain vendor-owned; this issue adds no account manager.

Provider execution keeps the existing bounded timeout, quarantine/recovery,
coordinator flow and post-session path/source guards. Provider stdout/stderr
flows through a bounded line relay before private 0600 logs are written. Known
environment secret values, recognizable token prefixes and Bearer credentials
are redacted; oversized lines are omitted, partial final lines are retained.
Redaction is defence in depth, not a guarantee of detecting arbitrary credentials
or copyrighted content: logs and handoffs remain private and must never be
uploaded. Telemetry still contains only bounded activity metadata. No blanket
approval bypass or unrestricted shell flag is added.

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

Tests use only synthetic help/version/status responses, temporary agent files
and local Python child processes. No original game files are needed. Covered:
exact argv for all seven providers, prompt-injection text kept in one argv
element, Windows native `.exe` path plus `.cmd`/`.bat` refusal, missing
executables, unrecognised versions, help-token mismatch, capability-evidence
bypass attempts, probe timeout/output cap, agent-file preparation and 0600/0700
modes, missing agent profile failing closed, OpenCode inline-config shape and
non-inheritance of secret environment variables, auth uncertainty, redaction,
worker `command_for`/`run_provider` regressions, unsupported-spec fail-closed
behaviour and the unknown-provider path.

Remaining genuinely unverified: none of `kimi`, `qwen`, `opencode` or `copilot`
is installed here, so their argv has not been exercised against a live vendor
binary or model. The Kimi `--agent-file` YAML and the OpenCode inline config are
enforced by tests as byte-exact documents and cited to the vendors' own pages,
but a live run could still reveal a vendor-side schema change. The help-token
gate is designed to catch flag removal; it cannot validate tool-module path
renames (Kimi) or managed-config precedence (OpenCode). Add status-only auth
commands only when officially documented. Do not resolve a capability failure
with `--yolo`, global approvals, a read-only substitute or a model readiness
prompt.
