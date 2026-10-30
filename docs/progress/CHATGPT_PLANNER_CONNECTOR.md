# ChatGPT planning connector (#141)

Status: **IMPLEMENTED / TESTED offline**, readiness **UNVERIFIED against a live
account**. Planner-only. No autonomous-worker, bridge, access-policy or
planning-memory file was modified.

## What this is

An opt-in **planning** provider that lets a verified owner/admin choose
ChatGPT/OpenAI for plan drafting and switch back to Hermes later. It is *not*
an autonomous worker credential, a ChatGPT website integration, or a claim of
access to ChatGPT conversations, memory or account history. The login flow
proves identity; using the user's ChatGPT plan needs a **separate explicit
consent record**, and inference only happens after readiness reports `READY`.

Files:

| Path | Purpose |
| --- | --- |
| `nova3_team/chatgpt_planner_auth.py` | Discovery, dynamic client registration, Authorization Code + PKCE + state/nonce loopback flow, ID-token verification, scopes, refresh/revocation, model discovery, protected credential storage, consent records, readiness |
| `nova3_team/chatgpt_planner_provider.py` | #139 envelope import/export, bounded Responses-API call, planner-owned budget/cancellation, prompt-injection handling, provider switching |
| `nova3_team/tests/test_chatgpt_planner_auth.py` | 45 offline auth tests |
| `nova3_team/tests/test_chatgpt_planner_provider.py` | 36 offline provider tests |

No new runtime dependencies: Python standard library only. RSA/PKCS#1 v1.5
SHA-256 verification is implemented with `pow`/`hashlib`/`hmac`.

## Documented protocol implemented

Fetched on **2026-10-30** (the exact pages used, not guessed):

* <https://developers.openai.com/siwc/quickstart>
* <https://developers.openai.com/siwc/token-sharing-open-source>
* <https://developers.openai.com/siwc/token-sharing-open-source/sign-in>
* <https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions>
* <https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference>
* <https://developers.openai.com/siwc/token-sharing-open-source/token-reference>
* <https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery>
* <https://developers.openai.com/siwc/website>

Parameters and endpoints taken from those pages:

```text
issuer                 https://auth.openai.com
authorization          https://auth.openai.com/api/accounts/authorize
token                  https://auth.openai.com/api/accounts/oauth/token
JWKS                   https://auth.openai.com/.well-known/jwks.json
discovery              https://auth.openai.com/.well-known/openid-configuration
revocation             revocation_endpoint from discovery
models                 https://api.openai.com/v1/models
responses              https://api.openai.com/v1/responses
resource               https://api.openai.com/v1
first registration     client_id=dynamic_agent_client (entrypoint only)
identity scopes        openid profile email
plan scopes            offline_access resource.invoke chatgpt.tokens.use.direct
redirect               http://127.0.0.1:<port>/auth/callback (exact URI)
PKCE                   S256
```

Implemented controls:

* **Dynamic client registration** — a fresh `state`, OIDC `nonce` and PKCE
  verifier per attempt; `agent_name_hint` and the stable opaque
  `ext_agent_host_id` on first registration only. The issued `client_id`
  (e.g. `oaiapp_...`) is persisted; `dynamic_agent_client` is rejected as a
  stored/exchanged client.
* **Exact callback validation** — scheme `http`, host exactly `127.0.0.1`
  (`localhost` and wildcard hosts rejected), registered path, explicit valid
  port, no query/fragment/userinfo. The callback URI must equal the pending
  attempt's exact URI, including port.
* **Single-use state/nonce** — expiring in-memory transaction store; replay,
  unknown state, missing state, expired attempt and redirect mismatch each get
  a distinct failure code. `access_denied` stops before token exchange.
* **Code exchange** — form-encoded `authorization_code` with the issued
  `client_id`, `code_verifier`, exact `redirect_uri` and `resource`; no client
  secret.
* **ID-token validation** — RS256 only (`none`/HS* rejected), kid must exist
  in the issuer JWKS, signature checked against the RSA key, `iss` exact,
  `aud`/`azp` equal to the issued client id, `exp`/`iat` bounded by 60 s skew,
  non-empty `sub`, valid `sid` when present, and the saved `nonce`. A new
  sign-in is kept separate from the active account until this passes.
* **Scope validation** — granted scopes are parsed from the token response;
  missing `openid` fails the sign-in; missing
  `chatgpt.tokens.use.direct` retains identity but sets `plan_usage = False`
  (never silently treated as plan access).
* **Refresh / revocation** — `grant_type=refresh_token` with the saved issued
  client id and rotating refresh token, serialized per account; the returned
  ID token is re-verified and its `sub` must match. Revocation posts
  `token`, `token_type_hint=refresh_token`, `client_id`; HTTP 200 confirms,
  `5xx` is retried with bounded attempts and reported `revoke-unconfirmed`,
  after which local tokens are cleared and the user is told so.
* **Account/workspace selection** — one protected record per issued client id
  and verified subject, even for duplicate emails; reauthorization uses the
  saved client id and may send retained `id_token_hint`/`login_hint`.
* **Model discovery** — account catalog from `/v1/models`; only
  `visibility: "list"` entries, server order preserved, `slug` passed as
  `model`.

## Explicit consent and readiness

Login is **not** plan-usage consent. `ConsentStore.record(identity, actor=...,
plan_usage=True)` writes a separate `nova3.chatgpt.plan-consent/v1` record
(files `0600`, directory `0700`, atomic replace). `ConsentRecord` is bound to
the verified account key and a named human actor; a model can never create one.

`assess_readiness()` is fail-closed:

| Condition | State / code |
| --- | --- |
| no stored registration | `UNAVAILABLE / not-signed-in` |
| discovery or JWKS not loaded/verified | `UNKNOWN / discovery-unverified`, `jwks-unverified` |
| no verified registration | `UNKNOWN / registration-unverified` |
| `chatgpt.tokens.use.direct` absent | `UNAVAILABLE / plan-scope-missing` |
| no explicit consent | `UNAVAILABLE / plan-consent-missing` |
| expired token and no refresh token | `UNAVAILABLE / credentials-expired` |
| model catalog not fetched | `UNKNOWN / models-unverified` |
| selected model not in catalog | `UNAVAILABLE / model-unavailable` |
| everything verified | `READY` |

`UNAVAILABLE`/`UNKNOWN` returns before any HTTP call to `/v1/responses`. This
is what the UI (#140/#125) must display honestly.

## Provider behaviour and isolation

* Imports only `chatgpt_planner_auth` and the standard library — a unit test
  parses the module AST and asserts that `worker`, `coordinator`, `observer`,
  `provider_runtime` and `planning_memory` are absent.
* Own `session_id` (`planner-<uuid>`), own `PlannerBudget` (request count,
  output-token bound, optional USD cap) and own `CancellationToken`. Cancelling,
  disconnecting, refreshing OAuth or switching ChatGPT account changes only
  these objects; a sentinel “worker” state in the tests is unchanged.
* `POST /v1/responses` with `store: false`, `stream: true`,
  `max_output_tokens` bounded by the planner budget (hard cap 4096), request
  timeout, and response-size limit. Success requires
  `response.completed`; `response.failed`, `response.incomplete`, malformed
  SSE and non-200 statuses are failures, preserving the service error code
  when present.
* **Cost**: exact USD is reported only when the caller supplies pricing and
  usage is returned. Otherwise `CostState(known=False, limit_state="UNKNOWN")`.
  A known cost that would exceed the cap fails with `COST_CAP_REACHED` before
  the request. Plan-tier/weekly allowance itself is never inferred; it is
  `UNKNOWN` unless the service reports it.
* **Prompt injection**: envelope text and model output are untrusted data. The
  request instructions say so; injection patterns and authorization markers
  (`NOVA3_APPROVED`, `nova3.approval`, …) only set audit flags. There is no
  approval field, no bridge call and no queue write in this module, so model
  output cannot approve, spend, merge or enqueue anything.
* **Storage**: credential files under `~/.config/nova3/chatgpt_planner/`
  (or `$XDG_CONFIG_HOME`), owner-only, atomic `fsync` + `os.replace`, refused
  inside a git worktree. `repr()` of credentials, tokens, pending attempts and
  errors is redacted; error details pass through a credential-pattern filter.
* **#139 integration**: envelopes are validated by schema and checksum in
  memory; `provider_hint` is excluded from the checksum, so
  ChatGPT → Hermes → ChatGPT keeps one continuation identity. No direct
  database write, no import of `planning_memory` mutation APIs.

## Manual end-user setup (requires a real, authorized operator)

1. Run NOVA3 Control Center locally as the repository owner/admin; open the
   planner provider settings (UI wiring owned by #140/#125).
2. Choose **Continue with ChatGPT**. The app starts a loopback listener on
   `127.0.0.1`, opens the system browser, and registers this host
   (`ext_agent_host_id`) as an agent. Sign in, select the workspace and review
   the requested permissions.
3. After the callback, the app validates the ID token and shows the verified
   account. If `chatgpt.tokens.use.direct` was not granted, plan usage shows
   **UNAVAILABLE**; the identity sign-in can still be kept.
4. Separately tick **Use my ChatGPT plan for planning** and confirm as the
   named owner/admin. This writes the consent record.
5. Refresh the model list and pick a model. Inference stays unavailable until
   readiness is `READY`.
6. **Disconnect** revokes the renewable session (or reports that remote
   revocation was not confirmed), clears local tokens and consent, and leaves
   worker sessions untouched. The account/client mapping and host id are kept
   for a later sign-in.

## Fallback: separately billed OpenAI API route

If SIWC registration, eligibility, host identity or the plan scope is not
available, use the documented API-key route instead: the operator supplies
their **own** OpenAI API key through the existing secure provider login, and
the same bounded Responses call is used with explicit pricing and a cost cap.
A ChatGPT subscription does not imply API billing and this connector never
falls back silently — the selected route and its usage/limit state
(`KNOWN` or `UNKNOWN`) must be shown. No API key is read from environment
variables, Codex/ChatGPT credentials or browser storage by this module.

## Verified vs unverified

Verified offline (81 tests, synthetic RSA key and tokens, fake transport):

* discovery/issuer/HTTPS validation; exact loopback redirect rules;
  PKCE/state/nonce single use and expiry; dynamic-vs-issued client rules;
  signed ID-token claims/alg/kid/expiry/nonce/audience; missing scope;
  refresh rotation, terminal refresh errors, different-user refresh;
  revocation success and unconfirmed; consent separation and readiness states;
  model filtering; envelope checksum/tamper; provider switching with the same
  envelope; unsupported model; request/cost caps; prompt injection flags;
  error redaction; no worker/DB imports; no worker state mutation.

**Unverified / cannot be claimed from this work:**

* A real Sign in with ChatGPT registration and any account/workspace
  eligibility for `chatgpt.tokens.use.direct`.
* The live discovery document, JWKS contents, token response fields, preview
  endpoint stability and any paid Responses inference.
* ChatGPT website conversations/memories — **not accessible by this flow**.
* Real cost figures, plan/weekly allowance and reset times (`UNKNOWN` unless
  the service returns them).
* End-to-end UI integration (#125/#140) and manual acceptance by a human
  owner/admin.

Until an authorized operator completes those steps, the connector must report
`UNAVAILABLE`/`UNKNOWN`, not success.

## Test commands

```bash
python3 -B -m unittest nova3_team.tests.test_chatgpt_planner_auth \
  nova3_team.tests.test_chatgpt_planner_provider -q
python3 -B -m unittest discover -s nova3_team/tests -q
```

No network, OAuth, model call, package install or worker start occurs in tests.
