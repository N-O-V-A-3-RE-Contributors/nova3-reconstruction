"""Best-effort GitHub contributor heartbeat, never required for work execution.

One editable comment per installation on a designated GitHub issue.
No machine name, local paths, secret values, source code or token is reported.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

MARKER = '<!-- NOVA3_WORKER_PRESENCE_V1 -->'
INTERVAL = 180
STATES = frozenset({'ONLINE','IDLE','CLAIMING','WORKING','VERIFYING','REVIEW','ERROR','STOPPED'})
TASK_RE = re.compile(r'#(\d+)')


def event_from_log(message: str) -> tuple[str, int | None] | None:
    """Parse existing worker messages, not model output or arbitrary source data."""
    if not isinstance(message,str):return None
    task = TASK_RE.search(message)
    issue = int(task.group(1)) if task else None
    if 'ALERT WORKER STOPPED' in message:
        return 'STOPPED',issue
    if 'ALERT #' in message or 'FAILED #' in message:
        return 'ERROR',issue
    if 'No queued tasks' in message or 'Skipping all' in message:
        return 'IDLE',None
    if 'Codex/AI session finished:' in message:
        return 'VERIFYING',issue
    if 'AI session ' in message and 'provider launched' in message:
        return 'WORKING',issue
    if 'PR submitted:' in message or 'PR created:' in message:
        return 'REVIEW',None
    if 'Trying #' in message or 'Resuming existing claim' in message:
        return 'CLAIMING',issue
    return None


def cmd(args:list[str], *, payload:dict|None=None, timeout:int=18):
    raw=json.dumps(payload, separators=(',',':')) if payload is not None else None
    proc=subprocess.run(args, input=raw, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or 'gh CLI failed')[:200])
    return json.loads(proc.stdout)


class Reporter:
    def __init__(self, repo:str, account:str, provider:str, *, state_dir:Path|None=None,
                 command=cmd, interval:int=INTERVAL):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo):
            raise ValueError('Invalid repo')
        self.repo=repo
        self.account=account
        self.provider=provider[:24]
        self.command=command
        self.interval=max(60,int(interval))
        home = state_dir or (Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state')) / 'nova3-team')
        self.path=Path(home)/('presence-'+re.sub('[^a-zA-Z0-9-]','-',repo)+'-'+self.provider+'.json')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.worker=uuid.uuid4().hex
        self.comment_id=None
        try:
            p=json.loads(self.path.read_text(encoding='utf-8'))
            if re.fullmatch(r'[a-f0-9]{32}',str(p.get('worker',''))):self.worker=p['worker']
            if isinstance(p.get('comment_id'),int) and p['comment_id']>0:self.comment_id=p['comment_id']
        except (ValueError,OSError,TypeError):pass
        self._write_local()
        self.lock=threading.Lock()
        self.wake=threading.Event()
        self.stop=threading.Event()
        self.state='ONLINE'
        self.issue=None
        self.last_sent=0.0
        self.last_key=None
        self.issue_number=None
        self.thread=threading.Thread(target=self._loop,name='nova3-presence',daemon=True)

    def _write_local(self):
        p=self.path.with_suffix('.tmp')
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump({'worker':self.worker,'comment_id':self.comment_id},stream)
        os.replace(p,self.path)

    def start(self):
        self.thread.start()
        self.wake.set()

    def signal_from_log(self, message:str):
        new=event_from_log(message)
        if new is None:return
        status,issue=new
        with self.lock:
            if status in ('REVIEW','VERIFYING') and issue is None:issue=self.issue
            changed=(status,issue)!=(self.state,self.issue)
            self.state,self.issue=status,issue
        if changed:self.wake.set()

    def _ensure_issue(self):
        if self.issue_number is not None:return self.issue_number
        raw=self.command(['gh','issue','list','-R',self.repo,'--state','open',
                          '--label','nova:presence','--limit','10','--json','number,title'])
        if not isinstance(raw,list):raise RuntimeError('Invalid presence issue list')
        for item in raw:
            if item.get('title')=='NOVA3 — contributor worker presence' and isinstance(item.get('number'),int):
                self.issue_number=item['number']
                return self.issue_number
        raise RuntimeError('NOVA3 presence issue not installed by maintainer')

    def publish(self):
        number=self._ensure_issue()
        with self.lock:
            state,issue=self.state,self.issue
        body = MARKER+'\n'+json.dumps({'v':1,'worker':self.worker,'provider':self.provider,
                 'state':state,'issue':issue,'at':datetime.now(timezone.utc).isoformat()},
                 separators=(',',':'))
        payload={'body':body}
        if self.comment_id:
            try:
                self.command(['gh','api','--method','PATCH',
                              f'repos/{self.repo}/issues/comments/{self.comment_id}',
                              '--input','-'],payload=payload)
                return
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                # A missing or inaccessible stored comment can be recreated.
                self.comment_id=None
        # On restart, recover own existing comment before creating another.
        rows=self.command(['gh','api',f'repos/{self.repo}/issues/{number}/comments?per_page=100'])
        if not isinstance(rows,list):raise RuntimeError('Invalid presence comments')
        for item in rows:
            if (isinstance(item,dict) and
                (item.get('user') or {}).get('login','').lower()==self.account.lower() and
                isinstance(item.get('body'),str) and
                item['body'].startswith(MARKER+'\n') and
                f'"worker":"{self.worker}"' in item['body'] and
                isinstance(item.get('id'),int)):
                self.comment_id=item['id']
                self.command(['gh','api','--method','PATCH',
                              f'repos/{self.repo}/issues/comments/{self.comment_id}',
                              '--input','-'],payload=payload)
                self._write_local()
                return
        created=self.command(['gh','api','--method','POST',
                              f'repos/{self.repo}/issues/{number}/comments',
                              '--input','-'],payload=payload)
        if not isinstance(created,dict) or not isinstance(created.get('id'),int):
            raise RuntimeError('GitHub did not confirm presence comment')
        self.comment_id=created['id']
        self._write_local()

    def _loop(self):
        while not self.stop.is_set():
            with self.lock:key=(self.state,self.issue)
            due=time.monotonic()-self.last_sent>=self.interval
            if key!=self.last_key or due:
                try:
                    self.publish()
                    self.last_key=key
                except (OSError,RuntimeError,ValueError,TypeError,subprocess.TimeoutExpired):
                    # Status reporting must never stop a Codex worker or consume retries.
                    pass
                self.last_sent=time.monotonic()
            self.wake.wait(min(self.interval,30))
            self.wake.clear()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread.is_alive():self.thread.join(timeout=2)
