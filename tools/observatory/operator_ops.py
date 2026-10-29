"""Owner-only NOVA3 operations: safe PR batching and local discovery timer.

No AI credentials are read or cached. GitHub actions use gh's existing login.
All mutating functions require a separate, confirmed caller action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Callable

REPO_DEFAULT = 'N-O-V-A-3-RE-Contributors/nova3-reconstruction'
# Issue #137 authority: only the authenticated organization owner or repository
# ADMIN may steer. MAINTAIN/WRITE/TRIAGE/READ are contributor levels.
OWNER_ROLES = frozenset({'ADMIN'})
CONTRIBUTOR_ROLES = frozenset({'MAINTAIN', 'WRITE', 'TRIAGE', 'READ'})
UNIT = 'nova3-discovery.timer'
SERVICE = 'nova3-discovery.service'
FIELDS = 'number,title,author,headRefName,headRefOid,baseRefName,mergeable,mergeStateStatus,isDraft,url,reviewDecision,state'

class CommandFailure(RuntimeError):
    pass

def run(args: list[str], timeout: float = 25) -> str:
    env = {**os.environ, 'GH_PAGER':'cat', 'PAGER':'cat', 'NO_COLOR':'1'}
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandFailure(f'{args[0]}: {exc}') from exc
    if p.returncode:
        msg = (p.stderr or p.stdout or f'exit {p.returncode}').strip()
        raise CommandFailure(f'{args[0]} {args[1] if len(args)>1 else ""}: {msg[:600]}')
    return p.stdout

def gh_json(*args: str, timeout: float = 30):
    try:
        return json.loads(run(['gh', *map(str,args)],timeout=timeout))
    except (ValueError, TypeError) as exc:
        raise CommandFailure('Malformed GitHub CLI JSON') from exc

def viewer() -> str:
    raw = gh_json('api','user','--jq','{"login":.login}')
    name = raw.get('login') if isinstance(raw,dict) else None
    if not isinstance(name,str) or not name:
        raise CommandFailure('Could not read current GitHub login')
    return name

def repo_permission(repo: str) -> str:
    """Read current authenticated account's *effective* repository role.

    Never infer privileges from a username, cached issue, local file, or UI
    state. A network/authentication failure must deny owner operations.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise CommandFailure('Invalid repository identifier')
    data = gh_json('repo', 'view', repo, '--json', 'viewerPermission')
    role = data.get('viewerPermission') if isinstance(data, dict) else None
    if role not in ('ADMIN', 'MAINTAIN', 'WRITE', 'TRIAGE', 'READ'):
        raise CommandFailure('GitHub did not return an authenticated repository permission')
    return role

def org_owner_role(org: str, login: str) -> str:
    """Return the organization membership role for the authenticated login.

    Failures (not a member, no organization, API error) simply return ANONYMOUS
    so the caller denies owner operations. No role metadata is trusted from the
    UI or a local cache.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', org or '') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', login or ''):
        raise CommandFailure('Invalid organization or account identifier')
    try:
        raw = run(['gh', 'api', f'orgs/{org}/memberships/{login}', '--jq', '.role'], timeout=8).strip()
    except CommandFailure:
        return 'ANONYMOUS'
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        value = raw.strip('"')
    return str(value).upper() if str(value).lower() in ('admin', 'member', 'billing_manager') else 'ANONYMOUS'


def owner_access(repo: str, *, permission_reader: Callable[[str], str] | None = None,
                 account_reader: Callable[[], str] | None = None,
                 org_reader: Callable[[str, str], str] | None = None) -> dict:
    """Resolve owner authority once, with injectable readers for offline tests.

    Allowed only for repository ADMIN or an authenticated organization owner.
    MAINTAIN/WRITE/TRIAGE/READ, unknown roles and every API failure fail closed.
    Every mutating operation must call this again at execution time.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo or ''):
        return {'allowed': False, 'role': 'UNKNOWN', 'account': 'UNKNOWN',
                'organization_owner': False, 'source': 'validation',
                'reason': 'Invalid repository identifier'}
    permission_reader = permission_reader or repo_permission
    account_reader = account_reader or viewer
    org_reader = org_reader or org_owner_role
    try:
        role = str(permission_reader(repo) or 'UNKNOWN').upper()
    except (OSError, ValueError, RuntimeError, CommandFailure) as exc:
        return {'allowed': False, 'role': 'UNKNOWN', 'account': 'UNKNOWN',
                'organization_owner': False, 'source': 'GitHub permission query',
                'reason': 'GitHub permission recheck failed; owner actions fail closed: ' + str(exc)[:120]}
    if role in OWNER_ROLES:
        return {'allowed': True, 'role': role, 'account': 'UNKNOWN',
                'organization_owner': False, 'source': 'repository permission',
                'reason': 'repository ADMIN'}
    if role not in CONTRIBUTOR_ROLES:
        return {'allowed': False, 'role': role, 'account': 'UNKNOWN',
                'organization_owner': False, 'source': 'repository permission',
                'reason': 'GitHub role could not be verified; owner actions fail closed'}
    org = repo.split('/', 1)[0]
    try:
        login = account_reader()
        membership = str(org_reader(org, login) or '').lower()
    except (OSError, ValueError, RuntimeError, CommandFailure):
        login, membership = 'UNKNOWN', 'ANONYMOUS'
    if membership == 'admin':
        return {'allowed': True, 'role': role, 'account': login,
                'organization_owner': True, 'source': 'organization owner + repository permission',
                'reason': f'authenticated owner of {org}'}
    return {'allowed': False, 'role': role, 'account': login,
            'organization_owner': False, 'source': 'organization owner + repository permission',
            'reason': 'Contributor role: owner actions require organization owner or repository ADMIN'}


def require_owner_access(repo: str) -> str:
    """Recheck owner/admin authority at the operation boundary (fail closed)."""
    status = owner_access(repo)
    if not status['allowed']:
        raise CommandFailure('Owner operation locked: ' + status['reason'])
    return status['role']

def load_pr(repo: str, number: int) -> dict:
    pr = gh_json('pr','view',str(number),'-R',repo,'--json',FIELDS)
    if not isinstance(pr,dict) or pr.get('number') != number:
        raise CommandFailure(f'Invalid pull-request response for #{number}')
    return pr

def check_state(repo: str, number: int) -> tuple[str,str]:
    # Some gh versions return nonzero when checks are pending, despite valid JSON.
    try:
        proc = subprocess.run(['gh','pr','checks',str(number),'-R',repo,'--json','bucket,name'],
                              text=True,capture_output=True,timeout=25)
        rows = json.loads(proc.stdout)
    except (OSError,subprocess.TimeoutExpired,ValueError) as exc:
        return 'ERROR',str(exc)[:120]
    if not isinstance(rows,list):
        return 'ERROR','Invalid status response'
    if not rows:
        return 'NONE','No checks configured'
    buckets=[]
    for row in rows:
        if not isinstance(row,dict):return 'ERROR','Malformed checks row'
        buckets.append(str(row.get('bucket','')).lower())
    if any(x in ('fail','cancel') for x in buckets):
        return 'FAIL','CI failed or cancelled'
    if any(x not in ('pass','skipping') for x in buckets):
        return 'PENDING','CI pending or unknown'
    if 'pass' not in buckets:
        return 'NONE','No passing checks'
    if proc.returncode:
        return 'ERROR',(proc.stderr or 'GitHub checks CLI error')[:120]
    return 'PASS',f'{buckets.count("pass")} passed'

def issues_with_pr(pr:dict,account:str,*,allow_others:bool=False,allow_no_checks:bool=False,
                   checks:tuple[str,str]|None=None) -> list[str]:
    problems=[]
    if pr.get('state') != 'OPEN': problems.append('not open')
    if pr.get('isDraft'): problems.append('draft')
    if pr.get('baseRefName')!='main': problems.append('base not main')
    if not str(pr.get('headRefName','')).startswith('nova/issue-'):
        problems.append('not a NOVA3 issue branch')
    author = pr.get('author') or {}
    if not allow_others and str(author.get('login','')).lower()!=account.lower():
        problems.append('other author')
    if pr.get('mergeable') not in ('MERGEABLE',):
        problems.append('mergeability '+str(pr.get('mergeable')))
    if pr.get('mergeStateStatus') in ('DIRTY','BLOCKED','UNKNOWN'):
        problems.append('merge state '+str(pr.get('mergeStateStatus')))
    if pr.get('reviewDecision') in ('REVIEW_REQUIRED','CHANGES_REQUESTED'):
        problems.append('review '+str(pr['reviewDecision']))
    if not re.fullmatch(r'[0-9a-fA-F]{40}',str(pr.get('headRefOid',''))):
        problems.append('invalid head SHA')
    if checks is not None:
        state,desc=checks
        if state != 'PASS' and not (state=='NONE' and allow_no_checks):
            problems.append(desc)
    return problems

@dataclass
class MergePreview:
    account:str
    candidates:dict[int,dict] = field(default_factory=dict)
    rejected:dict[int,str] = field(default_factory=dict)
    created:float=field(default_factory=time.monotonic)

    @property
    def eligible(self)->list[int]:
        return sorted(self.candidates)


def make_preview(repo:str, numbers:list[int]|None=None,*,limit:int=200,
                 allow_no_checks:bool=False,allow_others:bool=False,
                 emit:Callable[[str],None]|None=None) -> MergePreview:
    require_owner_access(repo)
    account=viewer()
    if numbers is None:
        prs=gh_json('pr','list','-R',repo,'--state','open','--limit',str(limit),'--json',FIELDS)
        if not isinstance(prs,list):raise CommandFailure('Unexpected PR list response')
    else:
        prs=[load_pr(repo,n) for n in sorted(set(numbers))]
    result=MergePreview(account=account)
    for pr in sorted(prs,key=lambda d:d.get('number',0)):
        number=pr.get('number')
        if not isinstance(number,int):continue
        # Do not query CI on branches that could never be merged by this tool.
        structural=issues_with_pr(pr,account,allow_others=allow_others,
                                  allow_no_checks=allow_no_checks,checks=None)
        if structural and not all(('mergeability UNKNOWN'==p or 'merge state UNKNOWN'==p) for p in structural):
            result.rejected[number]='; '.join(structural)
            if emit:emit(f'#{number}: skip {result.rejected[number]}')
            continue
        state=check_state(repo,number)
        problems=issues_with_pr(pr,account,allow_others=allow_others,
                                allow_no_checks=allow_no_checks,checks=state)
        # UNKNOWN is temporary after merges; accept for recheck at execution.
        problems=[p for p in problems if p not in ('mergeability UNKNOWN','merge state UNKNOWN')]
        if problems:
            result.rejected[number]='; '.join(problems)
            if emit:emit(f'#{number}: skip {result.rejected[number]}')
            continue
        result.candidates[number]=pr
        if emit:emit(f'#{number}: ready ({state[0]}, SHA {str(pr.get("headRefOid"))[:10]})')
    return result


def merge_batch(repo:str, preview:MergePreview,*,wait:int=90,
                emit:Callable[[str],None]|None=None,allow_no_checks:bool=False,
                allow_others:bool=False,stop_on_error:bool=False,
                sleep:Callable[[float],None]=time.sleep,
                clock:Callable[[],float]=time.monotonic)->dict:
    require_owner_access(repo)
    if not isinstance(preview,MergePreview):raise ValueError('Preview required')
    outcome={'merged':[],'skipped':{},'checked':[]}
    for number in preview.eligible:
        original=preview.candidates[number]
        deadline=clock()+max(0,wait)
        if emit:emit(f'#{number}: awaiting current GitHub mergeability')
        while True:
            try:
                current=load_pr(repo,number)
                if current.get('state')!='OPEN':
                    raise CommandFailure('PR no longer open')
                if current.get('headRefOid') != original.get('headRefOid'):
                    raise CommandFailure('head commit changed since preview')
                if current.get('mergeable')=='UNKNOWN' or current.get('mergeStateStatus')=='UNKNOWN':
                    if clock()>=deadline:
                        raise CommandFailure('GitHub mergeability remained UNKNOWN')
                    sleep(min(3,deadline-clock()))
                    continue
                checked=check_state(repo,number)
                problems=issues_with_pr(current,preview.account,allow_others=allow_others,
                                         allow_no_checks=allow_no_checks,checks=checked)
                if problems:
                    raise CommandFailure('; '.join(problems))
                sha=str(current['headRefOid'])
                outcome['checked'].append(number)
                # Explicit SHAs protect against a contributor updating the branch mid-merge.
                run(['gh','pr','merge',str(number),'-R',repo,'--squash','--delete-branch',
                     '--match-head-commit',sha],timeout=90)
                after=gh_json('pr','view',str(number),'-R',repo,'--json','state,mergedAt')
                if after.get('state') != 'MERGED' and not after.get('mergedAt'):
                    raise CommandFailure('GitHub did not confirm merge')
                outcome['merged'].append(number)
                if emit:emit(f'#{number}: MERGED; main updated')
                break
            except CommandFailure as exc:
                outcome['skipped'][number]=str(exc)
                if emit:emit(f'#{number}: SKIPPED {exc}')
                if stop_on_error:return outcome
                break
    return outcome


def unit_status(unit:str)->dict:
    try:
        raw=run(['systemctl','--user','show',unit,
                 '--property=LoadState,ActiveState,SubState,Result,NextElapseUSecRealtime,NextElapseUSecMonotonic,UnitFileState'],timeout=6)
        return {k:v for k,v in (line.split('=',1) for line in raw.splitlines() if '=' in line)}
    except CommandFailure as exc:
        return {'ActiveState':'unknown','error':str(exc)}


def discovery_status(root:Path)->dict:
    timer=unit_status(UNIT)
    svc=unit_status(SERVICE)
    path=Path.home()/'.config/systemd/user'
    return {'timer':timer,'service':svc,
            'timer_file':(path/UNIT).is_file(),
            'module':(root/'nova3_team/discovery_setup.py').is_file(),
            'root':str(root),
            'message': ('ACTIVE' if timer.get('ActiveState')=='active' else
                        'INACTIVE: discoverer will not schedule scans'),
            }


def service_error_lines(unit:str=SERVICE,lines:int=18)->list[str]:
    try:
        return run(['journalctl','--user','-u',unit,'-n',str(lines),
                    '--no-pager','-o','cat'],timeout=8).splitlines()[-lines:]
    except CommandFailure as exc:
        return [str(exc)]


def timer_repair(root:Path,repo:str,emit:Callable[[str],None]|None=None)->dict:
    """Reinstall discovery units through installed project code, with full diagnostics.

    Enable and start are separate; activation uses --no-block so a failed
    immediate scan cannot obscure timer installation. No worker restarts.
    """
    require_owner_access(repo)
    if sys.platform != 'linux':
        raise CommandFailure('Discovery timer is supported only on Linux/systemd')
    if not (root/'nova3_team/discovery_setup.py').is_file():
        raise CommandFailure('Shared discovery upgrade is missing from project')
    if root.is_symlink() or not root.is_dir():
        raise CommandFailure('Project root does not exist or is a symlink')
    if emit:emit('Setting up local discovery timer; keeping worker untouched')
    # Generate exactly the same unit names/text as the installed discovery system.
    sys.path.insert(0,str(root))
    try:
        from nova3_team.discovery_setup import unit_text
        service,timer=unit_text(root.resolve(),repo,Path(sys.executable).resolve())
        # Existing shared v1 generator accidentally quotes WorkingDirectory.
        # In systemd this is a PATH directive, not shell argv: quotes are
        # treated as literal path characters, so systemd rejects it.
        service,changes=re.subn(r'^WorkingDirectory="([^"\r\n]+)"$',
                                r'WorkingDirectory=\1', service, flags=re.MULTILINE)
        if changes>1 or not re.search(r'^WorkingDirectory=/',service,re.MULTILINE):
            raise CommandFailure('Discovery service working-directory line is invalid')
    finally:
        if sys.path and sys.path[0]==str(root):sys.path.pop(0)
    user_units=Path.home()/'.config/systemd/user'
    user_units.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name,body in ((SERVICE,service),(UNIT,timer)):
        path=user_units/name
        if path.is_symlink():
            raise CommandFailure(f'Refusing to overwrite symlink unit {path}')
        # Write atomically; never touch system-wide units or the worker service.
        tmp=path.with_name(path.name+'.nova-tmp')
        tmp.write_text(body,encoding='utf-8')
        os.replace(tmp,path)
    # Verify the generated units *before* systemctl activation. Report the
    # specific line number in the TUI instead of the generic 'Job failed'.
    if shutil.which('systemd-analyze'):
        try:
            run(['systemd-analyze','verify',str(user_units/SERVICE),str(user_units/UNIT)],timeout=12)
        except CommandFailure as exc:
            raise CommandFailure('Generated discovery unit rejected: '+str(exc)) from exc
    for args in (['daemon-reload'],['reset-failed',UNIT],['reset-failed',SERVICE],['enable',UNIT]):
        try:
            run(['systemctl','--user',*args],timeout=15)
        except CommandFailure as exc:
            if emit:emit(f'{args[0]}: {exc}')
            if args[0]!='reset-failed':raise
    # Non-blocking starts avoid propagating an immediate analysis failure as
    # if enabling the timer itself was invalid.
    try:
        run(['systemctl','--user','start','--no-block',UNIT],timeout=20)
    except CommandFailure as exc:
        if emit:emit(f'Start error: {exc}')
    state=discovery_status(root)
    for _ in range(5):
        if state['timer'].get('ActiveState')=='active':break
        time.sleep(0.2)
        state=discovery_status(root)
    if state['timer'].get('ActiveState')!='active':
        detail=' | '.join(service_error_lines(UNIT,6)+service_error_lines(SERVICE,6))
        raise CommandFailure('Discovery timer did not become active: '+detail[-480:])
    if emit:emit('Discovery timer ACTIVE: background scans scheduled')
    return state


def start_discovery_scan(repo:str=REPO_DEFAULT)->str:
    require_owner_access(repo)
    if sys.platform != 'linux':
        raise CommandFailure('Discovery scan service is Linux-only')
    service=unit_status(SERVICE)
    if service.get('ActiveState') in ('activating','active'):
        return 'Discovery already running; not starting a duplicate scan'
    run(['systemctl','--user','start','--no-block',SERVICE],timeout=15)
    return 'Discovery scan submitted; inspect scan journal for result'


def toggle_discovery_timer(enable:bool,repo:str=REPO_DEFAULT)->str:
    require_owner_access(repo)
    if sys.platform != 'linux':
        raise CommandFailure('Discovery timer is Linux-only')
    command='enable' if enable else 'disable'
    args=['systemctl','--user',command,'--now',UNIT]
    # A failed analysis job must not swallow systemctl diagnostics.
    run(args,timeout=20)
    return f'Discovery timer {"enabled" if enable else "disabled"}'


def git_alignment(root:Path)->dict:
    result={'branch':'unknown','ahead':'?','behind':'?','dirty':'?','note':''}
    if not (root/'.git').exists():
        result['note']='Local project is not a Git working tree'
        return result
    try:
        result['branch']=run(['git','-C',str(root),'branch','--show-current'],timeout=8).strip()
        dirty=run(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],timeout=10)
        result['dirty']='yes' if dirty.strip() else 'no'
        counts=run(['git','-C',str(root),'rev-list','--left-right','--count','HEAD...refs/remotes/origin/main'],timeout=10).strip().split()
        if len(counts)==2:
            result['ahead'],result['behind']=counts
        if result['branch']!='main':result['note']='local HEAD not main; no automatic change'
        elif result['dirty']=='yes':result['note']='local edits present; never automatically rebase'
        elif result['behind'] not in ('0','?'):
            result['note']='origin/main ahead of local checkout; merging PRs does not sync local Git'
        elif result['ahead'] not in ('0','?'):
            result['note']='local commits not on origin/main; review before syncing'
        else:result['note']='local remote-tracking refs agree (as of last fetch)'
    except CommandFailure as exc:
        result['note']=str(exc)[:180]
    return result


def export_diagnostics(out:Path,root:Path,repo:str,worker:str='nova3-worker.service')->Path:
    require_owner_access(repo)
    out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    sections=['NOVA3 OWNER DIAGNOSTIC',datetime.now(timezone.utc).isoformat(),f'Repo: {repo}',
              'NOTE: local paths and GitHub metadata may be private',
              '\nGIT STATUS',json.dumps(git_alignment(root),indent=2),
              '\nDISCOVERY STATUS',json.dumps(discovery_status(root),indent=2)]
    for unit in (worker,SERVICE,UNIT):
        sections += [f'\nJOURNAL: {unit}',*service_error_lines(unit,120)]
    try:
        sections += ['\nRECENT FEEDER RUNS',run(['gh','run','list','-R',repo,'--workflow','nova-autofeed.yml','--limit','6'],timeout=25)]
    except CommandFailure as exc:sections += ['GitHub unavailable: '+str(exc)]
    data='\n'.join(sections)+'\n'
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(data)
    return out
