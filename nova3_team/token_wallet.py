"""Durable token wallet and evidence-only AI task routing.

This module is deliberately offline and passive.  It owns *money accounting*,
*capability evidence* and *slot routing* only:

* :class:`Wallet` is a stdlib ``sqlite3`` budget ledger with reserve-before-
  dispatch, settle-with-actual-usage, separate cached-input / retry / credit-fee
  accounting and atomic release.  Money is stored as integer micro-USD so no
  binary floating point ever touches a budget calculation.
* :class:`Router` filters slots by hard prerequisites and then applies a
  task-specific score with confidence bounds, weighted fairness and bounded
  cheaper-first canaries for low-risk work.
* Slot/lease attestations are *consumed*; they are never minted here.  Issue
  #126 owns cross-process locking, durable machine identity, leases, isolated
  worktrees and crash recovery.  Issue #135 owns GitHub queue admission.  This
  module must never be treated as authorization to launch a worker.
* Sanitized snapshots contain only provider/model identifiers and aggregate
  money/readiness numbers.  Keys, prompts, private paths and source content are
  structurally absent from the projection and additionally rejected by
  :func:`assert_sanitized`.

No network access, subprocess launch or model call exists in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
MICROS = 1_000_000
MAX_TOKENS = 2_000_000
MAX_RATE = 1_000_000_000          # micro-USD per million tokens
MAX_AMOUNT = 1_000_000_000_000    # micro-USD ($1,000,000)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[A-Fa-f0-9]{16,128}$")
_MACHINE = re.compile(r"^[A-Fa-f0-9][A-Fa-f0-9-]{15,127}$")

METERED = "metered_api"
SUBSCRIPTION = "subscription"
RISKS = frozenset({"low", "medium", "high"})
PROOF_LEVELS = frozenset({"tested", "verified"})
EVIDENCE_KINDS = frozenset({"accepted_ci", "object_match", "source_comparison",
                            "verified_outcome"})
STATES = frozenset({"reserved", "settled", "settled_unmeasured", "orphaned",
                    "released"})
ATTESTATION_SCHEMA = "nova3.slot-attestation/1"
SNAPSHOT_SCHEMA = "nova3.token-wallet-snapshot/1"


class WalletError(ValueError):
    """Fixed, non-secret error identifiers."""


class WalletConflict(WalletError):
    pass


class BudgetExceeded(WalletError):
    pass


class UnmeasuredCap(WalletError):
    """The wallet cannot prove a cap in a unit it can measure."""


class DuplicateAssignment(WalletError):
    pass


class TaskConflict(WalletError):
    pass


class PriceChanged(WalletError):
    """A price snapshot/rate changed; never silently substitute one."""


class UnknownUsage(WalletError):
    pass


class InvalidEvidence(WalletError):
    pass


class NotEligible(WalletError):
    pass


# --------------------------------------------------------------------------
# money / identifiers / time


def to_micros(value: str | Decimal | int) -> int:
    """Convert a USD amount to integer micro-USD without binary floats."""
    if isinstance(value, bool):
        raise WalletError("invalid_money")
    if isinstance(value, int):
        if value < 0 or value > MAX_AMOUNT:
            raise WalletError("invalid_money")
        return value * MICROS
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str):
        if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,6})?", value):
            raise WalletError("invalid_money")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise WalletError("invalid_money") from None
    else:
        raise WalletError("invalid_money")
    if not amount.is_finite() or amount < 0:
        raise WalletError("invalid_money")
    scaled = amount * MICROS
    if scaled != scaled.to_integral_value():
        raise WalletError("money_precision")
    micros = int(scaled)
    if micros > MAX_AMOUNT:
        raise WalletError("invalid_money")
    return micros


def format_usd(micros: int | None) -> str:
    if micros is None:
        return "UNKNOWN"
    if not isinstance(micros, int) or micros < 0:
        raise WalletError("invalid_money")
    return f"{micros // MICROS}.{micros % MICROS:06d}"


def _int(value: Any, field: str, *, minimum: int = 0,
         maximum: int = MAX_AMOUNT) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise WalletError("invalid_" + field)
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise WalletError("invalid_" + field)
    return value


def _identifier(value: Any, field: str, pattern: re.Pattern = _IDENTIFIER) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise WalletError("invalid_" + field)
    return value


def _day_start(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _month_start(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return int(dt.replace(day=1, hour=0, minute=0, second=0,
                          microsecond=0).timestamp())


def _ceil_div(numerator: int, denominator: int) -> int:
    if numerator <= 0:
        return 0
    return (numerator + denominator - 1) // denominator


def _token_charge(tokens: int, rate_per_million: int) -> int:
    return _ceil_div(tokens * rate_per_million, 1_000_000)


def _wilson_lower(successes: int, samples: int, z: float = 1.96) -> float:
    if samples <= 0:
        return 0.0
    p = successes / samples
    denominator = 1.0 + z * z / samples
    centre = (p + z * z / (2 * samples)) / denominator
    margin = z * math.sqrt(p * (1.0 - p) / samples) / denominator
    return max(0.0, min(1.0, centre - margin))


# --------------------------------------------------------------------------
# immutable request / evidence types


@dataclass(frozen=True)
class WalletPolicy:
    contributor_id: str
    mode: str
    daily_limit_micros: int | None
    monthly_limit_micros: int | None
    cap_enforcement_required: bool = True

    def __post_init__(self) -> None:
        _identifier(self.contributor_id, "contributor_id")
        if self.mode not in (METERED, SUBSCRIPTION):
            raise WalletError("invalid_wallet_mode")
        if not isinstance(self.cap_enforcement_required, bool):
            raise WalletError("invalid_cap_enforcement_required")
        if self.mode == METERED:
            if self.daily_limit_micros is None or self.monthly_limit_micros is None:
                raise WalletError("metered_limits_required")
            _int(self.daily_limit_micros, "daily_limit", minimum=1)
            _int(self.monthly_limit_micros, "monthly_limit", minimum=1)
        else:
            # Subscription quotas are not USD-poolable; claiming a provider cap
            # in dollars would be fabricated telemetry.
            if self.daily_limit_micros is not None or self.monthly_limit_micros is not None:
                raise WalletError("subscription_not_poolable")


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    issue: str
    branch: str
    provider: str
    model: str
    snapshot: str
    estimated_micros: int
    max_output_tokens: int
    max_context_tokens: int
    category: str
    risk: str
    privacy_class: str
    input_rate: int
    output_rate: int
    cached_rate: int
    required_tools: tuple[str, ...] = ()
    required_binaries: tuple[str, ...] = ()
    approval_role: str | None = None
    priority: int = 0
    slot_id: str = ""

    def __post_init__(self) -> None:
        _identifier(self.task_id, "task_id", _TASK)
        _identifier(self.issue, "issue")
        _identifier(self.branch, "branch")
        _identifier(self.provider, "provider", _MODEL)
        _identifier(self.model, "model", _MODEL)
        _identifier(self.snapshot, "snapshot")
        _int(self.estimated_micros, "estimate")
        _int(self.max_output_tokens, "max_output_tokens", minimum=1,
             maximum=MAX_TOKENS)
        _int(self.max_context_tokens, "max_context_tokens", minimum=1,
             maximum=MAX_TOKENS)
        if self.max_output_tokens > self.max_context_tokens:
            raise WalletError("output_exceeds_context")
        _identifier(self.category, "category")
        if self.risk not in RISKS:
            raise WalletError("invalid_risk")
        _identifier(self.privacy_class, "privacy_class")
        _int(self.input_rate, "input_rate", maximum=MAX_RATE)
        _int(self.output_rate, "output_rate", maximum=MAX_RATE)
        _int(self.cached_rate, "cached_rate", maximum=MAX_RATE)
        for values, name in ((self.required_tools, "required_tools"),
                             (self.required_binaries, "required_binaries")):
            if not isinstance(values, tuple):
                raise WalletError("invalid_" + name)
            for item in values:
                _identifier(item, name)
        if self.approval_role is not None:
            _identifier(self.approval_role, "approval_role")
        if type(self.priority) is not int or not 0 <= self.priority <= 1000:
            raise WalletError("invalid_priority")
        if self.slot_id:
            _identifier(self.slot_id, "slot_id")

    def reserved_ceiling(self) -> int:
        """Worst-case burst accepted for this task (fail closed on overage)."""
        return self.estimated_micros + _token_charge(
            self.max_context_tokens, self.input_rate) + _token_charge(
            self.max_output_tokens, self.output_rate)


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    task_id: str
    issue: str
    branch: str
    provider: str
    model: str
    snapshot: str
    reserved_micros: int
    status: str
    attempt: int
    created_at: int
    expires_at: int
    slot_id: str = ""

    def as_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "task_id": self.task_id,
            "issue": self.issue,
            "branch": self.branch,
            "provider": self.provider,
            "model": self.model,
            "snapshot": self.snapshot,
            "reserved_usd": format_usd(self.reserved_micros),
            "status": self.status,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "slot_id": self.slot_id,
        }


@dataclass(frozen=True)
class Usage:
    """Provider-reported usage.  Token fields, never prompts or output text."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    retry_input_tokens: int = 0
    retry_output_tokens: int = 0
    retry_count: int = 0
    credit_fee_micros: int = 0
    reported_total_micros: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "cached_input_tokens", "output_tokens",
                     "retry_input_tokens", "retry_output_tokens"):
            _int(getattr(self, name), name, maximum=MAX_TOKENS)
        if self.cached_input_tokens > self.input_tokens:
            raise WalletError("cached_input_exceeds_input")
        _int(self.retry_count, "retry_count", maximum=1000)
        _int(self.credit_fee_micros, "credit_fee", maximum=MAX_AMOUNT)
        if self.reported_total_micros is not None:
            _int(self.reported_total_micros, "reported_total", maximum=MAX_AMOUNT)


@dataclass(frozen=True)
class Settlement:
    reservation_id: str
    usage_state: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    retry_input_tokens: int | None
    retry_output_tokens: int | None
    retry_count: int
    credit_fee_micros: int
    computed_micros: int
    reported_micros: int | None
    charged_micros: int
    settled_at: int
    over_budget: bool

    def as_dict(self) -> dict:
        return {
            "usage_state": self.usage_state,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "retry_input_tokens": self.retry_input_tokens,
            "retry_output_tokens": self.retry_output_tokens,
            "retry_count": self.retry_count,
            "credit_fee_usd": format_usd(self.credit_fee_micros),
            "computed_usd": format_usd(self.computed_micros),
            "reported_usd": (None if self.reported_micros is None
                             else format_usd(self.reported_micros)),
            "charged_usd": format_usd(self.charged_micros),
            "over_budget": self.over_budget,
        }


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    provider: str
    model: str
    category: str
    kind: str
    success: bool
    proof_level: str
    wall_seconds: int
    billed_micros: int
    rework: bool
    verified_by: str
    observed_at: int

    def __post_init__(self) -> None:
        _identifier(self.evidence_id, "evidence_id")
        _identifier(self.provider, "provider", _MODEL)
        _identifier(self.model, "model", _MODEL)
        _identifier(self.category, "category")
        if self.kind not in EVIDENCE_KINDS:
            raise InvalidEvidence("evidence_kind_not_independent")
        if type(self.success) is not bool or type(self.rework) is not bool:
            raise InvalidEvidence("invalid_outcome_flag")
        if self.proof_level not in PROOF_LEVELS:
            raise InvalidEvidence("proof_level_required")
        _int(self.wall_seconds, "wall_seconds", maximum=10_000_000)
        _int(self.billed_micros, "billed_micros")
        _identifier(self.verified_by, "verified_by")
        _int(self.observed_at, "observed_at", maximum=2 ** 40)
        lowered = self.verified_by.lower()
        if lowered in {"self", "model", "agent", "ai"} or lowered in {
                self.provider.lower(), self.model.lower()}:
            raise InvalidEvidence("self_reported_evidence")


@dataclass(frozen=True)
class Capability:
    provider: str
    model: str
    category: str
    state: str                    # KNOWN / UNKNOWN
    samples: int
    successes: int
    success_lower: float
    median_wall_seconds: int | None
    billed_micros: int
    cost_per_verified_unit_micros: int | None
    rework_rate: float | None
    proven: bool

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "category": self.category,
            "state": self.state,
            "samples": self.samples,
            "successes": self.successes,
            "success_lower": round(self.success_lower, 4),
            "median_wall_seconds": self.median_wall_seconds,
            "cost_per_verified_unit_usd": (
                None if self.cost_per_verified_unit_micros is None
                else format_usd(self.cost_per_verified_unit_micros)),
            "rework_rate": (None if self.rework_rate is None
                            else round(self.rework_rate, 4)),
            "proven": self.proven,
        }


# --------------------------------------------------------------------------
# durable ledger


class Wallet:
    """SQLite-backed wallet; one instance owns one connection.

    Callers supply ``now`` (epoch seconds) explicitly so tests and recovery are
    deterministic.  Reservations are written in a single ``BEGIN IMMEDIATE``
    transaction together with their assignment row.
    """

    def __init__(self, path: str | os.PathLike = ":memory:",
                 *, contributor_id: str | None = None,
                 policy: WalletPolicy | None = None):
        self.path = str(path)
        self._connection = sqlite3.connect(self.path, timeout=10,
                                           isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL"
                                 if self.path != ":memory:" else "PRAGMA journal_mode = MEMORY")
        self._create_schema()
        if policy is not None:
            self.register_policy(policy)
        if contributor_id is not None:
            self._default_contributor = contributor_id
        else:
            rows = self._connection.execute(
                "SELECT contributor_id FROM wallets ORDER BY contributor_id").fetchall()
            self._default_contributor = rows[0]["contributor_id"] if len(rows) == 1 else None

    # -- schema ---------------------------------------------------------
    def _create_schema(self) -> None:
        # executescript() issues an implicit COMMIT, so it must not be wrapped
        # in the explicit transaction used for normal statements.
        self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallets (
                    contributor_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    daily_limit_micros INTEGER,
                    monthly_limit_micros INTEGER,
                    cap_enforcement_required INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    contributor_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    issue TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    input_rate INTEGER NOT NULL,
                    output_rate INTEGER NOT NULL,
                    cached_rate INTEGER NOT NULL,
                    estimated_micros INTEGER NOT NULL,
                    reserved_micros INTEGER NOT NULL,
                    max_output_tokens INTEGER NOT NULL,
                    max_context_tokens INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    privacy_class TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    slot_id TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    settled_at INTEGER,
                    over_budget INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS reservations_task
                    ON reservations(contributor_id, task_id, attempt);
                CREATE TABLE IF NOT EXISTS settlements (
                    reservation_id TEXT PRIMARY KEY REFERENCES reservations,
                    contributor_id TEXT NOT NULL,
                    usage_state TEXT NOT NULL,
                    input_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    output_tokens INTEGER,
                    retry_input_tokens INTEGER,
                    retry_output_tokens INTEGER,
                    retry_count INTEGER NOT NULL,
                    credit_fee_micros INTEGER NOT NULL,
                    input_charge_micros INTEGER NOT NULL,
                    cached_charge_micros INTEGER NOT NULL,
                    output_charge_micros INTEGER NOT NULL,
                    retry_charge_micros INTEGER NOT NULL,
                    computed_micros INTEGER NOT NULL,
                    reported_micros INTEGER,
                    charged_micros INTEGER NOT NULL,
                    over_budget INTEGER NOT NULL,
                    settled_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS settlements_time
                    ON settlements(contributor_id, settled_at);
                CREATE TABLE IF NOT EXISTS assignments (
                    issue TEXT PRIMARY KEY,
                    branch TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL UNIQUE,
                    slot_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    category TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    proof_level TEXT NOT NULL,
                    wall_seconds INTEGER NOT NULL,
                    billed_micros INTEGER NOT NULL,
                    rework INTEGER NOT NULL,
                    verified_by TEXT NOT NULL,
                    observed_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_lookup
                    ON evidence(provider, model, category);
        """)
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
        elif int(row["value"]) != SCHEMA_VERSION:
            raise WalletConflict("unsupported_schema_version")

    class _Txn:
        def __init__(self, connection: sqlite3.Connection):
            self.connection = connection

        def __enter__(self):
            self.connection.execute("BEGIN IMMEDIATE")
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self.connection.execute("COMMIT")
            else:
                self.connection.execute("ROLLBACK")
            return False

    def _transaction(self) -> "Wallet._Txn":
        return Wallet._Txn(self._connection)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Wallet":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- policy ---------------------------------------------------------
    def register_policy(self, policy: WalletPolicy, *, now: int = 0) -> None:
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM wallets WHERE contributor_id=?",
                (policy.contributor_id,)).fetchone()
            values = (policy.mode, policy.daily_limit_micros,
                      policy.monthly_limit_micros,
                      int(policy.cap_enforcement_required))
            if row is not None:
                existing = (row["mode"], row["daily_limit_micros"],
                            row["monthly_limit_micros"],
                            row["cap_enforcement_required"])
                if existing != values:
                    raise WalletConflict("wallet_policy_conflict")
                return
            self._connection.execute(
                "INSERT INTO wallets(contributor_id, mode, daily_limit_micros,"
                " monthly_limit_micros, cap_enforcement_required, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (policy.contributor_id, *values, now))

    def policy(self, contributor_id: str | None = None) -> WalletPolicy:
        contributor = self._contributor(contributor_id)
        row = self._connection.execute(
            "SELECT * FROM wallets WHERE contributor_id=?",
            (contributor,)).fetchone()
        if row is None:
            raise WalletConflict("wallet_not_registered")
        return WalletPolicy(
            row["contributor_id"], row["mode"], row["daily_limit_micros"],
            row["monthly_limit_micros"], bool(row["cap_enforcement_required"]))

    def _contributor(self, contributor_id: str | None) -> str:
        value = contributor_id or self._default_contributor
        if value is None:
            raise WalletConflict("contributor_required")
        _identifier(value, "contributor_id")
        return value

    # -- spend views ----------------------------------------------------
    def _spent(self, contributor: str, start: int) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(charged_micros),0) AS total FROM settlements"
            " WHERE contributor_id=? AND settled_at>=?",
            (contributor, start)).fetchone()
        return int(row["total"])

    def _reserved(self, contributor: str, start: int) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(reserved_micros),0) AS total FROM reservations"
            " WHERE contributor_id=? AND status IN ('reserved','orphaned')"
            " AND created_at>=?",
            (contributor, start)).fetchone()
        return int(row["total"])

    def spent_today(self, now: int, contributor_id: str | None = None) -> int:
        return self._spent(self._contributor(contributor_id), _day_start(now))

    def spent_month(self, now: int, contributor_id: str | None = None) -> int:
        return self._spent(self._contributor(contributor_id), _month_start(now))

    def reserved_today(self, now: int, contributor_id: str | None = None) -> int:
        return self._reserved(self._contributor(contributor_id), _day_start(now))

    def reserved_month(self, now: int, contributor_id: str | None = None) -> int:
        return self._reserved(self._contributor(contributor_id), _month_start(now))

    def can_reserve(self, request: TaskRequest, now: int,
                    contributor_id: str | None = None) -> tuple[bool, str]:
        contributor = self._contributor(contributor_id)
        policy = self.policy(contributor)
        if policy.mode == SUBSCRIPTION:
            if policy.cap_enforcement_required:
                return False, "unmeasured_cap"
            return True, "allowed_unmetered"
        ceiling = request.reserved_ceiling()
        spent_day = self._spent(contributor, _day_start(now))
        spent_month = self._spent(contributor, _month_start(now))
        reserved_day = self._reserved(contributor, _day_start(now))
        reserved_month = self._reserved(contributor, _month_start(now))
        if spent_day + reserved_day + ceiling > policy.daily_limit_micros:
            return False, "daily_cap"
        if spent_month + reserved_month + ceiling > policy.monthly_limit_micros:
            return False, "monthly_cap"
        return True, "ok"

    # -- reserve / settle / release ------------------------------------
    def reserve(self, request: TaskRequest, *, now: int,
                ttl_seconds: int = 3600,
                contributor_id: str | None = None) -> Reservation:
        contributor = self._contributor(contributor_id)
        policy = self.policy(contributor)
        _int(ttl_seconds, "ttl_seconds", minimum=1, maximum=7 * 24 * 3600)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT * FROM reservations WHERE contributor_id=? AND task_id=?"
                " ORDER BY attempt DESC LIMIT 1",
                (contributor, request.task_id)).fetchone()
            if existing is not None and existing["status"] in (
                    "reserved", "orphaned"):
                terms = (
                    existing["provider"], existing["model"],
                    existing["snapshot"], existing["input_rate"],
                    existing["output_rate"], existing["cached_rate"],
                    existing["max_output_tokens"],
                    existing["max_context_tokens"],
                    existing["estimated_micros"],
                    existing["reserved_micros"])
                wanted = (
                    request.provider, request.model, request.snapshot,
                    request.input_rate, request.output_rate,
                    request.cached_rate, request.max_output_tokens,
                    request.max_context_tokens, request.estimated_micros,
                    self._reserve_amount(request, policy))
                if terms != wanted:
                    raise TaskConflict("task_reserved_with_different_terms")
                return self._reservation(existing)

            issue = self._connection.execute(
                "SELECT task_id FROM assignments WHERE issue=?",
                (request.issue,)).fetchone()
            if issue is not None and issue["task_id"] != request.task_id:
                raise DuplicateAssignment("issue_already_assigned")
            branch = self._connection.execute(
                "SELECT task_id FROM assignments WHERE branch=?",
                (request.branch,)).fetchone()
            if branch is not None and branch["task_id"] != request.task_id:
                raise DuplicateAssignment("branch_already_assigned")

            if policy.mode == SUBSCRIPTION and policy.cap_enforcement_required:
                raise UnmeasuredCap("subscription_cap_not_measurable")
            if policy.mode == SUBSCRIPTION and request.estimated_micros:
                raise WalletError("unmetered_wallet_requires_zero_estimate")

            amount = self._reserve_amount(request, policy)
            if policy.mode == METERED:
                spent_day = self._spent(contributor, _day_start(now))
                spent_month = self._spent(contributor, _month_start(now))
                reserved_day = self._reserved(contributor, _day_start(now))
                reserved_month = self._reserved(contributor, _month_start(now))
                if spent_day + reserved_day + amount > policy.daily_limit_micros:
                    raise BudgetExceeded("daily_cap")
                if spent_month + reserved_month + amount > policy.monthly_limit_micros:
                    raise BudgetExceeded("monthly_cap")

            attempt_row = self._connection.execute(
                "SELECT COALESCE(MAX(attempt),0) AS n FROM reservations"
                " WHERE contributor_id=? AND task_id=?",
                (contributor, request.task_id)).fetchone()
            attempt = int(attempt_row["n"]) + 1
            reservation_id = "resv-" + _digest(
                f"{contributor}|{request.task_id}|{attempt}|{now}|{amount}")
            try:
                self._connection.execute(
                    "INSERT INTO reservations(reservation_id, contributor_id,"
                    " task_id, issue, branch, provider, model, snapshot,"
                    " input_rate, output_rate, cached_rate, estimated_micros,"
                    " reserved_micros, max_output_tokens, max_context_tokens,"
                    " category, risk, privacy_class, priority, slot_id, attempt,"
                    " status, created_at, expires_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (reservation_id, contributor, request.task_id, request.issue,
                     request.branch, request.provider, request.model,
                     request.snapshot, request.input_rate, request.output_rate,
                     request.cached_rate, request.estimated_micros, amount,
                     request.max_output_tokens, request.max_context_tokens,
                     request.category, request.risk, request.privacy_class,
                     request.priority, request.slot_id, attempt, "reserved",
                     now, now + ttl_seconds))
                self._connection.execute(
                    "INSERT INTO assignments(issue, branch, task_id,"
                    " reservation_id, slot_id, status, created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (request.issue, request.branch, request.task_id,
                     reservation_id, request.slot_id, "reserved", now))
            except sqlite3.IntegrityError as exc:
                raise DuplicateAssignment("assignment_conflict") from exc
            row = self._connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,)).fetchone()
            return self._reservation(row)

    @staticmethod
    def _reserve_amount(request: TaskRequest, policy: WalletPolicy) -> int:
        if policy.mode == SUBSCRIPTION:
            return 0
        return request.reserved_ceiling()

    @staticmethod
    def _reservation(row: sqlite3.Row) -> Reservation:
        return Reservation(
            row["reservation_id"], row["task_id"], row["issue"], row["branch"],
            row["provider"], row["model"], row["snapshot"],
            int(row["reserved_micros"]), row["status"], int(row["attempt"]),
            int(row["created_at"]), int(row["expires_at"]), row["slot_id"])

    def reservation_for_task(self, task_id: str,
                             contributor_id: str | None = None) -> Reservation | None:
        contributor = self._contributor(contributor_id)
        row = self._connection.execute(
            "SELECT * FROM reservations WHERE contributor_id=? AND task_id=?"
            " ORDER BY attempt DESC LIMIT 1",
            (contributor, task_id)).fetchone()
        return None if row is None else self._reservation(row)

    def assignment_for(self, *, issue: str | None = None,
                       branch: str | None = None) -> dict | None:
        if issue is None and branch is None:
            raise WalletError("issue_or_branch_required")
        if issue is not None:
            row = self._connection.execute(
                "SELECT * FROM assignments WHERE issue=?", (issue,)).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM assignments WHERE branch=?", (branch,)).fetchone()
        return None if row is None else dict(row)

    def assignments(self) -> list[dict]:
        rows = self._connection.execute(
            "SELECT issue, branch, task_id, reservation_id, slot_id, status,"
            " created_at FROM assignments ORDER BY issue").fetchall()
        return [dict(row) for row in rows]

    def settle(self, reservation_id: str, usage: Usage | None, *, now: int,
               rate: tuple[int, int, int] | None = None,
               snapshot: str | None = None,
               contributor_id: str | None = None) -> Settlement:
        """Settle with actual provider usage.

        ``usage=None`` means the provider did not report measurable usage.  The
        reservation amount is then charged provisionally and the state is
        reported ``unknown`` -- never silently treated as free.  A rate that no
        longer matches the reserved price snapshot raises :class:`PriceChanged`
        before any mutation.
        """
        contributor = self._contributor(contributor_id)
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=? AND"
                " contributor_id=?", (reservation_id, contributor)).fetchone()
            if row is None:
                raise WalletError("reservation_not_found")
            existing = self._connection.execute(
                "SELECT * FROM settlements WHERE reservation_id=?",
                (reservation_id,)).fetchone()
            if existing is not None:
                if existing["usage_state"] == "unknown" and usage is not None:
                    # The provisional charge cannot be replaced silently; an
                    # operator must reconcile it through an explicit path.
                    raise UnknownUsage("unmeasured_settlement_requires_reconciliation")
                return self._settlement(existing)
            if row["status"] not in ("reserved", "orphaned"):
                raise WalletError("reservation_not_settleable")
            if snapshot is not None and snapshot != row["snapshot"]:
                raise PriceChanged("price_snapshot_changed")
            if rate is not None:
                if not isinstance(rate, tuple) or len(rate) != 3:
                    raise WalletError("invalid_rate")
                for value in rate:
                    _int(value, "rate", maximum=MAX_RATE)
                if rate != (row["input_rate"], row["output_rate"],
                            row["cached_rate"]):
                    raise PriceChanged("price_snapshot_changed")

            charged = int(row["reserved_micros"])
            computed = charged
            reported = None
            input_charge = cached_charge = output_charge = retry_charge = 0
            if usage is None:
                usage_state = "unknown"
                token_fields = (None, None, None, None, None)
                retry_count = 0
                fee = 0
                status = "settled_unmeasured"
            else:
                usage_state = "measured"
                billed_input = usage.input_tokens - usage.cached_input_tokens
                input_charge = _token_charge(billed_input, int(row["input_rate"]))
                cached_charge = _token_charge(usage.cached_input_tokens,
                                              int(row["cached_rate"]))
                output_charge = _token_charge(usage.output_tokens,
                                              int(row["output_rate"]))
                retry_charge = _token_charge(
                    usage.retry_input_tokens + usage.retry_output_tokens,
                    max(int(row["input_rate"]), int(row["output_rate"])))
                computed = (input_charge + cached_charge + output_charge +
                            retry_charge + usage.credit_fee_micros)
                reported = usage.reported_total_micros
                charged = computed if reported is None else reported
                token_fields = (usage.input_tokens, usage.cached_input_tokens,
                                usage.output_tokens, usage.retry_input_tokens,
                                usage.retry_output_tokens)
                retry_count = usage.retry_count
                fee = usage.credit_fee_micros
                status = "settled"

            policy = self.policy(contributor)
            over_budget = False
            if policy.mode == METERED:
                spent_day = self._spent(contributor, _day_start(now))
                spent_month = self._spent(contributor, _month_start(now))
                over_budget = (
                    spent_day + charged > policy.daily_limit_micros or
                    spent_month + charged > policy.monthly_limit_micros)
            self._connection.execute(
                "INSERT INTO settlements(reservation_id, contributor_id,"
                " usage_state, input_tokens, cached_input_tokens, output_tokens,"
                " retry_input_tokens, retry_output_tokens, retry_count,"
                " credit_fee_micros, input_charge_micros, cached_charge_micros,"
                " output_charge_micros, retry_charge_micros, computed_micros,"
                " reported_micros, charged_micros, over_budget, settled_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (reservation_id, contributor, usage_state, *token_fields,
                 retry_count, fee, input_charge, cached_charge, output_charge,
                 retry_charge, computed, reported, charged, int(over_budget),
                 now))
            self._connection.execute(
                "UPDATE reservations SET status=?, settled_at=?, over_budget=?"
                " WHERE reservation_id=?",
                (status, now, int(over_budget), reservation_id))
            settled = self._connection.execute(
                "SELECT * FROM settlements WHERE reservation_id=?",
                (reservation_id,)).fetchone()
            return self._settlement(settled)

    @staticmethod
    def _settlement(row: sqlite3.Row) -> Settlement:
        return Settlement(
            row["reservation_id"], row["usage_state"], row["input_tokens"],
            row["cached_input_tokens"], row["output_tokens"],
            row["retry_input_tokens"], row["retry_output_tokens"],
            int(row["retry_count"]), int(row["credit_fee_micros"]),
            int(row["computed_micros"]), row["reported_micros"],
            int(row["charged_micros"]), int(row["settled_at"]),
            bool(row["over_budget"]))

    def release(self, reservation_id: str, *, now: int, reason: str,
                contributor_id: str | None = None) -> None:
        """Atomically abandon a reservation that never produced a charge."""
        contributor = self._contributor(contributor_id)
        _identifier(reason, "reason")
        with self._transaction():
            row = self._connection.execute(
                "SELECT status FROM reservations WHERE reservation_id=? AND"
                " contributor_id=?", (reservation_id, contributor)).fetchone()
            if row is None:
                raise WalletError("reservation_not_found")
            if row["status"] not in ("reserved", "orphaned"):
                raise WalletError("reservation_not_releasable")
            self._connection.execute(
                "UPDATE reservations SET status='released' WHERE reservation_id=?",
                (reservation_id,))
            self._connection.execute(
                "DELETE FROM assignments WHERE reservation_id=?",
                (reservation_id,))

    def complete(self, reservation_id: str, *, now: int,
                 contributor_id: str | None = None) -> None:
        """Close a settled task's assignment without refunding its spend."""
        contributor = self._contributor(contributor_id)
        with self._transaction():
            row = self._connection.execute(
                "SELECT status FROM reservations WHERE reservation_id=? AND"
                " contributor_id=?", (reservation_id, contributor)).fetchone()
            if row is None:
                raise WalletError("reservation_not_found")
            if row["status"] not in ("settled", "settled_unmeasured"):
                raise WalletError("reservation_not_completeable")
            self._connection.execute(
                "DELETE FROM assignments WHERE reservation_id=?",
                (reservation_id,))

    def recover(self, *, now: int) -> list[str]:
        """Mark crashed reservations orphaned; budget stays reserved.

        Releasing an orphan automatically would let a crash destroy the
        evidence of spend.  An operator explicitly calls :meth:`release` with a
        reason after reconciling the provider statement.
        """
        with self._transaction():
            rows = self._connection.execute(
                "SELECT reservation_id FROM reservations WHERE status='reserved'"
                " AND expires_at < ?", (now,)).fetchall()
            ids = [row["reservation_id"] for row in rows]
            for reservation_id in ids:
                self._connection.execute(
                    "UPDATE reservations SET status='orphaned' WHERE"
                    " reservation_id=?", (reservation_id,))
            return ids

    # -- evidence / capability -----------------------------------------
    def record_evidence(self, evidence: Evidence) -> None:
        if not isinstance(evidence, Evidence):
            raise InvalidEvidence("evidence_object_required")
        with self._transaction():
            try:
                self._connection.execute(
                    "INSERT INTO evidence(evidence_id, provider, model, category,"
                    " kind, success, proof_level, wall_seconds, billed_micros,"
                    " rework, verified_by, observed_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (evidence.evidence_id, evidence.provider, evidence.model,
                     evidence.category, evidence.kind, int(evidence.success),
                     evidence.proof_level, evidence.wall_seconds,
                     evidence.billed_micros, int(evidence.rework),
                     evidence.verified_by, evidence.observed_at))
            except sqlite3.IntegrityError as exc:
                raise WalletConflict("duplicate_evidence") from exc

    def capability(self, provider: str, model: str, category: str) -> Capability:
        rows = self._connection.execute(
            "SELECT * FROM evidence WHERE provider=? AND model=? AND category=?",
            (provider, model, category)).fetchall()
        if not rows:
            return Capability(provider, model, category, "UNKNOWN", 0, 0, 0.0,
                              None, 0, None, None, False)
        samples = len(rows)
        successes = sum(1 for row in rows if row["success"])
        verified_successes = sum(
            1 for row in rows if row["success"] and row["proof_level"] == "verified")
        walls = sorted(int(row["wall_seconds"]) for row in rows)
        median = walls[len(walls) // 2]
        billed = sum(int(row["billed_micros"]) for row in rows)
        rework = sum(1 for row in rows if row["rework"]) / samples
        lower = _wilson_lower(successes, samples)
        cost = (billed // verified_successes) if verified_successes else None
        proven = verified_successes >= 2 and lower >= 0.5
        return Capability(provider, model, category, "KNOWN", samples, successes,
                          lower, median, billed, cost, rework, proven)

    # -- sanitized snapshot --------------------------------------------
    def snapshot(self, *, now: int,
                 contributor_id: str | None = None) -> dict:
        contributor = self._contributor(contributor_id)
        policy = self.policy(contributor)
        day, month = _day_start(now), _month_start(now)
        if policy.mode == SUBSCRIPTION:
            spent_day = spent_month = reserved_day = reserved_month = None
            remaining_day = remaining_month = None
        else:
            spent_day = self._spent(contributor, day)
            spent_month = self._spent(contributor, month)
            reserved_day = self._reserved(contributor, day)
            reserved_month = self._reserved(contributor, month)
            remaining_day = max(0, policy.daily_limit_micros - spent_day)
            remaining_month = max(0, policy.monthly_limit_micros - spent_month)
        assignments = [
            {"issue": row["issue"], "branch": row["branch"],
             "task_id": row["task_id"], "slot_id": row["slot_id"],
             "status": row["status"]}
            for row in self._connection.execute(
                "SELECT issue, branch, task_id, slot_id, status FROM assignments"
                " ORDER BY issue").fetchall()]
        data = {
            "schema": SNAPSHOT_SCHEMA,
            "generated_at": now,
            "wallet": {
                "contributor_id": contributor,
                "mode": policy.mode,
                "metering": ("metered_usd" if policy.mode == METERED
                             else "unmetered_subscription"),
                "day": {
                    "limit_usd": (None if policy.daily_limit_micros is None
                                  else format_usd(policy.daily_limit_micros)),
                    "spent_usd": (None if spent_day is None
                                  else format_usd(spent_day)),
                    "reserved_usd": (None if reserved_day is None
                                     else format_usd(reserved_day)),
                    "remaining_usd": (None if remaining_day is None
                                      else format_usd(remaining_day)),
                },
                "month": {
                    "limit_usd": (None if policy.monthly_limit_micros is None
                                  else format_usd(policy.monthly_limit_micros)),
                    "spent_usd": (None if spent_month is None
                                  else format_usd(spent_month)),
                    "reserved_usd": (None if reserved_month is None
                                     else format_usd(reserved_month)),
                    "remaining_usd": (None if remaining_month is None
                                      else format_usd(remaining_month)),
                },
                "cap_enforcement": ("enforced" if policy.mode == METERED else
                                    ("unmeasurable_fail_closed"
                                     if policy.cap_enforcement_required
                                     else "unmeasured")),
            },
            "assignments": assignments,
            "slots": [],
        }
        return assert_sanitized(data)


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# routing


@dataclass(frozen=True)
class Slot:
    slot_id: str
    provider: str
    model: str
    snapshot: str
    metered: bool
    input_rate: int
    output_rate: int
    cached_rate: int
    privacy_classes: frozenset[str]
    tools: frozenset[str]
    binaries: frozenset[str]
    approval_roles: frozenset[str]
    max_context_tokens: int
    strength_class: str
    weight: int = 1

    def __post_init__(self) -> None:
        _identifier(self.slot_id, "slot_id")
        _identifier(self.provider, "provider", _MODEL)
        _identifier(self.model, "model", _MODEL)
        _identifier(self.snapshot, "snapshot")
        _bool(self.metered, "metered")
        _int(self.input_rate, "input_rate", maximum=MAX_RATE)
        _int(self.output_rate, "output_rate", maximum=MAX_RATE)
        _int(self.cached_rate, "cached_rate", maximum=MAX_RATE)
        for values, name in ((self.privacy_classes, "privacy_classes"),
                             (self.tools, "tools"), (self.binaries, "binaries"),
                             (self.approval_roles, "approval_roles")):
            if not isinstance(values, frozenset):
                raise WalletError("invalid_" + name)
            for item in values:
                _identifier(item, name)
        _int(self.max_context_tokens, "max_context_tokens", minimum=1,
             maximum=MAX_TOKENS)
        _identifier(self.strength_class, "strength_class")
        _int(self.weight, "weight", minimum=1, maximum=100)


@dataclass(frozen=True)
class SlotAttestation:
    """Attestation produced by issue #126; the wallet only consumes it.

    Fields mirror the information the wallet needs to prove a slot is currently
    leased.  This module never writes, renews or forges a lease and never owns
    process locking.
    """
    slot_id: str
    machine_id: str
    lease_id: str
    attested_at: int
    lease_expires_at: int
    process_locked: bool
    healthy: bool
    schema: str = ATTESTATION_SCHEMA
    branch: str | None = None

    def __post_init__(self) -> None:
        if self.schema != ATTESTATION_SCHEMA:
            raise WalletError("unsupported_attestation_schema")
        _identifier(self.slot_id, "slot_id")
        _identifier(self.machine_id, "machine_id", _MACHINE)
        if not isinstance(self.lease_id, str) or not re.fullmatch(
                r"[A-Za-z0-9._:-]{8,128}", self.lease_id):
            raise WalletError("invalid_lease_id")
        _int(self.attested_at, "attested_at", maximum=2 ** 40)
        _int(self.lease_expires_at, "lease_expires_at", maximum=2 ** 40)
        _bool(self.process_locked, "process_locked")
        _bool(self.healthy, "healthy")
        if self.branch is not None:
            _identifier(self.branch, "branch")

    def usable(self, now: int, max_age_seconds: int) -> tuple[bool, str]:
        if not self.healthy:
            return False, "unhealthy"
        if not self.process_locked:
            return False, "process_lock_missing"
        if self.lease_expires_at <= now:
            return False, "lease_expired"
        if self.attested_at > now:
            return False, "attestation_from_future"
        if now - self.attested_at > max_age_seconds:
            return False, "attestation_stale"
        return True, "ok"


@dataclass(frozen=True)
class SchedulerPolicy:
    max_active_slots: int = 1
    allow_same_machine_multi_slot: bool = False
    strength_tolerance: float = 0.05
    starvation_seconds: int = 1800
    max_attestation_age_seconds: int = 120

    def __post_init__(self) -> None:
        _int(self.max_active_slots, "max_active_slots", minimum=1, maximum=64)
        _bool(self.allow_same_machine_multi_slot,
              "allow_same_machine_multi_slot")
        if not isinstance(self.strength_tolerance, (int, float)) or not (
                0 <= float(self.strength_tolerance) <= 1):
            raise WalletError("invalid_strength_tolerance")
        _int(self.starvation_seconds, "starvation_seconds", maximum=30 * 24 * 3600)
        _int(self.max_attestation_age_seconds, "max_attestation_age_seconds",
             minimum=1, maximum=3600)


@dataclass(frozen=True)
class RouteRequest:
    task_id: str
    issue: str
    branch: str
    category: str
    risk: str
    privacy_class: str
    snapshot: str
    estimated_micros: int
    max_output_tokens: int
    max_context_tokens: int
    required_tools: tuple[str, ...] = ()
    required_binaries: tuple[str, ...] = ()
    approval_role: str | None = None
    priority: int = 0
    enqueued_at: int = 0
    canary: bool = False
    preferred_slot: str | None = None
    allow_failover: bool = False

    def __post_init__(self) -> None:
        _identifier(self.task_id, "task_id", _TASK)
        _identifier(self.issue, "issue")
        _identifier(self.branch, "branch")
        _identifier(self.category, "category")
        if self.risk not in RISKS:
            raise WalletError("invalid_risk")
        _identifier(self.privacy_class, "privacy_class")
        _identifier(self.snapshot, "snapshot")
        _int(self.estimated_micros, "estimate")
        _int(self.max_output_tokens, "max_output_tokens", minimum=1,
             maximum=MAX_TOKENS)
        _int(self.max_context_tokens, "max_context_tokens", minimum=1,
             maximum=MAX_TOKENS)
        if self.max_output_tokens > self.max_context_tokens:
            raise WalletError("output_exceeds_context")
        for values, name in ((self.required_tools, "required_tools"),
                             (self.required_binaries, "required_binaries")):
            if not isinstance(values, tuple):
                raise WalletError("invalid_" + name)
            for item in values:
                _identifier(item, name)
        if self.approval_role is not None:
            _identifier(self.approval_role, "approval_role")
        if type(self.priority) is not int or not 0 <= self.priority <= 1000:
            raise WalletError("invalid_priority")
        _int(self.enqueued_at, "enqueued_at", maximum=2 ** 40)
        _bool(self.canary, "canary")
        _bool(self.allow_failover, "allow_failover")
        if self.preferred_slot is not None:
            _identifier(self.preferred_slot, "preferred_slot")


@dataclass(frozen=True)
class Decision:
    task_id: str
    status: str                  # assigned / refused / unmatched
    slot_id: str | None
    reason: str
    score: float | None
    capability_state: str
    reasons: tuple[str, ...] = ()
    reservation_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "slot_id": self.slot_id,
            "reason": self.reason,
            "score": None if self.score is None else round(self.score, 4),
            "capability_state": self.capability_state,
            "reasons": list(self.reasons),
            "reservation_id": self.reservation_id,
        }


class Router:
    """Hard-prerequisite filter + evidence score + fair queue.

    Scheduling is a *proposal*.  :meth:`dispatch` additionally goes through the
    wallet's transactional reserve, so a stale plan cannot double-book.  The
    router never starts a worker and grants no authorization.
    """

    def __init__(self, wallet: Wallet, *, slots: Sequence[Slot],
                 attestations: Sequence[SlotAttestation] = (),
                 policy: SchedulerPolicy | None = None,
                 contributor_id: str | None = None,
                 approved_tasks: Iterable[str] = ()):
        self.wallet = wallet
        self.policy = policy or SchedulerPolicy()
        self.contributor_id = wallet._contributor(contributor_id)
        self.slots = {slot.slot_id: slot for slot in slots}
        if len(self.slots) != len(slots):
            raise WalletError("duplicate_slot_id")
        self.attestations = {att.slot_id: att for att in attestations}
        self.approved_tasks = set(approved_tasks)
        self.served = {slot_id: 0 for slot_id in self.slots}

    # -- views ----------------------------------------------------------
    def _busy_slots(self) -> dict[str, str]:
        busy: dict[str, str] = {}
        for assignment in self.wallet.assignments():
            if assignment["slot_id"]:
                busy[assignment["slot_id"]] = assignment["task_id"]
        for slot_id, att in self.attestations.items():
            if att.branch:
                busy.setdefault(slot_id, "checkout:" + att.branch)
        return busy

    def _active_assignments(self) -> list[dict]:
        return self.wallet.assignments()

    def _machine_count(self, busy: Mapping[str, str],
                       attestations: Mapping[str, SlotAttestation]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for slot_id in busy:
            att = attestations.get(slot_id)
            if att is not None:
                counts[att.machine_id] = counts.get(att.machine_id, 0) + 1
        return counts

    def _prerequisites(self, request: RouteRequest,
                       slot: Slot) -> list[str]:
        reasons = []
        if request.privacy_class not in slot.privacy_classes:
            reasons.append("privacy_class_denied")
        missing_tools = set(request.required_tools) - slot.tools
        if missing_tools:
            reasons.append("missing_tools")
        missing_binaries = set(request.required_binaries) - slot.binaries
        if missing_binaries:
            reasons.append("missing_binaries")
        if (request.approval_role is not None and
                request.approval_role not in slot.approval_roles):
            reasons.append("approval_role_unavailable")
        if request.snapshot != slot.snapshot:
            reasons.append("price_snapshot_mismatch")
        if request.max_context_tokens > slot.max_context_tokens:
            reasons.append("context_exceeded")
        return reasons

    def _score(self, request: RouteRequest, slot: Slot) -> tuple[float, Capability]:
        cap = self.wallet.capability(slot.provider, slot.model, request.category)
        if cap.state == "KNOWN":
            success = cap.success_lower
            speed = 1.0 / (1.0 + (cap.median_wall_seconds or 3600) / 3600.0)
        else:
            success, speed = 0.5, 0.5
        worst = max(1, self._worst_case(request, slot))
        affordability = 1.0 / (1.0 + worst / MICROS)
        return (0.60 * success + 0.25 * speed + 0.15 * affordability), cap

    def _worst_case(self, request: RouteRequest, slot: Slot) -> int:
        return (request.estimated_micros +
                _token_charge(request.max_context_tokens, slot.input_rate) +
                _token_charge(request.max_output_tokens, slot.output_rate))

    def _task_request(self, request: RouteRequest, slot: Slot) -> TaskRequest:
        return TaskRequest(
            task_id=request.task_id, issue=request.issue, branch=request.branch,
            provider=slot.provider, model=slot.model, snapshot=slot.snapshot,
            estimated_micros=request.estimated_micros,
            max_output_tokens=request.max_output_tokens,
            max_context_tokens=request.max_context_tokens,
            category=request.category, risk=request.risk,
            privacy_class=request.privacy_class, input_rate=slot.input_rate,
            output_rate=slot.output_rate, cached_rate=slot.cached_rate,
            required_tools=request.required_tools,
            required_binaries=request.required_binaries,
            approval_role=request.approval_role, priority=request.priority,
            slot_id=slot.slot_id)

    # -- planning -------------------------------------------------------
    def plan(self, *, now: int, requests: Sequence[RouteRequest],
             approved_tasks: Iterable[str] | None = None) -> list[Decision]:
        approvals = (self.approved_tasks if approved_tasks is None
                     else set(approved_tasks))
        ordered = sorted(requests, key=lambda r: (-r.priority, r.enqueued_at,
                                                  r.task_id))
        busy = self._busy_slots()
        decisions: list[Decision] = []
        machine_counts = self._machine_count(busy, self.attestations)
        fresh_assignments = len(self._active_assignments())
        for request in ordered:
            existing = self.wallet.assignment_for(issue=request.issue)
            if existing is not None and existing["task_id"] != request.task_id:
                decisions.append(Decision(
                    request.task_id, "refused", None, "duplicate_issue",
                    None, "UNKNOWN"))
                continue
            if existing is not None and existing["task_id"] == request.task_id:
                decisions.append(Decision(
                    request.task_id, "refused", existing["slot_id"],
                    "already_in_flight", None, "UNKNOWN"))
                continue
            if fresh_assignments >= self.policy.max_active_slots:
                decisions.append(Decision(
                    request.task_id, "refused", None, "slot_limit",
                    None, "UNKNOWN"))
                continue

            evaluated: list[tuple[float, Slot, Capability, list[str]]] = []
            preferred_missing = False
            budget_blocked = False
            for slot in self.slots.values():
                if request.preferred_slot is not None and (
                        slot.slot_id != request.preferred_slot):
                    if not request.allow_failover:
                        preferred_missing = True
                        continue
                if slot.slot_id in busy:
                    continue
                att = self.attestations.get(slot.slot_id)
                if att is None:
                    continue
                usable, reason = att.usable(now, self.policy.max_attestation_age_seconds)
                if not usable:
                    continue
                reasons = self._prerequisites(request, slot)
                if reasons:
                    continue
                if (not self.policy.allow_same_machine_multi_slot and
                        machine_counts.get(att.machine_id, 0) >= 1):
                    continue
                ok, budget_reason = self.wallet.can_reserve(
                    self._task_request(request, slot), now,
                    self.contributor_id)
                if not ok:
                    if budget_reason in ("daily_cap", "monthly_cap",
                                         "unmeasured_cap"):
                        budget_blocked = True
                    continue
                score, cap = self._score(request, slot)
                if request.risk == "high" and not (
                        cap.proven or (
                            request.task_id in approvals and
                            request.approval_role is not None and
                            request.approval_role in slot.approval_roles)):
                    continue
                evaluated.append((score, slot, cap, reasons))

            if not evaluated:
                if preferred_missing and not request.allow_failover:
                    reason = "preferred_unavailable"
                elif budget_blocked:
                    reason = "budget_cap"
                else:
                    reason = "no_eligible_slot"
                decisions.append(Decision(
                    request.task_id, "unmatched", None, reason, None, "UNKNOWN"))
                continue

            if request.canary:
                if request.risk != "low":
                    decisions.append(Decision(
                        request.task_id, "refused", None,
                        "canary_high_risk_denied", None, "UNKNOWN"))
                    continue
                # Bounded cheaper-first canary for low-risk exploration.
                evaluated.sort(key=lambda item: (
                    self._worst_case(request, item[1]), item[1].slot_id))
                chosen = evaluated[0]
            else:
                best_score = max(item[0] for item in evaluated)
                starved = (now - request.enqueued_at >=
                           self.policy.starvation_seconds)
                if starved:
                    similar = [item for item in evaluated
                               if item[0] == best_score]
                else:
                    similar = [item for item in evaluated
                               if best_score - item[0] <=
                               self.policy.strength_tolerance]
                similar.sort(key=lambda item: (
                    self.served.get(item[1].slot_id, 0) / item[1].weight,
                    busy.get(item[1].slot_id, ""),
                    self._worst_case(request, item[1]), item[1].slot_id))
                chosen = similar[0]

            score, slot, cap, _ = chosen
            busy[slot.slot_id] = request.task_id
            att = self.attestations[slot.slot_id]
            machine_counts[att.machine_id] = machine_counts.get(
                att.machine_id, 0) + 1
            fresh_assignments += 1
            decisions.append(Decision(
                request.task_id, "assigned", slot.slot_id, "selected",
                score, cap.state))
        return decisions

    def dispatch(self, *, now: int, request: RouteRequest,
                 approved_tasks: Iterable[str] | None = None) -> Decision:
        decision = self.plan(now=now, requests=[request],
                             approved_tasks=approved_tasks)[0]
        if decision.status != "assigned" or decision.slot_id is None:
            return decision
        slot = self.slots[decision.slot_id]
        try:
            reservation = self.wallet.reserve(
                self._task_request(request, slot), now=now,
                contributor_id=self.contributor_id)
        except (BudgetExceeded, DuplicateAssignment, TaskConflict,
                UnmeasuredCap, WalletError) as exc:
            return Decision(request.task_id, "refused", decision.slot_id,
                            str(exc) or "reserve_failed", decision.score,
                            decision.capability_state)
        self.served[decision.slot_id] = self.served.get(
            decision.slot_id, 0) + 1
        return Decision(request.task_id, "assigned", decision.slot_id,
                        "reserved", decision.score,
                        decision.capability_state,
                        reservation_id=reservation.reservation_id)

    # -- snapshot -------------------------------------------------------
    def snapshot(self, *, now: int) -> dict:
        data = self.wallet.snapshot(now=now, contributor_id=self.contributor_id)
        busy = self._busy_slots()
        wallet_assignments = {
            row["slot_id"]: row for row in self.wallet.assignments()
            if row["slot_id"]}
        rows = []
        for slot_id in sorted(self.slots):
            slot = self.slots[slot_id]
            att = self.attestations.get(slot_id)
            if att is None:
                readiness = "no_attestation"
            elif not att.healthy:
                readiness = "unhealthy"
            elif not att.process_locked:
                readiness = "lock_missing"
            elif att.lease_expires_at <= now:
                readiness = "lease_expired"
            elif now - att.attested_at > self.policy.max_attestation_age_seconds:
                readiness = "stale"
            else:
                readiness = "ready"
            assignment = busy.get(slot_id)
            caps = self._slot_capability(slot)
            walls = caps["walls"]
            if len(walls) == 0:
                eta = "unknown"
            elif len(walls) < 2:
                eta = "low"
            elif len(walls) >= 5 and max(walls) <= 2 * max(1, min(walls)):
                eta = "high"
            else:
                eta = "medium"
            rows.append({
                "slot_id": slot_id,
                "provider": slot.provider,
                "model": slot.model,
                "snapshot": slot.snapshot,
                "metered": slot.metered,
                "readiness": readiness,
                "assignment": self._assignment_view(slot_id, att,
                                                    wallet_assignments),
                "proof_level": caps["proof_level"],
                "cost_per_verified_unit_usd": caps["cost"],
                "fairness": {"served": self.served.get(slot_id, 0)},
                "eta_confidence": eta,
            })
        data["slots"] = rows
        return assert_sanitized(data)

    @staticmethod
    def _assignment_view(slot_id: str, att: SlotAttestation | None,
                         wallet_assignments: Mapping[str, dict]) -> dict | None:
        row = wallet_assignments.get(slot_id)
        if row is not None:
            return {"task_id": row["task_id"], "state": "in_flight"}
        if att is not None and att.branch:
            return {"task_id": None, "state": "checkout"}
        return None

    def _slot_capability(self, slot: Slot) -> dict:
        rows = self.wallet._connection.execute(
            "SELECT success, proof_level, wall_seconds, billed_micros FROM"
            " evidence WHERE provider=? AND model=?",
            (slot.provider, slot.model)).fetchall()
        walls = [int(row["wall_seconds"]) for row in rows]
        verified = sum(1 for row in rows if row["success"] and
                       row["proof_level"] == "verified")
        billed = sum(int(row["billed_micros"]) for row in rows)
        if verified:
            proof = "verified"
        elif any(row["proof_level"] == "tested" for row in rows):
            proof = "tested"
        else:
            proof = "unknown"
        return {"walls": walls, "proof_level": proof,
                "cost": (format_usd(billed // verified) if verified else None)}


# --------------------------------------------------------------------------
# sanitization


_FORBIDDEN_KEYS = ("key", "secret", "prompt", "password", "credential",
                   "authorization", "auth", "path", "home", "cwd", "command")
_FORBIDDEN_VALUE = re.compile(
    r"(/home/|/Users/|\\Users\\|/root/|/tmp/|[A-Za-z]:\\|sk-[A-Za-z0-9]|"
    r"Bearer\s|api_key|BEGIN [A-Z ]*PRIVATE KEY|\x00)")


def assert_sanitized(data: Mapping[str, Any]) -> dict:
    """Return a plain JSON-safe copy or raise on credential/path leakage."""
    def walk(value: Any, key: str) -> Any:
        lowered = key.lower()
        if any(token in lowered for token in _FORBIDDEN_KEYS):
            raise WalletError("snapshot_forbidden_field")
        if isinstance(value, Mapping):
            return {str(k): walk(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(item, key) for item in value]
        if isinstance(value, str):
            if _FORBIDDEN_VALUE.search(value):
                raise WalletError("snapshot_sensitive_value")
            return value
        if value is None or type(value) in (bool, int, float):
            return value
        raise WalletError("snapshot_unsupported_type")
    result = walk(data, "")
    # Round-trip through JSON to prove the projection is serialisable.
    return json.loads(json.dumps(result, sort_keys=True))
