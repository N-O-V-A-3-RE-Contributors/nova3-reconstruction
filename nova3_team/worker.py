"""Cross-platform opt-in unattended worker. Runs one sandboxed local CLI at a time."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, time, uuid
from datetime import datetime, timezone
from .common import GitHub, lease_active, lease_get, label_names
from .guard import check as guard_check
from . import recovery  # NOVA3-DURABLE-RESUME-v1


def run(cmd, cwd=None, timeout=90, capture=True, check=True):
    cp=subprocess.run(cmd,cwd=cwd,text=True,capture_output=capture,timeout=timeout)
    if check and cp.returncode:
        raise RuntimeError(f'{cmd[:4]} failed ({cp.returncode}): {(cp.stderr or cp.stdout or "")[-900:]}')
    return cp


def note(s):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {s}', flush=True)


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


def command_for(provider,prompt):
    if provider=='codex':
        return ['codex','exec','--ephemeral','--sandbox','workspace-write',prompt]
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
    # -- includes untracked files after git add -A, but only after verifying original and dangerous contents
    out=git(work,'status','--porcelain','-z').stdout
    paths=[]
    for item in out.split('\0'):
        if item:
            if item[:2].strip() in ('R','C'):continue
            paths.append(item[3:])
    return paths


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
    name=upstream.split('/')[-1];owner=upstream.split('/')[0]
    account=upstream if user==owner else f'{user}/{name}'
    if user!=owner:
        cp=run(['gh','repo','view',account,'--json','name'],check=False)
        if cp.returncode:
            note(f'Creating fork {account}')
            run(['gh','repo','fork',upstream,'--clone=false'],timeout=180)
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


def record_handoff(workdir,round_num,cmd,cp,changes):
    folder=pathlib.Path(workdir).parent/'.nova-private'/pathlib.Path(workdir).name
    folder.mkdir(parents=True, exist_ok=True)
    log=folder/f'round-{round_num}.log'
    log.write_text((cp.stdout or '')[-12000:]+'\nSTDERR:\n'+(cp.stderr or '')[-3000:],encoding='utf-8')
    snippet=(cp.stdout or '')[-3500:]
    handoff=folder/'handoff.md'
    handoff.write_text(f'Round {round_num}; exit={cp.returncode}\nChanged files: {changes}\nLast model output:\n{snippet}\n',encoding='utf-8')
    return handoff.read_text()


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
            for k in range(1,rounds+1):
                gh.comment(n,f'/nova heartbeat {token}')
                recovery.update(root,n,phase='running',round=k)
                prompt=session_prompt(issue,k,handoff,allowed,binary_root)
                cmd=command_for(provider,prompt)
                note(f'#{n} AI session {k}/{rounds} ({provider})')
                try:
                    cp=run(cmd,cwd=work,timeout=1800,check=False)
                except subprocess.TimeoutExpired as e:
                    cp=subprocess.CompletedProcess(cmd,124,stdout=e.stdout.decode(errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or ''),stderr='Session timed out')
                paths=validate_scope(work,allowed)
                handoff=record_handoff(work,k,cmd,cp,paths)
                recovery.update(root,n,phase='checkpoint',round=k)
                if paths:
                    verified,checklog=verify(work)
                    handoff+='\nINDEPENDENT VALIDATION OUTPUT (abbreviated):\n'+checklog[-4500:]
                if cp.returncode==0 and paths and verified:break
                if cp.returncode!=0:note(f'Session {k} returned {cp.returncode}; bounded retry with fresh context')
            paths=validate_scope(work,allowed)
            if not paths:
                gh.comment(n,f'No source changes after bounded sessions; released for reassignment. Worker={user}, provider={provider}.')
                gh.comment(n,f'/nova release {token}')
                recovery.update(root,n,phase='released')
                return False
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
            # Look for a PR if the previous process died after creating one.
            existing=run(['gh','pr','list','--repo',upstream,'--head',f'{user}:{branch}',
                          '--state','all','--json','url'],cwd=work,timeout=90).stdout
            prs=json.loads(existing or '[]')
            if prs:
                pr=prs[0]['url']
            else:
                desc=f'Refs #{n}\n\nAutomated contribution. AI provider: {provider}. Local checks: {"PASS" if verified else "FAIL (draft PR)"}. Independently verify in CI. Byte matching is NOT claimed without reproducible evidence.\n\nLast local verification: {checklog[-800:]}'
                args=['gh','pr','create','--repo',upstream,'--head',f'{user}:{branch}',
                      '--base','main','--title',f'#{n}: {issue["title"]}','--body',desc]
                if not verified:args.append('--draft')
                pr=run(args,cwd=work,timeout=120).stdout.strip()
            recovery.update(root,n,phase='pr_created',pr=pr)
        current=recovery.load(root,n)
        if current.get('phase')=='pr_created':
            gh.comment(n,f'/nova submitted {token} {current["pr"]}')
            recovery.update(root,n,phase='review')
            note(f'PR submitted: {current["pr"]}')
            return True
        return False
    except Exception as e:
        note(f'FAILED #{n}: {e}')
        try:
            gh.comment(n, f'Worker failure ({provider}, {user}): {str(e)[:400]}; preserving local checkout for resumption.')
            gh.comment(n,f'/nova release {token}')
        except Exception:pass
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
    if args.command=='status':
        issues=gh.issues()
        counts={x:sum(x in label_names(i) for i in issues) for x in ('nova:queued','nova:claimed','nova:review')}
        print(json.dumps(counts,indent=2));return
    root=pathlib.Path.home()/'.cache'/'nova3-team'/upstream.replace('/','_')
    attempt=0
    while True:
        try:
            issues=[]
            for i in gh.issues():
                labels=label_names(i)
                if 'nova:queued' in labels: issues.append(i)
                elif 'nova:claimed' in labels:
                    previous=recovery.load(root,i['number'])
                    lease=lease_get(i.get('body'))
                    if (previous.get('user')==user and previous.get('token')
                            and lease and lease_active(lease) and lease.get('owner')==user
                            and lease.get('token')==previous.get('token')):
                        issues.append(i)
            issues.sort(key=lambda i:(0 if 'nova:claimed' in label_names(i) else 1,-int(next((line.split(':',1)[1].strip() for line in i.get('body','').splitlines() if line.startswith('Priority:')),'50')),i['number']))
            if not issues:note('No queued tasks. Polling again.')
            else:
                for issue in issues[:8]:
                    note(f'Trying #{issue["number"]}: {issue["title"]}')
                    result=work_one(gh,upstream,user,issue,args.provider,root,args.rounds,args.binary_root)
                    # A busy task should not stop a worker trying the next one.
                    if result is not None:
                        break
        except KeyboardInterrupt:
            raise
        except Exception as e:
            note(f'Coordinator/worker transient failure: {e}')
        attempt+=1
        if args.once:break
        time.sleep(args.poll)

if __name__=='__main__':main()
