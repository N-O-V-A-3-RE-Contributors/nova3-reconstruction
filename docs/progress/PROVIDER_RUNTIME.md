# Optional solo-worker provider runtime — task #127

This slice adds an immutable typed provider registry, bounded local probes,
registry-backed worker selection and synthetic/offline tests. Default provider:
`codex`. Its display name is **ChatGPT (Codex CLI)**. There is no separately
assumed official ChatGPT coding executable. This does not add concurrent workers,
change launchers/TUIs, manage vendor accounts or change coordinator claims.

## Current capability and verification limits

| ID | Executable / candidate invocation | Worker policy | Evidence in this slice |
| --- | --- | --- | --- |
| `codex` | `codex exec ...` | Existing ephemeral workspace-write sandbox; optional JSON | Existing argv regression tests; installed help required at worker startup |
| `claude` | `claude -p ...` | Existing 12-turn limit and Read/Edit/Write/Glob/Grep allowlist | Existing argv regression tests; installed help required |
| `gemini` | `gemini -p ...` | Existing `auto_edit` approval mode | Existing argv regression tests; installed help required |
| `kimi` | `kimi -p ...` | **Unavailable**: bounded writable headless approval profile unverified | Candidate supplied in task; no unrestricted approval flag added |
| `qwen` | `qwen -p ...` | Candidate `--approval-mode auto-edit --output-format text`; installed help must advertise these capabilities | Exact argv and rejection tests with synthetic help; no installed CLI or live session verified |
| `opencode` | `opencode run ...` | **Unavailable**: explicit permission profile/config precedence unverified | Candidate supplied in task; no guessed config or read-only fallback |
| `copilot` | `copilot -p ...` | Candidate `--allow-tool write --deny-tool shell`; installed help must advertise tool permissions | Exact argv and rejection tests with synthetic help; no installed CLI or live session verified |

**Task #127 is only partially complete.** Official network documentation was not
retrieved because this session explicitly prohibited network access. Reference
links below identify the vendor documentation to review; they are not evidence
that current flags, permission precedence or vendor platform availability were
verified here. Qwen/Copilot builders remain candidates until an operator checks
the official permission semantics and the installed CLI passes the local probes.
Kimi/OpenCode deliberately cannot launch, even with a synthetic AVAILABLE status.
Do not report all four adapters as verified or all supported provider releases as
tested. Version `1.2.3` in tests is synthetic, not a supported-version assertion.

Capability probing is used instead of inventing a minimum version: resolve the
executable with `shutil.which`, run `--version`, parse only a numeric version, and
check complete capability tokens in vendor help. Unsupported or failed probes
return fixed diagnostics. Each probe has closed stdin, no shell, a three-second
deadline and a 64 KiB retained-output limit; oversized output stops the process.
Probes never submit a model prompt, initiate login, install software, or expose
vendor stdout/stderr in reports. Installed executables and their local startup
configuration remain vendor-owned; flag presence alone cannot prove all their
permission semantics. Normal worker runs preflight before contacting GitHub or
claiming a task, and use the resolved executable path in the checkout.

## Official documentation and operator installation links

Install only providers you independently choose, outside the worker. These are
reference links, not automatic installers:

| Provider | Official install / source instructions | CLI / permission reference |
| --- | --- | --- |
| ChatGPT (Codex CLI) | <https://developers.openai.com/codex/cli/> | <https://developers.openai.com/codex/cli/reference/> |
| Claude Code | <https://code.claude.com/docs/en/setup> | <https://code.claude.com/docs/en/cli-reference> |
| Gemini CLI | <https://geminicli.com/docs/get-started/installation/> | <https://geminicli.com/docs/cli/headless/> |
| Kimi Code CLI | <https://github.com/MoonshotAI/kimi-cli#installation> | <https://github.com/MoonshotAI/kimi-cli> |
| Qwen Code | <https://github.com/QwenLM/qwen-code#installation> | <https://github.com/QwenLM/qwen-code> |
| OpenCode | <https://opencode.ai/docs/> | <https://opencode.ai/docs/permissions/> |
| GitHub Copilot CLI | <https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli> | <https://docs.github.com/en/copilot/reference/cli-command-reference> |
| GitHub CLI (separate required repository identity) | <https://cli.github.com/> | <https://cli.github.com/manual/gh_auth_status> |

Every AI provider is OPTIONAL per contributor. Each contributor obtains and pays
for their own applicable subscription or API account and checks vendor pricing,
quotas and eligibility. No shared credentials, pooled billing, bundled accounts,
subscription equivalence or included API credits are promised here.

## Platform boundary

| Worker host | All registered providers | Verification |
| --- | --- | --- |
| Linux | Direct argv to a locally installed executable on PATH | Synthetic Python child-process tests executed here; no new provider session tested |
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
   For Qwen/Copilot, confirm the candidate permission flags and their precedence
   against local settings; Kimi/OpenCode need further adapter work first.
2. Log in through the vendor's own normal interactive flow: `codex login`, launch
   `claude`, `gemini`, `qwen` or `kimi` and follow official instructions, or use
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
new runnable candidates require successful probe evidence. Unknown providers,
NUL/empty prompts, dash-prefixed prompts and incompatible status are rejected
without echoing their contents. Generated worker prompts always start with task
text, so the dash-prefix guard does not change normal session prompts.

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

The full collaboration suite passed **523 tests**, exit 0. Tests use only
synthetic help/version/status responses and local Python emitters. No original
game files are needed. CLI argv, missing executables, incompatible capabilities,
version failures, prompt injection/option rejection, Windows suffixes, auth
uncertainty, redaction, timeouts, private incremental logs and source guards are
covered. No provider live-model or cross-platform runtime success is claimed.

Next bounded slice: with official documentation access, verify actual writable
Kimi approval controls; verify OpenCode's permission schema and config precedence
before implementing a deny-default edit profile; audit Qwen/Copilot permission
semantics and pin recorded installed-help/version fixtures. Add status-only auth
commands only when officially documented. Retain UNKNOWN/incompatible outcomes
when evidence is absent. Do not resolve a capability failure with `--yolo`,
global approvals, a read-only substitute or a model readiness prompt.
