"""Offline, fail-closed OpenCode/OpenRouter pilot gates. No inference transport.

Evidence is an operator-reviewed, short-lived metadata snapshot, not an API
response or proof that an endpoint was actually selected. No credential files,
prompts, subprocess output, or source files are telemetry inputs.

Schema-validation policy: the reviewed schema is audited in full before any
value is accepted (unused properties/branches/definitions included), untrusted
JSON rejects duplicate keys, and only a value/branch mismatch may make an
anyOf/oneOf/allOf branch count as a non-match. ``enum``/``const`` compare by
strict JSON type identity: Python's ``True == 1``, ``False == 0`` and
``1.0 == 1`` must never satisfy a reviewed literal. This is deliberately
stricter than JSON Schema's numeric equivalence for ``enum``/``const``;
``type: number`` still accepts both int and float. Rejecting a value is
fail-closed, so the stricter choice is safe for a gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any, Literal

MODEL = "deepseek/deepseek-v4.1-flash-1017"
ROUTER = "https://openrouter.ai/api/v1"
ENDPOINTS = f"{ROUTER}/models/{MODEL}/endpoints"
OPTIONS = frozenset({"order", "only", "allow_fallbacks", "zdr",
                     "require_parameters", "quantizations", "max_price"})
State = Literal["PASS", "FAIL", "UNKNOWN"]
MAX_EVIDENCE_AGE = 3600
REPO_ROOT = Path(__file__).resolve().parents[1]


class GatewayError(ValueError):
    """Messages are fixed identifiers, never untrusted input."""


def _object(value: Any, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise GatewayError("invalid_fields")
    return value


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise GatewayError("invalid_integer")
    return value


def _money(value: Any) -> Decimal:
    # Strings avoid binary floating point and reject JSON true/NaN/infinity.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,9})?", value):
        raise GatewayError("invalid_money")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise GatewayError("invalid_money") from None
    return result


@dataclass(frozen=True)
class GatewayConfig:
    model: str
    provider: str
    quantization: str
    context_required: int
    input_tokens: int
    output_tokens: int
    input_cap_per_million: Decimal
    output_cap_per_million: Decimal
    budget_usd: Decimal
    timeout_seconds: int

    @classmethod
    def parse(cls, data: Any) -> GatewayConfig:
        d = _object(data, {"schema", "model", "provider", "quantization",
                           "context_required", "input_tokens", "output_tokens",
                           "input_cap_per_million", "output_cap_per_million",
                           "budget_usd", "timeout_seconds"})
        if d["schema"] != "nova3.gateway-config/1" or d["model"] != MODEL:
            raise GatewayError("model_or_schema_denied")
        if d["provider"] != "wafer":
            raise GatewayError("provider_not_implemented")
        if d["quantization"] not in ("fp32", "fp16", "bf16", "fp8", "int8", "int4"):
            raise GatewayError("precision_must_be_explicit")
        context = _integer(d["context_required"], 1, 1_000_000)
        inputs = _integer(d["input_tokens"], 1, 100_000)
        outputs = _integer(d["output_tokens"], 1, 8192)
        if inputs + outputs > context:
            raise GatewayError("context_budget_exceeded")
        inc = _money(d["input_cap_per_million"])
        outc = _money(d["output_cap_per_million"])
        budget = _money(d["budget_usd"])
        if not 0 < budget <= Decimal("0.25") or inc <= 0 or outc <= 0:
            raise GatewayError("pilot_budget_denied")
        if (inputs * inc + outputs * outc) / 1_000_000 > budget:
            raise GatewayError("worst_case_cost_exceeds_budget")
        return cls(MODEL, "wafer", d["quantization"], context, inputs, outputs,
                   inc, outc, budget, _integer(d["timeout_seconds"], 1, 120))


@dataclass(frozen=True)
class Review:
    """Human review of exact CLI version/schema AND router option semantics.

    This is a trust boundary: CI fixtures are synthetic, never live approval.
    Schema hash alone does not establish OpenRouter option semantics.
    """
    version: str
    schema_sha256: str
    checked_at: int
    config_schema: dict = field(repr=False, compare=False)

    @classmethod
    def parse(cls, data: Any, schema_bytes: bytes) -> Review:
        d = _object(data, {"schema", "version", "schema_sha256", "checked_at",
                           "router_options", "native_auth_list_reviewed",
                           "semantics_reviewed"})
        if (d["schema"] != "nova3.gateway-review/1" or
                not isinstance(d["version"], str) or
                not re.fullmatch(r"\d+\.\d+\.\d+", d["version"])):
            raise GatewayError("unsupported_version")
        if (not isinstance(d["router_options"], list) or
                not all(isinstance(x, str) for x in d["router_options"]) or
                frozenset(d["router_options"]) != OPTIONS or
                d["native_auth_list_reviewed"] is not True or
                d["semantics_reviewed"] is not True):
            raise GatewayError("unreviewed_semantics")
        if hashlib.sha256(schema_bytes).hexdigest() != d["schema_sha256"]:
            raise GatewayError("schema_mismatch")
        try:
            schema = json.loads(schema_bytes, object_pairs_hook=_unique_object)
        except GatewayError:
            raise
        except (ValueError, UnicodeError):
            raise GatewayError("invalid_schema") from None
        if not isinstance(schema, dict) or "properties" not in schema:
            raise GatewayError("invalid_schema")
        return cls(d["version"], d["schema_sha256"],
                   _integer(d["checked_at"], 0, 10**11), schema)


_SCHEMA_KEYWORDS = frozenset({
    "$ref", "$schema", "$id", "$defs", "definitions", "title", "description",
    "default", "examples", "deprecated", "type", "properties",
    "additionalProperties", "required", "items", "enum", "const", "anyOf",
    "allOf", "oneOf"})
_JSON_TYPE_NAMES = frozenset({"object", "array", "string", "boolean", "integer",
                              "number", "null"})
_MISMATCH = "settings_schema_mismatch"


def _unique_object(pairs: list[tuple]) -> dict:
    """Reject duplicate keys so untrusted JSON has one unambiguous meaning."""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise GatewayError("duplicate_json_key")
        result[key] = value
    return result


def _json_identical(left: Any, right: Any) -> bool:
    """JSON type identity, not Python equality (True != 1, 1.0 != 1)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (left.keys() == right.keys() and
                all(_json_identical(left[key], right[key]) for key in left))
    if isinstance(left, list):
        return (len(left) == len(right) and
                all(_json_identical(a, b) for a, b in zip(left, right)))
    return left == right


def _resolve_reference(ref: Any, root: dict) -> Any:
    """Resolve a local JSON pointer only; remote/implicit network is denied."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise GatewayError("remote_schema_denied")
    target: Any = root
    try:
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError):
        raise GatewayError("schema_reference_missing") from None
    return target


def _audit_schema(schema: Any, root: dict, depth: int = 0) -> None:
    """Structurally audit every subschema, including unused ones.

    The value walk alone only inspects properties/branches the generated value
    reaches, so an unreviewed constraint in an unused definition could be
    silently accepted. Audit is bounded and does not follow ``$ref`` (targets
    live in the same root and are audited where defined), so recursive schemas
    remain usable for finite values.
    """
    if depth > 48:
        raise GatewayError("schema_depth_denied")
    if schema is True or schema is False:
        return
    if not isinstance(schema, dict):
        raise GatewayError("invalid_schema")
    if set(schema) - _SCHEMA_KEYWORDS:
        raise GatewayError("unsupported_schema_constraint")
    for key in ("properties", "$defs", "definitions"):
        if key in schema:
            children = schema[key]
            if not isinstance(children, dict):
                raise GatewayError("invalid_schema")
            for child in children.values():
                _audit_schema(child, root, depth + 1)
    for key in ("additionalProperties", "items"):
        if key in schema:
            _audit_schema(schema[key], root, depth + 1)
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if (not names or not all(isinstance(x, str) for x in names) or
                any(x not in _JSON_TYPE_NAMES for x in names)):
            raise GatewayError("invalid_schema")
    if "required" in schema:
        if (not isinstance(schema["required"], list) or
                not all(isinstance(x, str) for x in schema["required"])):
            raise GatewayError("invalid_schema")
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise GatewayError("invalid_schema")
            for child in schema[key]:
                _audit_schema(child, root, depth + 1)
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise GatewayError("invalid_schema")
    if "$ref" in schema:
        _resolve_reference(schema["$ref"], root)


def _validate_schema(value: Any, schema: Any, root: dict, depth: int = 0) -> None:
    """Small offline JSON Schema subset; unsupported constraints fail closed.

    Local refs only. No remote schema resolver or implicit network access.
    Review must still establish semantics for arbitrary provider option maps.
    """
    if depth > 48:
        raise GatewayError("schema_depth_denied")
    if depth == 0:
        _audit_schema(schema, root)
    if schema is True:
        return
    if schema is False or not isinstance(schema, dict):
        raise GatewayError("settings_schema_mismatch")
    supported = {"$ref", "$schema", "$id", "$defs", "definitions", "title",
                 "description", "default", "examples", "deprecated", "type",
                 "properties", "additionalProperties", "required", "items",
                 "enum", "const", "anyOf", "allOf", "oneOf"}
    if set(schema) - supported:
        raise GatewayError("unsupported_schema_constraint")
    for key in ("properties", "$defs", "definitions"):
        if key in schema and not isinstance(schema[key], dict):
            raise GatewayError("invalid_schema")
    for key in ("required", "type"):
        if key in schema:
            values = schema[key] if isinstance(schema[key], list) else [schema[key]]
            if (key == "type" and not values) or not all(isinstance(x, str) for x in values):
                raise GatewayError("invalid_schema")
            if key == "required" and not isinstance(schema[key], list):
                raise GatewayError("invalid_schema")
    for key in ("anyOf", "oneOf", "allOf", "enum"):
        if key in schema and (not isinstance(schema[key], list) or not schema[key]):
            raise GatewayError("invalid_schema")
    if "$ref" in schema:
        _validate_schema(value, _resolve_reference(schema["$ref"], root), root, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            matches = 0
            for choice in schema[key]:
                try:
                    _validate_schema(value, choice, root, depth + 1)
                    matches += 1
                except GatewayError as error:
                    # Only a value/branch mismatch is a non-match. Unsupported
                    # constraints, invalid/remote schemas and depth exhaustion
                    # are schema-level failures and must abort validation.
                    if error.args != (_MISMATCH,):
                        raise
            if ((key == "allOf" and matches != len(schema[key])) or
                    (key == "oneOf" and matches != 1) or
                    (key == "anyOf" and matches == 0)):
                raise GatewayError("settings_schema_mismatch")
    types = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "boolean": type(value) is bool,
             "integer": type(value) is int, "number": type(value) in (int, float),
             "null": value is None}
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(types.get(name, False) for name in names):
            raise GatewayError("settings_schema_mismatch")
    if "enum" in schema and not any(_json_identical(value, candidate) for candidate in schema["enum"]):
        raise GatewayError(_MISMATCH)
    if "const" in schema and not _json_identical(value, schema["const"]):
        raise GatewayError(_MISMATCH)
    if isinstance(value, dict):
        if set(schema.get("required", [])) - set(value):
            raise GatewayError("settings_schema_mismatch")
        for key, item in value.items():
            child = schema.get("properties", {}).get(key, schema.get("additionalProperties", True))
            _validate_schema(item, child, root, depth + 1)
    if isinstance(value, list) and "items" in schema:
        for item in value:
            _validate_schema(item, schema["items"], root, depth + 1)


@dataclass(frozen=True)
class EndpointEvidence:
    """Normalized operator attestation; missing properties remain None.

    Prices are USD per million tokens, converted from live API units during
    review. Balance/privacy must describe the SAME native OpenRouter account.
    """
    model: str
    provider: str
    quantization: str | None
    checked_at: int
    reachable: bool | None
    zdr: bool | None
    retention_reviewed: bool | None
    account_privacy_allows: bool | None
    tools: bool | None
    context: int | None
    input_per_million: Decimal | None
    output_per_million: Decimal | None
    balance_usd: Decimal | None

    @classmethod
    def parse(cls, data: Any) -> EndpointEvidence:
        d = _object(data, {"schema", "source", "model", "provider", "quantization",
                           "checked_at", "reachable", "zdr", "retention_reviewed",
                           "account_privacy_allows", "tools", "context",
                           "input_per_million", "output_per_million", "balance_usd"})
        if d["schema"] != "nova3.gateway-endpoint/1" or d["source"] != ENDPOINTS:
            raise GatewayError("unsupported_endpoint_evidence")
        if d["model"] != MODEL or d["provider"] != "wafer":
            raise GatewayError("endpoint_identity_drift")
        if d["quantization"] not in (None, "fp32", "fp16", "bf16", "fp8", "int8", "int4"):
            raise GatewayError("unsupported_precision")
        for key in ("reachable", "zdr", "retention_reviewed", "account_privacy_allows", "tools"):
            if d[key] is not None and type(d[key]) is not bool:
                raise GatewayError("invalid_boolean")
        context = None if d["context"] is None else _integer(d["context"], 1, 10**7)
        prices = [None if d[k] is None else _money(d[k]) for k in
                  ("input_per_million", "output_per_million", "balance_usd")]
        return cls(MODEL, "wafer", d["quantization"],
                   _integer(d["checked_at"], 0, 10**11), d["reachable"], d["zdr"],
                   d["retention_reviewed"], d["account_privacy_allows"], d["tools"],
                   context, *prices)


@dataclass(frozen=True)
class NativeStatus:
    executable: State = "UNKNOWN"
    version: str | None = None
    credential: State = "UNKNOWN"
    command: State = "UNKNOWN"


def _native_command(executable: str, args: list[str], timeout: int) -> tuple[int, bytes]:
    """No shell, no raw errors. Bound pipe output in memory and kill on excess.

    Only 32 KiB are retained; excess output kills the foreground CLI. No raw
    CLI log is written. The installed executable is still a trusted program.
    """
    with subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          shell=False, bufsize=0) as process:
        captured = bytearray()
        excess = threading.Event()

        def drain() -> None:
            assert process.stdout is not None
            while True:
                try:
                    chunk = process.stdout.read(4096)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                if len(captured) + len(chunk) > 32768:
                    excess.set()
                    process.kill()
                    return
                captured.extend(chunk)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=1)
        if reader.is_alive() or excess.is_set():
            raise GatewayError("native_output_excess")
        return process.returncode, bytes(captured)


def native_status(review: Review | None, *, enabled: bool = False,
                  timeout: int = 5) -> NativeStatus:
    """Opt-in local status only. Never read auth.json or credential env vars.

    auth list reports storage presence, NOT key validity or account balance.
    Only the exact reviewed version/status format is accepted.
    """
    if not enabled:
        return NativeStatus()
    executable = shutil.which("opencode")
    if executable is None:
        return NativeStatus(executable="FAIL")
    # cmd/bat wrappers invoke a shell on Windows even with shell=False.
    if Path(executable).suffix.lower() in (".cmd", ".bat"):
        return NativeStatus(executable="FAIL", command="FAIL")
    try:
        code, raw = _native_command(executable, ["--version"], timeout)
        version = raw.decode("utf-8", errors="strict").strip()
        if code or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            return NativeStatus(executable="PASS", command="FAIL")
        if review is None or version != review.version:
            return NativeStatus(executable="PASS", version=version)
        code, raw = _native_command(executable, ["auth", "list"], timeout)
        if code:
            return NativeStatus("PASS", version, "FAIL", "FAIL")
        text = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode("utf-8", errors="strict"))
        # Anchored native credential row, never substring matches in error text.
        found = re.search(r"(?m)^\s*[│|]?\s*[●•]\s+OpenRouter\s+api\s*$", text)
        empty = re.search(r"(?m)^\s*[└│|]?\s*0 credentials\s*$", text)
        credential: State = "PASS" if found else "FAIL" if empty else "UNKNOWN"
        return NativeStatus("PASS", version, credential, "PASS")
    except (OSError, UnicodeError, subprocess.SubprocessError, GatewayError):
        return NativeStatus(executable="PASS", command="FAIL")


def _fresh(timestamp: int, now: int) -> bool:
    return 0 <= now - timestamp <= MAX_EVIDENCE_AGE


def preflight(config: GatewayConfig, endpoint: EndpointEvidence | None,
              review: Review | None, native: NativeStatus, *, now: int) -> dict:
    """Deterministic summary. PASS means reviewed readiness, not paid success."""
    def check(value: bool | None) -> State:
        return "UNKNOWN" if value is None else "PASS" if value else "FAIL"

    e = endpoint
    gates: dict[str, State] = {
        "executable": native.executable, "native_status": native.command,
        "credential_presence": native.credential,
        "schema_version": "UNKNOWN" if review is None else check(native.version == review.version),
        "review_fresh": "UNKNOWN" if review is None else check(_fresh(review.checked_at, now)),
        "endpoint_fresh": "UNKNOWN" if e is None else check(_fresh(e.checked_at, now)),
    }
    for key in ("reachable", "zdr", "retention_reviewed", "account_privacy_allows", "tools"):
        gates[key] = check(None if e is None else getattr(e, key))
    gates["identity"] = check(None if e is None else (e.model == config.model and e.provider == config.provider))
    gates["precision"] = check(None if e is None or e.quantization is None else e.quantization == config.quantization)
    gates["context"] = check(None if e is None or e.context is None else e.context >= config.context_required)
    gates["input_rate"] = check(None if e is None or e.input_per_million is None else e.input_per_million <= config.input_cap_per_million)
    gates["output_rate"] = check(None if e is None or e.output_per_million is None else e.output_per_million <= config.output_cap_per_million)
    gates["balance"] = check(None if e is None or e.balance_usd is None else e.balance_usd >= config.budget_usd)
    status = "FAIL" if "FAIL" in gates.values() else "UNKNOWN" if "UNKNOWN" in gates.values() else "PASS"
    return {"schema": "nova3.gateway-status/1", "status": status,
            "model": MODEL, "provider": config.provider,
            "requested_quantization": config.quantization,
            "quantization": e.quantization if e else None,
            "evaluated_at": now,
            "metadata_checked_at": e.checked_at if e else None,
            "review_checked_at": review.checked_at if review else None,
            "opencode_version": native.version,
            "opencode_schema_sha256": review.schema_sha256 if review else None,
            "endpoint_selection": "UNKNOWN",  # Only an actual response can establish selection.
            "evidence_kind": "operator_reviewed_metadata" if e else "unavailable",
            "context": e.context if e else None,
            "input_per_million": str(e.input_per_million) if e and e.input_per_million is not None else None,
            "output_per_million": str(e.output_per_million) if e and e.output_per_million is not None else None,
            "input_cap_per_million": str(config.input_cap_per_million),
            "output_cap_per_million": str(config.output_cap_per_million),
            "budget_usd": str(config.budget_usd), "gates": gates,
            "paid_requests": 0, "credential_validity": "UNKNOWN",
            "source_matching": "NOT MATCHED"}


def opencode_settings(config: GatewayConfig, endpoint: EndpointEvidence,
                      review: Review, native: NativeStatus, *, now: int) -> dict:
    if preflight(config, endpoint, review, native, now=now)["status"] != "PASS":
        raise GatewayError("readiness_not_passed")
    # Prices are USD/M tokens. No sort/floor/implicit cheaper provider routing.
    settings = {"$schema": "https://opencode.ai/config.json",
            "model": f"openrouter/{MODEL}",
            "small_model": f"openrouter/{MODEL}",
            "enabled_providers": ["openrouter"], "share": "disabled",
            "autoupdate": False,
            "permission": {"*": "ask", "webfetch": "deny", "external_directory": "deny"},
            "provider": {"openrouter": {"options": {"baseURL": ROUTER},
                "models": {MODEL: {"options": {"provider": {
                    "order": [config.provider], "only": [config.provider],
                    "allow_fallbacks": False, "zdr": True,
                    "require_parameters": True,
                    "quantizations": [config.quantization],
                    "max_price": {"prompt": float(config.input_cap_per_million),
                                  "completion": float(config.output_cap_per_million)}}}}}}}}
    _validate_schema(settings, review.config_schema, review.config_schema)
    return settings


def _exclusive_json(path: Path, data: dict) -> None:
    """Never overwrite/merge existing settings, follow symlinks, or leak output."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise GatewayError("symlink_denied")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_user_settings(path: Path, settings: dict, *, home: Path | None = None) -> None:
    """Explicit user-local export. Existing OpenCode settings are never changed.

    Use ~/.config/opencode/opencode.json on all platforms; custom XDG locations
    and Windows ACL provisioning require manual review, not guessed paths.
    """
    home = Path.home() if home is None else home
    expected = home / ".config" / "opencode" / "opencode.json"
    if path.absolute() != expected.absolute():
        raise GatewayError("user_config_path_denied")
    # Never write provider routing into this source checkout, whatever the cwd.
    if expected.resolve().is_relative_to(REPO_ROOT):
        raise GatewayError("user_config_path_denied")
    _exclusive_json(path, settings)


def write_private_report(cache: Path, report: dict) -> None:
    # Validate the telemetry boundary even when a future caller appends fields.
    _object(report, {"schema", "status", "model", "provider", "requested_quantization",
        "quantization", "evaluated_at", "metadata_checked_at", "review_checked_at",
        "opencode_version", "opencode_schema_sha256", "endpoint_selection",
        "evidence_kind", "context", "input_per_million", "output_per_million",
        "input_cap_per_million", "output_cap_per_million", "budget_usd", "gates",
        "paid_requests", "credential_validity", "source_matching"})
    fixed = {"schema": "nova3.gateway-status/1", "model": MODEL, "provider": "wafer",
             "endpoint_selection": "UNKNOWN", "credential_validity": "UNKNOWN",
             "source_matching": "NOT MATCHED"}
    if any(report[k] != v for k, v in fixed.items()):
        raise GatewayError("unsafe_telemetry")
    gates = _object(report["gates"], {"executable", "native_status", "credential_presence",
        "schema_version", "review_fresh", "endpoint_fresh", "reachable", "zdr",
        "retention_reviewed", "account_privacy_allows", "tools", "identity", "precision",
        "context", "input_rate", "output_rate", "balance"})
    if report["status"] not in ("PASS", "FAIL", "UNKNOWN") or any(v not in ("PASS", "FAIL", "UNKNOWN") for v in gates.values()):
        raise GatewayError("unsafe_telemetry")
    if report["evidence_kind"] not in ("operator_reviewed_metadata", "unavailable"):
        raise GatewayError("unsafe_telemetry")
    for key in ("requested_quantization", "quantization"):
        if report[key] not in (None, "fp32", "fp16", "bf16", "fp8", "int8", "int4"):
            raise GatewayError("unsafe_telemetry")
    for key in ("evaluated_at", "metadata_checked_at", "review_checked_at", "context"):
        if report[key] is not None:
            _integer(report[key], 0, 10**11)
    for key in ("input_per_million", "output_per_million", "input_cap_per_million", "output_cap_per_million", "budget_usd"):
        if report[key] is not None:
            _money(report[key])
    if report["opencode_version"] is not None and not re.fullmatch(r"\d+\.\d+\.\d+", report["opencode_version"]):
        raise GatewayError("unsafe_telemetry")
    if report["opencode_schema_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", report["opencode_schema_sha256"]):
        raise GatewayError("unsafe_telemetry")
    _integer(report["paid_requests"], 0, 0)
    # Fixed filename under a dedicated private cache; no arbitrary overwrite.
    _exclusive_json(cache / "model-gateway" / "preflight.json", report)


@dataclass(frozen=True)
class PilotApproval:
    issue: int
    budget_usd: Decimal
    confirmed: bool = False
    max_attempts: int = 1
    retries: int = 0


class PilotSession:
    """One issue, one attempt, no retries, no transport or workspace mutation.

    Approval is supplied by a HUMAN caller, never derived from a green preflight.
    This process-local guard is not a scheduler, spend ledger or account cap.
    """
    def __init__(self, config: GatewayConfig, endpoint: EndpointEvidence,
                 review: Review, native: NativeStatus, approval: PilotApproval, *, now: int):
        if (approval.confirmed is not True or type(approval.issue) is not int or
                not 1 <= approval.issue <= 10**9 or type(approval.max_attempts) is not int or
                approval.max_attempts != 1 or type(approval.retries) is not int or approval.retries != 0 or
                approval.budget_usd != config.budget_usd):
            raise GatewayError("human_budget_approval_required")
        if preflight(config, endpoint, review, native, now=now)["status"] != "PASS":
            raise GatewayError("readiness_not_passed")
        self.config, self.issue, self.used = config, approval.issue, False
        self.endpoint, self.review, self.native = endpoint, review, native

    def reserve(self, *, now: int) -> dict:
        if self.used:
            raise GatewayError("attempt_exhausted")
        if preflight(self.config, self.endpoint, self.review, self.native, now=now)["status"] != "PASS":
            raise GatewayError("readiness_not_passed")
        self.used = True
        return {"issue": self.issue, "task": "synthetic-sort-unique-v1",
                "model": MODEL, "provider": "wafer", "attempt": 1, "retries": 0,
                "input_token_limit": self.config.input_tokens,
                "output_token_limit": self.config.output_tokens,
                "timeout_seconds": self.config.timeout_seconds,
                "budget_usd": str(self.config.budget_usd),
                "execution": "MANUAL_ONLY"}


def pilot_metrics(data: Any, config: GatewayConfig) -> dict:
    """Strict allowlist for normalized synthetic-task results; no output text.

    Both candidates use the same task ID and objective CI harness. Missing actual
    endpoint/cost/cache observations stay UNKNOWN; inferred cost is never actual.
    """
    d = _object(data, {"candidate", "task", "input_tokens", "output_tokens",
                       "cache_read_tokens", "cost_usd", "latency_ms", "endpoint",
                       "tool_success", "ci_passed", "attempt", "retries", "timed_out"})
    if d["candidate"] not in ("codex", MODEL) or d["task"] != "synthetic-sort-unique-v1":
        raise GatewayError("synthetic_task_required")
    inputs = _integer(d["input_tokens"], 0, config.input_tokens)
    outputs = _integer(d["output_tokens"], 0, config.output_tokens)
    cache = None if d["cache_read_tokens"] is None else _integer(d["cache_read_tokens"], 0, inputs)
    cost = None if d["cost_usd"] is None else _money(d["cost_usd"])
    latency = _integer(d["latency_ms"], 0, 86_400_000)
    if d["attempt"] != 1 or type(d["attempt"]) is not int or type(d["retries"]) is not int or d["retries"] != 0:
        raise GatewayError("attempt_exhausted")
    for key in ("tool_success", "ci_passed", "timed_out"):
        if type(d[key]) is not bool:
            raise GatewayError("invalid_boolean")
    allowed_endpoints = (None, "wafer") if d["candidate"] == MODEL else (None, "codex-native")
    if d["endpoint"] not in allowed_endpoints:
        raise GatewayError("selected_endpoint_drift")
    failed = (d["timed_out"] or latency > config.timeout_seconds * 1000 or
              not d["tool_success"] or not d["ci_passed"] or
              (cost is not None and cost > config.budget_usd))
    status = "FAIL" if failed else "UNKNOWN" if cost is None or cache is None or d["endpoint"] is None else "PASS"
    return {"schema": "nova3.gateway-pilot-metrics/1", "status": status,
            "candidate": d["candidate"], "task": d["task"], "input_tokens": inputs,
            "output_tokens": outputs, "cache_read_tokens": cache,
            "cost_usd": None if cost is None else str(cost), "latency_ms": latency,
            "endpoint": d["endpoint"], "tool_success": d["tool_success"],
            "ci_passed": d["ci_passed"], "timed_out": d["timed_out"],
            "attempt": 1, "retries": 0, "source_matching": "NOT MATCHED"}
