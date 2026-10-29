"""Cross-platform opt-in unattended worker. Runs one sandboxed local CLI at a time."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, shutil, signal, subprocess, sys, time, uuid
from datetime import datetime, timezone
from functools import lru_cache
from .common import GitHub, lease_active, lease_get, label_names
from .guard import check as guard_check
from . import recovery  # NOVA3-DURABLE-RESUME-v1
from . import reliability  # NOVA3-RELIABILITY-FAILSTOP-v2
from . import activity_feed


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


def run_provider(cmd, cwd, log, timeout, activity=None):
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
        structured = cmd[:2] == ['codex', 'exec'] and '--json' in cmd[2:-1]
        activity = activity_feed.ActivityFeed(log, structured=structured)
    _publish_activity(activity, force=True)
    try:
        with os.fdopen(descriptor, 'wb') as sink:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=sink,
                                    stderr=subprocess.STDOUT, **settings)
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
    try:
        cp = subprocess.run(['codex', 'exec', '--help'], capture_output=True,
                            text=True, timeout=3, check=False)
        return cp.returncode == 0 and '--json' in (cp.stdout or '').split()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return False


def command_for(provider,prompt):
    if provider=='codex':
        options = ['--json'] if _codex_json_supported() else []
        return ['codex','exec','--ephemeral','--sandbox','workspace-write',*options,prompt]
    if provider=='claude':
        # Limit tools to file operations by default. No shell execution is auto-approved.
        return ['claude','-p',prompt,'--max-turns','12','--allowedTools','Read','Edit','Write','Glob','Grep','--output-format','text']
    if provider=='gemini':
        return ['gemini','-p',prompt,'--approval-mode','auto_edit','--output-format','text']
    raise ValueError(provider)


def allowed_from_issue(body):
    for line in (body or '').splitlines():
        if line.startswith('Allowed paths:'):
            return [s.strip().rstrip('/') for s in line.split(':',1)[1].split(',') if s.strip()]
    return []


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
        log.write_text((cp.stdout or '')[-12000:]+'\nSTDERR:\n'+(cp.stderr or '')[-3000:],encoding='utf-8')
        if os.name == 'posix':
            log.chmod(0o600)
    snippet=(cp.stdout or '')[-3500:]
    handoff=folder/'handoff.md'
    handoff.write_text(f'Round {round_num}; exit={cp.returncode}\n'
                       f'Changed files: {changes}\n'
                       f'Private full output: {log.name}\n'
                       f'Provider diagnostic: {(cp.stderr or "")[:400]}\n'
                       f'Last model output:\n{snippet}\n',encoding='utf-8')
    if os.name == 'posix':
        handoff.chmod(0o600)
    return handoff.read_text()


def pause_failed_task(gh, root, number, token, provider, user, error):
    """Fail-closed, externally visible state without retry/comment storms.

    Never delete local checkouts. GitHub coordinator is the sole label writer.
    """
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


def work_one(gh,upstream,user,issue,provider,root,rounds,binary_root):
    n=issue['number']
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
                cmd=command_for(provider,prompt)
                note(f'#{n} AI session {k}/{rounds} ({provider}); provider launched')
                log=private_round_log(work,k)
                structured=provider == 'codex' and '--json' in cmd[2:-1]
                activity=activity_feed.ActivityFeed(log, structured=structured)
                cp=run_provider(cmd,work,log,timeout=ai_session_timeout(),activity=activity)
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



def main():
    p=argparse.ArgumentParser(description='Contribute continuously using your own installed AI CLI and GitHub account')
    p.add_argument('command',choices=('run','status'))
    p.add_argument('--repo',help='OWNER/REPO (defaults to current repository)')
    p.add_argument('--provider',choices=('codex','claude','gemini'),default='codex')
    p.add_argument('--once',action='store_true',help='Handle at most one task attempt')
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--poll',type=int,default=90)
    p.add_argument('--binary-root',help='PRIVATE local path to legally held reference binaries; never uploaded')
    args=p.parse_args()
    if args.rounds<1 or args.rounds>12:p.error('--rounds must be in 1..12')
    if args.poll<20:p.error('--poll must be at least 20 seconds')
    if args.command=='run' and not shutil.which(args.provider):p.error(f'CLI missing: {args.provider}. Install and authenticate it first.')
    upstream=get_repo(args.repo)
    gh=GitHub(upstream);user=gh.me()
    note(f'Repo={upstream} account={user} provider={args.provider}')
    # NOVA3_WORKER_PRESENCE_V1: one editable GitHub comment per machine.
    global _nova_presence
    try:
        from .presence import Reporter as _NOVA3PresenceReporter
        _nova_presence = _NOVA3PresenceReporter(upstream, user, args.provider)
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
    root=pathlib.Path.home()/'.cache'/'nova3-team'/upstream.replace('/','_')
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
            # Only machines holding local originals can meaningfully reconstruct
            # function-address tasks. Everyone can still contribute other code.
            if not args.binary_root:
                needs_ref=[i for i in issues if 'Reference SHA256:' in (i.get('body') or '')]
                if needs_ref:
                    note(f'Skipping {len(needs_ref)} reference-only tasks: no local original ELF; other contributors may claim them')
                    issues=[i for i in issues if i not in needs_ref]
            issues.sort(key=lambda i:(0 if 'nova:claimed' in label_names(i) else 1,-int(next((line.split(':',1)[1].strip() for line in i.get('body','').splitlines() if line.startswith('Priority:')),'50')),i['number']))
            if not issues:
                note('No queued tasks. Polling again.')
                _kick_local_discovery_if_due()
            else:
                for issue in issues[:8]:
                    note(f'Trying #{issue["number"]}: {issue["title"]}')
                    result=work_one(gh,upstream,user,issue,args.provider,root,args.rounds,args.binary_root)
                    # An unresponsive GitHub/API setup must not burn hours or tokens.
                    if result is True:
                        consecutive_failures=0
                        unclaimed_attempts=0
                        break
                    if result is None:
                        unclaimed_attempts+=1
                        if unclaimed_attempts>=3:
                            note('ALERT WORKER STOPPED: 3 task claims were not granted. '
                                 'Check GitHub Actions coordinator permissions and task leases.')
                            raise SystemExit(4)
                    if result is False:
                        consecutive_failures+=1
                        if consecutive_failures>=3:
                            note('ALERT WORKER STOPPED: 3 consecutive task failures. '
                                 'Check nova:blocked/attention tasks on the GitHub status issue. '
                                 'Checkouts retained. Fix the shared cause before relaunching.')
                            raise SystemExit(3)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            coordinator_failures+=1
            note(f'Coordinator/worker failure {coordinator_failures}/3: {e}')
            if coordinator_failures>=3:
                note('ALERT WORKER STOPPED: repeated coordinator/API failures. '
                     'No additional AI will launch until the configuration is fixed.')
                raise SystemExit(5)
        else:
            coordinator_failures=0
        attempt+=1
        if args.once:break
        time.sleep(args.poll)

if __name__=='__main__':main()
