"""Planner-only Sign in with ChatGPT (SIWC) authentication.

This module implements the *documented* OpenAI Sign in with ChatGPT flow for
open-source applications: a public OAuth client that performs dynamic agent
registration, Authorization Code + PKCE, OIDC ``state``/``nonce`` validation,
signed ID-token verification and optional ChatGPT-plan usage through the
Responses API.

Scope and safety rules (binding for this module):

* **Planner only.** Nothing here can start, stop, reassign or configure an
  autonomous worker. There is no import of ``worker``, ``coordinator``,
  ``observer``, ``provider_runtime`` or ``planning_memory``.
* **Fail closed.** If discovery, JWKS, the issued client registration, the
  granted ``chatgpt.tokens.use.direct`` scope, the signed ID token or a model
  catalog cannot be verified, callers get ``UNAVAILABLE``/``UNKNOWN`` and must
  not make an inference request.
* **Login is not plan consent.** A successful sign-in only proves identity.
  Plan usage additionally needs an explicit :class:`ConsentRecord` created by
  the human operator (see :class:`ConsentStore`).
* **No credential reuse.** Browser cookies, ChatGPT web sessions, Codex
  credentials and API keys are never read, scraped or imported.
* **No claim of ChatGPT history.** This flow cannot read ChatGPT
  conversations, memory or account context.
* Credentials live in protected local storage (directory ``0700``, files
  ``0600``, atomic replace) and are redacted from ``repr``/errors/logs.

Documented sources (fetched 2026-10-30):

* https://developers.openai.com/siwc/token-sharing-open-source
* https://developers.openai.com/siwc/token-sharing-open-source/sign-in
* https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
* https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
* https://developers.openai.com/siwc/token-sharing-open-source/token-reference
* https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
* https://developers.openai.com/siwc/website

Unverified boundary: the endpoints, issuer and scope names below are copied
from the fetched documentation. Whether a particular ChatGPT account or
workspace is *eligible* for plan usage, and how long the preview endpoints stay
stable, cannot be verified offline. ``UNKNOWN``/``UNAVAILABLE`` readiness is the
honest answer until a real, authorized sign-in is performed by the operator.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

# --------------------------------------------------------------------------
# Documented protocol constants
# --------------------------------------------------------------------------

DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
DOCUMENTED_ISSUER = "https://auth.openai.com"
DOCUMENTED_AUTHORIZE_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
DOCUMENTED_TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
DOCUMENTED_JWKS_URI = "https://auth.openai.com/.well-known/jwks.json"
DOCUMENTED_REVOCATION_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/revoke"
DOCUMENTED_RESOURCE = "https://api.openai.com/v1"
DOCUMENTED_MODELS_ENDPOINT = "https://api.openai.com/v1/models"
DOCUMENTED_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"

DYNAMIC_AGENT_CLIENT = "dynamic_agent_client"  # entrypoint only, never saved
IDENTITY_SCOPES = ("openid", "profile", "email")
PLAN_SCOPES = ("offline_access", "resource.invoke", "chatgpt.tokens.use.direct")
PLAN_SCOPE = "chatgpt.tokens.use.direct"
DEFAULT_SCOPES = IDENTITY_SCOPES + PLAN_SCOPES
CONSENT_VERSION = "nova3.chatgpt.plan-consent/v1"
CREDENTIAL_SCHEMA = "nova3.chatgpt.credentials/v1"
DEFAULT_REDIRECT_PATH = "/auth/callback"
DEFAULT_PORT = 1455
PENDING_TTL_SECONDS = 600
MAX_ID_TOKEN_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
CLOCK_SKEW_SECONDS = 60


class AuthError(ValueError):
    """Stable, secret-free failure. Never put tokens in ``detail``."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = redact(detail)
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class AuthState(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\boaiapp_[A-Za-z0-9_-]{6,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r'(?i)"(access_token|refresh_token|id_token|code_verifier)"\s*:\s*"[^"]*"'),
    re.compile(r"(?i)\b(access_token|refresh_token|id_token|code_verifier)=([^&\s]+)"),
    re.compile(r"(?i)\bcode=[A-Za-z0-9._~-]{8,}"),
)


def redact(value: object) -> str:
    """Redact credential-looking substrings; safe for logs and exceptions."""
    text = value if isinstance(value, str) else str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def redacted_mapping(data: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key, value in data.items():
        if any(part in key.lower() for part in ("token", "secret", "verifier", "code")):
            out[str(key)] = "[redacted]"
        elif isinstance(value, Mapping):
            out[str(key)] = redacted_mapping(value)
        else:
            out[str(key)] = redact(value)
    return out


# --------------------------------------------------------------------------
# Transport abstraction (offline-injectable)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> dict:
        try:
            value = json.loads(self.body)
        except ValueError as exc:
            raise AuthError("invalid-response", "response is not JSON") from exc
        if not isinstance(value, dict):
            raise AuthError("invalid-response", "response must be a JSON object")
        return value

    def __repr__(self) -> str:  # never show bodies; they may contain tokens
        return f"HttpResponse(status={self.status}, bytes={len(self.body)})"


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Default network transport. Tests always inject a fake transport."""

    def __init__(self, *, max_bytes: int = MAX_RESPONSE_BYTES) -> None:
        self.max_bytes = max_bytes

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        data = None
        hdrs = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise AuthError("response-too-large", "response exceeded limit")
                return HttpResponse(resp.status, raw.decode("utf-8", "replace"),
                                    dict(resp.headers))
        except urllib.error.HTTPError as exc:
            raw = exc.read(self.max_bytes + 1)
            return HttpResponse(exc.code, raw.decode("utf-8", "replace"),
                                dict(exc.headers or {}))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AuthError("network-error", redact(exc)) from exc


# --------------------------------------------------------------------------
# Small encoding helpers
# --------------------------------------------------------------------------


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise AuthError("invalid-encoding", "invalid base64url")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise AuthError("invalid-encoding", "invalid base64url") from exc


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def parse_scopes(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(part for part in value.split() if part)
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(part) for part in value if str(part))
    return frozenset()


def validate_scopes(granted: object, required: Sequence[str]) -> frozenset[str]:
    scopes = parse_scopes(granted)
    missing = sorted(set(required) - scopes)
    if missing:
        raise AuthError("missing-scope", "missing scope(s): " + ", ".join(missing))
    return scopes


# --------------------------------------------------------------------------
# OIDC discovery
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    revocation_endpoint: str
    raw: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (f"OIDCConfig(issuer={self.issuer!r}, "
                f"authorization_endpoint={self.authorization_endpoint!r}, "
                f"token_endpoint={self.token_endpoint!r}, jwks_uri={self.jwks_uri!r})")


def _require_https_url(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthError("invalid-discovery", f"{field_name} missing")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise AuthError("invalid-discovery", f"{field_name} must be https")
    if parts.username or parts.password or parts.fragment:
        raise AuthError("invalid-discovery", f"{field_name} has unsafe components")
    return value


def load_discovery(
    transport: Transport,
    *,
    expected_issuer: str = DOCUMENTED_ISSUER,
    timeout: float = 20.0,
) -> OIDCConfig:
    """Fetch and validate the issuer discovery document. Fails closed."""
    resp = transport.request("GET", DISCOVERY_URL, timeout=timeout)
    if resp.status != 200:
        raise AuthError("discovery-unavailable", f"HTTP {resp.status}")
    raw = resp.json()
    issuer = raw.get("issuer")
    if issuer != expected_issuer:
        raise AuthError("issuer-mismatch", "discovery issuer is not the expected issuer")
    return OIDCConfig(
        issuer=issuer,
        authorization_endpoint=_require_https_url(
            raw.get("authorization_endpoint"), field_name="authorization_endpoint"),
        token_endpoint=_require_https_url(
            raw.get("token_endpoint"), field_name="token_endpoint"),
        jwks_uri=_require_https_url(raw.get("jwks_uri"), field_name="jwks_uri"),
        revocation_endpoint=_require_https_url(
            raw.get("revocation_endpoint"), field_name="revocation_endpoint"),
        raw=raw,
    )


def documented_config() -> OIDCConfig:
    """The static values published in the fetched documentation (unverified live)."""
    return OIDCConfig(
        issuer=DOCUMENTED_ISSUER,
        authorization_endpoint=DOCUMENTED_AUTHORIZE_ENDPOINT,
        token_endpoint=DOCUMENTED_TOKEN_ENDPOINT,
        jwks_uri=DOCUMENTED_JWKS_URI,
        revocation_endpoint=DOCUMENTED_REVOCATION_ENDPOINT,
    )


# --------------------------------------------------------------------------
# RSA / JWT verification (stdlib only)
# --------------------------------------------------------------------------

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _int_from_b64(value: object, *, field_name: str) -> int:
    if not isinstance(value, str) or not value:
        raise AuthError("invalid-jwks", f"{field_name} missing")
    return int.from_bytes(b64url_decode(value), "big")


def _rsa_pkcs1v15_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if k < 256 or len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    encoded = pow(s, e, n).to_bytes(k, "big")
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(digest_info) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(encoded, expected)


class JwksCache:
    """Kid-indexed RSA signing keys from the issuer's documented JWKS."""

    def __init__(self) -> None:
        self._keys: dict[str, tuple[int, int]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    def __repr__(self) -> str:
        return f"JwksCache(loaded={self._loaded}, keys={len(self._keys)})"

    def load(self, transport: Transport, *, jwks_uri: str = DOCUMENTED_JWKS_URI,
             timeout: float = 20.0) -> None:
        resp = transport.request("GET", jwks_uri, timeout=timeout)
        if resp.status != 200:
            raise AuthError("jwks-unavailable", f"HTTP {resp.status}")
        body = resp.json()
        keys = body.get("keys")
        if not isinstance(keys, list):
            raise AuthError("invalid-jwks", "keys must be a list")
        parsed: dict[str, tuple[int, int]] = {}
        for item in keys:
            if not isinstance(item, Mapping):
                continue
            if item.get("kty") != "RSA" or item.get("use", "sig") != "sig":
                continue
            if item.get("alg", "RS256") != "RS256":
                continue
            kid = item.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            n = _int_from_b64(item.get("n"), field_name="n")
            e = _int_from_b64(item.get("e"), field_name="e")
            if e < 3 or e % 2 == 0:
                raise AuthError("invalid-jwks", "invalid RSA exponent")
            parsed[kid] = (n, e)
        if not parsed:
            raise AuthError("invalid-jwks", "no usable RS256 signing keys")
        with self._lock:
            self._keys = parsed
            self._loaded = True

    def verify(self, token: str, *, issuer: str, audience: str, nonce: str | None,
               now: int | None = None, leeway: int = CLOCK_SKEW_SECONDS) -> dict:
        return verify_id_token(
            token, keys=self._keys, issuer=issuer, audience=audience,
            nonce=nonce, now=now, leeway=leeway)


def _decode_jwt_segment(token: str, index: int) -> bytes:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("invalid-id-token", "token must have three segments")
    return b64url_decode(parts[index])


def verify_id_token(
    token: str,
    *,
    keys: Mapping[str, tuple[int, int]],
    issuer: str,
    audience: str,
    nonce: str | None,
    now: int | None = None,
    leeway: int = CLOCK_SKEW_SECONDS,
) -> dict:
    """Verify RS256 signature and required ID-token claims. Returns claims."""
    if not isinstance(token, str) or not token or len(token) > MAX_ID_TOKEN_BYTES:
        raise AuthError("invalid-id-token", "token missing or too large")
    header_raw = _decode_jwt_segment(token, 0)
    try:
        header = json.loads(header_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("invalid-id-token", "malformed header") from exc
    if not isinstance(header, dict):
        raise AuthError("invalid-id-token", "header must be a JSON object")
    if header.get("alg") != "RS256":
        raise AuthError("invalid-id-token", "only RS256 is accepted")
    payload_raw = _decode_jwt_segment(token, 1)
    signature = _decode_jwt_segment(token, 2)
    try:
        claims = json.loads(payload_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("invalid-id-token", "malformed JSON") from exc
    if not isinstance(claims, dict):
        raise AuthError("invalid-id-token", "segments must be JSON objects")
    if header.get("crit"):
        raise AuthError("invalid-id-token", "critical header extensions are unsupported")
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        raise AuthError("unknown-signing-key", "ID token kid is not in the issuer JWKS")
    n, e = keys[kid]
    if not _rsa_pkcs1v15_verify(n, e, (token.split(".")[0] + "." + token.split(".")[1]).encode("ascii"),
                                signature):
        raise AuthError("invalid-signature", "ID token signature did not verify")

    current = int(time.time()) if now is None else int(now)
    if claims.get("iss") != issuer:
        raise AuthError("issuer-mismatch", "ID token issuer mismatch")
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else aud
    if not isinstance(audiences, list) or audience not in audiences:
        raise AuthError("audience-mismatch", "ID token audience mismatch")
    azp = claims.get("azp")
    if azp is not None and azp != audience:
        raise AuthError("audience-mismatch", "ID token azp mismatch")
    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise AuthError("invalid-id-token", "exp missing")
    if not isinstance(iat, int) or isinstance(iat, bool):
        raise AuthError("invalid-id-token", "iat missing")
    if current > exp + leeway:
        raise AuthError("id-token-expired", "ID token expired")
    if iat > current + leeway:
        raise AuthError("invalid-id-token", "iat is in the future")
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise AuthError("invalid-id-token", "sub missing")
    sid = claims.get("sid")
    if sid is not None and (not isinstance(sid, str) or not sid.strip()):
        raise AuthError("invalid-id-token", "sid invalid")
    if nonce is not None:
        expected_nonce = claims.get("nonce")
        if not isinstance(expected_nonce, str) or not hmac.compare_digest(expected_nonce, nonce):
            raise AuthError("nonce-mismatch", "ID token nonce mismatch")
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        raise AuthError("invalid-id-token", "email invalid")
    return claims


# --------------------------------------------------------------------------
# Host identity / redirect validation
# --------------------------------------------------------------------------


def new_host_id() -> str:
    """Opaque, stable-per-host identifier (documented URN example)."""
    return f"urn:uuid:{uuid.uuid4()}"


def validate_redirect_uri(uri: object, *, path: str = DEFAULT_REDIRECT_PATH) -> str:
    """Exact loopback redirect validation.

    The documentation requires an HTTP loopback callback on ``127.0.0.1``;
    later sign-ins may change only the port. ``localhost``, wildcard hosts,
    query strings, fragments, userinfo and any path other than the registered
    one are rejected.
    """
    if not isinstance(uri, str) or not uri:
        raise AuthError("invalid-redirect", "redirect URI missing")
    parts = urllib.parse.urlsplit(uri)
    if parts.scheme != "http":
        raise AuthError("invalid-redirect", "redirect must use http loopback")
    if parts.hostname != "127.0.0.1":
        raise AuthError("invalid-redirect", "redirect host must be exactly 127.0.0.1")
    if parts.username or parts.password:
        raise AuthError("invalid-redirect", "redirect must not contain userinfo")
    if parts.query or parts.fragment:
        raise AuthError("invalid-redirect", "redirect must not contain query or fragment")
    if parts.path != path:
        raise AuthError("invalid-redirect", f"redirect path must be exactly {path}")
    try:
        port = parts.port
    except ValueError as exc:
        raise AuthError("invalid-redirect", "redirect port invalid") from exc
    if port is None or not 1 <= port <= 65535:
        raise AuthError("invalid-redirect", "redirect must include an explicit valid port")
    return f"http://127.0.0.1:{port}{path}"


def make_redirect_uri(port: int = DEFAULT_PORT, *, path: str = DEFAULT_REDIRECT_PATH) -> str:
    return validate_redirect_uri(f"http://127.0.0.1:{int(port)}{path}", path=path)


# --------------------------------------------------------------------------
# PKCE and pending authorization attempts
# --------------------------------------------------------------------------


def new_pkce_verifier() -> str:
    return b64url_encode(os.urandom(48))  # 64 chars, unreserved


def pkce_challenge(verifier: str) -> str:
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        raise AuthError("invalid-pkce", "verifier length out of range")
    if not re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier):
        raise AuthError("invalid-pkce", "verifier contains invalid characters")
    return b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    return b64url_encode(os.urandom(32))


def new_nonce() -> str:
    return b64url_encode(os.urandom(32))


@dataclass(frozen=True)
class PendingAuthorization:
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    client_id: str
    ext_agent_host_id: str
    new_registration: bool
    created_at: int
    account_label: str | None = None
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    def __repr__(self) -> str:
        return (f"PendingAuthorization(state=[redacted], client_id={self.client_id!r}, "
                f"new_registration={self.new_registration}, "
                f"redirect_uri={self.redirect_uri!r})")


class PendingStore:
    """In-memory, single-use, expiring transaction state.

    Consuming is atomic; a state value can succeed at most once. Replays and
    unknown values are distinguished for diagnostics.
    """

    def __init__(self, *, ttl_seconds: int = PENDING_TTL_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        self._pending: dict[str, PendingAuthorization] = {}
        self._consumed: set[str] = set()
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def add(self, pending: PendingAuthorization) -> None:
        with self._lock:
            self._pending[pending.state] = pending

    def __len__(self) -> int:
        return len(self._pending)

    def consume(self, state: object, *, now: int | None = None) -> PendingAuthorization:
        if not isinstance(state, str) or not state:
            raise AuthError("state-missing", "callback did not include state")
        with self._lock:
            if state in self._consumed:
                raise AuthError("state-replayed", "authorization state was already used")
            pending = self._pending.pop(state, None)
            self._consumed.add(state)
        if pending is None:
            raise AuthError("state-mismatch", "authorization state is unknown")
        current = int(self._clock()) if now is None else int(now)
        if current - pending.created_at > self._ttl:
            raise AuthError("state-expired", "authorization attempt expired")
        return pending

    def discard(self, state: str) -> None:
        with self._lock:
            self._pending.pop(state, None)


# --------------------------------------------------------------------------
# Authorization request
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    pending: PendingAuthorization

    def __repr__(self) -> str:
        return "AuthorizationRequest(url=[redacted], pending=%r)" % (self.pending,)


def build_authorization_url(
    *,
    config: OIDCConfig,
    pending: PendingAuthorization,
    id_token_hint: str | None = None,
    login_hint: str | None = None,
    agent_name: str | None = None,
    force_consent: bool = False,
) -> str:
    """Build the documented authorization URL with exact callback binding."""
    scopes = " ".join(pending.scopes)
    params: dict[str, str] = {
        "client_id": pending.client_id,
        "response_type": "code",
        "redirect_uri": pending.redirect_uri,
        "scope": scopes,
        "resource": DOCUMENTED_RESOURCE,
        "state": pending.state,
        "nonce": pending.nonce,
        "code_challenge_method": "S256",
        "code_challenge": pkce_challenge(pending.verifier),
    }
    if pending.new_registration:
        if pending.client_id != DYNAMIC_AGENT_CLIENT:
            raise AuthError("invalid-client", "new registration must use dynamic entrypoint")
        if not agent_name:
            raise AuthError("invalid-client", "agent_name_hint is required for registration")
    else:
        if pending.client_id == DYNAMIC_AGENT_CLIENT:
            raise AuthError("invalid-client", "issued client_id is required for reauthorization")
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if login_hint:
            params["login_hint"] = login_hint
    if agent_name and pending.new_registration:
        params["agent_name_hint"] = agent_name
    params["ext_agent_host_id"] = pending.ext_agent_host_id
    if force_consent:
        params["force_reconsent"] = "true"
    return config.authorization_endpoint + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# --------------------------------------------------------------------------
# Credentials and protected local storage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    client_id: str
    email: str | None = None
    name: str | None = None
    sid: str | None = None

    @property
    def account_key(self) -> str:
        return hashlib.sha256(f"{self.issuer}\x00{self.client_id}\x00{self.subject}"
                              .encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Credentials:
    issuer: str
    subject: str
    client_id: str
    ext_agent_host_id: str
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_in: int = 3600
    scopes: frozenset[str] = field(default_factory=frozenset)
    saved_at: int = 0
    email: str | None = None
    name: str | None = None
    sid: str | None = field(default=None, repr=False)
    label: str | None = None
    schema: str = CREDENTIAL_SCHEMA

    @property
    def plan_usage(self) -> bool:
        return PLAN_SCOPE in self.scopes

    @property
    def expires_at(self) -> int:
        return int(self.saved_at) + int(self.expires_in)

    def expired(self, *, now: int | None = None, leeway: int = 30) -> bool:
        current = int(time.time()) if now is None else int(now)
        return current >= self.expires_at - leeway

    def identity(self) -> Identity:
        return Identity(self.issuer, self.subject, self.client_id,
                        self.email, self.name, self.sid)

    def with_label(self, label: str) -> "Credentials":
        return replace(self, label=label)

    def __repr__(self) -> str:
        return (f"Credentials(subject={self.subject!r}, client_id={self.client_id!r}, "
                f"plan_usage={self.plan_usage}, expires_at={self.expires_at}, "
                f"label={self.label!r}, tokens=[redacted])")

    def to_json(self) -> dict:
        return {
            "schema": self.schema,
            "email": self.email,
            "issuer": self.issuer,
            "subject": self.subject,
            "client_id": self.client_id,
            "ext_agent_host_id": self.ext_agent_host_id,
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scopes": sorted(self.scopes),
            "saved_at": self.saved_at,
            "name": self.name,
            "sid": self.sid,
            "label": self.label,
        }

    @classmethod
    def from_json(cls, raw: object) -> "Credentials":
        if not isinstance(raw, Mapping):
            raise AuthError("invalid-credentials", "credential record must be an object")
        if raw.get("schema") != CREDENTIAL_SCHEMA:
            raise AuthError("invalid-credentials", "unknown credential schema")
        required = ("issuer", "subject", "client_id", "ext_agent_host_id",
                    "id_token", "access_token")
        for key in required:
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise AuthError("invalid-credentials", f"{key} missing")
        expires_in = raw.get("expires_in", 3600)
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise AuthError("invalid-credentials", "expires_in invalid")
        return cls(
            issuer=raw["issuer"], subject=raw["subject"], client_id=raw["client_id"],
            ext_agent_host_id=raw["ext_agent_host_id"],
            id_token=raw["id_token"], access_token=raw["access_token"],
            refresh_token=raw.get("refresh_token") if isinstance(raw.get("refresh_token"), str) else None,
            token_type=str(raw.get("token_type") or "Bearer"),
            expires_in=expires_in, scopes=parse_scopes(raw.get("scopes")),
            saved_at=int(raw.get("saved_at") or 0),
            email=raw.get("email") if isinstance(raw.get("email"), str) else None,
            name=raw.get("name") if isinstance(raw.get("name"), str) else None,
            sid=raw.get("sid") if isinstance(raw.get("sid"), str) else None,
            label=raw.get("label") if isinstance(raw.get("label"), str) else None,
        )


_REPO_MARKERS = (".git", "nova3_team", "originals", "analysis")


def _assert_private_dir(path: Path) -> None:
    resolved = path.expanduser().resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists():
            raise AuthError("storage-in-repo", "credential store must not be in a git worktree")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, stat.S_IRWXU)


def _atomic_private_write(target: Path, payload: str) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class CredentialStore:
    """Per-account credential files, owner-only and atomically replaced."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        if root is None:
            base = os.environ.get("XDG_CONFIG_HOME")
            root = Path(base) / "nova3" / "chatgpt_planner" if base else \
                Path.home() / ".config" / "nova3" / "chatgpt_planner"
        self.root = Path(root).expanduser()
        _assert_private_dir(self.root)

    def _path(self, label: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:64]
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
        return self.root / f"{safe}-{digest}.json"

    def save(self, credentials: Credentials, *, label: str | None = None) -> str:
        label = label or credentials.label or credentials.subject[:16]
        record = replace(credentials, label=label)
        _atomic_private_write(self._path(label), canonical_json(record.to_json()))
        return label

    def load(self, label: str) -> Credentials | None:
        path = self._path(label)
        if not path.exists():
            return None
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise AuthError("insecure-permissions",
                            "credential record is accessible by other users")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuthError("invalid-credentials", "cannot read credential record") from exc
        return Credentials.from_json(raw)

    def labels(self) -> list[str]:
        labels = []
        for path in sorted(self.root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            label = raw.get("label")
            if isinstance(label, str):
                labels.append(label)
        return labels

    def delete(self, label: str) -> bool:
        path = self._path(label)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


@dataclass(frozen=True)
class ConsentRecord:
    account_key: str
    actor: str
    plan_usage: bool
    scopes: tuple[str, ...]
    granted_at: int
    version: str = CONSENT_VERSION

    def __repr__(self) -> str:
        return (f"ConsentRecord(account_key={self.account_key!r}, actor={self.actor!r}, "
                f"plan_usage={self.plan_usage}, granted_at={self.granted_at})")

    def to_json(self) -> dict:
        return {
            "version": self.version, "account_key": self.account_key,
            "actor": self.actor, "plan_usage": self.plan_usage,
            "scopes": list(self.scopes), "granted_at": self.granted_at,
        }

    @classmethod
    def from_json(cls, raw: object) -> "ConsentRecord":
        if not isinstance(raw, Mapping) or raw.get("version") != CONSENT_VERSION:
            raise AuthError("invalid-consent", "unknown consent record")
        if raw.get("plan_usage") is not True:
            raise AuthError("invalid-consent", "plan usage was not explicitly granted")
        return cls(
            account_key=str(raw.get("account_key") or ""),
            actor=str(raw.get("actor") or ""),
            plan_usage=True,
            scopes=tuple(parse_scopes(raw.get("scopes"))),
            granted_at=int(raw.get("granted_at") or 0),
        )


class ConsentStore:
    """Explicit, human-created plan-usage consent. Separate from sign-in."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._credentials = CredentialStore(root)

    def record(self, identity: Identity, *, actor: str, plan_usage: bool,
               granted_at: int | None = None) -> ConsentRecord:
        if not actor.strip():
            raise AuthError("invalid-consent", "consent requires a named human actor")
        if plan_usage is not True:
            raise AuthError("invalid-consent", "plan usage must be explicitly true")
        record = ConsentRecord(
            account_key=identity.account_key, actor=actor, plan_usage=True,
            scopes=tuple(sorted(set(DEFAULT_SCOPES))),
            granted_at=int(time.time()) if granted_at is None else int(granted_at),
        )
        _atomic_private_write(self._credentials._path(f"consent-{identity.account_key}"),
                              canonical_json(record.to_json()))
        return record

    def load(self, identity: Identity) -> ConsentRecord | None:
        path = self._credentials._path(f"consent-{identity.account_key}")
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return ConsentRecord.from_json(raw)
        except (OSError, ValueError) as exc:
            raise AuthError("invalid-consent", "cannot read consent record") from exc

    def revoke(self, identity: Identity) -> bool:
        try:
            self._credentials._path(f"consent-{identity.account_key}").unlink()
            return True
        except FileNotFoundError:
            return False


# --------------------------------------------------------------------------
# Token exchange / refresh / revocation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenResponse:
    access_token: str = field(repr=False)
    id_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_in: int = 3600
    scopes: frozenset[str] = field(default_factory=frozenset)
    raw: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (f"TokenResponse(scopes={sorted(self.scopes)}, "
                f"expires_in={self.expires_in}, tokens=[redacted])")

    @property
    def plan_usage(self) -> bool:
        return PLAN_SCOPE in self.scopes


def parse_token_response(resp: HttpResponse, *, require_refresh: bool = False) -> TokenResponse:
    if resp.status != 200:
        raise AuthError("token-request-failed", f"HTTP {resp.status}")
    body = resp.json()
    access = body.get("access_token")
    id_token = body.get("id_token")
    if not isinstance(access, str) or not access:
        raise AuthError("invalid-token-response", "access_token missing")
    if not isinstance(id_token, str) or not id_token:
        raise AuthError("invalid-token-response", "id_token missing")
    token_type = body.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("invalid-token-response", "unsupported token type")
    expires_in = body.get("expires_in")
    if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
        raise AuthError("invalid-token-response", "expires_in invalid")
    refresh = body.get("refresh_token")
    if refresh is not None and not isinstance(refresh, str):
        raise AuthError("invalid-token-response", "refresh_token invalid")
    if require_refresh and not refresh:
        raise AuthError("invalid-token-response", "refresh_token missing")
    scope_value = body.get("scope")
    if scope_value is not None and not isinstance(scope_value, str):
        raise AuthError("invalid-token-response", "scope invalid")
    return TokenResponse(
        access_token=access, id_token=id_token, refresh_token=refresh,
        token_type="Bearer", expires_in=expires_in,
        scopes=parse_scopes(scope_value), raw=body,
    )


class TokenClient:
    """Token endpoint operations. Refreshes are serialized per account."""

    def __init__(self, transport: Transport, *, config: OIDCConfig | None = None,
                 timeout: float = 30.0) -> None:
        self.transport = transport
        self.config = config or documented_config()
        self.timeout = timeout
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def exchange_code(self, *, client_id: str, code: str, verifier: str,
                      redirect_uri: str, require_refresh: bool = True) -> TokenResponse:
        if client_id == DYNAMIC_AGENT_CLIENT:
            raise AuthError("invalid-client", "the dynamic entrypoint cannot be exchanged")
        resp = self.transport.request(
            "POST", self.config.token_endpoint,
            form={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": DOCUMENTED_RESOURCE,
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        return parse_token_response(resp, require_refresh=require_refresh)

    def refresh(self, credentials: Credentials) -> TokenResponse:
        if not credentials.refresh_token:
            raise AuthError("refresh-unavailable", "no refresh token stored")
        with self._lock_for(credentials.identity().account_key):
            resp = self.transport.request(
                "POST", self.config.token_endpoint,
                form={
                    "grant_type": "refresh_token",
                    "client_id": credentials.client_id,
                    "refresh_token": credentials.refresh_token,
                    "resource": DOCUMENTED_RESOURCE,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            if resp.status != 200:
                code = "refresh-rejected"
                try:
                    body = resp.json()
                    err = str(body.get("error") or "")
                except AuthError:
                    err = ""
                if err in {"invalid_grant", "invalid_refresh_token", "token_expired",
                           "refresh_token_expired", "refresh_token_invalidated",
                           "refresh_token_reused"}:
                    code = "refresh-unusable"
                elif err == "invalid_client":
                    code = "invalid-client"
                raise AuthError(code, f"HTTP {resp.status}")
            return parse_token_response(resp, require_refresh=True)

    def revoke(self, credentials: Credentials, *, attempts: int = 2) -> bool:
        """Revoke the renewable session. Returns whether revocation was confirmed."""
        if not credentials.refresh_token:
            return False
        last_error = ""
        for attempt in range(max(1, attempts)):
            try:
                resp = self.transport.request(
                    "POST", self.config.revocation_endpoint,
                    form={
                        "token": credentials.refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": credentials.client_id,
                    },
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                )
            except AuthError as exc:
                last_error = exc.code
                if attempt + 1 < attempts:
                    time.sleep(0)
                continue
            if resp.status == 200:
                return True
            if 500 <= resp.status < 600:
                last_error = f"HTTP {resp.status}"
                continue
            last_error = f"HTTP {resp.status}"
            break
        raise AuthError("revoke-unconfirmed",
                        f"remote revocation not confirmed ({last_error})")


def refreshed_credentials(old: Credentials, token: TokenResponse, *,
                          now: int | None = None) -> Credentials:
    """Merge a refresh response with the existing registration (rotating token)."""
    if not token.refresh_token:
        raise AuthError("invalid-token-response", "refresh response lacked replacement token")
    return replace(
        old,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        id_token=token.id_token or old.id_token,
        token_type=token.token_type,
        expires_in=token.expires_in,
        scopes=token.scopes or old.scopes,
        saved_at=int(time.time()) if now is None else int(now),
    )


# --------------------------------------------------------------------------
# Models and readiness
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Model:
    slug: str
    display_name: str

    def __repr__(self) -> str:
        return f"Model(slug={self.slug!r}, display_name={self.display_name!r})"


def list_models(transport: Transport, credentials: Credentials, *,
                endpoint: str = DOCUMENTED_MODELS_ENDPOINT,
                timeout: float = 30.0) -> tuple[Model, ...]:
    """Account-specific model catalog, preserving server order."""
    if not credentials.plan_usage:
        raise AuthError("plan-scope-missing", "plan usage scope was not granted")
    resp = transport.request(
        "GET", endpoint,
        headers={"Authorization": f"Bearer {credentials.access_token}",
                 "Accept": "application/json"},
        timeout=timeout,
    )
    if resp.status != 200:
        raise AuthError("models-unavailable", f"HTTP {resp.status}")
    body = resp.json()
    models = body.get("models")
    if not isinstance(models, list):
        raise AuthError("invalid-models", "models must be a list")
    result: list[Model] = []
    for item in models:
        if not isinstance(item, Mapping) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        display = item.get("display_name")
        if isinstance(slug, str) and slug and isinstance(display, str) and display:
            result.append(Model(slug, display))
    return tuple(result)


@dataclass(frozen=True)
class ReadinessReport:
    state: AuthState
    code: str
    detail: str
    plan_usage_enabled: bool = False
    identity_verified: bool = False
    keys_verified: bool = False

    @property
    def ready(self) -> bool:
        return self.state is AuthState.READY

    def __repr__(self) -> str:
        return (f"ReadinessReport(state={self.state.value}, code={self.code!r}, "
                f"plan_usage_enabled={self.plan_usage_enabled})")


def assess_readiness(
    *,
    discovery: OIDCConfig | None,
    credentials: Credentials | None,
    consent: ConsentRecord | None,
    jwks_loaded: bool,
    registration_verified: bool,
    model_available: bool | None,
    now: int | None = None,
) -> ReadinessReport:
    """Honest, fail-closed readiness for planner inference.

    ``UNKNOWN`` means a required fact could not be established (for example
    JWKS was never loaded or the model catalog was never fetched).
    ``UNAVAILABLE`` means a required fact is known to be absent or denied.
    """
    current = int(time.time()) if now is None else int(now)
    if credentials is None:
        return ReadinessReport(AuthState.UNAVAILABLE, "not-signed-in",
                               "no ChatGPT registration is stored")
    if discovery is None:
        return ReadinessReport(AuthState.UNKNOWN, "discovery-unverified",
                               "issuer discovery has not been loaded")
    if not jwks_loaded:
        return ReadinessReport(AuthState.UNKNOWN, "jwks-unverified",
                               "signing keys have not been loaded")
    if not registration_verified:
        return ReadinessReport(AuthState.UNKNOWN, "registration-unverified",
                               "no verified registration for this account")
    if credentials.issuer != discovery.issuer:
        return ReadinessReport(AuthState.UNAVAILABLE, "issuer-mismatch",
                               "stored credentials belong to a different issuer")
    if not credentials.plan_usage:
        return ReadinessReport(AuthState.UNAVAILABLE, "plan-scope-missing",
                               "chatgpt.tokens.use.direct was not granted",
                               identity_verified=True)
    if consent is None or not consent.plan_usage:
        return ReadinessReport(AuthState.UNAVAILABLE, "plan-consent-missing",
                               "the operator has not explicitly consented to plan usage",
                               plan_usage_enabled=True, identity_verified=True)
    if credentials.expired(now=current) and not credentials.refresh_token:
        return ReadinessReport(AuthState.UNAVAILABLE, "credentials-expired",
                               "access token expired and no refresh token is stored",
                               plan_usage_enabled=True, identity_verified=True)
    if model_available is None:
        return ReadinessReport(AuthState.UNKNOWN, "models-unverified",
                               "the account model catalog has not been verified",
                               plan_usage_enabled=True, identity_verified=True)
    if model_available is False:
        return ReadinessReport(AuthState.UNAVAILABLE, "model-unavailable",
                               "no supported model for this account",
                               plan_usage_enabled=True, identity_verified=True)
    return ReadinessReport(AuthState.READY, "ready",
                           "verified identity, plan scope, consent and model",
                           plan_usage_enabled=True, identity_verified=True,
                           keys_verified=True)


# --------------------------------------------------------------------------
# Full authorization flow
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthResult:
    credentials: Credentials
    identity: Identity
    granted_scopes: frozenset[str]
    plan_usage: bool
    registration_client_id: str
    new_registration: bool

    def __repr__(self) -> str:
        return (f"AuthResult(identity={self.identity!r}, plan_usage={self.plan_usage}, "
                f"new_registration={self.new_registration}, tokens=[redacted])")


class ChatGPTAuthFlow:
    """Stateful, injectable Authorization Code + PKCE + OIDC loopback flow."""

    def __init__(
        self,
        transport: Transport,
        *,
        config: OIDCConfig,
        store: CredentialStore | None = None,
        pending: PendingStore | None = None,
        jwks: JwksCache | None = None,
        agent_name: str = "N.O.V.A. 3",
        ext_agent_host_id: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.transport = transport
        self.config = config
        self.store = store
        self.pending = pending or PendingStore(clock=clock)
        self.jwks = jwks or JwksCache()
        self.agent_name = agent_name
        self.ext_agent_host_id = ext_agent_host_id or new_host_id()
        self.clock = clock
        self.tokens = TokenClient(transport, config=config)

    def __repr__(self) -> str:
        return (f"ChatGPTAuthFlow(issuer={self.config.issuer!r}, "
                f"host_id={self.ext_agent_host_id!r})")

    def load_keys(self) -> None:
        self.jwks.load(self.transport, jwks_uri=self.config.jwks_uri)

    def refresh(self, credentials: Credentials, *, now: int | None = None) -> Credentials:
        """Refresh and re-verify the returned identity before replacing credentials.

        The refresh ID token has no pending ``nonce``; its signature, issuer,
        audience and expiry are still verified, and the subject must match the
        selected account.
        """
        if not self.jwks.loaded:
            raise AuthError("jwks-unverified", "signing keys must be loaded before refresh")
        token = self.tokens.refresh(credentials)
        claims = self._verify_with_refresh(
            token.id_token, audience=credentials.client_id, nonce=None, now=now)
        if claims.get("sub") != credentials.subject:
            raise AuthError("different-user", "refreshed identity differs from the selected account")
        return refreshed_credentials(credentials, token, now=now)

    def _verify_with_refresh(self, token: str, *, audience: str, nonce: str | None,
                             now: int | None) -> dict:
        try:
            return self.jwks.verify(token, issuer=self.config.issuer,
                                    audience=audience, nonce=nonce, now=now)
        except AuthError as exc:
            if exc.code != "unknown-signing-key":
                raise
            # Documented guidance: refresh keys when an unfamiliar kid appears.
            self.load_keys()
            return self.jwks.verify(token, issuer=self.config.issuer,
                                    audience=audience, nonce=nonce, now=now)

    def begin(
        self,
        *,
        redirect_uri: str,
        issued_client_id: str | None = None,
        account_label: str | None = None,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        id_token_hint: str | None = None,
        login_hint: str | None = None,
        force_consent: bool = False,
    ) -> AuthorizationRequest:
        exact_redirect = validate_redirect_uri(redirect_uri)
        new_registration = issued_client_id is None
        client_id = DYNAMIC_AGENT_CLIENT if new_registration else issued_client_id
        if not new_registration and client_id == DYNAMIC_AGENT_CLIENT:
            raise AuthError("invalid-client", "issued client_id required")
        pending = PendingAuthorization(
            state=new_state(), nonce=new_nonce(), verifier=new_pkce_verifier(),
            redirect_uri=exact_redirect, client_id=client_id,
            ext_agent_host_id=self.ext_agent_host_id,
            new_registration=new_registration,
            created_at=int(self.clock()), account_label=account_label,
            scopes=tuple(scopes),
        )
        self.pending.add(pending)
        url = build_authorization_url(
            config=self.config, pending=pending, id_token_hint=id_token_hint,
            login_hint=login_hint, agent_name=self.agent_name,
            force_consent=force_consent,
        )
        return AuthorizationRequest(url=url, pending=pending)

    def complete(self, callback_url: str, *, now: int | None = None) -> AuthResult:
        """Validate the callback and exchange the code. Single use."""
        parts = urllib.parse.urlsplit(callback_url)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1":
            raise AuthError("invalid-callback", "callback is not a loopback URL")
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        for key in ("state", "code", "client_id", "error"):
            if len(query.get(key, [])) > 1:
                raise AuthError("invalid-callback", f"duplicate {key} parameter")
        state = (query.get("state") or [None])[0]
        pending = self.pending.consume(state, now=now)
        if urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, parts.path, "", "")) != pending.redirect_uri:
            raise AuthError("redirect-mismatch", "callback URI differs from the pending exact URI")
        error = (query.get("error") or [None])[0]
        if error:
            if error == "access_denied":
                raise AuthError("access-denied", "the user declined authorization")
            raise AuthError("authorization-error", "authorization failed: " + redact(error))
        code = (query.get("code") or [None])[0]
        if not isinstance(code, str) or not code:
            raise AuthError("code-missing", "callback did not include a code")
        callback_client = (query.get("client_id") or [None])[0]
        if pending.new_registration:
            if not isinstance(callback_client, str) or not callback_client:
                raise AuthError("registration-incomplete", "issued client_id missing")
            if callback_client == DYNAMIC_AGENT_CLIENT:
                raise AuthError("registration-incomplete", "dynamic entrypoint was not replaced")
            issued_client = callback_client
        else:
            issued_client = pending.client_id
            if callback_client is not None and callback_client != issued_client:
                raise AuthError("client-mismatch", "callback client_id differs from the registration")

        token = self.tokens.exchange_code(
            client_id=issued_client, code=code, verifier=pending.verifier,
            redirect_uri=pending.redirect_uri,
            require_refresh="offline_access" in pending.scopes,
        )
        if not self.jwks.loaded:
            raise AuthError("jwks-unverified", "signing keys must be loaded before completing")
        claims = self._verify_with_refresh(
            token.id_token, audience=issued_client, nonce=pending.nonce, now=now)
        identity_scopes = validate_scopes(token.scopes, ("openid",))
        identity = Identity(
            issuer=claims["iss"], subject=claims["sub"], client_id=issued_client,
            email=claims.get("email"), name=claims.get("name"), sid=claims.get("sid"),
        )
        if pending.account_label:
            existing = self.store.load(pending.account_label) if self.store else None
            if existing and existing.subject != identity.subject:
                raise AuthError("different-user", "verified identity differs from the selected account")
        credentials = Credentials(
            issuer=identity.issuer, subject=identity.subject,
            client_id=issued_client,
            ext_agent_host_id=pending.ext_agent_host_id,
            id_token=token.id_token, access_token=token.access_token,
            refresh_token=token.refresh_token, token_type=token.token_type,
            expires_in=token.expires_in, scopes=identity_scopes | token.scopes,
            saved_at=int(self.clock()) if now is None else int(now),
            email=identity.email, name=identity.name, sid=identity.sid,
            label=pending.account_label,
        )
        if self.store and pending.account_label:
            self.store.save(credentials, label=pending.account_label)
        return AuthResult(
            credentials=credentials, identity=identity,
            granted_scopes=token.scopes, plan_usage=token.plan_usage,
            registration_client_id=issued_client,
            new_registration=pending.new_registration,
        )


__all__ = [
    "AuthError", "AuthState", "AuthResult", "AuthorizationRequest",
    "ChatGPTAuthFlow", "ConsentRecord", "ConsentStore", "CredentialStore",
    "Credentials", "HttpResponse", "Identity", "JwksCache", "Model", "OIDCConfig",
    "PendingAuthorization", "PendingStore", "ReadinessReport", "TokenClient",
    "TokenResponse", "Transport", "UrllibTransport", "assess_readiness",
    "b64url_decode", "b64url_encode", "build_authorization_url",
    "canonical_json", "documented_config", "list_models", "load_discovery",
    "make_redirect_uri", "new_host_id", "new_nonce", "new_pkce_verifier",
    "new_state", "parse_scopes", "parse_token_response", "pkce_challenge",
    "redact", "redacted_mapping", "refreshed_credentials", "validate_redirect_uri",
    "validate_scopes", "verify_id_token",
]
