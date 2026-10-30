"""Cross-platform opt-in unattended worker. Runs one sandboxed local CLI at a time."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, re, shutil, signal, subprocess, sys, threading, time, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from .common import GitHub, lease_active, lease_get, label_names
from .guard import check as guard_check
from . import recovery  # NOVA3-DURABLE-RESUME-v1
from . import reliability  # NOVA3-RELIABILITY-FAILSTOP-v2
from . import activity_feed
from .coordinator import EvidenceUnavailable, QueueDecision, build_gate
from . import provider_runtime
from . import hermes_runtime


def run(cmd, cwd=None, timeout=90, capture=True, check=True):
    cp=subprocess.run(cmd,cwd=cwd,text=True,capture_output=capture,timeout=timeout)
    if check and cp.returncode:
        raise RuntimeError(f'{cmd[:4]} failed ({cp.returncode}): {(cp.stderr or cp.stdout or "")[-900:]}')
    return cp


def ai_session_timeout():
    """Bound provider runtime before launching an expensive, unattended job."""
    raw = os.environ.get('NOVA3_AI_SESSION_TIMEOUT', '1200')
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError('NOVA3_AI_SESSION_TIMEOUT must be an integer number of seconds') from exc
    if not 300 <= seconds <= 1800:
        raise ValueError('NOVA3_AI_SESSION_TIMEOUT must be between 300 and 1800 seconds')
    return seconds


def private_round_log(workdir, round_num):
    """Never write provider output to GitHub or the tracked source checkout."""
    workdir = pathlib.Path(workdir)
    folder = workdir.parent / '.nova-private' / workdir.name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'posix':
        folder.chmod(0o700)
    return folder / f'round-{round_num}.log'


def _tail_file(path, limit=16000):
    try:
        with pathlib.Path(path).open('rb') as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - limit))
            return file.read().decode('utf-8', errors='replace')
    except OSError:
        return ''


def _stop_provider_group(proc):
    """Stop a timed-out CLI and its subprocesses, avoiding runaway local jobs."""
    if proc.poll() is not None:
        if os.name == 'posix':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
    if os.name == 'posix':
        # The CLI might exit on SIGTERM while its Java/Ghidra or shell children
        # survive. They remain in its dedicated process group; kill that group.
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            pass
        else:
            os.killpg(proc.pid, signal.SIGKILL)


def _publish_activity(activity, **kwargs):
    # Optional local telemetry must never interrupt provider/guard/PR control flow.
    try:
        activity.update(**kwargs)
    except Exception:
        pass


def _relay_provider_output(source, sink):
    """Redact before persisting; never buffer an unbounded unterminated line."""
    pending = b''
    discarding = False
    limit = 64 * 1024
    while True:
        chunk = source.read(4096)
        if not chunk:
            if pending and not discarding:
                sink.write(provider_runtime.redact_diagnostic(
                    pending.decode('utf-8', errors='replace')).encode('utf-8'))
                sink.flush()
            return
        pending += chunk
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            if not discarding:
                if len(line) <= limit:
                    sink.write(provider_runtime.redact_diagnostic(
                        line.decode('utf-8', errors='replace')).encode('utf-8') + b'\n')
                else:
                    sink.write(b'[oversized provider output line omitted]\n')
            discarding = False
            sink.flush()
        if len(pending) > limit:
            if not discarding:
                sink.write(b'[oversized provider output line omitted]\n')
                sink.flush()
            pending = b''
            discarding = True


def run_provider(cmd, cwd, log, timeout, activity=None, env=None):
    """Stream AI output to a private local file; retain diagnostics on timeout.

    subprocess.run(capture_output=True) hid all progress until termination and
    returned empty logs for issue #82's three 30-minute timeouts. This
    implementation exposes only elapsed time/byte counts to worker telemetry.
    A separate private summary contains allowlisted observational metadata.
    Logs stay in .nova-private and are never attached to GitHub comments.
    """
    log = pathlib.Path(log)
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        log.parent.chmod(0o700)
    descriptor = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    if os.name == 'posix':
        os.chmod(log, 0o600)
    settings = {'start_new_session': True} if os.name == 'posix' else {}
    started = time.monotonic()
    heartbeat_due = started + 60
    timed_out = False
    if activity is None:
        structured = (len(cmd) > 1 and provider_runtime.executable_stem(cmd[0]) == 'codex'
                      and cmd[1] == 'exec' and '--json' in cmd[2:-1])
        activity = activity_feed.ActivityFeed(log, structured=structured)
    _publish_activity(activity, force=True)
    try:
        with os.fdopen(descriptor, 'wb') as sink:
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, shell=False, bufsize=0,
                                        env=env, **settings)
            except OSError:
                raise provider_runtime.ProviderUnavailable(
                    'Provider could not start; check the local executable and permissions.') from None
            relay_errors = []
            def relay_output():
                try:
                    _relay_provider_output(proc.stdout, sink)
                except Exception:
                    relay_errors.append(True)
            relay = threading.Thread(target=relay_output, daemon=True)
            relay.start()
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        _stop_provider_group(proc)
                        break
                    try:
                        proc.wait(timeout=min(activity_feed.POLL_SECONDS, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        current = time.monotonic()
                        _publish_activity(activity)
                        elapsed = int(current - started)
                        if current >= heartbeat_due:
                            note(f'AI provider still running: elapsed={elapsed}s; '
                                 f'local_log_bytes={log.stat().st_size}')
                            heartbeat_due = current + 60
            except BaseException:
                _stop_provider_group(proc)
                raise
            finally:
                relay.join(timeout=1)
                if relay.is_alive():
                    # A child retaining stdout must not keep the log relay alive.
                    _stop_provider_group(proc)
                    proc.stdout.close()
                    relay.join(timeout=1)
                else:
                    proc.stdout.close()
            if relay_errors:
                raise provider_runtime.ProviderUnavailable('Provider output could not be recorded safely.')
            sink.flush()
    except BaseException:
        _publish_activity(activity, force=True, returncode=1)
        # os.fdopen owns descriptor after successful construction. On errors
        # before ownership is transferred, close the raw descriptor safely.
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    _publish_activity(activity, force=True,
                      returncode=124 if timed_out else proc.returncode)
    output = _tail_file(log)
    return subprocess.CompletedProcess(
        cmd, 124 if timed_out else proc.returncode, stdout=output,
        stderr=f'Provider session timed out after {int(timeout)} seconds' if timed_out else '')


def note(s):
    s = provider_runtime.redact_diagnostic(s)
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {s}', flush=True)
    # NOVA3_WORKER_PRESENCE_V1: nonblocking best-effort status; never logs credentials.
    reporter = globals().get('_nova_presence')
    if reporter is not None:
        try: reporter.signal_from_log(s)
        except Exception: pass


def git(cwd,*args,check=True):
    return run(['git',*args],cwd=cwd,check=check)


def get_repo(default=None):
    if default:return default
    cp=run(['gh','repo','view','--json','nameWithOwner','--jq','.nameWithOwner'])
    return cp.stdout.strip()


def claim(gh, issue, user, maxwait=100, saved=None):
    token = (saved or {}).get('token')
    lease = lease_get(issue.get('body'))
    if (token and lease and lease_active(lease)
            and lease.get('owner') == user and lease.get('token') == token):
        note(f'Resuming existing claim for #{issue["number"]}')
        gh.comment(issue['number'], f'/nova heartbeat {token}')
        return token
    token = str(uuid.uuid4())
    n=issue['number']
    gh.comment(n, f'/nova claim {token}')
    note(f'Requested task #{n}; waiting for coordinator claim')
    until=time.monotonic()+maxwait
    while time.monotonic()<until:
        current=gh.issue(n)
        lease=lease_get(current.get('body'))
        if lease and lease.get('owner')==user and lease.get('token')==token and lease_active(lease):
            return token
        if 'nova:review' in label_names(current):break
        if lease and lease_active(lease):break
        time.sleep(5)
    note(f'No claim for #{n}; skip (coordinator disabled or another worker claimed it)')
    return None


@lru_cache(maxsize=1)
def _codex_json_supported():
    """One bounded local help probe, never launch a model or require a login."""
    path = shutil.which('codex')
    if not path or pathlib.Path(path).suffix.lower() in ('.cmd', '.bat'):
        return False
    result = provider_runtime._probe([path, 'exec', '--help'])
    return result.returncode == 0 and '--json' in result.output.split()


def command_for(provider, prompt, status=None, aux_dir=None):
    # A legacy status may lack capability tokens (help text drift); fall back to
    # the direct bounded probe rather than silently dropping --json.
    codex_json = False
    if provider == 'codex':
        codex_json = ('--json' in status.capabilities) if status is not None else False
        if not codex_json:
            codex_json = _codex_json_supported()
    return provider_runtime.build_command(
        provider, prompt, status=status, aux_dir=aux_dir, codex_json=codex_json)


def hermes_command(prompt, work, aux_dir, status=None, config=None):
    """Build one bounded, file-based Hermes worker session.

    Task text is written to a 0600 prompt file inside the private round
    directory and referenced with ``--query-file``; it never enters argv, the
    environment or a shell. Readiness and operator configuration are
    re-validated here, immediately before launch, so a stale probe or a mutated
    environment cannot start an unverified session. The role is fixed to
    worker: a planner configuration can never be reused for coding.
    """
    config = config or hermes_runtime.load_config(
        hermes_runtime.Role.WORKER, status=status,
        timeout_seconds=ai_session_timeout())
    # Re-probe at the launch boundary. A readiness record captured when the
    # worker started (or an "available" string) is not proof that the binary
    # and required flags still exist; worker rounds can run for hours.
    if status is None or not hermes_runtime.fresh(status):
        status = hermes_runtime.probe(config.executable)
    ready = hermes_runtime.assert_ready(status, role=config.role)
    prompt_path = hermes_runtime.materialise_prompt(aux_dir, prompt)
    return hermes_runtime.build_invocation(
        ready, role=config.role, prompt_path=prompt_path, cwd=str(work),
        provider=config.provider, model=config.model, budget=config.budget)


def allowed_from_issue(body):
    for line in (body or '').splitlines():
        if line.startswith('Allowed paths:'):
            return [s.strip().rstrip('/') for s in line.split(':',1)[1].split(',') if s.strip()]
    return []


# --- Priority schema (issue #135 regression: malformed Priority killed queue) ---
# Documented schema: at most one line, exactly `Priority: <decimal integer>`
# with optional intra-line spaces/tabs after the colon, value 0..999. No
# suffixes, punctuation, sign, fraction, wrap or extra lines. Missing is
# 50 by documented default; present-but-invalid is quarantined, never guessed.
PRIORITY_DEFAULT = 50
PRIORITY_MAX = 999
_PRIORITY_LINE = re.compile(r'Priority:[ \t]*([0-9]{1,4})[ \t]*')
_PRIORITY_PREFIX = re.compile(r'Priority:[ \t]*')


@dataclass(frozen=True)
class Priority:
    value: int | None
    error: str | None = None


def parse_priority(body):
    """Strict, deterministic Priority parser. Never raises on task text."""
    text = body if isinstance(body, str) else ''
    lines = [line for line in text.splitlines() if _PRIORITY_PREFIX.match(line)]
    if not lines:
        return Priority(PRIORITY_DEFAULT)
    if len(lines) > 1:
        return Priority(None, 'duplicate')
    line = lines[0]
    match = _PRIORITY_LINE.fullmatch(line)
    if not match:
        if _PRIORITY_PREFIX.fullmatch(line):
            return Priority(None, 'empty_or_wrapped')
        return Priority(None, 'non_numeric_or_suffixed')
    value = int(match.group(1))
    if value > PRIORITY_MAX:
        return Priority(None, 'out_of_range')
    return Priority(value)


def priority_sort_key(issue, *, claimed=False):
    """Total order that is safe for malformed bodies (validated upstream)."""
    priority = parse_priority(issue.get('body', ''))
    value = priority.value if priority.value is not None else PRIORITY_DEFAULT
    return (0 if claimed else 1, -value, issue.get('number', 0))


def quarantine_priority(gh, root, issue, reason):
    """Skip one malformed task with one concise diagnostic; never launch AI."""
    number = issue.get('number')
    digest = hashlib.sha256((str(reason) + '\0' + str(issue.get('body') or '')).encode('utf-8')).hexdigest()[:16]
    previous = recovery.load(root, number)
    if previous.get('priority_diagnostic') == digest:
        note(f'Skipping #{number}: Priority {reason} (diagnostic already posted)')
        return False
    recovery.update(root, number, priority_error=reason, priority_diagnostic=digest,
                    retry_after=int(time.time()) + 86400, phase='blocked')
    note(f'QUARANTINED #{number}: malformed Priority ({reason}); no AI session will start. '
         'Expected exactly one line "Priority: <integer 0-999>".')
    try:
        gh.comment(number, (
            f'⚠️ NOVA3 worker skipped #{number} without starting an AI session: `Priority:` is '
            f'invalid (`{reason}`). Expected exactly one line `Priority: <integer 0-999>`; '
            f'suffixes, punctuation, signs, fractions, wrapping and duplicate lines are rejected. '
            f'An owner/admin should correct the task body and requeue it.'))
    except Exception as exc:
        note(f'WARN #{number}: could not post Priority diagnostic: {type(exc).__name__}')
    return False


def admit_queue(gate, issues, root):
    """Filter the queue BEFORE any provider launch; never guess authorization."""
    admitted = []
    for issue in issues:
        if gate is None:
            note(f'Skipping #{issue["number"]}: owner/admin approval gate unavailable')
            continue
        decision = gate.verify(issue)
        if not decision.allowed:
            previous = recovery.load(root, issue['number'])
            if previous.get('admission_reason') != decision.reason:
                recovery.update(root, issue['number'], admission_reason=decision.reason,
                                retry_after=int(time.time()) + 3600)
            note(f'Skipping #{issue["number"]}: queue admission denied ({decision.reason}); '
                 'no AI session will start. See docs/progress/OWNER_APPROVAL_GATE.md')
            continue
        recovery.update(root, issue['number'], approval_id=decision.approval_id,
                        approval_binding=decision.binding,
                        task_text_sha256=decision.task_text_sha256)
        admitted.append(issue)
    return admitted


def revalidate_before_publish(gh, gate, number):
    """Recheck owner approval immediately before pushing/opening a PR.

    No legacy/backfill bypass exists: a lease without a captured approval
    binding cannot publish. Such work is quarantined for fresh owner review.
    """
    issue = gh.issue(number)
    lease = lease_get(issue.get('body'))
    if not lease_active(lease):
        # The claim was lost/expired; another worker may own the task now.
        return QueueDecision(False, 'lease_inactive')
    return gate.revalidate(issue, lease)


def record_worker_failure(root, code, cause):
    """Preserve the deliberate exit cause instead of systemd start-limit-hit."""
    cause = provider_runtime.redact_diagnostic(str(cause))[:500]
    payload = {'exit_code': int(code), 'cause': cause,
               'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    try:
        path = pathlib.Path(root)/'worker-failure.json'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        pass
    note(f'WORKER EXIT {int(code)}: {cause}')
    return payload


def stop_worker(root, code, cause):
    record_worker_failure(root, code, cause)
    raise SystemExit(int(code))


def changed_paths(work):
    # NOVA3_CHANGED_FILE_ENUMERATION_V2: git status -uall gives leaf files.
    # This prevents untracked directories from failing valid task scopes.
    from .path_inventory import changed_files
    return changed_files(work)



def validate_scope(work,allowed):
    if not allowed:raise RuntimeError('Task has no Allowed paths: field; failing closed')
    changed=changed_paths(work)
    bad=[]
    for path in changed:
        if not any(path==a or path.startswith(a+'/') for a in allowed):
            bad.append(path)
    if bad:raise RuntimeError(f'Unapproved path changes: {bad[:20]}')
    bad=guard_check(pathlib.Path(work),changed)
    if bad:raise RuntimeError(f'Forbidden files: {bad[:20]}')
    return changed


# NOVA_PR_OWNER_FIX_V1: distinguish a real fork from a redirected old repository URL.
def canonical_repo_name(requested):
    cp=run(['gh','repo','view',requested,'--json','nameWithOwner','--jq','.nameWithOwner'],
           timeout=90,check=False)
    if cp.returncode!=0 or not cp.stdout.strip():
        return None
    return cp.stdout.strip()


def can_push_to_repo(upstream):
    cp=run(['gh','api',f'repos/{upstream}','--jq','.permissions.push'],timeout=90,check=False)
    return cp.returncode==0 and cp.stdout.strip().lower()=='true'


def pr_head(upstream,account,branch):
    """Use bare branch for upstream branches, user:branch for genuine forks."""
    resolved=canonical_repo_name(account)
    if not resolved:
        raise RuntimeError(f'Cannot resolve GitHub head repository {account}; check access')
    if resolved.casefold()==upstream.casefold():
        return branch
    if resolved.casefold()!=account.casefold():
        raise RuntimeError(f'Fork address {account} redirects to {resolved}; cannot submit as a fork')
    return f'{resolved.split("/",1)[0]}:{branch}'


def prepare_checkout(upstream,user,root,issue):
    state=recovery.resume_info(root, issue['number'], upstream, user)
    if state is not None:
        work=pathlib.Path(state['work'])
        branch=state['branch']
        current=git(work,'branch','--show-current',check=False)
        if current.returncode==0 and current.stdout.strip()==branch:
            note(f'Reusing saved checkout {work} (no files reset)')
            return work,branch,state['account']
        raise RuntimeError(f'Saved checkout {work} is on an unexpected branch; refusing to overwrite')
    name=upstream.split('/')[-1]
    # Owner/maintainer with write access can submit branches directly to the upstream.
    # Read-only contributors must have a real fork, NOT a redirect from a transfer.
    if can_push_to_repo(upstream):
        account=upstream
    else:
        account=f'{user}/{name}'
        resolved=canonical_repo_name(account)
        if not resolved or resolved.casefold()!=account.casefold():
            if resolved:
                note(f'{account} resolves to {resolved}; that is not a fork')
            note(f'Creating contributor fork {account}')
            cp=run(['gh','repo','fork',upstream,'--clone=false'],timeout=180,check=False)
            if cp.returncode:
                raise RuntimeError('Unable to create a private fork. Check the organisation fork policy and contributor access. '+(cp.stderr or '')[-350:])
            resolved=canonical_repo_name(account)
            if not resolved or resolved.casefold()!=account.casefold():
                raise RuntimeError(f'Expected fork {account}, but GitHub resolves it to {resolved!r}. Refusing to push to the wrong repository.')
    work=root/(str(issue['number'])+'-'+uuid.uuid4().hex[:6])
    work.parent.mkdir(parents=True,exist_ok=True)
    run(['gh','repo','clone',account,str(work),'--','--quiet'], timeout=180)
    git(work,'remote','remove','upstream',check=False)
    git(work,'remote','add','upstream',f'https://github.com/{upstream}.git')
    run(['git','fetch','upstream','main'],cwd=work,timeout=120)
    branch=f'nova/issue-{issue["number"]}-{uuid.uuid4().hex[:6]}'
    git(work,'checkout','-b',branch,'upstream/main')
    recovery.update(root,issue['number'],repo=upstream,user=user,work=str(work.resolve()),
                    branch=branch,account=account,phase='checkout')
    return work,branch,account


def session_prompt(issue,round_num,prior,allowed,binary_root):
    body=issue.get('body','')
    return f'''You are an engineering agent working on the N.O.V.A. 3 source reconstruction.
Read AGENTS.md and docs/NOVA_TEAM.md before edits. Never patch an APK, shared library or game executable.
Do not run sudo, install system packages, access network resources, or modify GitHub state.
Do not upload, copy, quote extensively or commit original game binaries, decompiler dumps, copyrighted assets, credentials or local analysis files.
This is one disposable, non-interactive session. Use the repository and task-specific handoff files; never rely on previous chat history.
TASK #{issue['number']}: {issue['title']}
{body}
ALLOWED EDIT PATHS (strict): {', '.join(allowed)}
Round: {round_num}. Existing handoff:
{prior[-7000:]}
Local original binaries available? {f'Local read-only reference directory: {binary_root}. May inspect locally, but NEVER commit or upload game binaries or large decompiler exports.' if binary_root else 'Not provided; do not invent matching results.'}
Do useful verifiable engineering work inside allowed paths. Prefer small complete source+tests over guesses.
For matching claims give compiler version, flags, source hash, reference hash, and machine-code comparison evidence; otherwise explicitly state NOT MATCHED.
At session end create or update docs only if docs/worker-notes/ is allowed. Otherwise provide a concise factual final handoff: files, tests, blockers, next attempts.
Do not start nested AI sessions. End after this bounded slice; supervisor handles further sessions.'''


def verify(workdir):
    """Independent bounded checks, all build artifacts outside the tracked checkout."""
    root=pathlib.Path(workdir)
    checks=[[sys.executable,'-m','unittest','discover','-s','nova3_team/tests','-v']]
    if (root/'CMakeLists.txt').exists() and shutil.which('cmake'):
        scratch=root.parent/'.nova-build'/root.name
        scratch.mkdir(parents=True,exist_ok=True)
        checks.extend([
            ['cmake','-S',str(root),'-B',str(scratch),'-DCMAKE_BUILD_TYPE=Debug'],
            ['cmake','--build',str(scratch),'-j','2'],
            ['ctest','--test-dir',str(scratch),'--output-on-failure'],
        ])
    feedback=[]
    for cmd in checks:
        try:
            cp=run(cmd,cwd=root,timeout=480,check=False)
            feedback.append(f'CMD: {cmd}\nEXIT: {cp.returncode}\n{(cp.stdout or "")[-1500:]}\n{(cp.stderr or "")[-1500:]}')
            if cp.returncode:return False,'\n'.join(feedback)
        except (OSError,subprocess.TimeoutExpired) as e:
            feedback.append(f'Command failed: {cmd}: {e}')
            return False,'\n'.join(feedback)
    return True,'\n'.join(feedback)


def record_handoff(workdir,round_num,cmd,cp,changes,streamed=False):
    log=private_round_log(workdir,round_num)
    folder=log.parent
    if not streamed:
        log.write_text(provider_runtime.redact_diagnostic(
            (cp.stdout or '')[-12000:]+'\nSTDERR:\n'+(cp.stderr or '')[-3000:]),encoding='utf-8')
        if os.name == 'posix':
            log.chmod(0o600)
    snippet=provider_runtime.redact_diagnostic((cp.stdout or '')[-3500:])
    handoff=folder/'handoff.md'
    handoff.write_text(f'Round {round_num}; exit={cp.returncode}\n'
                       f'Changed files: {changes}\n'
                       f'Private full output: {log.name}\n'
                       f'Provider diagnostic: {provider_runtime.redact_diagnostic(cp.stderr or "")[:400]}\n'
                       f'Last model output:\n{snippet}\n',encoding='utf-8')
    if os.name == 'posix':
        handoff.chmod(0o600)
    return handoff.read_text()


def pause_failed_task(gh, root, number, token, provider, user, error):
    """Fail-closed, externally visible state without retry/comment storms.

    Never delete local checkouts. GitHub coordinator is the sole label writer.
    """
    error = provider_runtime.redact_diagnostic(error)
    previous = recovery.load(root, number)
    decision = reliability.failure_decision(previous, error)
    recovery.update(root, number,
                    failure_key=decision['key'], failure_code=decision['code'],
                    failure_count=decision['count'],
                    retry_after=int(time.time())+(86400 if decision['blocked'] else decision['delay']),
                    last_error=str(error)[:600],
                    phase=(previous.get('phase') if previous.get('phase') in ('pushed','committed','pr_created')
                           else 'blocked' if decision['blocked'] else previous.get('phase', 'error')))
    if decision['blocked']:
        note(f'ALERT #{number}: BLOCKED ({decision["code"]}). Existing checkout retained. '
             'No more automatic attempts until a maintainer fixes the cause.')
        try:
            gh.comment(number, f'/nova block {token} {decision["code"]}')
        except Exception as exc:
            note(f'ALERT #{number}: unable to notify GitHub coordinator: {exc}')
    else:
        note(f'ALERT #{number}: transient worker failure. 10-minute cooldown; '
             'one identical recurrence will block the task.')
        try:
            gh.comment(number, f'/nova release {token}')
        except Exception as exc:
            note(f'ALERT #{number}: unable to release claim: {exc}')
    return False


def work_one(gh,upstream,user,issue,provider,root,rounds,binary_root,provider_status=None,gate=None,guard=None):
    n=issue['number']
    if guard is not None:
        # Local collision guard: two slots on this machine cannot work the same
        # issue id. The GitHub lease remains the cross-machine authority.
        try:
            guard.claim(str(n))
        except WorkIdConflict as exc:
            note(f'Skipping #{n}: {exc}')
            return None
    prior=recovery.load(root,n)
    token=claim(gh,issue,user,saved=prior)
    if token is None:return None
    recovery.update(root,n,repo=upstream,user=user,token=token)
    allowed=allowed_from_issue(issue.get('body',''))
    if not allowed:
        note(f'Skipping #{n}: missing Allowed paths')
        gh.comment(n,f'/nova release {token}')
        return False
    try:
        work,branch,account=prepare_checkout(upstream,user,root,issue)
        note(f'#{n} working in {work}')
        # Previously completed provider sessions are intentionally disposable.
        handofffile=work.parent/'.nova-private'/work.name/'handoff.md'
        handoff=handofffile.read_text(encoding='utf-8') if handofffile.exists() else 'No previous attempts.'
        existing=git(work,'status','--short',check=False).stdout.strip()
        if existing:
            handoff+='\nINTERRUPTED SESSION: existing uncommitted changes (preserve and inspect):\n'+existing[-2200:]
        verified=False
        checklog='No verification run'
        phase=recovery.load(root,n).get('phase','checkout')
        if phase not in ('committed','pushed','pr_created'):
            # NOVA3_COMPLETED_SESSION_RECOVERY_V2: never rerun a successful
            # Codex session just because prior file-scope validation failed.
            recovered_ready = False
            saved = recovery.load(root,n)
            if (saved.get('provider_exit') == 0
                    and saved.get('phase') in ('blocked','running','checkpoint')
                    and git(work,'status','--short',check=False).stdout.strip()):
                existing_files = validate_scope(work,allowed)
                if existing_files:
                    verified,checklog=verify(work)
                    recovered_ready = True
                    note(f'#{n} reusing completed AI output: {len(existing_files)} files; '
                         f'independent tests: {"PASS" if verified else "FAIL (draft PR)"}')
            for k in ([] if recovered_ready else range(1,rounds+1)):

                gh.comment(n,f'/nova heartbeat {token} AI_RUNNING')
                current_calls=int(recovery.load(root,n).get('provider_calls',0))
                recovery.update(root,n,phase='running',round=k,
                                provider_calls=current_calls+1,
                                provider_started_unix=int(time.time()),
                                provider_exit=None)
                prompt=session_prompt(issue,k,handoff,allowed,binary_root)
                log=private_round_log(work,k)
                # Bounded agent/profile files live beside the private log only.
                aux_dir=log.parent/'provider-aux'
                provider_env_extra={}
                if provider == 'hermes':
                    # File-based adapter: prompt stays in a private 0600 file.
                    invocation=hermes_command(prompt,work,aux_dir,provider_status)
                    cmd=list(invocation.argv)
                    provider_env_extra=dict(invocation.env)
                else:
                    provider_runtime.prepare_aux_files(provider,aux_dir)
                    cmd=command_for(provider,prompt,status=provider_status,aux_dir=aux_dir)
                note(f'#{n} AI session {k}/{rounds} ({provider}); provider launched')
                structured=provider == 'codex' and '--json' in cmd[2:-1]
                activity=activity_feed.ActivityFeed(log, structured=structured)
                provider_env={**os.environ, **provider_runtime.build_environment(provider),
                              **provider_env_extra}
                cp=run_provider(cmd,work,log,timeout=ai_session_timeout(),activity=activity,
                                env=provider_env)
                recovery.update(root,n,provider_completed_unix=int(time.time()),
                                provider_exit=cp.returncode)
                try:
                    gh.comment(n,f'/nova heartbeat {token} AI_FINISHED')
                except Exception as exc:
                    note(f'WARN #{n}: GitHub activity signal unavailable: {exc}')
                note(f'#{n} Codex/AI session finished: exit={cp.returncode}; checking changed files')
                paths=validate_scope(work,allowed)
                _publish_activity(activity, force=True, returncode=cp.returncode,
                                  file_changed_count=len(paths))
                recovery.update(root,n,changed_files=len(paths))
                handoff=record_handoff(work,k,cmd,cp,paths,streamed=True)
                recovery.update(root,n,phase='checkpoint',round=k)
                if paths:
                    verified,checklog=verify(work)
                    handoff+='\nINDEPENDENT VALIDATION OUTPUT (abbreviated):\n'+checklog[-4500:]
                if cp.returncode==124:
                    note(f'ALERT #{n}: AI provider TIMEOUT; private output and any local edits '
                         f'are retained. Quarantining instead of repeating a stalled session.')
                    return pause_failed_task(
                        gh,root,n,token,provider,user,
                        RuntimeError('Provider session timed out; inspect local session log before retry'))
                if cp.returncode==0 and paths and verified:break
                if cp.returncode!=0:note(f'Session {k} returned {cp.returncode}; bounded retry with fresh context')
            paths=validate_scope(work,allowed)
            if not paths:
                return pause_failed_task(gh,root,n,token,provider,user,
                                         RuntimeError('No source changes after bounded AI sessions'))
            git(work,'add','--',*paths)
            staged=git(work,'diff','--cached','--name-only','-z').stdout.split('\0')
            bad=guard_check(pathlib.Path(work),[x for x in staged if x])
            if bad:raise RuntimeError(f'Guard rejected staged files: {bad}')
            if run(['git','diff','--cached','--quiet'],cwd=work,check=False).returncode==0:
                gh.comment(n,f'/nova release {token}')
                return False
            git(work,'commit','-m',f'Reconstruction task #{n}: {issue["title"][:55]}')
            recovery.update(root,n,phase='committed',verified=verified,checklog=checklog[-1600:])
        current=recovery.load(root,n)
        verified=current.get('verified',False)
        checklog=current.get('checklog','No verification run')
        if gate is not None and current.get('phase') in ('committed','pushed'):
            # Revalidate owner/admin approval with fresh evidence immediately
            # before any push or PR; never publish unapproved work. Leases
            # without captured approval metadata are quarantined, not published.
            approval = revalidate_before_publish(gh,gate,n)
            if not approval.allowed:
                return pause_failed_task(
                    gh,root,n,token,provider,user,
                    RuntimeError(f'Owner approval no longer valid before publish: {approval.reason}'))
        if current.get('phase')=='committed':
            git(work,'push','-u','origin',branch)
            recovery.update(root,n,phase='pushed')
        current=recovery.load(root,n)
        if current.get('phase')=='pushed':
            # GitHub redirects old owner paths after a repository transfer. Resolve
            # the ACTUAL owner of the pushed branch before creating a PR.
            head=pr_head(upstream,account,branch)
            remote=git(work,'ls-remote','--heads','origin',branch,check=False)
            if remote.returncode or not remote.stdout.strip():
                raise RuntimeError(f'Pushed branch {branch} is missing from origin; refusing to invent a PR')
            note(f'#{n} PR source: {head} -> {upstream}:main')
            existing=run(['gh','pr','list','--repo',upstream,'--head',head,
                          '--state','all','--json','url'],cwd=work,timeout=90).stdout
            prs=json.loads(existing or '[]')
            if prs:
                pr=prs[0]['url']
            else:
                desc=f'Closes #{n}\n\nAutomated contribution. AI provider: {provider}. Local checks: {"PASS" if verified else "FAIL (draft PR)"}. Independently verify in CI. Byte matching is NOT claimed without reproducible evidence.\n\nLast local verification: {checklog[-800:]}'
                args=['gh','pr','create','--repo',upstream,'--head',head,
                      '--base','main','--title',f'#{n}: {issue["title"]}','--body',desc]
                if not verified:args.append('--draft')
                pr=run(args,cwd=work,timeout=120).stdout.strip()
            recovery.update(root,n,phase='pr_created',pr=pr,retry_after=0,pr_failures=0,
                            failure_count=0,failure_key='',failure_code='')
        current=recovery.load(root,n)
        if current.get('phase')=='pr_created':
            gh.comment(n,f'/nova submitted {token} {current["pr"]}')
            recovery.update(root,n,phase='review')
            note(f'PR submitted: {current["pr"]}')
            return True
        return False
    except Exception as e:
        note(f'FAILED #{n}: {e}')
        # Preserve existing commits, pushed PR head and all uncommitted work.
        return pause_failed_task(gh,root,n,token,provider,user,e)


# NOVA3_SOLO_WORKER_COMPAT_V2
# Inventory discovery is performed by the local sidecar, not an AI coding job.
# Exclude *only* queued, generated expansion jobs. Preserve other issue states
# and all ordinary source-reconstruction tasks.
def _solo_eligible_issues(issues):
    return [issue for issue in issues if not (
        'nova:queued' in label_names(issue) and
        (issue.get('title') or '').startswith('Expand verified ARM ELF function inventory') and
        '<!-- NOVA3_AUTOTASK_KEY: expand-evidence-' in (issue.get('body') or ''))]


def _kick_local_discovery_if_due():
    # An idle coding queue is the best time to run optional Ghidra. The
    # systemd service runs its own RAM/swap/PSI guard and never runs Codex.
    if not sys.platform.startswith('linux') or not shutil.which('systemctl'):
        return False
    if not (pathlib.Path.home()/'.config/systemd/user/nova3-discovery.service').is_file():
        return False
    # Respect explicit user choices to disable scanning (e.g. after OOM).
    # Do not silently restart a service the contributor intentionally disabled.
    try:
        enabled=subprocess.run(['systemctl','--user','is-enabled','nova3-discovery.timer'],
                               capture_output=True,text=True,timeout=10,check=False)
        if enabled.returncode or enabled.stdout.strip() not in ('enabled','enabled-runtime'):
            return False
    except (OSError,subprocess.TimeoutExpired):
        return False
    stamp=pathlib.Path.home()/'.cache/nova3-team/discovery-idle-kick.txt'
    try:
        last=float(stamp.read_text()) if stamp.is_file() else 0.0
        if time.time()-last < 30*60:
            return False
    except (OSError,ValueError):
        pass
    try:
        stamp.parent.mkdir(parents=True,exist_ok=True)
        # Write before requesting to prevent repeated service activations.
        stamp.write_text(str(time.time()))
        cp=subprocess.run(['systemctl','--user','start','--no-block','nova3-discovery.service'],
                          capture_output=True,text=True,timeout=15,check=False)
        if cp.returncode==0:
            note('Requested background function discovery; resource guard controls whether it scans.')
            return True
        note('Discovery service unavailable; timer will retry (coding tasks unaffected).')
    except (OSError,subprocess.TimeoutExpired):
        note('Discovery activation deferred; coding tasks unaffected.')
    return False



# ---------------------------------------------------------------------------
# NOVA3_MULTI_PROVIDER_SLOTS_V1 (issue #126)
#
# Bounded multi-provider slots: stable per-worker identity, OS-local process
# locks, slot-scoped state/log/worktree paths, a fair scheduler with failure
# isolation, and a guided launcher that refuses to start more than the
# reviewed resource budget. Nothing here weakens the #149/#135 queue gate:
# `launch_slots` consults the owner/admin approval gate and pinned deployment
# preflight BEFORE any provider process is created, and per-slot publishing
# still revalidates approval inside `work_one`.
# ---------------------------------------------------------------------------

DEFAULT_SLOT = 'slot-1'
MAX_SLOTS = 4                      # N5100: 4 physical cores; hard ceiling.
DEFAULT_TOTAL_PROCESSES = 1        # Conservative default, explicit consent to raise.
_SLOT_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,23}')
_ACCOUNT_RE = re.compile(r'[A-Za-z0-9-]{1,39}')
_SECRET_RE = re.compile(
    r'(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
    r'|sk-[A-Za-z0-9_-]{16,}|password\s*[=:]|passwd\s*[=:]|api[_-]?key\s*[=:]'
    r'|bearer\s+[A-Za-z0-9._-]{12,})')
_RATE_LIMIT = ('429', 'rate limit', 'rate_limit', 'quota', 'too many requests',
               'insufficient_quota', 'usage limit')


class SlotError(RuntimeError):
    """Fixed diagnostic; never contains a token, prompt or provider log."""


class SlotLimitError(SlotError):
    """Requested concurrency exceeds the reviewed local budget."""


class IdentityError(SlotError):
    """Persisted worker identity cannot be safely reused."""


class IsolationError(SlotError):
    """Two slots would share a mutable local path."""


class LockHeld(SlotError):
    """A process lock is already owned elsewhere on this machine."""


class WorkIdConflict(SlotError):
    """Another local slot already owns this work id."""


class LaunchRefused(SlotError):
    """A launch precondition (gate, budget, lock) was not satisfied."""


def _atomic_json(path, payload):
    """Durable same-directory replace; never truncates a good file in place."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    return path


def state_root(repo):
    """Per-repository shared root; slot directories live beneath it."""
    return pathlib.Path.home()/'.cache'/'nova3-team'/str(repo).replace('/', '_')


def validate_slot_id(value):
    text = str(value or '')
    if not _SLOT_RE.fullmatch(text):
        raise SlotError('slot id must match [a-z0-9][a-z0-9-]{0,23}')
    return text


@dataclass(frozen=True)
class SlotPaths:
    repository_root: object
    slot: str
    root: object
    state: object
    worktrees: object
    cache: object
    logs: object
    identity: object
    presence: object


def slot_paths(repository_root, slot_id, *, create=False):
    """Every mutable path for one slot; accepts PurePath roots for tests."""
    slot = validate_slot_id(slot_id)
    root = pathlib.Path(repository_root) if not hasattr(repository_root, 'joinpath') else repository_root
    base = root / '.nova-slots' / slot
    paths = SlotPaths(
        repository_root=root, slot=slot, root=base,
        state=base / 'state', worktrees=base / 'worktrees', cache=base / 'cache',
        logs=base / 'logs', identity=base / 'identity', presence=base / 'presence')
    if create:
        for folder in (paths.state, paths.worktrees, paths.cache, paths.logs,
                       paths.identity, paths.presence):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == 'posix':
                folder.chmod(0o700)
    return paths


def assert_slot_isolation(repository_root, slot_ids):
    """Refuse aliasing slots: each must own distinct mutable directories."""
    seen = {}
    for slot_id in slot_ids:
        paths = slot_paths(repository_root, slot_id)
        for name in ('state', 'worktrees', 'cache', 'logs', 'identity', 'presence'):
            key = os.path.normcase(str(getattr(paths, name)))
            if key in seen and seen[key] != paths.slot:
                raise IsolationError(
                    f'slots {seen[key]} and {paths.slot} share the {name} path; '
                    'every slot needs its own cache, log and worktree directory')
            seen[key] = paths.slot
    # A slot cache must never be the shared repository-level .cache directory.
    shared = os.path.normcase(str(slot_paths(repository_root, DEFAULT_SLOT).repository_root / '.cache'))
    for slot_id in slot_ids:
        if os.path.normcase(str(slot_paths(repository_root, slot_id).cache)) == shared:
            raise IsolationError('slot cache must not be the shared .cache file')
    return True


def machine_uuid(repository_root):
    """One persisted UUID per installation; stable across slots and restarts."""
    path = pathlib.Path(repository_root)/'identity'/'machine.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        value = data.get('machine_id') if isinstance(data, dict) else None
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value):
            return value
    except (FileNotFoundError, ValueError, OSError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = uuid.uuid4().hex
    payload = json.dumps({'schema': 1, 'machine_id': value}, sort_keys=True).encode('utf-8')
    # O_EXCL keeps concurrent first starts from adopting different UUIDs.
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            existing = data.get('machine_id') if isinstance(data, dict) else None
            if isinstance(existing, str) and re.fullmatch(r'[0-9a-f]{32}', existing):
                return existing
        except (ValueError, OSError):
            pass
        _atomic_json(path, {'schema': 1, 'machine_id': value})
        return value
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return value


@dataclass(frozen=True)
class WorkerIdentity:
    account: str
    machine_id: str
    slot: str
    provider: str

    @property
    def worker_id(self):
        return f'{self.account}:{self.machine_id}:{self.slot}:{self.provider}'

    def sanitized(self):
        return {'account': self.account, 'machine_id': self.machine_id,
                'slot': self.slot, 'provider': self.provider}


def worker_identity(repository_root, account, slot_id, provider):
    """GitHub account + per-machine UUID + explicit slot + provider.

    Reusing a slot file with a different account or provider fails closed:
    recovery state belongs to exactly one acknowledged worker identity.
    """
    if not _ACCOUNT_RE.fullmatch(str(account or '')):
        raise IdentityError('invalid GitHub account for worker identity')
    slot = validate_slot_id(slot_id)
    if provider not in provider_runtime.provider_ids():
        raise SlotError('unknown provider for worker identity')
    machine = machine_uuid(repository_root)
    path = slot_paths(repository_root, slot, create=True).identity/'worker.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError, OSError):
        data = None
    if isinstance(data, dict):
        stored_machine = data.get('machine_id')
        stored_account = data.get('account')
        stored_slot = data.get('slot')
        stored_provider = data.get('provider')
        if stored_machine != machine:
            raise IdentityError('slot identity belongs to a different machine UUID')
        if str(stored_account).casefold() != str(account).casefold():
            raise IdentityError(
                f'slot {slot} is bound to another GitHub account; use a distinct slot id')
        if stored_slot != slot:
            raise IdentityError('slot identity file does not match its directory')
        if stored_provider != provider:
            raise IdentityError(
                f'slot {slot} is bound to provider {stored_provider!r}; start a new slot instead')
    identity = WorkerIdentity(account, machine, slot, provider)
    _atomic_json(path, {'schema': 1, **identity.sanitized()})
    return identity


def migrate_legacy_state(repository_root, paths, *, slot=DEFAULT_SLOT):
    """Copy (never move) pre-slot recovery files into the default slot.

    Existing `.nova-state` records, failure records and saved checkouts stay
    exactly where they are, so a downgrade and an interrupted migration both
    remain recoverable. Only the first slot inherits legacy state.
    """
    repository_root = pathlib.Path(repository_root)
    if validate_slot_id(slot) != paths.slot:
        return {'migrated': [], 'retained': False}
    migrated = []
    source = repository_root/'.nova-state'
    target = pathlib.Path(paths.state)/'.nova-state'
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in sorted(source.glob('*.json')):
            destination = target/item.name
            if destination.exists():
                continue
            try:
                shutil.copy2(item, destination)
                migrated.append(item.stem)
            except OSError:
                continue
    failure = repository_root/'worker-failure.json'
    if failure.is_file():
        destination = pathlib.Path(paths.state)/'worker-failure.json'
        if not destination.exists():
            try:
                shutil.copy2(failure, destination)
                migrated.append('worker-failure')
            except OSError:
                pass
    return {'migrated': migrated, 'retained': source.is_dir()}


# --- OS-local atomic process locks (fcntl POSIX, msvcrt Windows) ------------

def lock_backend(platform_name=None):
    name = (platform_name or os.name or '').lower()
    return 'nt' if name in ('nt', 'win', 'windows', 'win32') else 'posix'


class _LockBackend:
    """Adapter so both lock APIs are testable on any host."""

    def __init__(self, kind, api=None):
        self.kind = 'nt' if kind == 'nt' else 'posix'
        self.api = api

    def module(self):
        if self.api is not None:
            return self.api
        if self.kind == 'nt':
            import msvcrt
            return msvcrt
        import fcntl
        return fcntl

    def acquire(self, descriptor):
        api = self.module()
        if self.kind == 'nt':
            try:
                api.locking(descriptor, api.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        try:
            api.flock(descriptor, api.LOCK_EX | api.LOCK_NB)
        except OSError:
            return False
        return True

    def release(self, descriptor):
        api = self.module()
        try:
            if self.kind == 'nt':
                os.lseek(descriptor, 0, 0)
                api.locking(descriptor, api.LK_UNLCK, 1)
            else:
                api.flock(descriptor, api.LOCK_UN)
        except OSError:
            pass


class ProcessFileLock:
    """Non-blocking exclusive lock held by the OS until release or process exit."""

    def __init__(self, path, *, backend=None, api=None, platform_name=None):
        self.path = pathlib.Path(path) if not hasattr(path, 'joinpath') else path
        self.kind = backend or lock_backend(platform_name)
        self._backend = _LockBackend(self.kind, api)
        self._descriptor = None

    @property
    def held(self):
        return self._descriptor is not None

    def acquire(self):
        self.release()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if self.kind == 'nt' and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'\0')
        os.lseek(descriptor, 0, 0)
        if not self._backend.acquire(descriptor):
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def release(self):
        if self._descriptor is not None:
            self._backend.release(self._descriptor)
            try:
                os.close(self._descriptor)
            except OSError:
                pass
            self._descriptor = None

    def __enter__(self):
        if not self.acquire():
            raise LockHeld(f'lock {pathlib.Path(self.path).name} is already held')
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def slot_lock_path(repository_root, slot_id):
    return pathlib.Path(repository_root)/'.nova-locks'/f'slot-{validate_slot_id(slot_id)}.lock'


def acquire_slot_lock(repository_root, slot_id, *, backend=None, api=None):
    lock = ProcessFileLock(slot_lock_path(repository_root, slot_id),
                           backend=backend, api=api)
    if not lock.acquire():
        raise LockHeld(
            f'slot {slot_id} already has an active worker process on this machine; '
            'one process per slot is enforced locally')
    return lock


def slot_is_active(repository_root, slot_id, *, backend=None, api=None):
    """Probe without keeping the lock: stale files from a crash are not owners."""
    probe = ProcessFileLock(slot_lock_path(repository_root, slot_id),
                            backend=backend, api=api)
    if probe.acquire():
        probe.release()
        return False
    return True


class CheckoutGuard:
    """Refuse two local slots working the same work id or checkout."""

    def __init__(self, repository_root, slot_id, *, backend=None, api=None):
        self.repository_root = pathlib.Path(repository_root)
        self.slot = validate_slot_id(slot_id)
        self.backend = backend
        self.api = api
        self._current = None
        self._lock = None

    def _path(self, work_id):
        key = str(work_id)
        digest = hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]
        return self.repository_root/'.nova-locks'/f'work-{digest}.lock'

    def claim(self, work_id):
        key = str(work_id or '')
        if not key or len(key) > 128:
            raise WorkIdConflict('work id must be a bounded non-empty string')
        if self._current == key and self._lock is not None and self._lock.held:
            return self._lock
        lock = ProcessFileLock(self._path(key), backend=self.backend, api=self.api)
        if not lock.acquire():
            raise WorkIdConflict(
                f'work id {key!r} is already active in another local slot; '
                'the GitHub lease is the cross-machine authority')
        self.release()
        self._current = key
        self._lock = lock
        return lock

    def release(self):
        if self._lock is not None:
            self._lock.release()
        self._lock = None
        self._current = None

    def checkout_path(self, work_id, label='work'):
        safe = re.sub(r'[^A-Za-z0-9._-]', '-', str(label))[:40] or 'work'
        digest = hashlib.sha256(f'{self.slot}\0{work_id}'.encode('utf-8')).hexdigest()[:8]
        return slot_paths(self.repository_root, self.slot).worktrees/f'{safe}-{digest}'


# --- Guided provider selection and resource budget --------------------------

def default_concurrency():
    """N5100 policy: one active AI process unless the human opts in."""
    return DEFAULT_TOTAL_PROCESSES


@dataclass(frozen=True)
class ResourceBudget:
    total_processes: int
    cores: int
    memory_mb: int

    def cap(self):
        by_memory = max(1, self.memory_mb//2048)  # ~2 GiB per provider session
        return max(1, min(MAX_SLOTS, self.cores, self.total_processes, by_memory))


def detected_memory_mb():
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        size = os.sysconf('SC_PAGE_SIZE')
        if pages > 0 and size > 0:
            return int(pages*size//(1024*1024))
    except (AttributeError, ValueError, OSError):
        pass
    return 4096  # conservative assumption when the platform cannot report it


def resource_budget(total=DEFAULT_TOTAL_PROCESSES, *, cores=None, memory_mb=None):
    total = int(total)
    if total < 1 or total > MAX_SLOTS:
        raise SlotLimitError(f'total AI processes must be 1..{MAX_SLOTS}')
    return ResourceBudget(total, int(cores or os.cpu_count() or 1),
                          int(memory_mb or detected_memory_mb()))


@dataclass(frozen=True)
class ProviderCandidate:
    provider: str
    installed: bool
    authenticated: bool
    detail: str = ''


def provider_candidates(provider_ids=None, *, probe=None, auth=None):
    """Installed + authenticated only; never initiates login or a paid prompt."""
    probe = probe or provider_runtime.probe_provider
    auth = auth or provider_runtime.check_auth
    out = []
    for provider_id in provider_ids or provider_runtime.provider_ids():
        status = probe(provider_id)
        evidence = auth(provider_id)
        state = getattr(getattr(evidence, 'state', None), 'value', str(getattr(evidence, 'state', '')))
        out.append(ProviderCandidate(
            provider_id, bool(getattr(status, 'available', False)),
            state == provider_runtime.AuthState.AUTHENTICATED.value,
            str(getattr(status, 'reason', '') or getattr(evidence, 'reason', ''))[:200]))
    return out


@dataclass(frozen=True)
class SlotSelection:
    slot: str
    provider: str


@dataclass(frozen=True)
class LaunchPlan:
    slots: tuple
    total: int
    consent: bool
    notes: tuple = ()


def select_slot_providers(candidates, *, total=DEFAULT_TOTAL_PROCESSES, consent=False,
                          allow_duplicate_provider=False, max_total=MAX_SLOTS):
    """Pick 1..N distinct installed+authenticated providers, fail closed."""
    total = int(total)
    if total < 1 or total > int(max_total) or total > MAX_SLOTS:
        raise SlotLimitError(f'total AI processes must be 1..{min(MAX_SLOTS, int(max_total))}')
    if total > 1 and not consent:
        raise SlotLimitError(
            'more than one active AI process requires explicit consent '
            '(--allow-multiple) and a reviewed resource budget')
    eligible = [c for c in candidates if c.installed and c.authenticated]
    notes = []
    for candidate in candidates:
        if candidate in eligible:
            continue
        if not candidate.installed:
            notes.append(f'{candidate.provider}: not installed')
        else:
            notes.append(f'{candidate.provider}: authentication unconfirmed')
    unique = []
    seen = set()
    for candidate in eligible:
        if candidate.provider in seen:
            continue
        seen.add(candidate.provider)
        unique.append(candidate)
    if allow_duplicate_provider and unique and len(unique) < total:
        pool = list(unique)
        index = 0
        while len(unique) < total and index < MAX_SLOTS*2:
            unique.append(pool[index % len(pool)])
            index += 1
        notes.append('duplicate provider reuse explicitly allowed')
    if len(unique) < total:
        raise SlotLimitError(
            f'requested {total} slot(s) but only {len(unique)} installed and '
            'authenticated provider(s) are available')
    chosen = unique[:total]
    slots = tuple(SlotSelection(f'slot-{index+1}', candidate.provider)
                  for index, candidate in enumerate(chosen))
    return LaunchPlan(slots, total, bool(consent), tuple(notes))


def assert_argv_safe(argv):
    """No shell, no NUL, no credential-shaped argument, no unbounded token."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise SlotError('provider argv must be a non-empty list')
    checked = []
    for item in argv:
        if not isinstance(item, str):
            raise SlotError('provider argv entries must be strings')
        if not item or '\x00' in item or len(item) > 4096:
            raise SlotError('provider argv entry is empty, NUL-containing or oversized')
        if _SECRET_RE.search(item):
            raise SlotError('refusing to pass a credential-shaped argument to a provider')
        checked.append(item)
    return checked


def slot_command(slot, provider, *, python=None, repo=None, once=False):
    argv = [python or sys.executable, '-m', 'nova3_team.worker', 'run',
            '--provider', provider, '--slot', validate_slot_id(slot)]
    if repo:
        argv += ['--repo', repo]
    if once:
        argv.append('--once')
    return assert_argv_safe(argv)


def preview_launch(plan, *, python=None, repo=None):
    """Command preview only; nothing is executed and no shell is involved."""
    if not isinstance(plan, LaunchPlan):
        raise SlotError('preview requires a LaunchPlan')
    return tuple({'slot': item.slot, 'provider': item.provider,
                  'argv': slot_command(item.slot, item.provider,
                                       python=python, repo=repo),
                  'shell': False}
                 for item in plan.slots)


# --- Launch authorization: gate first, always -------------------------------

@dataclass(frozen=True)
class LaunchAuthorization:
    allowed: bool
    reason: str
    admitted: int = 0


def authorize_launch(gate, issues, root):
    """Consult the owner/admin gate and pinned preflight before any launch."""
    if gate is None:
        return LaunchAuthorization(False, 'approval_gate_unavailable')
    try:
        preflight = getattr(gate, 'preflight', None)
        if callable(preflight):
            preflight()
    except Exception:
        return LaunchAuthorization(False, 'approval_gate_unavailable')
    admitted = admit_queue(gate, issues, root)
    if not admitted:
        return LaunchAuthorization(False, 'no_approved_tasks')
    return LaunchAuthorization(True, 'allowed', len(admitted))


@dataclass(frozen=True)
class LaunchResult:
    started: bool
    reason: str
    previews: tuple = ()
    processes: tuple = ()


def launch_slots(plan, *, gate, issues, repository_root, account=None, dry_run=False,
                 python=None, repo=None, cwd=None, popen=None, budget=None,
                 consent=False, backend=None, api=None):
    """Start the planned slots only after gate admission and local guards.

    Order is deliberate and must not be reordered: budget -> preview -> gate
    preflight -> queue admission -> slot activity probe -> provider spawn.
    """
    if not isinstance(plan, LaunchPlan):
        raise SlotError('launch requires a LaunchPlan')
    total = int(plan.total)
    if total < 1 or total > MAX_SLOTS:
        raise SlotLimitError(f'total AI processes must be 1..{MAX_SLOTS}')
    if total > 1 and not (plan.consent and consent):
        raise SlotLimitError('more than one active AI process needs explicit consent')
    if budget is not None and total > budget.cap():
        raise SlotLimitError(
            f'plan requests {total} process(es) but the local resource budget allows {budget.cap()}')
    previews = preview_launch(plan, python=python, repo=repo)
    if dry_run:
        return LaunchResult(False, 'dry_run', previews, ())
    if gate is None:
        return LaunchResult(False, 'approval_gate_unavailable', previews, ())
    root = slot_paths(repository_root, plan.slots[0].slot).state
    try:
        # authorize_launch performs the pinned deployment preflight and then
        # owner/admin queue admission; both happen before any spawn.
        authorization = authorize_launch(gate, issues, root)
    except Exception as exc:
        return LaunchResult(False, f'approval_gate_unavailable: {type(exc).__name__}', previews, ())
    if not authorization.allowed:
        return LaunchResult(False, authorization.reason, previews, ())
    # All local preconditions are checked BEFORE the first provider process.
    try:
        for item in plan.slots:
            slot_paths(repository_root, item.slot, create=True)
            if account:
                worker_identity(repository_root, account, item.slot, item.provider)
            if slot_is_active(repository_root, item.slot, backend=backend, api=api):
                raise LockHeld(f'slot {item.slot} already has an active worker process')
    except (SlotError, OSError) as exc:
        return LaunchResult(False, f'local_guard: {exc}', previews, ())
    spawn = popen or subprocess.Popen
    processes = []
    for preview in previews:
        try:
            handle = spawn(preview['argv'], cwd=cwd or None, shell=False,
                           stdin=subprocess.DEVNULL,
                           start_new_session=(os.name == 'posix'))
        except OSError:
            return LaunchResult(False, 'provider_spawn_failed', previews, tuple(processes))
        processes.append(handle)
    return LaunchResult(True, 'launched', previews, tuple(processes))


# --- Fair scheduling and failure isolation ----------------------------------

SLOT_STATES = frozenset({'idle', 'running', 'paused', 'cooldown', 'blocked', 'stopped'})


def is_rate_limit(error):
    text = str(error or '').casefold()
    return any(marker in text for marker in _RATE_LIMIT)


def rate_limit_backoff(attempt):
    """Bounded exponential backoff; never a tight retry loop."""
    attempt = max(1, int(attempt))
    return min(3600, 300*(2**(attempt-1)))


@dataclass
class SlotRuntime:
    slot: str
    provider: str
    state: str = 'idle'
    failures: int = 0
    failure_key: str = ''
    failure_code: str = ''
    cooldown_until: float = 0.0
    last_started: float = 0.0
    launches: int = 0
    window_start: float = 0.0
    process: object = None
    timeout_s: float = 0.0

    def sanitized(self, now=None):
        remaining = max(0, int(self.cooldown_until - (now if now is not None else time.time())))
        return {'slot': self.slot, 'provider': self.provider, 'state': self.state,
                'failures': self.failures, 'failure_code': self.failure_code,
                'cooldown_s': remaining if self.state == 'cooldown' else 0,
                'timeout_s': int(self.timeout_s)}


class SlotScheduler:
    """Per-slot timeout/cooldown, fair round-robin, no retry storms.

    It owns no provider prompts or logs; telemetry is fixed metadata only.
    """

    def __init__(self, *, total_processes=DEFAULT_TOTAL_PROCESSES, clock=None,
                 max_failures=2, launch_window=3600, max_launches=6):
        if int(total_processes) < 1 or int(total_processes) > MAX_SLOTS:
            raise SlotLimitError(f'total AI processes must be 1..{MAX_SLOTS}')
        self.total_processes = int(total_processes)
        self.clock = clock or time.time
        self.max_failures = max(1, int(max_failures))
        self.launch_window = max(60, int(launch_window))
        self.max_launches = max(1, int(max_launches))
        self.slots = {}

    def add(self, slot_id, provider, *, timeout_s=None):
        slot = validate_slot_id(slot_id)
        if slot in self.slots:
            raise SlotError(f'slot {slot} already exists')
        if len(self.slots) >= self.total_processes:
            raise SlotLimitError(
                f'local resource budget allows {self.total_processes} active slot(s)')
        if provider not in provider_runtime.provider_ids():
            raise SlotError('unknown provider')
        self.slots[slot] = SlotRuntime(slot, provider,
                                       timeout_s=float(timeout_s or ai_session_timeout()))
        return self.slots[slot]

    def get(self, slot_id):
        try:
            return self.slots[validate_slot_id(slot_id)]
        except KeyError:
            raise SlotError('unknown slot') from None

    def next_ready(self, now=None):
        now = self.clock() if now is None else float(now)
        ready = []
        for slot in self.slots.values():
            if slot.state == 'cooldown' and now >= slot.cooldown_until:
                slot.state = 'idle'
            if slot.state != 'idle':
                continue
            ready.append(slot)
        ready.sort(key=lambda item: (item.last_started, item.slot))
        return ready[0] if ready else None

    def start(self, slot_id, *, process=None, now=None):
        slot = self.get(slot_id)
        now = self.clock() if now is None else float(now)
        if slot.state != 'idle':
            raise SlotError(f'slot {slot.slot} is not idle ({slot.state})')
        if slot.window_start and now - slot.window_start < self.launch_window:
            if slot.launches >= self.max_launches:
                slot.state = 'cooldown'
                slot.cooldown_until = slot.window_start + self.launch_window
                raise SlotError('per-slot launch budget exhausted; cooling down')
        else:
            slot.window_start = now
            slot.launches = 0
        slot.state = 'running'
        slot.last_started = now
        slot.launches += 1
        slot.process = process
        return slot

    def mark_finished(self, slot_id, *, state='idle'):
        slot = self.get(slot_id)
        slot.process = None
        slot.state = state if state in SLOT_STATES else 'idle'
        return slot

    def pause(self, slot_id):
        slot = self.get(slot_id)
        if slot.state == 'running':
            slot.state = 'paused'
        return slot

    def resume(self, slot_id):
        slot = self.get(slot_id)
        if slot.state != 'paused':
            raise SlotError('only a paused slot can resume')
        slot.state = 'idle'
        return slot

    def stop(self, slot_id, *, terminate=None):
        """Stop exactly one slot; other slots keep their processes."""
        slot = self.get(slot_id)
        handle, slot.process = slot.process, None
        slot.state = 'stopped'
        if handle is not None and terminate is not None:
            terminate(handle)
        return slot

    def record_failure(self, slot_id, error, *, now=None):
        slot = self.get(slot_id)
        now = self.clock() if now is None else float(now)
        previous = {'failure_key': slot.failure_key, 'failure_count': slot.failures}
        decision = reliability.failure_decision(previous, error)
        slot.failures = decision['count']
        slot.failure_key = decision['key']
        slot.failure_code = 'RATE_LIMITED' if is_rate_limit(error) else decision['code']
        slot.process = None
        if is_rate_limit(error):
            slot.state = 'cooldown'
            slot.cooldown_until = now + rate_limit_backoff(slot.failures)
        elif decision['blocked'] or slot.failures >= self.max_failures:
            slot.state = 'blocked'
            slot.cooldown_until = now + 86400
        else:
            slot.state = 'cooldown'
            slot.cooldown_until = now + decision['delay']
        return {**decision, 'code': slot.failure_code,
                'retry_after': int(slot.cooldown_until - now), 'state': slot.state}

    def record_rate_limit(self, slot_id, retry_after=None, *, now=None):
        slot = self.get(slot_id)
        now = self.clock() if now is None else float(now)
        slot.failures += 1
        delay = int(retry_after) if retry_after else rate_limit_backoff(slot.failures)
        delay = max(60, min(3600, delay))
        slot.failure_code = 'RATE_LIMITED'
        slot.state = 'cooldown'
        slot.cooldown_until = now + delay
        slot.process = None
        return {'code': 'RATE_LIMITED', 'state': 'cooldown', 'retry_after': delay}

    def telemetry(self, now=None):
        now = self.clock() if now is None else float(now)
        rows = [slot.sanitized(now) for slot in self.slots.values()]
        return {'total': self.total_processes, 'configured': len(rows),
                'active': sum(row['state'] == 'running' for row in rows), 'slots': rows}

    def save(self, repository_root):
        for slot in self.slots.values():
            data = slot.sanitized()
            data['schema'] = 1
            data['cooldown_until'] = slot.cooldown_until
            data['failures'] = slot.failures
            _atomic_json(slot_paths(repository_root, slot.slot, create=True).state/'scheduler-slot.json',
                         data)

    @classmethod
    def load(cls, repository_root, slot_ids, *, clock=None, **kwargs):
        scheduler = cls(clock=clock, **kwargs)
        for slot_id in slot_ids:
            path = slot_paths(repository_root, slot_id).state/'scheduler-slot.json'
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (FileNotFoundError, ValueError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            provider = data.get('provider')
            if provider not in provider_runtime.provider_ids():
                continue
            slot = scheduler.add(slot_id, provider)
            slot.failures = int(data.get('failures') or 0)
            slot.failure_code = str(data.get('failure_code') or '')
            slot.cooldown_until = float(data.get('cooldown_until') or 0)
            # A record saved while running belongs to a process that is gone.
            slot.state = 'cooldown' if slot.cooldown_until > scheduler.clock() and data.get('state') == 'cooldown' else 'idle'
        return scheduler


def main():
    p=argparse.ArgumentParser(description='Contribute continuously using your own installed AI CLI and GitHub account')
    p.add_argument('command',choices=('run','status','providers','slots'))
    p.add_argument('--repo',help='OWNER/REPO (defaults to current repository)')
    p.add_argument('--provider',choices=provider_runtime.provider_ids(),default='codex')
    p.add_argument('--slot',default=DEFAULT_SLOT,
                   help='explicit local slot id (default slot-1); each slot has its own state, logs and worktrees')
    p.add_argument('--slots',type=int,default=DEFAULT_TOTAL_PROCESSES,
                   help='with slots: how many installed/authenticated providers to plan (default 1)')
    p.add_argument('--allow-multiple',action='store_true',
                   help='explicit consent to more than one active AI process on this machine')
    p.add_argument('--allow-duplicate-provider',action='store_true',
                   help='with slots: permit the same provider more than once (explicit consent)')
    p.add_argument('--dry-run',action='store_true',help='with slots: preview commands only')
    p.add_argument('--check-auth',action='store_true',
                   help='With providers: use bounded vendor status checks, never login')
    p.add_argument('--once',action='store_true',help='Handle at most one task attempt')
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--poll',type=int,default=90)
    p.add_argument('--binary-root',help='PRIVATE local path to legally held reference binaries; never uploaded')
    args=p.parse_args()
    try:
        args.slot=validate_slot_id(args.slot)
    except SlotError as exc:
        p.error(str(exc))
    if args.rounds<1 or args.rounds>12:p.error('--rounds must be in 1..12')
    if args.poll<20:p.error('--poll must be at least 20 seconds')
    if args.command=='slots':
        candidates=provider_candidates()
        try:
            plan=select_slot_providers(candidates,total=args.slots,
                                       consent=args.allow_multiple,
                                       allow_duplicate_provider=args.allow_duplicate_provider)
        except SlotLimitError as exc:
            p.error(str(exc))
        for line in plan.notes:
            note(line)
        previews=preview_launch(plan,repo=args.repo)
        print(json.dumps({'dry_run':True,
                          'total':plan.total,
                          'budget_default':default_concurrency(),
                          'plan':[{'slot':item.slot,'provider':item.provider} for item in plan.slots],
                          'commands':[{'slot':item['slot'],'argv':item['argv'],'shell':False}
                                      for item in previews]},indent=2))
        return
    if args.command=='providers':
        reports=[]
        for provider_id, spec in provider_runtime.PROVIDERS.items():
            status=provider_runtime.probe_provider(provider_id)
            report={'id':provider_id,'name':spec.display_name,'state':status.state.value,
                    'version':status.version,'reason':status.reason,'docs':spec.docs_url,
                    'install':spec.install_url,
                    'permission_profile':spec.permission_profile,
                    'unsupported':bool(spec.unsupported_reason),
                    'login_guidance':spec.login_guidance,
                    'readiness':provider_runtime.readiness(provider_id,status=status)}
            if args.check_auth:
                auth=provider_runtime.check_auth(provider_id)
                report['auth']={'state':auth.state.value,'reason':auth.reason}
            reports.append(report)
        identity=(provider_runtime.check_github_identity() if args.check_auth else None)
        print(json.dumps({'providers':reports,'github_identity':
                          {'state':identity.state.value,'reason':identity.reason} if identity else
                          {'state':'unknown','reason':'GitHub CLI identity is separately required.'}},indent=2))
        return
    ready=None
    if args.command=='run':
        ready=provider_runtime.probe_provider(args.provider)
        if not ready.available:
            # #127 freezes the argv of providers that predate the registry, so a
            # help text change must not block an invocation that previously
            # worked. Warn and launch; refuse only when there is no executable.
            if provider_runtime.is_legacy(args.provider) and ready.executable_path:
                note(f'WARN {provider_runtime.get_provider(args.provider).display_name}: '
                     f'capability probe reported {ready.state.value} '
                     f'({ready.reason}); launching the unchanged documented invocation. '
                     'The probe is advisory for pre-registry providers.')
            else:
                p.error(ready.reason)
        if args.provider=='hermes':
            # Fail closed before any task is claimed: unverified CLI, missing
            # capability tokens, bad provider/model/role or an interactive TTY
            # must deny here rather than degrade at launch time.
            try:
                hermes_runtime.load_config(hermes_runtime.Role.WORKER, status=ready,
                                           tty=lambda: sys.stdin.isatty())
            except hermes_runtime.HermesError as exc:
                p.error(f'Hermes backend denied: {exc}')
        if not shutil.which('gh'):p.error('GitHub CLI is required separately; install gh and run gh auth login locally.')
    upstream=get_repo(args.repo)
    gh=GitHub(upstream);user=gh.me()
    note(f'Repo={upstream} account={user} provider={provider_runtime.get_provider(args.provider).display_name}')
    # NOVA3_WORKER_PRESENCE_V1: one editable GitHub comment per machine.
    global _nova_presence
    try:
        from .presence import Reporter as _NOVA3PresenceReporter
        _nova_presence = _NOVA3PresenceReporter(upstream, user, args.provider,
                                                slot_id=args.slot,
                                                machine_id=identity.machine_id)
        _nova_presence.start()
    except Exception:
        _nova_presence = None  # Reporting is optional; never block reconstruction.
    if args.command=='status':
        issues=_solo_eligible_issues(gh.issues())
        counts={x:sum(x in label_names(i) for i in issues) for x in ('nova:queued','nova:claimed','nova:review')}
        print(json.dumps(counts,indent=2));return
    # NOVA3_AUTO_REFERENCE_OWNER_V1
    # Task-specific binary matching requires a locally held original reference.
    # Never upload, fetch or auto-acquire game binaries. Paths remain local.
    if not args.binary_root:
        candidate=pathlib.Path(os.environ.get('NOVA3_REFERENCE_ROOT') or (pathlib.Path.cwd()/'analysis')).expanduser()
        if candidate.is_dir():
            try:
                from .discovery import find_elfs
                has_elf=bool(find_elfs(pathlib.Path.cwd(), candidate))
                if has_elf:
                    args.binary_root=str(candidate.resolve())
                    note('Local private ELF references detected; available to matching tasks (never uploaded)')
            except OSError:
                pass
    repository_root=state_root(upstream)
    try:
        paths=slot_paths(repository_root,args.slot,create=True)
        if args.slot==DEFAULT_SLOT:
            # Copy, never move: legacy checkpoints stay readable after downgrade.
            migrate_legacy_state(repository_root,paths)
        identity=worker_identity(repository_root,user,args.slot,args.provider)
        _nova_slot_lock=acquire_slot_lock(repository_root,args.slot)
    except (SlotError,OSError) as exc:
        stop_worker(repository_root,7,f'local slot setup refused: {exc}')
    root=paths.state
    guard=CheckoutGuard(repository_root,args.slot)
    gate=None
    if args.command=='run':
        try:
            gate=build_gate(gh)
        except EvidenceUnavailable as exc:
            # Fail closed: without a verified owner/admin queue gate the worker
            # must not launch paid AI sessions on arbitrary queued input.
            stop_worker(root,6,f'owner/admin approval gate unavailable: {exc}')
    attempt=0
    consecutive_failures=0
    coordinator_failures=0
    unclaimed_attempts=0
    while True:
        try:
            issues=[]
            for i in _solo_eligible_issues(gh.issues()):
                labels=label_names(i)
                if 'nova:queued' in labels: issues.append(i)
                elif 'nova:claimed' in labels:
                    previous=recovery.load(root,i['number'])
                    lease=lease_get(i.get('body'))
                    if (previous.get('user')==user and previous.get('token')
                            and lease and lease_active(lease) and lease.get('owner')==user
                            and lease.get('token')==previous.get('token')):
                        issues.append(i)
            issues=[i for i in issues if float(recovery.load(root,i['number']).get('retry_after',0))<=time.time()]
            # Malformed Priority quarantines only that task; the queue continues.
            valid=[]
            for i in issues:
                priority=parse_priority(i.get('body',''))
                if priority.error:
                    quarantine_priority(gh,root,i,priority.error)
                    continue
                valid.append(i)
            issues=valid
            # Owner/admin approval is checked before reference scoping, sorting
            # or any provider launch. Unapproved tasks never spend AI tokens.
            issues=admit_queue(gate,issues,root)
            # Only machines holding local originals can meaningfully reconstruct
            # function-address tasks. Everyone can still contribute other code.
            if not args.binary_root:
                needs_ref=[i for i in issues if 'Reference SHA256:' in (i.get('body') or '')]
                if needs_ref:
                    note(f'Skipping {len(needs_ref)} reference-only tasks: no local original ELF; other contributors may claim them')
                    issues=[i for i in issues if i not in needs_ref]
            issues.sort(key=lambda i: priority_sort_key(i, claimed='nova:claimed' in label_names(i)))
            if not issues:
                note('No queued tasks. Polling again.')
                _kick_local_discovery_if_due()
            else:
                for issue in issues[:8]:
                    note(f'Trying #{issue["number"]}: {issue["title"]}')
                    result=work_one(gh,upstream,user,issue,args.provider,root,args.rounds,args.binary_root,
                                    provider_status=ready,gate=gate,guard=guard)
                    # An unresponsive GitHub/API setup must not burn hours or tokens.
                    if result is True:
                        consecutive_failures=0
                        unclaimed_attempts=0
                        break
                    if result is None:
                        unclaimed_attempts+=1
                        if unclaimed_attempts>=3:
                            stop_worker(root,4,
                                '3 task claims were not granted. Check GitHub Actions coordinator '
                                'permissions, the owner/admin approval locator and task leases.')
                    if result is False:
                        consecutive_failures+=1
                        if consecutive_failures>=3:
                            stop_worker(root,3,
                                '3 consecutive task failures. Check nova:blocked/attention tasks '
                                'on the GitHub status issue; checkouts retained. Fix the shared '
                                'cause before relaunching.')
        except KeyboardInterrupt:
            raise
        except Exception as e:
            coordinator_failures+=1
            note(f'Coordinator/worker failure {coordinator_failures}/3: {e}')
            if coordinator_failures>=3:
                stop_worker(root,5,
                    f'repeated coordinator/API failures: {type(e).__name__}: {e}. '
                    'No additional AI will launch until the configuration is fixed.')
        else:
            coordinator_failures=0
        attempt+=1
        if args.once:break
        time.sleep(args.poll)

if __name__=='__main__':main()
