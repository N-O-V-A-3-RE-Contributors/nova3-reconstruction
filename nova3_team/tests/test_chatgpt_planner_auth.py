"""Offline tests for the planner-only SIWC authentication module.

No network, no real account, no paid inference. RSA keys and tokens are
synthetic fixtures generated for this test module only; they are not secrets
and are never accepted by the production issuer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from nova3_team import chatgpt_planner_auth as auth

# Synthetic 2048-bit RSA key (test-only, committed intentionally, not a secret).
N = ("tX0KAjSUeX6GESktU2TjE4y-baUOiRDWL67vlKWcD0KTOhwZ1I0uJmgxf7Wvcd7Zxmo1lumibsOnq-on"
     "-l6ucBg1Bo9VFSFbPGgXCfGq2oKXeXg7uak_ijhY_aJcqNyWHif-Xgjj9c_RVcNXyq-HPOtQMx4a1UIBW"
     "4Z9p0egEhzaoJHY7KU-IKPaCAzR4q7pbKNIaXMq7ZqDWSRVzGtXfahTTg2owzoUdaLTPBs1cV4WPR2nGi"
     "C1y4Iys-b_G3QS_DVAUQr5SM0gZ8MnUfGaoy-XnW5OIP-7WZcN_Y9iBU3K6uGCYH1TLWnHzs0745yCWaN"
     "vil-tGOEjk9BZffvTGw")
E = "AQAB"
D = ("OQ8dYPEgBwyY4PdVuUTcQa67xiQhLEW_WVGCIHVq2b9gI2JnWpSigE-81D6OcNoUFNYakB5XQ8S-yAV_2S8A"
     "X9y0eyIAKXsJwLWnHKi-wyFQt09uU6JbCD1L-T4dLLfheIyxY-YeqY9TbMfUJ7TE64m99rxNymOMorZndfr"
     "GYDrplBFgVIrismhRR1zwuQg5YWTxlOfI3OugYkhp1SlgLo9yf-QjpwTfMaty1HuT3MQAib8hQhO5Tp0E_f"
     "FaHBmEZkG5kWU0GOia3RzLTrKILUuOhXwN2nZqUnPMaAym4L-nCq7YS61EGi2EvpiZduRDVfhjNvXbF5sn"
     "Np98ZLMEWQ")
KID = "test-key-1"


def _int(value: str) -> int:
    return int.from_bytes(auth.b64url_decode(value), "big")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_sign(message: bytes) -> bytes:
    k = 256
    t = DIGEST_INFO + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), _int(D), _int(N)).to_bytes(k, "big")


def make_id_token(*, issuer: str = auth.DOCUMENTED_ISSUER, audience: str = "oaiapp_test-client",
                  nonce: str, subject: str = "sub-test-user", exp: int | None = None,
                  iat: int | None = None, kid: str = KID, email: str = "test@example.com",
                  mutate: bool = False) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": [audience],
        "nonce": nonce,
        "email": email,
        "iat": now if iat is None else iat,
        "exp": (now + 3600) if exp is None else exp,
        "sid": "sid_test",
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    signature = rsa_sign(signing_input.encode("ascii"))
    if mutate:
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    return f"{signing_input}.{_b64(signature)}"


def jwks_body(*, kid: str = KID) -> dict:
    return {"keys": [{"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": N, "e": E}]}


def discovery_body() -> dict:
    return {
        "issuer": auth.DOCUMENTED_ISSUER,
        "authorization_endpoint": auth.DOCUMENTED_AUTHORIZE_ENDPOINT,
        "token_endpoint": auth.DOCUMENTED_TOKEN_ENDPOINT,
        "jwks_uri": auth.DOCUMENTED_JWKS_URI,
        "revocation_endpoint": auth.DOCUMENTED_REVOCATION_ENDPOINT,
    }


class FakeTransport:
    """Deterministic transport. Records calls; never opens a socket."""

    def __init__(self, *, discovery: dict | None = None, jwks: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.routes: list[tuple[str, str, auth.HttpResponse]] = []
        self.add("GET", "openid-configuration",
                 auth.HttpResponse(200, json.dumps(discovery or discovery_body())))
        self.add("GET", "jwks.json", auth.HttpResponse(200, json.dumps(jwks or jwks_body())))

    def add(self, method: str, contains: str, response: auth.HttpResponse) -> None:
        self.routes.append((method, contains, response))

    def request(self, method, url, *, headers=None, form=None, json_body=None, timeout=30.0):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "form": dict(form or {}), "json": json_body})
        for m, needle, response in reversed(self.routes):
            if m == method and needle in url:
                return response
        raise AssertionError(f"unexpected request {method} {url}")

    def count(self, needle: str) -> int:
        return sum(1 for call in self.calls if needle in call["url"])


class AuthTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = auth.CredentialStore(Path(self.tmp.name) / "creds")
        self.transport = FakeTransport()
        self.config = auth.load_discovery(self.transport)
        self.flow = auth.ChatGPTAuthFlow(
            self.transport, config=self.config, store=self.store,
            ext_agent_host_id="urn:uuid:test-host",
        )
        self.flow.load_keys()

    def start(self, **kwargs):
        kwargs.setdefault("redirect_uri", "http://127.0.0.1:1455/auth/callback")
        return self.flow.begin(**kwargs)

    def token_response(self, *, nonce: str, client_id: str, subject: str = "sub-test-user",
                       scope: str = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
                       refresh: str = "refresh-1", expires_in: int = 3600,
                       id_token: str | None = None) -> auth.HttpResponse:
        body = {
            "access_token": "access-token-value",
            "refresh_token": refresh,
            "id_token": id_token or make_id_token(nonce=nonce, audience=client_id, subject=subject),
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": scope,
        }
        return auth.HttpResponse(200, json.dumps(body))

    def callback(self, pending, *, client_id=None, code="auth-code-1", **params) -> str:
        query = {"code": code, "state": pending.state}
        if client_id is not None:
            query["client_id"] = client_id
        query.update(params)
        return pending.redirect_uri + "?" + urllib.parse.urlencode(query)


class DiscoveryTests(unittest.TestCase):
    def test_loads_documented_configuration(self):
        transport = FakeTransport()
        config = auth.load_discovery(transport)
        self.assertEqual(config.issuer, auth.DOCUMENTED_ISSUER)
        self.assertEqual(config.token_endpoint, auth.DOCUMENTED_TOKEN_ENDPOINT)
        self.assertEqual(config.jwks_uri, auth.DOCUMENTED_JWKS_URI)
        self.assertIn("revocation_endpoint", config.raw)

    def test_rejects_wrong_issuer(self):
        body = discovery_body()
        body["issuer"] = "https://evil.example"
        with self.assertRaises(auth.AuthError) as ctx:
            auth.load_discovery(FakeTransport(discovery=body))
        self.assertEqual(ctx.exception.code, "issuer-mismatch")

    def test_rejects_non_https_endpoint(self):
        body = discovery_body()
        body["token_endpoint"] = "http://auth.openai.com/token"
        with self.assertRaises(auth.AuthError) as ctx:
            auth.load_discovery(FakeTransport(discovery=body))
        self.assertEqual(ctx.exception.code, "invalid-discovery")

    def test_documented_static_config_matches_fetched_docs(self):
        config = auth.documented_config()
        self.assertEqual(config.authorization_endpoint,
                         "https://auth.openai.com/api/accounts/authorize")
        self.assertEqual(config.token_endpoint, "https://auth.openai.com/api/accounts/oauth/token")


class RedirectTests(unittest.TestCase):
    def test_accepts_loopback_with_any_port(self):
        self.assertEqual(auth.validate_redirect_uri("http://127.0.0.1:54321/auth/callback"),
                         "http://127.0.0.1:54321/auth/callback")

    def test_rejects_localhost(self):
        with self.assertRaises(auth.AuthError) as ctx:
            auth.validate_redirect_uri("http://localhost:1455/auth/callback")
        self.assertEqual(ctx.exception.code, "invalid-redirect")

    def test_rejects_wrong_path(self):
        with self.assertRaises(auth.AuthError):
            auth.validate_redirect_uri("http://127.0.0.1:1455/callback")

    def test_rejects_query_and_fragment(self):
        for uri in ("http://127.0.0.1:1455/auth/callback?x=1",
                    "http://127.0.0.1:1455/auth/callback#frag"):
            with self.assertRaises(auth.AuthError):
                auth.validate_redirect_uri(uri)


class PkceTests(unittest.TestCase):
    def test_verifier_and_challenge_shape(self):
        verifier = auth.new_pkce_verifier()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        challenge = auth.pkce_challenge(verifier)
        self.assertEqual(challenge, _b64(hashlib.sha256(verifier.encode("ascii")).digest()))
        self.assertNotIn("=", challenge)

    def test_rejects_short_verifier(self):
        with self.assertRaises(auth.AuthError):
            auth.pkce_challenge("short")


class AuthorizationRequestTests(AuthTestBase):
    def test_new_registration_url_contains_documented_parameters(self):
        request = self.start()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)
        self.assertEqual(request.url.split("?")[0], auth.DOCUMENTED_AUTHORIZE_ENDPOINT)
        self.assertEqual(query["client_id"], [auth.DYNAMIC_AGENT_CLIENT])
        self.assertEqual(query["agent_name_hint"], ["N.O.V.A. 3"])
        self.assertEqual(query["ext_agent_host_id"], ["urn:uuid:test-host"])
        self.assertEqual(query["resource"], [auth.DOCUMENTED_RESOURCE])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:1455/auth/callback"])
        self.assertIn(auth.PLAN_SCOPE, query["scope"][0].split())
        self.assertEqual(len(query["state"][0]) >= 32, True)
        self.assertEqual(len(query["nonce"][0]) >= 32, True)

    def test_reauthorization_reuses_issued_client_and_omits_agent_name(self):
        request = self.start(issued_client_id="oaiapp_test-client", id_token_hint="hint.jwt.value")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)
        self.assertEqual(query["client_id"], ["oaiapp_test-client"])
        self.assertNotIn("agent_name_hint", query)
        self.assertEqual(query["id_token_hint"], ["hint.jwt.value"])

    def test_rejects_dynamic_entrypoint_as_issued_client(self):
        with self.assertRaises(auth.AuthError):
            self.start(issued_client_id=auth.DYNAMIC_AGENT_CLIENT)


class StateAndCallbackTests(AuthTestBase):
    def test_state_mismatch(self):
        request = self.start()
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued"))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(request.pending.redirect_uri + "?code=x&state=wrong")
        self.assertEqual(ctx.exception.code, "state-mismatch")

    def test_state_single_use_replay(self):
        request = self.start()
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued"))
        self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "state-replayed")

    def test_state_expiry(self):
        request = self.start()
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending), now=int(time.time()) + 3600)
        self.assertEqual(ctx.exception.code, "state-expired")

    def test_unknown_client_new_registration_requires_issued_id(self):
        request = self.start()
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending))
        self.assertEqual(ctx.exception.code, "registration-incomplete")
        self.assertEqual(self.transport.count("oauth/token"), 0)

    def test_new_registration_rejects_dynamic_client_in_callback(self):
        request = self.start()
        with self.assertRaises(auth.AuthError):
            self.flow.complete(self.callback(request.pending, client_id=auth.DYNAMIC_AGENT_CLIENT))

    def test_reauthorization_rejects_different_callback_client(self):
        request = self.start(issued_client_id="oaiapp_test-client")
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_other"))
        self.assertEqual(ctx.exception.code, "client-mismatch")

    def test_access_denied_stops_before_token_exchange(self):
        request = self.start()
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, code="", error="access_denied"))
        self.assertEqual(ctx.exception.code, "access-denied")
        self.assertEqual(self.transport.count("oauth/token"), 0)


class TokenValidationTests(AuthTestBase):
    def _complete(self, *, token_body=None, client_id="oaiapp_issued", scope=None):
        request = self.start()
        self.transport.add("POST", "oauth/token", token_body or self.token_response(
            nonce=request.pending.nonce, client_id=client_id))
        return self.flow.complete(self.callback(request.pending, client_id=client_id))

    def test_successful_new_registration(self):
        result = self._complete()
        self.assertTrue(result.plan_usage)
        self.assertEqual(result.identity.subject, "sub-test-user")
        self.assertEqual(result.registration_client_id, "oaiapp_issued")
        self.assertTrue(result.new_registration)
        self.assertNotIn("refresh-1", repr(result))

    def test_stored_credentials_are_owner_only_and_redacted(self):
        self._complete()
        label = "primary"
        creds = self.store.load("primary")
        # Complete did not persist without a label; save explicitly as the UI would.
        self.assertIsNone(creds)
        result = self._complete()
        label = self.store.save(result.credentials, label=label)
        path = self.store._path(label)
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        loaded = self.store.load(label)
        self.assertEqual(loaded.subject, "sub-test-user")
        self.assertNotIn("access-token-value", repr(loaded))
        self.assertNotIn("refresh-1", repr(loaded))
        self.assertEqual(loaded.scopes, result.granted_scopes)

    def test_missing_plan_scope_is_not_plan_usage(self):
        request = self.start()
        body = self.token_response(nonce=request.pending.nonce, client_id="oaiapp_issued",
                                   scope="openid profile email offline_access resource.invoke")
        self.transport.add("POST", "oauth/token", body)
        result = self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertFalse(result.plan_usage)
        with self.assertRaises(auth.AuthError) as ctx:
            auth.validate_scopes(result.granted_scopes, auth.DEFAULT_SCOPES)
        self.assertEqual(ctx.exception.code, "missing-scope")

    def test_missing_openid_scope_is_rejected(self):
        request = self.start()
        body = self.token_response(nonce=request.pending.nonce, client_id="oaiapp_issued",
                                   scope="profile email")
        self.transport.add("POST", "oauth/token", body)
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "missing-scope")

    def test_invalid_id_token_signature(self):
        request = self.start()
        bad = make_id_token(nonce=request.pending.nonce, audience="oaiapp_issued", mutate=True)
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued", id_token=bad))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "invalid-signature")

    def test_expired_id_token(self):
        request = self.start()
        expired = make_id_token(nonce=request.pending.nonce, audience="oaiapp_issued",
                                exp=int(time.time()) - 3600)
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued", id_token=expired))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "id-token-expired")

    def test_nonce_mismatch(self):
        request = self.start()
        other = make_id_token(nonce="different-nonce", audience="oaiapp_issued")
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued", id_token=other))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "nonce-mismatch")

    def test_audience_mismatch(self):
        request = self.start()
        wrong = make_id_token(nonce=request.pending.nonce, audience="oaiapp_other")
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued", id_token=wrong))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "audience-mismatch")

    def test_unknown_signing_key(self):
        request = self.start()
        token = make_id_token(nonce=request.pending.nonce, audience="oaiapp_issued", kid="unknown")
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.jwks.verify(token, issuer=auth.DOCUMENTED_ISSUER,
                                  audience="oaiapp_issued", nonce=request.pending.nonce)
        self.assertEqual(ctx.exception.code, "unknown-signing-key")

    def test_alg_none_is_rejected(self):
        header = _b64(json.dumps({"alg": "none", "kid": KID}).encode())
        claims = _b64(json.dumps({"iss": auth.DOCUMENTED_ISSUER, "sub": "s", "aud": "oaiapp_issued",
                                  "nonce": "n", "iat": int(time.time()),
                                  "exp": int(time.time()) + 60}).encode())
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.jwks.verify(f"{header}.{claims}.x", issuer=auth.DOCUMENTED_ISSUER,
                                  audience="oaiapp_issued", nonce="n")
        self.assertEqual(ctx.exception.code, "invalid-id-token")

    def test_different_user_than_selected_account_is_rejected(self):
        first = self.start(issued_client_id="oaiapp_issued", account_label="work")
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=first.pending.nonce, client_id="oaiapp_issued", subject="sub-first"))
        self.flow.complete(self.callback(first.pending, client_id="oaiapp_issued"))
        second = self.start(issued_client_id="oaiapp_issued", account_label="work")
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=second.pending.nonce, client_id="oaiapp_issued", subject="sub-second"))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.complete(self.callback(second.pending, client_id="oaiapp_issued"))
        self.assertEqual(ctx.exception.code, "different-user")


class RefreshRevokeTests(AuthTestBase):
    def _credentials(self) -> auth.Credentials:
        request = self.start()
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued"))
        return self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued")).credentials

    def test_refresh_rotates_tokens(self):
        creds = self._credentials()
        now = int(time.time())
        body = {"access_token": "access-2", "refresh_token": "refresh-2",
                "id_token": make_id_token(nonce="refresh-nonce", audience="oaiapp_issued",
                                          iat=now, exp=now + 3600),
                "token_type": "Bearer", "expires_in": 3600,
                "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"}
        self.transport.add("POST", "oauth/token", auth.HttpResponse(200, json.dumps(body)))
        token = self.flow.tokens.refresh(creds)
        updated = auth.refreshed_credentials(creds, token, now=now + 10)
        self.assertEqual(updated.access_token, "access-2")
        self.assertEqual(updated.refresh_token, "refresh-2")
        self.assertEqual(updated.client_id, "oaiapp_issued")
        self.assertTrue(updated.plan_usage)
        self.assertNotEqual(updated.refresh_token, creds.refresh_token)

    def test_refresh_rejects_different_user(self):
        creds = self._credentials()
        now = int(time.time())
        body = {"access_token": "access-2", "refresh_token": "refresh-2",
                "id_token": make_id_token(nonce="unused", audience="oaiapp_issued",
                                          subject="sub-other", iat=now, exp=now + 3600),
                "token_type": "Bearer", "expires_in": 3600,
                "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"}
        self.transport.add("POST", "oauth/token", auth.HttpResponse(200, json.dumps(body)))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.refresh(creds)
        self.assertEqual(ctx.exception.code, "different-user")

    def test_refresh_invalid_grant_is_terminal(self):
        creds = self._credentials()
        self.transport.add("POST", "oauth/token", auth.HttpResponse(
            400, json.dumps({"error": "invalid_grant"})))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.tokens.refresh(creds)
        self.assertEqual(ctx.exception.code, "refresh-unusable")

    def test_revoke_success(self):
        creds = self._credentials()
        self.transport.add("POST", "revoke", auth.HttpResponse(200, ""))
        self.assertTrue(self.flow.tokens.revoke(creds))

    def test_revoke_unconfirmed_on_server_error(self):
        creds = self._credentials()
        self.transport.add("POST", "revoke", auth.HttpResponse(503, ""))
        with self.assertRaises(auth.AuthError) as ctx:
            self.flow.tokens.revoke(creds, attempts=2)
        self.assertEqual(ctx.exception.code, "revoke-unconfirmed")


class ConsentAndReadinessTests(AuthTestBase):
    def _result(self):
        request = self.start()
        self.transport.add("POST", "oauth/token", self.token_response(
            nonce=request.pending.nonce, client_id="oaiapp_issued"))
        return self.flow.complete(self.callback(request.pending, client_id="oaiapp_issued"))

    def test_login_alone_is_not_plan_consent(self):
        result = self._result()
        report = auth.assess_readiness(
            discovery=self.config, credentials=result.credentials, consent=None,
            jwks_loaded=True, registration_verified=True, model_available=True)
        self.assertEqual(report.state, auth.AuthState.UNAVAILABLE)
        self.assertEqual(report.code, "plan-consent-missing")
        self.assertFalse(report.ready)

    def test_explicit_consent_then_ready(self):
        result = self._result()
        store = auth.ConsentStore(Path(self.tmp.name) / "creds")
        consent = store.record(result.identity, actor="owner", plan_usage=True)
        report = auth.assess_readiness(
            discovery=self.config, credentials=result.credentials, consent=consent,
            jwks_loaded=True, registration_verified=True, model_available=True)
        self.assertEqual(report.state, auth.AuthState.READY)
        self.assertTrue(report.ready)

    def test_unverified_keys_report_unknown(self):
        result = self._result()
        report = auth.assess_readiness(
            discovery=self.config, credentials=result.credentials, consent=None,
            jwks_loaded=False, registration_verified=True, model_available=None)
        self.assertEqual(report.state, auth.AuthState.UNKNOWN)
        self.assertEqual(report.code, "jwks-unverified")

    def test_consent_requires_named_actor(self):
        result = self._result()
        store = auth.ConsentStore(Path(self.tmp.name) / "creds")
        with self.assertRaises(auth.AuthError):
            store.record(result.identity, actor="  ", plan_usage=True)

    def test_consent_rejects_false_flag(self):
        result = self._result()
        store = auth.ConsentStore(Path(self.tmp.name) / "creds")
        with self.assertRaises(auth.AuthError):
            store.record(result.identity, actor="owner", plan_usage=False)


class ModelAndRedactionTests(AuthTestBase):
    def test_model_list_filters_and_preserves_order(self):
        creds = auth.Credentials(
            issuer=auth.DOCUMENTED_ISSUER, subject="sub", client_id="oaiapp_issued",
            ext_agent_host_id="host", id_token="id", access_token="access",
            scopes=frozenset(auth.DEFAULT_SCOPES), saved_at=int(time.time()),
        )
        body = {"models": [
            {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
            {"slug": "gpt-b", "display_name": "GPT B", "visibility": "list"},
        ]}
        self.transport.add("GET", "/v1/models", auth.HttpResponse(200, json.dumps(body)))
        models = auth.list_models(self.transport, creds)
        self.assertEqual([m.slug for m in models], ["gpt-a", "gpt-b"])

    def test_model_list_requires_plan_scope(self):
        creds = auth.Credentials(
            issuer=auth.DOCUMENTED_ISSUER, subject="sub", client_id="oaiapp_issued",
            ext_agent_host_id="host", id_token="id", access_token="access",
            scopes=frozenset({"openid"}), saved_at=int(time.time()),
        )
        with self.assertRaises(auth.AuthError):
            auth.list_models(self.transport, creds)

    def test_credential_store_refuses_repository_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            with self.assertRaises(auth.AuthError) as ctx:
                auth.CredentialStore(Path(tmp) / "creds")
            self.assertEqual(ctx.exception.code, "storage-in-repo")

    def test_errors_and_repr_are_redacted(self):
        message = redacted = auth.redact(
            "Bearer abcdefghijklmnop failed with refresh_token=refresh-1 and "
            "id_token=eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.signature12345")
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("refresh-1", redacted)
        self.assertIn("[redacted]", message)


if __name__ == "__main__":
    unittest.main()
