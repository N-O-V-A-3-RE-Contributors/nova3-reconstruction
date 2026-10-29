# Task #130: bounded OpenCode / OpenRouter gateway

Implemented offline policy and synthetic tests, **not a live-certified provider**.
The exact model is `deepseek/deepseek-v4.1-flash-1017`; no alias, older Flash,
`:floor`, or automatic provider substitution is accepted. Wafer is the sole
candidate. Direct DeepSeek is an explicit later implementation and is rejected
by this version. No worker, adapter #127, scheduler, observer, credentials or
project-root OpenCode settings have been changed.

Session constraints forbid network access and package installation. OpenCode is
absent from PATH. Consequently live model existence/reachability, endpoint
precision, ZDR, retention, tools, context, current rates, credential validity,
balance and OpenCode option compatibility remain **UNKNOWN**. No paid inference,
provider login, original-file access/upload or source matching was performed.
All source matching claims remain **NOT MATCHED**. Test schemas and version
`0.0.0` are deliberately synthetic, not supported production versions.

## Stable offline API and trust boundaries

`nova3_team/model_gateway.py` provides:

* `GatewayConfig.parse`: strict versioned JSON, exact model and Wafer, explicit
  precision, bounded tokens/context/time, Decimal rates and budget. Maximum pilot
  budget is $0.25, one attempt, zero retries. Worst-case uncached input plus
  output at the configured rate ceilings must fit the budget.
* `EndpointEvidence.parse`: strict **normalized human-reviewed metadata**, not
  a raw OpenRouter response. Identity, current prices (USD per million tokens),
  precision, context, tools, ZDR, retention and account restrictions must all be
  reviewed. Unknown fields are null; missing keys or extra fields are rejected.
  Balance must describe the same OpenRouter account used by native OpenCode.
* `Review.parse`: human review of an exact stable OpenCode version, SHA-256 of
  that version's config schema, native `auth list` format, and **all router
  option semantics** listed in `OPTIONS`. This is a local attestation, not an
  independently authenticated server statement. Do not manufacture an approval
  from fixtures, a loose schema, a version guess or a model catalogue entry.
* `native_status`: opt-in `opencode --version`, then `opencode auth list` only
  for that reviewed version. No auth-file reads or credential environment
  inspection. Accepts only a reviewed native OpenRouter API credential row;
  missing, failed and ambiguous status stay FAIL/UNKNOWN. Stored credential
  presence does **not** establish key validity. Raw output/errors are discarded.
* `preflight`: deterministic PASS/FAIL/UNKNOWN summary at an explicit timestamp.
  FAIL wins over UNKNOWN; every gate must pass for readiness. Review and endpoint
  metadata expire after one hour; future timestamps fail. Actual endpoint
  selection and key validity remain UNKNOWN even when readiness passes.
* `opencode_settings`: requires PASS, validates generated settings against the
  reviewed schema with a bounded offline JSON Schema subset, and rejects
  unsupported constraints/remote references. Pins both `model` and `small_model`.
  Router options are **model-specific**: `order` and `only` contain Wafer,
  `allow_fallbacks:false`, `zdr:true`, `require_parameters:true`, exactly one
  `quantizations` entry, and `max_price` prompt/completion ceilings in USD/M.
  No `sort` is needed with one allowed provider. The fixed OpenRouter base URL
  cannot be supplied by input. Generation is conditional on later semantic
  review; this session did not verify those API semantics live.
* `write_user_settings`: explicit, exclusive creation of
  `~/.config/opencode/opencode.json`, never merging/overwriting existing settings.
  No credentials in generated JSON. Other paths, a project-root `opencode.json`
  and any location inside this source checkout are rejected regardless of the
  current working directory, so running the documented command from `$HOME`
  works. Custom XDG locations require manual review.
* `PilotApproval` / `PilotSession`: explicit human confirmation of **one issue
  and the exact budget**, reserve at most one attempt, recheck freshness at
  reservation, no retries. Returns a manual-only plan; contains **no inference
  executor**. This in-memory guard is not a persistent spend ledger and must
  not be used as authorization for a scheduler or restarted to bypass the cap.
* `pilot_metrics`: allowlisted numeric/boolean results for the same synthetic
  task versus Codex. Records input/output/cache tokens, actual dollar cost when
  available, latency, endpoint where returned, tools, objective CI, timeout,
  attempts and retries. Missing cost/cache/endpoint stays UNKNOWN; over-budget,
  CI/tool failure or timeout is FAIL. Extra fields, prompts, raw tool output,
  endpoint drift and retries are rejected. Metrics validate reported outcomes;
  they do not run CI or independently authenticate billing/endpoint data.

The local JSON Schema validator deliberately supports only types, objects,
arrays, required/properties/additionalProperties/items, enum/const, local refs
and anyOf/allOf/oneOf. Unsupported schema versions or constraints must be
reviewed/implemented before use, never silently ignored. The whole reviewed
schema is audited up front, including unused properties, branches and
definitions, so an unreviewed constraint cannot hide in a subschema the
generated value never reaches. Schema bytes are decoded with duplicate-key
rejection. (The probe's config/endpoint metadata parsing remains plain
`json.loads`; it is outside this change's file allowlist and should get the
same duplicate-key hook in a follow-up.) Inside anyOf/oneOf/allOf, only a
value/branch mismatch counts as a
non-match; unsupported constraints, invalid/remote schemas, missing refs and
depth exhaustion abort the whole validation. `enum`/`const` compare by strict
JSON type identity (`true` is not `1`, `1.0` is not `1`), intentionally
stricter than JSON Schema numeric equivalence because rejecting is fail-closed;
`type: number` still accepts int and float. Permissive provider option maps
cannot prove routing semantics; human API review remains necessary.

## No-cost probe

The probe has **no HTTP transport**, paid command or inference endpoint. It
ingests explicitly supplied sanitized metadata. Offline mode ignores endpoint
and review files and never invokes OpenCode. `--local-native` permits only local
CLI status; the installed CLI is a trusted executable and its own startup
behavior must be reviewed before use in a network-restricted environment.
Windows `.cmd`/`.bat` wrappers are rejected; use a native executable. No shell,
root, install, background AI process or shared-workspace mutation is performed.
Native output is limited to 32 KiB, status commands time out after five seconds.

Create this **private** budget file manually under `.cache/gateway-inputs/`:

```json
{
  "schema": "nova3.gateway-config/1",
  "model": "deepseek/deepseek-v4.1-flash-1017",
  "provider": "wafer",
  "quantization": "fp8",
  "context_required": 8192,
  "input_tokens": 2048,
  "output_tokens": 512,
  "input_cap_per_million": "0.04",
  "output_cap_per_million": "0.50",
  "budget_usd": "0.01",
  "timeout_seconds": 30
}
```

This is a hypothetical low-spend policy, **not evidence that Wafer offers fp8**
or meets these rates. Change precision only after live review; never downgrade
it implicitly. The task-creation quote ($0.038/M uncached input, $0.50/M output)
is historical, not a verified current price. Cached input discounts are never
assumed when reserving budget.

```sh
PYTHONDONTWRITEBYTECODE=1 python tools/model_gateway_probe.py \
  --config .cache/gateway-inputs/budget.json --offline
```

Outputs sanitized JSON and exclusively creates
`.cache/model-gateway/preflight.json` (POSIX file 0600, new directory 0700).
Exit 0 = reviewed readiness PASS; 1 = FAIL; 2 = UNKNOWN. Repeated writes fail
rather than overwrite evidence; use a fresh private `--cache .cache/gateway-N`
directory for each probe. `--stdout-only` writes no report. No prompts/source
paths, balances, credential values, CLI output or private analysis enter the
report. On Windows, manually ensure the user-local cache/config directory ACL
is private; POSIX modes do not establish Windows ACL privacy.

For a later reviewed probe, add `--endpoint-evidence`, `--review`,
`--opencode-schema` and `--local-native`. Do not pass raw API responses,
credential files or logs as metadata. Both JSON contracts and required keys
are documented by the typed `parse` methods; all endpoint booleans, precision,
context, prices and balance accept null. `source` must equal the fixed public
metadata URL `https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints`.
`checked_at` is UTC Unix seconds. Human review must confirm the endpoint record
and API units, plus account privacy/credit status, without recording secrets.

The review contract is `nova3.gateway-review/1`, with exact `version`,
`schema_sha256`, `checked_at`, `router_options` (all names in `OPTIONS`),
`native_auth_list_reviewed:true`, and `semantics_reviewed:true`. Supply the exact
reviewed schema bytes separately. It is intentionally impossible to populate a
truthful live review from this session's evidence.

After PASS, `--write-user-config ~/.config/opencode/opencode.json` explicitly
creates user settings. Existing config is a blocker: compare/merge manually in
a separate authorized session. OpenCode merges multiple configuration sources;
plugins, MCP, project configs, environment overrides, defaults and version
changes may defeat restrictions. Review effective configuration in an empty,
disposable synthetic workspace before any manual run. Generated permissions
ask for tools, deny webfetch/external directories, disable sharing/autoupdate,
and enable only OpenRouter; this does not sandbox an untrusted CLI.

## Human-only functional smoke test (not run)

1. In a separately authorized session, consult official instructions, install a
   reviewed pinned native OpenCode release, and use its native login flow
   (`opencode auth login`, choose OpenRouter). Do not paste a key into scripts,
   issue comments, shell command arguments or committed JSON. Do not change
   Codex/Claude credentials. Confirm the release's actual version/auth commands.
2. In OpenRouter account settings, disable unwanted data collection/training,
   review provider retention and ZDR, and apply account/API-key spending limits
   wherever supported. Disable automatic top-ups. Review balance, remaining
   limits, credit-purchase fees/minimums/taxes in billing before funding; purchase
   fees are separate from inference rates and not counted by these metrics.
   Do not assume an account cap is a guaranteed per-request hard stop.
3. Obtain current public endpoint metadata without a completion; verify exact
   model availability, provider slug/routing precision, context, tools, rates,
   retention and ZDR. Confirm key validity/account restrictions via the native
   account UI/status or authorized no-completion account metadata; do not place
   credentials in this gateway. Populate the short-lived normalized evidence.
   Review the exact installed config schema **and** actual OpenCode/OpenRouter
   semantics, including USD/M `max_price`, `only`, precision and fallback
   rejection. If any requirement is absent, stop with UNKNOWN/FAIL.
4. A human explicitly confirms issue number and maximum total spend, including
   all auxiliary model calls. Only then construct `PilotApproval` and reserve
   one `PilotSession` plan. Config rate ceilings are not total-spend enforcement;
   OpenCode can make auxiliary/multiple calls. This module does not supply a
   verified token-limited executor; do not launch until a human has verified the
   native controls and account cap for the complete session.
5. In separate empty temporary directories, give both candidates the identical
   synthetic task: implement `sort_unique(values)` returning sorted distinct
   integers without mutating the input, and run a supplied unittest harness for
   empty, negative, duplicate and large integer cases plus input preservation.
   Use identical harness bytes, task ID `synthetic-sort-unique-v1`, input/token
   limits and wall-clock deadline; no NOVA3 files, originals, ASM, decompiler
   output, network tools or repository write access. Ask before each tool call.
   Compare actual CI exit results, not model self-reports. No retries or workers.
6. Record sanitized `pilot_metrics` for each candidate from native usage/billing
   and harness results. Missing returned endpoint/cost/cache is null, never an
   invented estimate or free request. Record failure/timeout as an attempt and
   stop. No reference-byte/source-equivalence claim follows from this exercise.

## Official references to verify in the authorized live review

These are documentation starting points, **not fetched in this offline session**:

* [Exact OpenRouter model](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017)
* [OpenRouter provider selection](https://openrouter.ai/docs/guides/routing/provider-selection)
* [OpenRouter privacy and logging](https://openrouter.ai/docs/guides/privacy-and-logging)
* [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr)
* [OpenRouter FAQ: billing, credits and fees](https://openrouter.ai/docs/faq)
* [OpenCode installation](https://opencode.ai/docs/)
* [OpenCode OpenRouter provider/login](https://opencode.ai/docs/providers/#openrouter)
* [OpenCode config/schema](https://opencode.ai/docs/config/)
* [OpenCode CLI](https://opencode.ai/docs/cli/)
* [Direct DeepSeek API](https://api-docs.deepseek.com/)

A direct DeepSeek account is a planned alternative with its own credentials,
exact model mapping, rates, privacy, tools, context and explicit human review.
It is **not currently implemented or interchangeable**, and is never a price
fallback. Provider/runtime #127 and scheduler #126 may consume these APIs later;
#125 may consume sanitized status. No such integration is included here.

## Verification and handoff

On 2026-10-30, Python 3.14.9, after the schema-validation hardening and the
`write_user_settings` path-guard repair in PR #144:

| Command | Actual result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s nova3_team/tests -q` | 537 tests passed, 6 skipped, exit 0. |
| `PYTHONDONTWRITEBYTECODE=1 python -m unittest nova3_team.tests.test_model_gateway -q` | 37 gateway tests passed, exit 0. |

Gateway tests cover model downgrade, provider/rate/precision/context drift, ZDR
and account privacy failures, unknown metadata, stale evidence, native credential
failure/absence/ambiguity, low balance, timeouts, noisy-output bounds/redaction,
schema/version mismatch, no fallbacks, private/exclusive writes, symlinks,
offline no-execution/no-network, budget approval, one attempt, metrics/tool/CI
failure, and synthetic Codex comparison. No real OpenCode command is executed.
Schema tests also cover strict `enum`/`const` type identity, propagation of
schema-level errors out of anyOf/oneOf/allOf, duplicate JSON keys, and auditing
of unsupported constraints in unused subschemas.

Next authorized session: install/review exact OpenCode, verify the official
schemas and live endpoint/account metadata, populate truthful review evidence,
and inspect effective settings. Actual paid smoke testing remains blocked on
human budget confirmation and a verified bounded native executor. No live
validation or paid pilot is claimed by passing mock tests.
