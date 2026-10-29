"""Offline, evidence-gated Observatory forecasts. See FORECAST_POLICY.md.

Inputs are normalized snapshots, never GitHub payloads. All times are UTC epoch
seconds supplied by the caller. Nothing here reads files, clocks or services.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Policy:
    recent_seconds: float = 14 * 86400
    stale_seconds: float = 900
    min_samples: int = 5
    max_samples: int = 20
    max_records: int = 2000
    max_queue_tasks: int = 50
    max_forecast_seconds: float = 7 * 86400
    worker_cap: int = 4
    worker_exponent: float = 0.5


@dataclass(frozen=True)
class Task:
    id: str
    cohort: str
    kind: str = "source"  # infrastructure/source/matching/unknown (adapter has no classification)
    stage: str = "queued"  # queued/active/review/blocked/done
    stage_started_at: float | None = None
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullRequest:
    task_id: str
    observed_at: float
    state: str = "open"  # open/merged/closed
    draft: bool = False


@dataclass(frozen=True)
class CI:
    task_id: str
    observed_at: float
    state: str = "unknown"  # passed/pending/failed/unknown


@dataclass(frozen=True)
class Worker:
    id: str
    task_id: str | None
    observed_at: float
    state: str = "active"  # active/idle/blocked/offline


@dataclass(frozen=True)
class Session:
    id: str
    task_id: str
    started_at: float
    ceiling_seconds: float
    ended_at: float | None = None
    outcome: str = "running"  # running/completed/timeout/failed/cancelled


@dataclass(frozen=True)
class Attempt:
    id: str
    task_id: str
    cohort: str  # include size, toolchain/provider and throughput regime
    kind: str
    phase: str  # execution/review/matching; matching includes verification
    started_at: float
    ended_at: float
    outcome: str = "completed"  # completed/timeout/failed/cancelled
    number: int = 1
    matched_units: tuple[str, ...] = ()  # distinct stable ledger IDs, not bytes
    match_kind: str | None = None  # object/linked_window
    source_owned: bool = False
    evidence: str = ""  # reviewed evidence identifier, never a dump


@dataclass(frozen=True)
class Milestone:
    id: str
    state: str = "unknown"  # unknown/pending/blocked/in-progress/complete
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchingEvidence:
    verified_original_matches: int | None = None
    verified_count_evidence: str = ""
    inventory_functions: int | None = None
    inventory_confidence: str = "unknown"  # unknown/partial/verified
    inventory_evidence: str = ""
    cohort: str = ""
    throughput_calibrated: bool = False
    calibration_evidence: str = ""


@dataclass(frozen=True)
class Snapshot:
    now: float
    observed_at: float
    scope: str
    queue_complete: bool = False  # complete *within scope*, not whole project
    tasks: tuple[Task, ...] = ()
    prs: tuple[PullRequest, ...] = ()
    ci: tuple[CI, ...] = ()
    workers: tuple[Worker, ...] = ()
    sessions: tuple[Session, ...] = ()
    history: tuple[Attempt, ...] = ()
    milestones: tuple[Milestone, ...] = ()
    matching: MatchingEvidence = MatchingEvidence()


def _number(value, name, *, positive=False, limit=1e12):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < 0 or value > limit or not math.isfinite(value)
            or (positive and value == 0)):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number <= {limit}")


def _integer(value, name, *, positive=False, limit=10**9):
    _number(value, name, positive=positive, limit=limit)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")


def _text(value, name, *, empty=False):
    if (not isinstance(value, str) or len(value) > 500
            or (not value.strip() and (not empty or value != ""))):
        raise ValueError(f"{name} must be {'optional' if empty else 'nonempty'} text <= 500 characters")


def _choice(value, choices, name):
    if value not in choices:
        raise ValueError(f"invalid {name}: {value!r}")


def _validate(s, p):
    for name in ("recent_seconds", "stale_seconds", "max_forecast_seconds"):
        _number(getattr(p, name), name, positive=True, limit=365 * 86400)
    for name in ("min_samples", "max_samples", "max_records", "max_queue_tasks", "worker_cap"):
        _integer(getattr(p, name), name, positive=True, limit=10000)
    if p.min_samples < 3 or p.max_samples < p.min_samples or p.worker_cap > 32:
        raise ValueError("require >=3 samples, max_samples >= min_samples, worker_cap <=32")
    _number(p.worker_exponent, "worker_exponent", positive=True, limit=0.75)
    _number(s.now, "now")
    _number(s.observed_at, "observed_at")
    _text(s.scope, "scope")
    if type(s.queue_complete) is not bool:
        raise ValueError("queue_complete must be bool")
    collections = (s.tasks, s.prs, s.ci, s.workers, s.sessions, s.history, s.milestones)
    if sum(len(items) for items in collections) > p.max_records:
        raise ValueError("snapshot exceeds max_records; narrow the explicit scope")
    for items, key in ((s.tasks, "id"), (s.prs, "task_id"), (s.ci, "task_id"),
                       (s.workers, "id"), (s.sessions, "id"), (s.history, "id"), (s.milestones, "id")):
        ids = [getattr(item, key) for item in items]
        for value in ids:
            _text(value, key)
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate {key} in snapshot collection")
    for t in s.tasks:
        _text(t.cohort, "cohort")
        _choice(t.kind, ("source", "infrastructure", "matching", "unknown"), "task kind")
        _choice(t.stage, ("queued", "active", "review", "blocked", "done"), "task stage")
        if t.stage_started_at is not None:
            _number(t.stage_started_at, "stage_started_at")
        if len(t.blockers) > 100:
            raise ValueError("too many blockers")
        for b in t.blockers:
            _text(b, "blocker")
    for items, choices in ((s.prs, ("open", "merged", "closed")),
                           (s.ci, ("passed", "pending", "failed", "unknown")),
                           (s.workers, ("active", "idle", "blocked", "offline"))):
        for item in items:
            _number(item.observed_at, "observation timestamp")
            _choice(item.state, choices, "observation state")
            if item.task_id is not None:
                _text(item.task_id, "task_id")
    for pr in s.prs:
        if type(pr.draft) is not bool:
            raise ValueError("draft must be bool")
    for session in s.sessions:
        _text(session.task_id, "task_id")
        _number(session.started_at, "session started_at")
        _number(session.ceiling_seconds, "session ceiling", positive=True)
        if session.ended_at is not None:
            _number(session.ended_at, "session ended_at")
        _choice(session.outcome, ("running", "completed", "timeout", "failed", "cancelled"), "session outcome")
    for a in s.history:
        _text(a.task_id, "task_id")
        _text(a.cohort, "cohort")
        _choice(a.kind, ("source", "infrastructure", "matching"), "history kind")
        _choice(a.phase, ("execution", "review", "matching"), "history phase")
        _choice(a.outcome, ("completed", "timeout", "failed", "cancelled"), "history outcome")
        _number(a.started_at, "attempt started_at")
        _number(a.ended_at, "attempt ended_at")
        _integer(a.number, "attempt number", positive=True)
        if type(a.source_owned) is not bool or len(a.matched_units) > 100:
            raise ValueError("invalid source_owned or too many matched_units")
        for unit in a.matched_units:
            _text(unit, "matched unit")
        _text(a.evidence, "attempt evidence", empty=True)
        if a.match_kind is not None:
            _choice(a.match_kind, ("object", "linked_window"), "match_kind")
    for m in s.milestones:
        _choice(m.state, ("unknown", "pending", "blocked", "in-progress", "complete"), "milestone state")
        if len(m.evidence) > 100:
            raise ValueError("too much milestone evidence")
        for e in m.evidence:
            _text(e, "milestone evidence")
    match = s.matching
    for name in ("verified_original_matches", "inventory_functions"):
        value = getattr(match, name)
        if value is not None:
            _integer(value, name)
    _choice(match.inventory_confidence, ("unknown", "partial", "verified"), "inventory_confidence")
    for name in ("verified_count_evidence", "inventory_evidence", "calibration_evidence", "cohort"):
        _text(getattr(match, name), name, empty=True)
    if type(match.throughput_calibrated) is not bool:
        raise ValueError("throughput_calibrated must be bool")


def _fresh(timestamp, s, p):
    return 0 <= s.now - timestamp <= p.stale_seconds


def _range(values):
    """Linearly interpolated empirical p20/p50/p80; not a confidence interval."""
    values = sorted(values)
    def quantile(q):
        pos = (len(values) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)
    return dict(zip(("p20", "median", "p80"), (quantile(q) for q in (0.2, 0.5, 0.8))))


def _confidence(n, p):
    return "medium" if n >= max(10, 2 * p.min_samples) else "low"


def _history(s, p):
    groups = defaultdict(list)
    for a in s.history:
        groups[(a.task_id, a.phase)].append(a)
    samples = defaultdict(list)
    censored = Counter()
    excluded = Counter()
    for attempts in groups.values():
        attempts.sort(key=lambda a: (a.number, a.id))
        first, last = attempts[0], attempts[-1]
        key = (first.kind, first.cohort, first.phase)
        if max(a.ended_at for a in attempts) < s.now - p.recent_seconds:
            excluded["old_history"] += 1
            continue
        valid = (all(a.started_at < a.ended_at <= s.now for a in attempts)
                 and first.started_at >= s.now - p.recent_seconds
                 and [a.number for a in attempts] == list(range(1, len(attempts) + 1))
                 and all((a.kind, a.cohort, a.phase) == key for a in attempts)
                 and all(a.outcome != "completed" for a in attempts[:-1])
                 and all(a.ended_at <= b.started_at for a, b in zip(attempts, attempts[1:])))
        if not valid:
            # Poison every involved cohort rather than silently bias its successes.
            for k in {(a.kind, a.cohort, a.phase) for a in attempts}:
                censored[k] += 1
            excluded["invalid_or_incomplete_history"] += 1
        elif last.outcome != "completed":
            censored[key] += 1
            excluded["unresolved_censored_history"] += 1
        else:
            samples[key].append((last.ended_at, last.ended_at - first.started_at, last))
    for key in samples:
        samples[key].sort(key=lambda row: (row[0], row[2].task_id))
        samples[key] = samples[key][-p.max_samples:]
    return samples, censored, excluded


def forecast(snapshot: Snapshot, policy: Policy = Policy()) -> dict:
    """Return JSON-compatible telemetry; never turn missing evidence into zero.

    Invalid numeric/enum/bounded input raises ValueError. Invalid chronology in
    history is excluded and gates its cohort; invalid session chronology yields
    unknown elapsed time. Neither is treated as a successful completion.
    """
    s, p = snapshot, policy
    _validate(s, p)
    fresh = _fresh(s.observed_at, s, p)
    samples, censored, excluded = _history(s, p)
    def result(state="unknown", reasons=(), **fields):
        return {"state": state, "confidence": "none", "sample_size": 0,
                "reasons": list(reasons), "as_of": s.now,
                "observed_at": s.observed_at, **fields}

    milestones = []
    for m in s.milestones:
        reasons = []
        state = m.state
        if not fresh:
            state, reasons = "unknown", ["stale_or_future_snapshot"]
        elif not m.evidence:
            state, reasons = "unknown", ["missing_milestone_evidence"]
        milestones.append({"id": m.id, "state": state, "evidence": list(m.evidence), "reasons": reasons})
    states = {m["state"] for m in milestones}
    setup_state = next((state for state in ("blocked", "unknown", "in-progress", "pending") if state in states), "complete" if states else "unknown")
    setup = result(setup_state, ("no_setup_milestones",) if not states else (), milestones=milestones)

    sessions = []
    for session in s.sessions:
        end = s.now if session.ended_at is None else session.ended_at
        valid = (session.started_at <= end <= s.now
                 and (session.outcome == "running") == (session.ended_at is None))
        elapsed = end - session.started_at if valid and (fresh or session.ended_at is not None) else None
        reasons = ([] if valid else ["invalid_session_chronology"])
        if not fresh:
            reasons.append("stale_or_future_snapshot")
        sessions.append(result("observed" if fresh and valid else "unknown",
                               reasons,
                               id=session.id, task_id=session.task_id, outcome=session.outcome,
                               started_at=session.started_at, ended_at=session.ended_at,
                               elapsed_seconds=elapsed, ceiling_seconds=session.ceiling_seconds,
                               remaining_budget_seconds=max(0, session.ceiling_seconds - elapsed) if elapsed is not None else None,
                               ceiling_exceeded=elapsed > session.ceiling_seconds if elapsed is not None else None,
                               completion_eta_seconds=None))
    prs = {pr.task_id: pr for pr in s.prs}
    ci = {check.task_id: check for check in s.ci}
    latest_sessions = {}
    for session in sorted(s.sessions, key=lambda x: (x.started_at, x.id)):
        latest_sessions[session.task_id] = session
    blockers = {}
    runnable = []
    for t in sorted(s.tasks, key=lambda t: t.id):
        if t.stage == "done":
            continue
        reasons = list(t.blockers)
        if t.kind == "unknown":
            reasons.append("task_kind_and_comparable_cohort_unavailable")
        if t.stage == "blocked":
            reasons.append("task_blocked")
        if t.stage_started_at is not None and t.stage_started_at > s.now:
            reasons.append("future_task_start")
        if t.stage == "active" and t.stage_started_at is None:
            reasons.append("missing_active_task_start")
        session = latest_sessions.get(t.id)
        if session and t.stage in ("queued", "active"):
            if session.outcome in ("timeout", "failed", "cancelled"):
                reasons.append("session_" + session.outcome + "_requires_reconciliation")
            if (session.started_at > s.now or
                    (session.ended_at is not None and not session.started_at <= session.ended_at <= s.now)
                    or (session.outcome == "running") != (session.ended_at is None)):
                reasons.append("invalid_current_session_chronology")
        pr, check = prs.get(t.id), ci.get(t.id)
        if t.stage == "review" and pr is None:
            reasons.append("missing_review_pr")
        if pr:
            if not _fresh(pr.observed_at, s, p):
                reasons.append("stale_or_future_pr")
            elif pr.state != "open":
                reasons.append("pr_requires_task_reconciliation")
            elif pr.draft:
                reasons.append("draft_pr")
        if check:
            if not _fresh(check.observed_at, s, p):
                reasons.append("stale_or_future_ci")
            elif check.state != "passed":
                reasons.append("ci_" + check.state)
        elif t.stage == "review":
            reasons.append("missing_review_ci")
        if reasons:
            blockers[t.id] = sorted(set(reasons))
        else:
            runnable.append(t)

    # Count only fresh workers actually assigned to an unblocked active task.
    by_id = {t.id: t for t in runnable}
    workers = {"execution": set(), "review": set()}
    for w in s.workers:
        t = by_id.get(w.task_id)
        if fresh and t and t.stage in ("active", "review") and w.state == "active" and _fresh(w.observed_at, s, p):
            phase = "review" if t.stage == "review" else "execution"
            workers[phase].add(t.id)  # duplicate heartbeats cannot multiply capacity

    queue_reasons = []
    if not fresh:
        queue_reasons.append("stale_or_future_snapshot")
    if not s.queue_complete:
        queue_reasons.append("incomplete_queue_snapshot")
    if len(runnable) > p.max_queue_tasks:
        queue_reasons.append("queue_exceeds_short_range_limit")
    per_task = []
    for t in runnable:
        phase = "review" if t.stage == "review" else "execution"
        key = (t.kind, t.cohort, phase)
        rows = samples.get(key, [])
        elapsed = s.now - t.stage_started_at if t.stage_started_at is not None and t.stage != "queued" else 0
        # Conditional remaining duration; never blindly subtract a median to zero.
        remaining = [duration - elapsed for _, duration, _ in rows if duration > elapsed]
        reasons = []
        if censored[key]:
            reasons.append("unresolved_or_invalid_history:" + t.cohort + ":" + phase)
        if len(remaining) < p.min_samples:
            reasons.append("insufficient_comparable_remaining_samples:" + t.cohort + ":" + phase)
        if not workers[phase]:
            reasons.append("no_observed_active_workers:" + phase)
        queue_reasons.extend(reasons)
        per_task.append({"task_id": t.id, "phase": phase, "cohort": t.cohort,
                         "kind": t.kind, "elapsed_seconds": elapsed,
                         "sample_size": len(remaining), "censored_tasks": censored[key],
                         "newest_sample_at": rows[-1][0] if rows else None,
                         "seconds": _range(remaining) if not reasons else None,
                         "reasons": reasons})
    scaling = {phase: min(len(ids), p.worker_cap) ** p.worker_exponent for phase, ids in workers.items()}
    queue_range = None
    if not queue_reasons:
        queue_range = {label: sum(max(sum(t["seconds"][label] for t in per_task if t["phase"] == phase) / max(1, scaling[phase]),
                                     max((t["seconds"][label] for t in per_task if t["phase"] == phase), default=0))
                                 for phase in workers) for label in ("p20", "median", "p80")}
        if queue_range["p80"] > p.max_forecast_seconds:
            queue_reasons.append("queue_exceeds_short_range_horizon")
            queue_range = None
    # Count observations once per cohort/phase, not once per queued task.
    cohort_sizes = {}
    for t in per_task:
        key = (t["kind"], t["cohort"], t["phase"])
        cohort_sizes[key] = min(cohort_sizes.get(key, t["sample_size"]), t["sample_size"])
    sample_size = sum(cohort_sizes.values())
    queue = result("estimated" if queue_range is not None and runnable else "empty" if not queue_reasons and not blockers else "unknown",
                   sorted(set(queue_reasons)), scope=s.scope,
                   sample_size=sample_size,
                   confidence=_confidence(min(cohort_sizes.values()), p) if queue_range is not None and cohort_sizes else "none",
                   runnable_task_ids=[t.id for t in runnable], blocked_tasks=blockers,
                   stage_counts=dict(Counter(t.stage for t in s.tasks)),
                   active_workers={phase: len(ids) for phase, ids in workers.items()},
                   effective_workers=scaling, per_task=per_task,
                   horizon_seconds=p.max_forecast_seconds,
                   runnable_seconds=queue_range,
                   all_supplied_queue_seconds=queue_range if not blockers else None,
                   all_supplied_queue_reasons=["blocked_work_has_no_resolution_time"] if blockers else sorted(set(queue_reasons)),
                   completion_boundary="Current stage work only: queued/active to review-ready, review to closure; future stages excluded.",
                   assumption="Conditional on unchanged capacity, comparable cohorts and no new blockers; execution and review are serial phases.")

    match = s.matching
    rows = samples.get(("matching", match.cohort, "matching"), [])
    match_reasons = []
    if not fresh:
        match_reasons.append("stale_or_future_snapshot")
    if match.verified_original_matches is None or not match.verified_count_evidence:
        match_reasons.append("missing_verified_match_count_evidence")
    elif match.verified_original_matches == 0:
        match_reasons.append("zero_verified_original_matches")
    if censored[("matching", match.cohort, "matching")]:
        match_reasons.append("unresolved_or_invalid_matching_history")
    eligible = [row for row in rows if row[2].source_owned and row[2].evidence
                and row[2].match_kind in ("object", "linked_window") and row[2].matched_units]
    unit_counts = Counter(unit for _, _, a in eligible for unit in a.matched_units)
    if any(count != 1 for count in unit_counts.values()):
        match_reasons.append("duplicate_matching_unit_ids")
    if match.verified_original_matches is not None and len(unit_counts) > match.verified_original_matches:
        match_reasons.append("samples_exceed_verified_match_count")
    if len(eligible) < p.min_samples:
        match_reasons.append("insufficient_true_matching_samples")
    throughput = _range([len(a.matched_units) / duration for _, duration, a in eligible]) if not match_reasons else None
    matching = result("observed" if throughput else "unknown", match_reasons,
                      sample_size=len(eligible), confidence=_confidence(len(eligible), p) if throughput else "none",
                      verified_original_matches=match.verified_original_matches,
                      units="verified_original_functions_per_worker_second",
                      per_worker_throughput=throughput,
                      newest_sample_at=eligible[-1][0] if eligible else None,
                      target_remaining_seconds=None)
    target_reasons = list(match_reasons)
    if match.inventory_confidence != "verified" or not match.inventory_evidence or match.inventory_functions is None:
        target_reasons.append("function_denominator_not_independently_verified")
    if not match.throughput_calibrated or not match.calibration_evidence:
        target_reasons.append("per_unit_throughput_not_calibrated")
    if match.inventory_functions is not None and match.verified_original_matches is not None and match.inventory_functions < match.verified_original_matches:
        target_reasons.append("inventory_smaller_than_verified_count")
    if not target_reasons:
        remaining = match.inventory_functions - match.verified_original_matches
        # Per-worker effort only: no assumption of continuous staffing or calendar days.
        matching["target_remaining_seconds"] = {"p20": remaining / throughput["p80"],
                                                 "median": remaining / throughput["median"],
                                                 "p80": remaining / throughput["p20"]}
    matching["target_reasons"] = target_reasons
    matching["target_units"] = "single_worker_effort_seconds_for_matching_scope_only"
    game = result(reasons=tuple(target_reasons) + ("whole_game_integration_and_gameplay_uncalibrated",),
                  eta_seconds=None, label="UNKNOWN")
    return {"schema_version": 1, "as_of": s.now, "observed_at": s.observed_at,
            "sample_window_start": max(0, s.now - p.recent_seconds),
            "setup": setup, "sessions": sessions, "queue": queue,
            "source_matching": matching, "whole_game": game,
            "excluded_history": dict(sorted(excluded.items()))}
