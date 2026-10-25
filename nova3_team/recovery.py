"""Crash-safe worker checkpoint records, independent of disposable AI conversations.

No reference binary, user credential, or generated source is stored here.
Source changes remain in persistent task-specific Git checkouts.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import time


def state_path(root: Path, issue_number: int) -> Path:
    return Path(root) / '.nova-state' / f'{int(issue_number)}.json'


def load(root: Path, issue_number: int) -> dict:
    try:
        obj = json.loads(state_path(root,issue_number).read_text(encoding='utf-8'))
        if not isinstance(obj, dict) or obj.get('issue') != int(issue_number):
            return {}
        return obj
    except (FileNotFoundError, ValueError, OSError):
        return {}


def update(root: Path, issue_number: int, **changes) -> dict:
    p=state_path(root,issue_number)
    p.parent.mkdir(parents=True,exist_ok=True)
    data=load(root,issue_number)
    data.update(changes)
    data['issue']=int(issue_number)
    data['saved_unix']=int(time.time())
    tmp=p.with_name(p.name+f'.{os.getpid()}.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data,f,sort_keys=True,indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,p)
    return data


def resume_info(root: Path, issue_number: int, upstream: str, user: str):
    state=load(root,issue_number)
    if state.get('repo')!=upstream or state.get('user')!=user:
        return None
    raw=state.get('work')
    if not isinstance(raw,str) or not raw:return None
    work=Path(raw).resolve()
    # Fail closed if checkpoint references arbitrary file paths.
    if work.parent != Path(root).resolve() or not (work/'.git').exists():
        return None
    if not isinstance(state.get('branch'),str) or not state.get('branch','').startswith(f'nova/issue-{issue_number}-'):
        return None
    return state
