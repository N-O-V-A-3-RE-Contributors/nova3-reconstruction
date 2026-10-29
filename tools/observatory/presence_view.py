"""Read-only GitHub worker-presence viewer for the NOVA3 Control Center.

A reporting worker owns exactly one editable comment on a dedicated status issue.
This module never guesses that unreported machines are idle/offline.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re
import subprocess

MARKER = '<!-- NOVA3_WORKER_PRESENCE_V1 -->'
ALLOWED_STATES = frozenset({'ONLINE', 'IDLE', 'CLAIMING', 'WORKING', 'VERIFYING', 'REVIEW', 'ERROR', 'STOPPED'})
REPO_RE = re.compile(r'^[\w.-]+/[\w.-]+$')
WORKER_RE = re.compile(r'^[a-f0-9]{16,64}$')
MAX_AGE_SECONDS = 10 * 60


def parse_comment(item: dict) -> dict | None:
    if not isinstance(item, dict) or not isinstance(item.get('body'), str):
        return None
    body = item['body']
    if not body.startswith(MARKER + '\n') or len(body) > 4096:
        return None
    try:
        payload = json.loads(body[len(MARKER):].strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get('v') != 1:
        return None
    owner = (item.get('user') or {}).get('login')
    worker = payload.get('worker')
    state = payload.get('state')
    if (not isinstance(owner, str) or not owner or len(owner)>80
            or not isinstance(worker, str) or not WORKER_RE.fullmatch(worker)
            or state not in ALLOWED_STATES):
        return None
    provider = payload.get('provider', '')
    if not isinstance(provider, str):provider = ''
    issue = payload.get('issue')
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        issue = None
    updated = item.get('updated_at')
    if not isinstance(updated, str):return None
    try:
        stamp = datetime.fromisoformat(updated.replace('Z', '+00:00'))
        if stamp.tzinfo is None:return None
    except ValueError:
        return None
    return {'owner':owner,'worker':worker,'provider':provider[:24],
            'state':state,'issue':issue,'updated':stamp.astimezone(timezone.utc).isoformat(),
            'comment_id':item.get('id') if isinstance(item.get('id'), int) else 0}


def classify(record: dict, now: datetime | None = None) -> tuple[str, int]:
    now = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(record['updated'].replace('Z','+00:00'))
        age = max(0, int((now-ts).total_seconds()))
    except (KeyError, ValueError, TypeError):
        return 'UNKNOWN', -1
    if age > MAX_AGE_SECONDS:
        return 'STALE', age
    return record.get('state','UNKNOWN'), age


def summary(records: list[dict], now: datetime | None = None) -> dict[str, int]:
    counts = {'WORKING':0,'IDLE':0,'ERROR':0,'STALE':0,'OTHER':0}
    for item in records:
        state, _ = classify(item,now)
        k = state if state in counts else 'OTHER'
        counts[k] += 1
    counts['TOTAL'] = len(records)
    return counts


def collect(repo: str, *, command=None, timeout: int = 20) -> dict:
    """Fetch presence via GitHub CLI. No tokens/credentials are inspected.

    A missing issue means presence has not been deployed, NOT no workers.
    """
    if not REPO_RE.fullmatch(repo):
        return {'records': [], 'issue': None, 'error': 'Invalid repository name'}
    if command is None:
        def command(args):
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            if proc.returncode:
                raise RuntimeError((proc.stderr or proc.stdout).strip()[:160])
            return json.loads(proc.stdout)
    try:
        issues = command(['gh','issue','list','-R',repo,'--state','open',
                          '--label','nova:presence','--limit','10','--json','number,title'])
        if not isinstance(issues, list): raise ValueError('Malformed issue list')
        dedicated = [i for i in issues if i.get('title') == 'NOVA3 — contributor worker presence'
                     and isinstance(i.get('number'),int)]
        if not dedicated:
            return {'records': [], 'issue': None,
                    'error': 'Worker reporting not installed; older workers cannot be distinguished from offline ones'}
        number = dedicated[0]['number']
        comments = command(['gh','api',f'repos/{repo}/issues/{number}/comments?per_page=100'])
        if not isinstance(comments,list):raise ValueError('Malformed comment list')
        found = {}
        for item in comments:
            parsed = parse_comment(item)
            if parsed:
                ident=(parsed['owner'].lower(),parsed['worker'])
                if ident not in found or parsed['updated']>found[ident]['updated']:
                    found[ident]=parsed
        return {'records':sorted(found.values(),key=lambda x:(x['owner'].lower(),x['worker'])),
                'issue':number,'error':'',
                'note':'Last 100 comments only; stale reports are not proof of a running worker'}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, TypeError) as exc:
        return {'records': [], 'issue': None, 'error':str(exc)[:200]}
