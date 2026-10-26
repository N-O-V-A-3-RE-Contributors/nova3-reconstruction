"""Audit/requeue known saved failed reconstructions without re-running AI.

The program refuses changes outside identifiable task-specific paths.
Use --apply only after reviewing the preview; live GitHub writes need gh auth.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import time

from .path_inventory import changed_files, UnsafeChange
from .guard import check as guard_check
from . import recovery
from .common import lease_get, lease_active, lease_put

ADDRESSES = re.compile(r'0x([0-9a-fA-F]{6,16})')
LIBRARY = re.compile(r'\b(lib[A-Za-z0-9_+.-]+\.so)\b')
VALID_EXT = {'.cpp', '.c', '.h', '.hpp', '.json'}


def allowed_from_body(body: str) -> list[str]:
    found=[s for s in body.splitlines() if s.startswith('Allowed paths:')]
    if len(found)!=1:
        raise ValueError('Expected exactly one Allowed paths: line')
    return [v.strip().rstrip('/') for v in found[0].split(':',1)[1].split(',') if v.strip()]


def is_allowed(path, allowed):
    return any(path==x or path.startswith(x+'/') for x in allowed)


def safe_extra(path, body):
    """Allow only task-address-named files in the named reference library tree."""
    lib=LIBRARY.search(body)
    addrs={a.lower().lstrip('0') for a in ADDRESSES.findall(body)}
    if not lib or not addrs:
        return False
    name=lib.group(1).removesuffix('.so')
    libname=re.sub(r'[^a-zA-Z0-9_]', '_', name)
    prefix=(f'reconstruction/recovered/{libname}/',
            f'reconstruction/tests/recovered/{libname}/')
    if not path.startswith(prefix) or PurePosixPath(path).suffix.lower() not in VALID_EXT-{'.json'}:
        return False
    # All newly allowed file names must be explicitly related to one task
    # address, or inside an already address-named dedicated directory.
    bits=PurePosixPath(path).parts
    return any(addr and any(addr in part.lower().replace('0x','').lstrip('0') for part in bits[3:])
               for addr in addrs)


def report_saved(repo: str, n: int, user: str):
    base=Path.home()/'.cache'/'nova3-team'/repo.replace('/','_')
    state=recovery.load(base,n)
    work=Path(state.get('work','')).expanduser() if state.get('work') else None
    if not work or work.resolve().parent!=base.resolve() or not (work/'.git').exists():
        raise ValueError(f'No trusted saved checkout for issue #{n}')
    if state.get('repo')!=repo or state.get('user')!=user:
        raise ValueError(f'Saved checkpoint owner/repo mismatch for #{n}')
    data=json.loads(subprocess.run(['gh','issue','view',str(n),'-R',repo,'--json','body,title,state,labels'],
                                   text=True,capture_output=True,check=True).stdout)
    body=data.get('body') or ''
    allowed=allowed_from_body(body)
    files=changed_files(work)
    forbidden=guard_check(work, files)
    if forbidden:
        raise ValueError(f'Guard blocked files: {forbidden}')
    extras=[p for p in files if not is_allowed(p,allowed)]
    unsafe=[p for p in extras if not safe_extra(p,data['title']+'\n'+body)]
    return dict(issue=n,work=work,state=state,issue_data=data,allowed=allowed,files=files,extras=extras,unsafe=unsafe,base=base)


def run_gh(args, *, input_obj=None):
    return subprocess.run(args, input=json.dumps(input_obj) if input_obj else None,
                          text=True,capture_output=True,check=True)


def apply_issue(r, repo, user):
    data=r['issue_data']
    if data['state']!='OPEN':raise ValueError('Cannot requeue closed issue')
    labels=[v['name'] for v in data.get('labels',[])]
    if 'nova:review' in labels or 'nova:integrated' in labels:
        raise ValueError('Issue already in review or integrated')
    lease=lease_get(data['body'])
    if lease and lease_active(lease) and lease.get('owner')!=user:
        raise ValueError('Task is actively claimed by a different contributor')
    if not r['files'] or r['unsafe']:
        raise ValueError('No safe source files to recover')
    old='Allowed paths: '+', '.join(r['allowed'])
    new='Allowed paths: '+', '.join(dict.fromkeys(r['allowed']+r['extras']))
    if data['body'].count(old)!=1:
        raise ValueError('Task body scope changed; refusing concurrent edit')
    updated=lease_put(data['body'].replace(old,new,1),None)
    new_labels=[v for v in labels if v not in ('nova:blocked','nova:claimed','nova:attention','nova:queued')]
    new_labels.append('nova:queued')
    # Refresh before writing; refuse concurrent changes.
    latest=json.loads(run_gh(['gh','issue','view',str(r['issue']),'-R',repo,'--json','body,labels']).stdout)
    if latest['body']!=data['body']:
        raise ValueError('Issue changed since preview; rerun before writing')
    run_gh(['gh','api','--method','PATCH',f'repos/{repo}/issues/{r["issue"]}','--input','-'],
           input_obj={'body':updated,'labels':new_labels})
    recovery.update(r['base'],r['issue'],retry_after=0,failure_count=0,pr_failures=0,
                    failure_key='',failure_code='',last_error='scope repaired; resuming saved session')


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',required=True)
    ap.add_argument('--issues',nargs='+',type=int,default=[29,30,31])
    ap.add_argument('--apply',action='store_true',help='Requeue reviewed safe source changes via GitHub API')
    a=ap.parse_args(argv)
    user=run_gh(['gh','api','user','--jq','.login']).stdout.strip()
    if a.apply:
        cp=subprocess.run(['systemctl','--user','is-active','nova3-worker.service'],
                          capture_output=True,text=True)
        if cp.returncode==0 and cp.stdout.strip()=='active':
            raise SystemExit('Stop nova3-worker.service first; do not edit active tasks')
    reports=[]
    for n in a.issues:
        try:
            r=report_saved(a.repo,n,user)
            print(f'\nISSUE #{n}: {len(r["files"])} saved file(s), {len(r["extras"])} scope additions needed')
            for p in r['files']:
                tag='UNSAFE' if p in r['unsafe'] else ('ADD' if p in r['extras'] else 'OK')
                print(f'  [{tag}] {p}')
            reports.append(r)
        except (ValueError, OSError, subprocess.CalledProcessError, UnsafeChange) as e:
            print(f'\nISSUE #{n}: manual review required: {e}')
    if a.apply:
        if len(reports)!=len(set(a.issues)) or any(r['unsafe'] for r in reports):
            raise SystemExit('Refusing partial automatic recovery: unexpected files need manual review')
        print('\nOnly those exact safe filenames will be added to the issues. No original binaries will be uploaded.')
        if input('Type RECOVER to requeue these issues: ').strip()!='RECOVER':
            raise SystemExit('Not modified')
        for r in reports:
            apply_issue(r,a.repo,user)
            print(f'Issue #{r["issue"]} requeued; saved checkout preserved; restart worker after upgrading it.')
    else:
        print('\nRead-only. Nothing changed. Use --apply only after checking these files.')


if __name__=='__main__':main()
