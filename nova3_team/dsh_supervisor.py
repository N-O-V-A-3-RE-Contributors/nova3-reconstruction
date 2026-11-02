"""Continuous DSH supervisor: the durable process that keeps reconstruction alive.

One supervisor owns one slot. It reuses everything the project already trusts
and adds only the DSH execution backend plus a durable state machine:

    queue (GitHub issues + coordinator leases)   <- unchanged authority
    checkout + scope guard + verify()            <- unchanged (nova3_team.worker)
    DSH headless session per bounded pass        <- nova3_team.dsh_runtime
    supervisor state machine + budgets           <- nova3_team.dsh_policy
    evidence planner when the queue runs low     <- nova3_team.dsh_planner

Lifetime of one cycle (every step is checkpointed before it can be repeated):

1. read durable state, honour STOP/RESUME, re-arm WAITING_* states on a timer;
2. re-validate the DSH endpoint locally (no model call);
3. refresh the queue and the GitHub lease for our task (``/nova heartbeat``);
4. if the queue is thin, run a bounded planning pass (also a DSH session, also
   budgeted) and file proposal issues for owner review;
5. otherwise claim the next safe task exactly as the Codex worker does
   (``work_one`` -> ``prepare_checkout`` -> ``validate_scope`` -> ``verify`` ->
   commit -> PR, never merge), but execute the AI session through DSH;
6. classify the objective outcome, persist spend + task checkpoints, and choose
   the next state. A credit pause is a STATE, never an exit.

Nothing here creates labels, edits licences, force-pushes, merges, or touches
another machine's lease. Every mutable path is the slot's own 0700 directory.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys
import time
import uuid

from . import dsh_planner, dsh_policy, dsh_runtime, recovery
from .common import GitHub, label_names, lease_active, lease_get
from .coordinator import EvidenceUnavailable, build_gate

DEFAULT_POLL_S = 90
DEFAULT_PLAN_TIMEOUT_S = 900
# How long to wait before planning again when proposals are already awaiting
# review. Re-planning every poll would spend 2-3 sessions an hour on the same
# evidence, since the queue stays empty until a human approves something.
PLAN_RECHECK_S = 3600
HEARTBEAT_EVERY_S = 20 * 60

STATUSES = frozenset({
    dsh_runtime.COMPLETED, dsh_runtime.RATE_LIMIT, dsh_runtime.WAITING_CREDITS,
    dsh_runtime.AUTH, dsh_runtime.PROVIDER_OUTAGE, dsh_runtime.SESSION_TIMEOUT,
    dsh_runtime.STEP_LIMIT, dsh_runtime.EXECUTION_ERROR, dsh_runtime.CANCELLED,
})


class SupervisorExit(RuntimeError):
    """A deliberate, recorded stop (never a silent restart loop)."""

    def __init__(self, code: int, cause: str) -> None:
        super().__init__(cause)
        self.code = int(code)
        self.cause = cause


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------

class SupervisorState:
    """Atomic JSON checkpoint: survives SIGKILL, reboot and re-imaging."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, **changes) -> dict:
        data = self.load()
        data.update(changes)
        data['updated_unix'] = int(time.time())
        data['version'] = 1
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + f'.{os.getpid()}.tmp')
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, sort_keys=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        return data


# ---------------------------------------------------------------------------
# Wiring helpers (all reuse the existing worker; nothing is duplicated)
# ---------------------------------------------------------------------------

def _worker():
    from . import worker
    return worker


def endpoint_from_env(project: pathlib.Path, overlay_path: pathlib.Path) -> dsh_runtime.DshEndpoint:
    home = dsh_runtime.discover_home()
    binary = dsh_runtime.discover_binary(project, home=home)
    if not binary:
        raise dsh_runtime.DshUnavailable(
            'No DSH runtime found (set NOVA3_DSH_BIN or install the NOVA3 launcher).')
    env = os.environ
    try:
        max_steps = max(10, min(400, int(env.get('NOVA3_DSH_MAX_STEPS', '120'))))
    except ValueError:
        max_steps = 120
    return dsh_runtime.DshEndpoint(
        binary=binary, home=home, profile=env.get('NOVA3_DSH_PROFILE', 'headless'),
        patch=overlay_path, route=env.get('NOVA3_DSH_ROUTE', dsh_runtime.DEFAULT_ROUTE),
        model=env.get('NOVA3_DSH_MODEL', dsh_runtime.DEFAULT_MODEL),
        base_url=env.get('NOVA3_DSH_BASE_URL', dsh_runtime.DEFAULT_BASE_URL),
        key_ref=env.get('NOVA3_DSH_KEY_REF', dsh_runtime.DEFAULT_KEY_REF),
        max_steps=max_steps)


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def task_timeout_s() -> int:
    return _bounded_env_int('NOVA3_AI_SESSION_TIMEOUT', 1200, 300, 1800)


def plan_timeout_s() -> int:
    return _bounded_env_int('NOVA3_DSH_PLAN_TIMEOUT', DEFAULT_PLAN_TIMEOUT_S, 120, 1800)


def _log(message: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {message}', flush=True)


def _redact(text: object) -> str:
    try:
        from . import provider_runtime
        return provider_runtime.redact_diagnostic(text)
    except Exception:
        return str(text)


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------

class DshSupervisor:
    def __init__(self, *, repo: str | None = None, slot: str | None = None,
                 project: pathlib.Path | None = None, once: bool = False,
                 plan_only: bool = False, no_plan: bool = False,
                 poll: int = DEFAULT_POLL_S) -> None:
        self.worker = _worker()
        self.project = pathlib.Path(project or os.environ.get('NOVA3_PROJECT_ROOT') or
                                    pathlib.Path.cwd()).resolve()
        self.slot = self.worker.validate_slot_id(slot or os.environ.get('NOVA3_SLOT', 'dsh'))
        self.repo = repo
        self.once = bool(once)
        self.plan_only = bool(plan_only)
        self.no_plan = bool(no_plan)
        self.poll = max(30, int(poll))
        self.gh = None
        self.upstream = ''
        self.user = ''
        self.paths = None
        self.state = None
        self.ledger = None
        self.reporter = None
        self.endpoint = None
        self.gate = None
        self.guard = None
        self.stop_signal = ''
        self._last_heartbeat = 0.0

    # -- setup ---------------------------------------------------------------
    def setup(self) -> None:
        worker = self.worker
        self.upstream = worker.get_repo(self.repo)
        self.gh = GitHub(self.upstream)
        self.user = self.gh.me()
        repository_root = worker.state_root(self.upstream)
        self.paths = worker.slot_paths(repository_root, self.slot, create=True)
        self.state = SupervisorState(self.paths.state / 'supervisor.json')
        self.ledger = dsh_policy.SpendLedger(self.paths.state / 'spend.jsonl')
        control = self.paths.state / 'control'
        control.mkdir(parents=True, exist_ok=True, mode=0o700)
        overlay = self.paths.state / 'dsh-overlay.yml'
        self.endpoint = endpoint_from_env(self.project, overlay)
        dsh_runtime.write_overlay(overlay, self.endpoint)
        try:
            from . import presence
            self.reporter = presence.Reporter(self.upstream, self.user, 'dsh',
                                              slot_id=self.slot)
            self.reporter.start()
        except Exception as exc:
            self.reporter = None
            _log(f'presence reporter unavailable ({type(exc).__name__}); continuing')
        try:
            self.gate = build_gate(self.gh)
        except EvidenceUnavailable as exc:
            raise SupervisorExit(6, f'owner/admin approval gate unavailable: {exc}') from exc
        self.guard = worker.CheckoutGuard(repository_root, self.slot)
        self._install_signals()
        probe = dsh_runtime.probe_endpoint(self.endpoint)
        self.state.save(slot=self.slot, repo=self.upstream, account=self.user,
                        endpoint={k: probe.get(k) for k in
                                  ('ok', 'reason', 'version', 'profile', 'route', 'model',
                                   'key_ref')},
                        overlay=str(overlay), project=str(self.project),
                        supervisor_pid=os.getpid(),
                        supervisor_started_unix=int(time.time()),
                        budget=dsh_policy.Budget.from_env().as_dict())
        _log('DSH supervisor: repo=%s account=%s slot=%s profile=%s route=%s model=%s dsh=%s'
             % (self.upstream, self.user, self.slot, self.endpoint.profile,
                self.endpoint.route, self.endpoint.model, probe.get('version')))
        _log(f'local endpoint probe: {probe.get("reason")}')
        if not probe.get('ok'):
            self.state.save(state=dsh_policy.WAITING_APPROVAL,
                            reason=f'endpoint probe: {probe.get("reason")}')
            raise SupervisorExit(8, f'DSH endpoint is not usable: {probe.get("reason")}')
        if not dsh_runtime.credential_present(self.endpoint.home, self.endpoint.key_ref):
            _log(f'credential reference {self.endpoint.key_ref} is absent from the DSH store; '
                 'staying in WAITING_CREDITS and rechecking on a timer (no restart needed)')
            self.state.save(state=dsh_policy.WAITING_CREDITS,
                            reason='credential_reference_absent')
            self._request('credential', 'the DSH credential store does not name '
                          f'{self.endpoint.key_ref}',
                          'Save the provider key on the DSH Web Models page (it writes '
                          '$DSH_HOME/.credentials.yaml), or set NOVA3_DSH_KEY_REF to a '
                          'reference the store already has.', 'No session can start.')

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            self.stop_signal = signal.Signals(signum).name
            _log(f'received {self.stop_signal}; finishing the current step cleanly')
        for name in ('SIGINT', 'SIGTERM'):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (OSError, ValueError):
                    pass

    # -- helpers -------------------------------------------------------------
    def _issues(self) -> list:
        return list(self.gh.issues())

    def _assessment(self) -> dsh_planner.Assessment:
        return dsh_planner.assess(issues=self._issues(), ledger_root=self.project,
                                  checkpoint_root=self.paths.state / '.nova-state',
                                  spend_records=self.ledger.records())

    def _our_task(self, issues) -> dict | None:
        number = (self.state.load().get('task') or {}).get('number')
        if not number:
            return None
        for issue in issues:
            if issue.get('number') == number:
                return issue
        return None

    def _lease_status(self, issue) -> tuple[bool, bool]:
        """(we hold an active lease, the lease is inactive/absent)."""
        token = (self.state.load().get('task') or {}).get('token')
        lease = lease_get(issue.get('body'))
        if lease and lease_active(lease) and lease.get('owner') == self.user \
                and token and lease.get('token') == token:
            return True, False
        return False, (lease is None or not lease_active(lease))

    def _heartbeat(self, issue, *, force: bool = False) -> None:
        token = (self.state.load().get('task') or {}).get('token')
        if not token:
            return
        if not force and time.time() - self._last_heartbeat < HEARTBEAT_EVERY_S:
            return
        try:
            self.gh.comment(issue['number'], f'/nova heartbeat {token}')
            self._last_heartbeat = time.time()
        except Exception as exc:
            _log(f'heartbeat failed (will retry): {_redact(exc)[:200]}')

    def _presence(self, status: str, issue=None) -> None:
        if self.reporter is None:
            return
        message = f'#{issue} {status}' if issue else status
        try:
            self.reporter.signal_from_log(message)
        except Exception:
            pass

    def _wait(self, seconds: int, why: str) -> None:
        seconds = max(0, int(seconds))
        self.state.save(next_check_unix=int(time.time()) + seconds, wait_reason=why)
        _log(f'{why}; next check in {seconds}s (state is durable; no restart needed)')
        end = time.time() + seconds
        control = self.paths.state / 'control'
        while time.time() < end:
            if self.stop_signal:
                return
            if dsh_policy.stop_requested(control):
                return
            if dsh_policy.resume_requested(control):
                _log('RESUME file found; rechecking now')
                try:
                    (control / 'RESUME').unlink()
                except OSError:
                    pass
                return
            time.sleep(min(15, max(1, end - time.time())))

    # -- the loop ------------------------------------------------------------
    def run(self) -> int:
        while not self.stop_signal:
            try:
                state = self.state.load()
                current = state.get('state') or dsh_policy.RUNNING
                next_check = int(state.get('next_check_unix') or 0)
                if current in (dsh_policy.BACKOFF, dsh_policy.WAITING_CREDITS,
                               dsh_policy.WAITING_APPROVAL):
                    pending = self._approved_and_awaiting_claim(state)
                    if pending is not None:
                        # The honoured WAITING state no longer describes reality:
                        # a human approved work into the queue. Resume at once.
                        _log(f'{pending} approved task(s) in the queue; leaving {current}')
                        self.state.save(state=dsh_policy.RUNNING, reason='queue replenished')
                        continue
                    if next_check > time.time():
                        self._wait(next_check - time.time(),
                                   f'{current}: {state.get("reason", "")}')
                        continue
                if current == dsh_policy.COMPLETE:
                    _log('state COMPLETE: no queued or plannable work remains')
                    return 0
                if current == dsh_policy.BLOCKED:
                    _log('previous task was BLOCKED; moving on to different work')
                    self.state.save(state=dsh_policy.RUNNING, task=None, reason='')
                self.cycle()
            except SupervisorExit:
                raise
            except KeyboardInterrupt:
                self.stop_signal = 'SIGINT'
                break
            except Exception as exc:          # one bad cycle never kills the loop
                _log(f'cycle error: {type(exc).__name__}: {_redact(exc)[:400]}')
                count = int(self.state.load().get('cycle_errors') or 0) + 1
                self.state.save(cycle_errors=count, state=dsh_policy.BACKOFF,
                                reason=f'cycle error: {type(exc).__name__}')
                if count >= 8:
                    self._request('environment',
                                  'eight consecutive supervisor cycle errors',
                                  'Inspect journalctl --user -u nova3-dsh-worker -n 200, fix '
                                  'the cause, then restart the unit.',
                                  'No spend occurs while the loop cannot run.')
                    self._wait(1800, 'repeated cycle errors; backing off')
                else:
                    self._wait(dsh_policy.backoff_delay(count), 'cycle error backoff')
                continue
            if self.once or self.plan_only:
                return 0
            self._wait(self.poll, 'polling for work')

    def cycle(self) -> None:
        self.state.save(cycle_errors=0)
        issues = self._issues()
        our = self._our_task(issues)
        if our is not None:
            labels = label_names(our)
            if (our.get('state') or 'open') != 'open' or 'nova:review' in labels \
                    or 'nova:blocked' in labels:
                _log(f'task #{our["number"]} left the working set (labels={labels}); '
                     'dropping its checkpoint link (the GitHub lease stays authoritative)')
                self.state.save(task=None, state=dsh_policy.RUNNING)
                our = None
            elif self._lease_status(our)[0]:
                self._heartbeat(our)

        assessment = self._assessment()
        self.state.save(assessment=assessment.as_dict())
        _log(' | '.join(assessment.evidence))
        self._presence('VERIFYING' if our else 'ONLINE', (our or {}).get('number'))

        if our is not None:
            self.run_task(our)
            return
        candidates = self._claimable(issues)
        if candidates:
            self.run_task(candidates[0], claim_first=True)
            return
        if not self.no_plan and assessment.needs_planning():
            self.plan()
            return
        self.state.save(state=dsh_policy.RUNNING, reason='no claimable task; idling')
        _log('no claimable task; polling the coordinator queue')

    def _approved_and_awaiting_claim(self, state: dict) -> int | None:
        """Count runnable tasks when a wait state is waiting on the human.

        Covers both cases: proposals awaiting approval (an approver may have
        queued one) and BACKOFF (a human may have queued work while the
        provider was failing). One cheap list call per wait cycle.
        """
        if not state.get('proposed') and state.get('state') != dsh_policy.BACKOFF:
            return None
        try:
            runnable = [i for i in self._issues()
                        if (i.get('state') or 'open') == 'open'
                        and 'nova:queued' in label_names(i)
                        and not i.get('pull_request')]
        except Exception:
            return None
        return len(runnable) or None

    def _claimable(self, issues) -> list:
        worker = self.worker
        pool = []
        for issue in worker._solo_eligible_issues(issues):
            if 'nova:queued' not in label_names(issue):
                continue
            if worker.parse_priority(issue.get('body', '')).error:
                continue
            pool.append(issue)
        admitted = worker.admit_queue(self.gate, pool, self.paths.state)
        binary_root = self._binary_root()
        if not binary_root:
            admitted = [i for i in admitted if 'Reference SHA256:' not in (i.get('body') or '')]
        admitted.sort(key=lambda i: worker.priority_sort_key(i, claimed=False))
        return admitted

    def _binary_root(self) -> str:
        candidate = pathlib.Path(os.environ.get('NOVA3_REFERENCE_ROOT') or
                                 (self.project / 'analysis'))
        if not candidate.is_dir():
            return ''
        try:
            from .discovery import find_elfs
            return str(candidate.resolve()) if find_elfs(self.project, candidate) else ''
        except OSError:
            return ''

    # -- one task ------------------------------------------------------------
    def run_task(self, issue: dict, *, claim_first: bool = False) -> None:
        worker = self.worker
        number = issue['number']
        started = time.time()
        saved = self.state.load().get('task') or {}
        if claim_first or saved.get('number') != number:
            if self._claim(number, issue.get('title', '')) is None:
                return
            saved = self.state.load().get('task') or saved
        try:
            self.state.save(state=dsh_policy.RUNNING, reason='')
            self._presence('VERIFYING', number)
            result = worker.work_one(self.gh, self.upstream, self.user, issue, 'dsh',
                                     self.paths.state, 3, self._binary_root(),
                                     provider_status=None, gate=self.gate, guard=self.guard)
        except SystemExit as exc:            # work_one stops deliberately on fatal causes
            raise SupervisorExit(int(exc.code or 1), 'worker requested a deliberate stop') from exc
        except Exception as exc:
            result = exc
            _log(f'task #{number}: worker error {_redact(exc)[:300]}')
        task_state = recovery.load(self.paths.state, number)
        spend = task_state.get('dsh_spend') or {}
        outcome = dsh_runtime.SessionOutcome(
            status=self._task_status(task_state, result),
            exit_code=task_state.get('provider_exit'),
            session_id=task_state.get('dsh_session'),
            turn_reason=task_state.get('dsh_turn_reason'),
            error_code=task_state.get('dsh_error_code'),
            error_message=str(task_state.get('dsh_error_message') or ''),
            steps=int(task_state.get('dsh_steps') or 0), tool_calls=0,
            elapsed_s=float(time.time() - started),
            input_tokens=int(spend.get('input_tokens') or 0),
            output_tokens=int(spend.get('output_tokens') or 0))
        lease_inactive = False
        try:
            fresh = self.gh.issue(number)
            if (fresh.get('state') or 'open') == 'open':
                _, lease_inactive = self._lease_status(fresh)
            if 'nova:review' in label_names(fresh):
                lease_inactive = False       # submitted+released is a healthy end state
        except Exception:
            pass
        previous = self.state.load().get('same_failure') or {}
        same = 1 + int(previous.get('count') or 0) if previous.get('status') == outcome.status else 1
        prior_ok_inactive = int(self.state.load().get('ok_with_inactive_lease') or 0)
        ok_inactive = prior_ok_inactive + 1 if (outcome.ok and lease_inactive) else 0
        context = dsh_policy.Context(
            task=str(number), budget=dsh_policy.Budget.from_env(),
            consecutive_same_failure=same, ok_with_inactive_lease=ok_inactive,
            lease_inactive=lease_inactive,
            control_dir=self.paths.state / 'control',
            attempt_started=float(saved.get('claimed_unix') or 0) or None)
        decision = dsh_policy.decide(outcome, self.ledger, context)
        self.ledger.append({'task': str(number), 'status': outcome.status,
                            'error_code': outcome.error_code,
                            'elapsed_s': outcome.summary()['elapsed_s'],
                            'input_tokens': outcome.input_tokens,
                            'output_tokens': outcome.output_tokens,
                            'phase': task_state.get('phase'),
                            'result': None if result is None else bool(result)})
        if result is not True and task_state.get('verified'):
            # The model finished AND the independent checks passed; what failed
            # was our own publish plumbing (push/PR/network), not the work. That
            # must never consume the task's session budget or block the task:
            # only a provider or work failure counts against it.
            recovered = self._resume_publish(number, issue, task_state, result)
            if recovered:
                _log(f'task #{number}: publish recovered without another AI session')
                return
            _log(f'task #{number}: verified work is committed locally but publishing failed; '
                 f'checkout preserved at {task_state.get("work")}')
        _log(f'task #{number}: {outcome.status} -> {decision.state} ({decision.reason})')
        self.state.save(state=decision.state, reason=decision.reason,
                        next_check_unix=int(time.time()) + decision.delay_s,
                        last_outcome=outcome.summary(),
                        same_failure={'status': outcome.status, 'count': same},
                        ok_with_inactive_lease=ok_inactive,
                        last_decision=decision.as_dict())
        if decision.request:
            self._request(**decision.request, task=str(number))
        if decision.state in (dsh_policy.BLOCKED, dsh_policy.WAITING_CREDITS) \
                or not decision.retry_same_task:
            self.state.save(task=None)
        self._presence('REVIEW' if task_state.get('phase') == 'review' else 'ONLINE', number)

    def _resume_publish(self, number: int, issue: dict, task_state: dict, error) -> bool:
        """Retry only the publish half of `work_one` for already-verified work.

        `work_one` is resumable by design: with phase `committed`/`pushed` it
        skips the AI session entirely and repeats push -> PR -> submitted. The
        supervisor calls it again with the same checkpoint; if the phase has
        already reached `review` there is nothing left to do.
        """
        phase = task_state.get('phase')
        if phase not in ('committed', 'pushed'):
            return False
        if int(self.state.load().get('publish_attempts') or 0) >= 2:
            self._request('publish', f'verified work for #{number} cannot be published',
                          f'Push the branch in {task_state.get("work")} manually or fix the '
                          'credential/network error; the commits and verification are kept.',
                          'Nothing is merged automatically either way.', task=str(number))
            return False
        self.state.save(publish_attempts=int(self.state.load().get('publish_attempts') or 0) + 1)
        try:
            self.worker.work_one(self.gh, self.upstream, self.user, issue, 'dsh',
                                 self.paths.state, 3, self._binary_root(),
                                 provider_status=None, gate=self.gate, guard=self.guard)
        except Exception as exc:              # keep the original, more useful error
            _log(f'task #{number}: publish retry failed: {_redact(exc)[:200]}')
            return False
        return recovery.load(self.paths.state, number).get('phase') == 'review'

    def _claim(self, number: int, title: str) -> str | None:
        """The exact coordinator handshake the Codex worker uses: one lease."""
        token = str(uuid.uuid4())
        self.gh.comment(number, f'/nova claim {token}')
        _log(f'requested task #{number}; waiting for the coordinator lease')
        until = time.monotonic() + 100
        while time.monotonic() < until:
            if self.stop_signal:
                return None
            current = self.gh.issue(number)
            lease = lease_get(current.get('body'))
            if lease and lease.get('owner') == self.user and lease.get('token') == token \
                    and lease_active(lease):
                self.state.save(task={'number': number, 'token': token, 'title': title,
                                      'claimed_unix': int(time.time())})
                recovery.update(self.paths.state, number, repo=self.upstream, user=self.user,
                                token=token)
                return token
            if 'nova:review' in label_names(current):
                break
            if lease and lease_active(lease):
                break
            time.sleep(5)
        _log(f'no lease for #{number} (coordinator disabled or another worker owns it)')
        return None

    def _task_status(self, task_state: dict, result) -> str:
        status = task_state.get('dsh_status')
        if status in STATUSES:
            return status
        if task_state.get('provider_exit') == 124:
            return dsh_runtime.SESSION_TIMEOUT
        if result is True:
            return dsh_runtime.COMPLETED
        return dsh_runtime.EXECUTION_ERROR

    # -- planning ------------------------------------------------------------
    def plan(self) -> None:
        _log('queue is at the low-water mark; running a bounded planning pass')
        issues = self._issues()
        assessment = self._assessment()
        existing = dsh_planner.issue_texts(
            [i for i in issues if (i.get('state') or 'open') == 'open']) + \
            dsh_planner.issue_texts([i for i in issues if (i.get('state') or '') == 'closed'][-40:])
        rejected = list(self.state.load().get('rejected_proposals') or [])
        # Proposals filed by this supervisor are deliberately queued WITHOUT a
        # label so a human decides; that also makes them invisible to the open-
        # issue text used above. Feed them back explicitly or the next pass
        # would re-propose its own unanswered work.
        filed = []
        for number in self.state.load().get('proposed') or []:
            try:
                issue = self.gh.issue(number)
                if (issue.get('state') or 'open') == 'open':
                    filed.append({'title': issue.get('title') or '', 'body': issue.get('body') or ''})
            except Exception:
                continue
        existing += dsh_planner.issue_texts(filed)
        overrides_file = self.project / '.local' / 'dsh-loop' / 'prompt-overrides.md'
        try:
            overrides = overrides_file.read_text(encoding='utf-8') if overrides_file.is_file() else ''
        except OSError:
            overrides = ''
        prompt = dsh_planner.planner_prompt(
            assessment, existing=existing, rejected=rejected,
            next_md_excerpt=_head(self.project / 'docs/progress/NEXT.md'), overrides=overrides)
        log_path = self.paths.logs / f'planner-{time.strftime("%Y%m%d-%H%M%S")}.log'
        self._presence('PLANNING')
        outcome = dsh_runtime.run_session(self.endpoint, prompt, cwd=self.project,
                                          log_path=log_path, timeout_s=plan_timeout_s(),
                                          cancel=lambda: bool(self.stop_signal))
        self.ledger.append({'task': 'planner', 'status': outcome.status,
                            'error_code': outcome.error_code,
                            'input_tokens': outcome.input_tokens,
                            'output_tokens': outcome.output_tokens})
        if outcome.status != dsh_runtime.COMPLETED:
            decision = dsh_policy.decide(
                outcome, self.ledger,
                dsh_policy.Context(task='planner', budget=dsh_policy.Budget.from_env(),
                                   consecutive_same_failure=1,
                                   control_dir=self.paths.state / 'control'))
            _log(f'planner session {outcome.status} -> {decision.state} ({decision.reason})')
            self.state.save(state=decision.state, reason=f'planner: {decision.reason}',
                            next_check_unix=int(time.time()) + decision.delay_s,
                            last_outcome=outcome.summary())
            if decision.request:
                self._request(**decision.request, task='planner')
            return
        proposals, bad = dsh_planner.parse_proposals(_final_text(outcome),
                                                     evidence=assessment.evidence)
        proposals = dsh_planner.deduplicate(proposals, existing_texts=existing,
                                            rejected_keys=rejected)
        for title, reason in bad:
            _log(f'planner rejected a proposal ({reason}): {title}')
        self._presence('ONLINE')
        if not proposals:
            working = _working(issues)
            complete = assessment.queue.runnable == 0 and not working
            _log('planner produced no acceptable new task; '
                 + ('nothing remains queued' if complete else
                    f'{len(working)} task(s) still in flight elsewhere'))
            self.state.save(state=dsh_policy.COMPLETE if complete else dsh_policy.RUNNING,
                            reason='planner: no honest task derived from evidence')
            if complete:
                self._request('evidence', 'the planner found no honest, non-duplicate task',
                              'Run the discovery timer for new evidence, or narrow a blocked '
                              'task. The supervisor resumes as soon as evidence exists.',
                              'Autonomous progress is paused until then.', task='planner')
            return
        numbers = []
        for proposal in proposals:
            try:
                created = self.gh.post('issues', dsh_planner.write_proposal_issue_payload(proposal))
                numbers.append(created.get('number'))
                _log(f'filed planner proposal #{created.get("number")}: {proposal.title}')
            except Exception as exc:
                _log(f'could not file proposal: {_redact(exc)[:200]}')
                self.state.save(rejected_proposals=(rejected + [proposal.key])[-50:])
        if not numbers:
            self.state.save(state=dsh_policy.BACKOFF, reason='proposal filing failed',
                            next_check_unix=int(time.time()) + 600)
            return
        self.state.save(proposed=numbers, state=dsh_policy.WAITING_APPROVAL,
                        reason='planner proposals await owner approval into the queue',
                        next_check_unix=int(time.time()) + PLAN_RECHECK_S)
        self._request('approval', 'planner filed new task proposals',
                      ', '.join(f'#{n}' for n in numbers)
                      + ' need owner review: apply the owner approval label plus `nova:queued` '
                        'to approve, or close them to reject.',
                      'Unapproved proposals are never claimed and cost nothing.',
                      task='planner')

    # -- human requests ------------------------------------------------------
    def _request(self, kind: str, detail: str, action: str, risk: str = '',
                 task: str | None = None) -> None:
        path = self.paths.state / 'requests.jsonl'
        record = {'at': int(time.time()), 'kind': kind, 'task': task,
                  'detail': detail[:800], 'action': action[:800], 'risk': risk[:400],
                  'slot': self.slot}
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, sort_keys=True) + '\n')
            os.chmod(path, 0o600)
        except OSError:
            pass
        _log(f'ACTION REQUIRED ({kind})' + (f' for #{task}' if task else '')
             + f': {action} -- {detail}')


def _head(path: pathlib.Path, limit: int = 9000) -> str:
    try:
        return path.read_text(encoding='utf-8')[:limit]
    except OSError:
        return ''


def _final_text(outcome: dsh_runtime.SessionOutcome) -> str:
    for event in reversed(outcome.events):
        if event.get('type') == 'final':
            return str(event.get('text') or '')
    return ''


def _working(issues) -> list:
    return [i for i in issues if (i.get('state') or 'open') == 'open'
            and {'nova:queued', 'nova:claimed', 'nova:review'} & set(label_names(i))]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m nova3_team.dsh_supervisor',
        description='Continuous DSH-backed N.O.V.A. 3 reconstruction supervisor')
    p.add_argument('command', nargs='?', default='run', choices=('run', 'status', 'probe'))
    p.add_argument('--repo', help='OWNER/REPO (defaults to the current repository)')
    p.add_argument('--slot', default=os.environ.get('NOVA3_SLOT', 'dsh'),
                   help='slot id; each slot owns its own state, logs and checkouts')
    p.add_argument('--project', default=str(pathlib.Path.cwd()),
                   help='project checkout used for planning and as the base for state paths')
    p.add_argument('--once', action='store_true', help='run one cycle and exit')
    p.add_argument('--plan-only', action='store_true', help='run only a planning pass')
    p.add_argument('--no-plan', action='store_true', help='never plan; execute the queue only')
    p.add_argument('--poll', type=int, default=DEFAULT_POLL_S)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    project = pathlib.Path(args.project).resolve()
    if args.command == 'probe':
        endpoint = endpoint_from_env(project, project / '.local' / 'dsh' / 'overlay.yml')
        print(json.dumps(dsh_runtime.probe_endpoint(endpoint), indent=2))
        return 0
    worker = _worker()
    upstream = worker.get_repo(args.repo)
    if args.command == 'status':
        paths = worker.slot_paths(worker.state_root(upstream), args.slot)
        state = SupervisorState(paths.state / 'supervisor.json').load()
        ledger = dsh_policy.SpendLedger(paths.state / 'spend.jsonl')
        print(json.dumps({
            'slot': args.slot, 'state': state.get('state'), 'reason': state.get('reason'),
            'task': state.get('task'), 'last_outcome': state.get('last_outcome'),
            'assessment': state.get('assessment'), 'endpoint': state.get('endpoint'),
            'updated_unix': state.get('updated_unix'),
            'next_check_unix': state.get('next_check_unix'),
            'sessions_today': ledger.sessions_today(), 'tokens_today': ledger.tokens_today(),
            'requests': _tail_requests(paths.state / 'requests.jsonl')}, indent=2))
        return 0
    supervisor = DshSupervisor(repo=args.repo, slot=args.slot, project=project,
                               once=args.once, plan_only=args.plan_only,
                               no_plan=args.no_plan, poll=args.poll)
    try:
        supervisor.setup()
        return supervisor.run()
    except (SupervisorExit, dsh_runtime.DshUnavailable) as exc:
        code = int(getattr(exc, 'code', 8))
        cause = getattr(exc, 'cause', str(exc))
        payload = worker.record_worker_failure(worker.state_root(upstream), code, cause)
        _log(f'exit {code}: {payload.get("cause")}')
        return code


def _tail_requests(path: pathlib.Path, limit: int = 8) -> list:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


if __name__ == '__main__':
    sys.exit(main())
