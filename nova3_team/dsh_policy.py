"""Durable supervision policy for DSH-backed autonomous sessions.

The supervisor is deliberately *dumb about conversations and strict about
state*: every decision it takes is a pure function of (1) the last objective
session outcome, (2) a durable spend/attempt record on disk, and (3) an
operator-controlled STOP file. Nothing here reads a model's self-report.

Explicit supervisor states (the only values ever written to disk):

    RUNNING         a session may start now
    BACKOFF         temporary provider problem; a bounded exponential sleep
    WAITING_CREDITS the account cannot pay and no approved fallback exists
    WAITING_APPROVAL a human must act (credential, grant, dangerous permission)
    BLOCKED         this task cannot be retried without human repair
    COMPLETE        the queue/goal is done; no further sessions

Why this exists (the failure the old unit hid): ``RestartPreventExitStatus=3``
made systemd *refuse to restart* the worker after a credit pause, so autonomous
recovery was impossible without a human. Here a credit pause is a STATE, not an
exit: the supervisor stays alive, rechecks availability on a bounded interval,
and returns to RUNNING by itself once the session succeeds again.

Budgets are hard: a rolling hourly window, a daily session cap and a daily
token cap. Exceeding any of them is ``WAITING_APPROVAL`` with an actionable
request -- never a silent extra spend, and never a restart loop.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass, field

from . import dsh_runtime

RUNNING = 'RUNNING'
WAITING_CREDITS = 'WAITING_CREDITS'
WAITING_APPROVAL = 'WAITING_APPROVAL'
BACKOFF = 'BACKOFF'
BLOCKED = 'BLOCKED'
COMPLETE = 'COMPLETE'

STATES = frozenset({RUNNING, WAITING_CREDITS, WAITING_APPROVAL, BACKOFF, BLOCKED, COMPLETE})

# Bounded exponential backoff (seconds). The cap is also the "recheck when the
# provider might be back" interval, so a long outage costs one probe per hour.
BACKOFF_BASE_S = 120
BACKOFF_CAP_S = 3600

DEFAULT_MAX_SESSIONS_PER_DAY = 48
DEFAULT_MAX_SESSIONS_PER_HOUR = 6
DEFAULT_MAX_SESSIONS_PER_TASK = 3
DEFAULT_MAX_TOKENS_PER_DAY = 6_000_000
CREDIT_RECHECK_S = 1800


@dataclass(frozen=True)
class Budget:
    sessions_per_hour: int = DEFAULT_MAX_SESSIONS_PER_HOUR
    sessions_per_day: int = DEFAULT_MAX_SESSIONS_PER_DAY
    # One attempt = ONE task session plus one no-change retry. The cap exists so
    # a task that keeps "succeeding" without closing is handed back for a
    # replan; it must not be so loose that four hours pass before anyone looks.
    sessions_per_task: int = DEFAULT_MAX_SESSIONS_PER_TASK
    tokens_per_day: int = DEFAULT_MAX_TOKENS_PER_DAY

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> 'Budget':
        env = os.environ if env is None else env
        def _int(name, default):
            try:
                value = int(str(env.get(name, '')).strip())
            except ValueError:
                return default
            return value if value > 0 else default
        return cls(sessions_per_hour=_int('NOVA3_DSH_SESSIONS_PER_HOUR', DEFAULT_MAX_SESSIONS_PER_HOUR),
                   sessions_per_day=_int('NOVA3_DSH_SESSIONS_PER_DAY', DEFAULT_MAX_SESSIONS_PER_DAY),
                   sessions_per_task=_int('NOVA3_DSH_SESSIONS_PER_TASK', DEFAULT_MAX_SESSIONS_PER_TASK),
                   tokens_per_day=_int('NOVA3_DSH_TOKENS_PER_DAY', DEFAULT_MAX_TOKENS_PER_DAY))

    def as_dict(self) -> dict:
        return dict(sessions_per_hour=self.sessions_per_hour,
                    sessions_per_day=self.sessions_per_day,
                    sessions_per_task=self.sessions_per_task,
                    tokens_per_day=self.tokens_per_day)


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    delay_s: int = 0                     # sleep before the next attempt
    action: str = ''                     # human action required, when any
    request: dict | None = None          # actionable, JSON-safe (no secrets)
    retry_same_task: bool = True

    def as_dict(self) -> dict:
        return {'state': self.state, 'reason': self.reason, 'delay_s': self.delay_s,
                'action': self.action, 'retry_same_task': self.retry_same_task,
                'request': self.request}


def backoff_delay(attempt: int) -> int:
    """Bounded exponential backoff: 120s, 240s, 480s ... capped at one hour."""
    attempt = max(1, int(attempt))
    return int(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** min(attempt - 1, 10))))


# ---------------------------------------------------------------------------
# Spend/attempt record: append-only, survives crashes and reboots
# ---------------------------------------------------------------------------

class SpendLedger:
    """One JSON object per finished session, outside any model conversation.

    The file is append-only (a crash mid-write loses at most the last line) and
    is read back with a bounded tail so an old ledger cannot grow the prompt.
    """

    def __init__(self, path: pathlib.Path, *, keep: int = 4000) -> None:
        self.path = pathlib.Path(path)
        self.keep = keep

    def append(self, record: dict) -> dict:
        record = dict(record)
        record.setdefault('at', int(time.time()))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._close_torn_tail()
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _close_torn_tail(self) -> None:
        """Terminate an unterminated last line left by a crash mid-write.

        Without this a new record would be appended onto the torn line and be
        lost with it on read -- exactly the state a crash is most likely to
        leave behind.
        """
        try:
            with self.path.open('r+b') as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    return
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b'\n':
                    handle.write(b'\n')
        except OSError:
            pass

    def records(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-self.keep:]:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                out.append(value)
        return out

    def window(self, *, now: float | None = None, seconds: int = 3600) -> list[dict]:
        now = time.time() if now is None else now
        return [r for r in self.records() if float(r.get('at') or 0) >= now - seconds]

    def tokens_today(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        start = _local_midnight(now)
        return sum(int(r.get('input_tokens') or 0) + int(r.get('output_tokens') or 0)
                   for r in self.records() if float(r.get('at') or 0) >= start)

    def session_records(self, *, since: float | None = None) -> list[dict]:
        """One row per model session; the per-task summary row mirrors one.

        Counting summary rows as sessions made three real sessions look like
        six, which tripped the hourly cap and refused to publish an
        already-verified commit (task #171). Token accounting was fixed the
        same way earlier; sessions must be filtered identically.
        """
        return [r for r in self.records()
                if not r.get('summary')
                and (since is None or float(r.get('at') or 0) >= float(since))]

    def sessions_last_hour(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return len(self.session_records(since=now - 3600))

    def sessions_today(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return len(self.session_records(since=_local_midnight(now)))

    def sessions_for_task(self, task: str, *, since: float | None = None) -> int:
        """Sessions spent on one task, optionally only after a checkpoint.

        Counting only the CURRENT attempt keeps the per-task cap meaningful: a
        task requeued by a human after a genuine fix starts with a fresh budget,
        while an automatic loop that keeps re-reaching the same task does not.
        """
        return sum(1 for r in self.session_records(since=since)
                   if str(r.get('task')) == str(task))


def _local_midnight(now: float) -> float:
    import datetime
    moment = datetime.datetime.fromtimestamp(now)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


# ---------------------------------------------------------------------------
# Operator controls
# ---------------------------------------------------------------------------

def stop_requested(control_dir: pathlib.Path) -> str:
    """An operator STOP file is honoured before, during and after sessions."""
    path = pathlib.Path(control_dir) / 'STOP'
    try:
        return path.read_text(encoding='utf-8').strip()[:400] if path.is_file() else ''
    except OSError:
        return ''


def resume_requested(control_dir: pathlib.Path) -> bool:
    return (pathlib.Path(control_dir) / 'RESUME').is_file()


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Context:
    task: str
    budget: Budget = field(default_factory=Budget)
    consecutive_same_failure: int = 1     # identical failure class, back to back
    ok_with_inactive_lease: int = 0       # sessions that finished but the lease stayed wrong
    lease_inactive: bool = False
    control_dir: pathlib.Path | None = None
    attempt_started: float | None = None   # claim time; None counts all history
    budget_consent: bool = False           # operator RESUME consumed for THIS session


def _human_request(kind: str, task: str, detail: str, action: str, risk: str = '') -> dict:
    return {'kind': kind, 'task': task, 'detail': detail, 'action': action, 'risk': risk}


def decide(outcome, ledger: SpendLedger, context: Context, *, now: float | None = None) -> Decision:
    """Pure decision: the single place that maps an outcome to a supervisor state."""
    now = time.time() if now is None else now
    task = str(context.task)
    budget = context.budget

    # 0. Operator controls and hard budgets come before any provider judgement.
    # An explicit human request refills the HOURLY allowance only. The daily
    # window and the token cap still apply: this is "try again now", not
    # permission to spend the day's budget in one burst. The consent is passed
    # in by the caller that consumed the RESUME (control_dir may be None when a
    # caller asks "may a session start now?").
    ignore_hourly_cap = bool(context.budget_consent)
    if context.control_dir is not None:
        stop = stop_requested(context.control_dir)
        if stop:
            return Decision(BACKOFF, f'operator STOP file present: {stop}', delay_s=BACKOFF_CAP_S,
                            action='clear_stop', retry_same_task=False)
        ignore_hourly_cap = ignore_hourly_cap or resume_requested(context.control_dir)
    if ledger.sessions_today(now=now) >= budget.sessions_per_day:
        return Decision(WAITING_APPROVAL,
                        f'daily session cap reached ({budget.sessions_per_day})',
                        action='raise_session_budget', retry_same_task=True,
                        request=_human_request(
                            'budget', task,
                            f'{budget.sessions_per_day} DSH sessions already ran today',
                            'Raise NOVA3_DSH_SESSIONS_PER_DAY, or wait until tomorrow; '
                            'the supervisor resumes automatically at the next count window.',
                            'Higher API spend on the configured model route.'))
    if not ignore_hourly_cap and ledger.sessions_last_hour(now=now) >= budget.sessions_per_hour:
        return Decision(BACKOFF, f'hourly session cap reached ({budget.sessions_per_hour})',
                        delay_s=300, retry_same_task=True)
    if ledger.tokens_today(now=now) >= budget.tokens_per_day:
        return Decision(WAITING_APPROVAL,
                        f'daily token cap reached ({budget.tokens_per_day})',
                        action='raise_token_budget', retry_same_task=True,
                        request=_human_request(
                            'budget', task,
                            f'{ledger.tokens_today(now=now)} tokens used today',
                            'Raise NOVA3_DSH_TOKENS_PER_DAY or wait for the next day; '
                            'the supervisor resumes automatically.', 'Higher API spend.'))

    success = outcome.status == dsh_runtime.COMPLETED
    if success:
        # A session that finished cleanly: reset every provider-level counter.
        if context.lease_inactive and context.ok_with_inactive_lease >= 2:
            return Decision(BLOCKED, 'sessions succeed but the task lease is never returned',
                            action='repair_lease', retry_same_task=False,
                            request=_human_request(
                                'coordination', task,
                                'two consecutive sessions completed while the coordinator still '
                                'reports the lease inactive',
                                'Run python -m nova3_team.coordinator manually and inspect the '
                                'issue body lease; the task is quarantined meanwhile.',
                                'A stale lease keeps the task unclaimable by any worker.'))
        if context.lease_inactive:
            return Decision(RUNNING, 'session completed; lease reconciliation pending',
                            retry_same_task=True)
        # Never loop on one task: a per-task session cap forces a replan.
        # A 'success' that does not close the task is exactly the loop this cap
        # exists for -- EXCEPT for the planner, whose sessions are expected to
        # end without closing anything (it files proposals for a human).
        if task != 'planner' and \
                ledger.sessions_for_task(task, since=context.attempt_started) >= budget.sessions_per_task:
            return Decision(BLOCKED,
                            f'per-task session cap reached ({budget.sessions_per_task}) '
                            'without closing the task',
                            action='replan_task', retry_same_task=False,
                            request=_human_request(
                                'replan', task,
                                f'{budget.sessions_per_task} sessions on this task produced no '
                                'closed milestone',
                                'The supervisor will pick a different task next; re-scope this '
                                'one with smaller acceptance criteria before it is requeued.',
                                'Repeating the same approach is what the cap prevents.'))
        return Decision(RUNNING, 'session completed')

    status = outcome.status
    attempt = max(1, int(context.consecutive_same_failure))
    detail = f'DSH reported {status}' + (f' ({outcome.error_code})' if outcome.error_code else '')

    if status == dsh_runtime.WAITING_CREDITS:
        if context.consecutive_same_failure >= 3:
            return Decision(WAITING_APPROVAL, detail + '; repeated after rechecks',
                            action='fund_or_failover', delay_s=CREDIT_RECHECK_S,
                            retry_same_task=True,
                            request=_human_request(
                                'credits', task,
                                'the DSH model route reports an exhausted quota/balance',
                                'Top up the OpenRouter balance (or approved DeepSeek direct key) '
                                'for the route used by $DSH_HOME; the supervisor rechecks on its '
                                'own and resumes without a restart.',
                                'No spend occurs while waiting; other tasks are not attempted '
                                'because every session would fail identically.'))
        return Decision(WAITING_CREDITS, detail, delay_s=CREDIT_RECHECK_S, retry_same_task=True,
                        request=_human_request(
                            'credits', task, 'the DSH model route reports an exhausted quota',
                            'Top up the account; no restart is needed.', 'None while waiting.'))
    if status == dsh_runtime.AUTH:
        if attempt >= 3:
            return Decision(WAITING_APPROVAL, detail + '; credential rejected repeatedly',
                            action='refresh_credential', delay_s=CREDIT_RECHECK_S,
                            retry_same_task=True,
                            request=_human_request(
                                'credential', task,
                                'DSH cannot use the credential named by the generated overlay',
                                'Open the DSH Web Models page and re-save the provider key (it '
                                'writes $DSH_HOME/.credentials.yaml), or rotate it. The overlay '
                                'refers to the key by NAME only; no secret is in the repo.',
                                'An invalid key bills nothing but blocks all sessions.'))
        return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
    if status == WAITING_APPROVAL:
        # The adapter itself declared a human decision is required.
        return Decision(WAITING_APPROVAL, detail, action='human_action_required')
    if status == dsh_runtime.RATE_LIMIT:
        return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
    if status == dsh_runtime.PROVIDER_OUTAGE:
        if attempt >= 5:
            return Decision(WAITING_APPROVAL, detail + '; provider unreachable 5 times',
                            action='provider_outage', delay_s=CREDIT_RECHECK_S,
                            retry_same_task=True,
                            request=_human_request(
                                'provider', task, 'the model endpoint failed repeatedly',
                                'Check the provider status page or switch NOVA3_DSH_MODEL to an '
                                'approved alternative; the supervisor retries hourly.',
                                'No spend while the endpoint is down.'))
        return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
    if status == dsh_runtime.SESSION_TIMEOUT:
        return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
    if status == dsh_runtime.STEP_LIMIT:
        # The context filled up or the model looped: a fresh session is correct,
        # but the task context must be rebuilt rather than resent verbatim.
        return Decision(RUNNING, detail + '; starting a fresh bounded session',
                        retry_same_task=True)
    if status == dsh_runtime.CANCELLED:
        return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
    # EXECUTION_ERROR and anything unknown: bounded retry, then block the task.
    if attempt >= 3:
        return Decision(BLOCKED, detail + f'; {attempt} identical failures',
                        action='repair_task', retry_same_task=False,
                        request=_human_request(
                            'failure', task,
                            'three identical DSH execution failures on this task',
                            'Inspect the private session log under the slot logs directory, fix '
                            'the task scope or the environment, then requeue.',
                            'Retrying an identical broken attempt wastes quota.'))
    return Decision(BACKOFF, detail, delay_s=backoff_delay(attempt), retry_same_task=True)
