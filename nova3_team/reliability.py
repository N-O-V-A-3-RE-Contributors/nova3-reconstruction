"""Bounded failure policy, no dependency on an AI's self-reported progress."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from . import recovery

# PR errors that a retry, another subscriber, or more AI tokens cannot repair.
PERMANENT_PR = ('head sha can\'t be blank', 'base sha can\'t be blank',
                'no commits between', 'head ref must be a branch',
                'resource not accessible by integration', 'permission denied to',
                'pull request create failed: graphql')


def fingerprint(error):
    value = str(error).casefold()
    value = re.sub(r'(/home|/users)/[^\s:]+', '<path>', value)
    value = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{20,}\b', '<id>', value)
    value = re.sub(r'\b\d+\b', '<n>', value)
    return hashlib.sha256(value[:700].encode()).hexdigest()[:20]


def code_for(error):
    v = str(error).casefold()
    if 'provider session timed out' in v or 'ai provider timeout' in v:
        return 'PROVIDER_TIMEOUT'
    if 'head sha can\'t be blank' in v or 'base sha can\'t be blank' in v:
        return 'INVALID_PR_REFERENCE'
    if any(x in v for x in PERMANENT_PR):
        return 'PR_CONFIGURATION_ERROR'
    if 'no source changes' in v:
        return 'NO_SOURCE_PROGRESS'
    if 'forbidden files' in v or 'unapproved path changes' in v:
        return 'SOURCE_GUARD_FAILURE'
    return 'REPEATED_WORKER_ERROR'


def failure_decision(previous, error):
    key = fingerprint(error)
    code = code_for(error)
    count = 1 + (int(previous.get('failure_count', 0)) if previous.get('failure_key') == key else 0)
    deterministic = code in ('INVALID_PR_REFERENCE', 'PR_CONFIGURATION_ERROR',
                             'NO_SOURCE_PROGRESS', 'SOURCE_GUARD_FAILURE',
                             'PROVIDER_TIMEOUT')
    blocked = deterministic or count >= 2
    delay = 0 if blocked else 600  # One retry after 10m for transient failures.
    return dict(code=code, key=key, count=count, blocked=blocked, delay=delay)


def local_status(root):
    folder = Path(root) / '.nova-state'
    rows = []
    for path in sorted(folder.glob('*.json')) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                rows.append(data)
        except (OSError, ValueError):
            continue
    return rows


def main():
    parser = argparse.ArgumentParser(description='Inspect saved worker state; never deletes checkouts')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--reset-issue',type=int,help='Locally clear cooldown AFTER maintainer fixes and requeues blocked issue')
    args = parser.parse_args()
    root = Path.home() / '.cache' / 'nova3-team' / args.repo.replace('/', '_')
    if args.reset_issue is not None:
        if args.reset_issue < 1: parser.error('--reset-issue must be positive')
        recovery.update(root,args.reset_issue,retry_after=0,failure_count=0,
                        failure_key='',failure_code='',last_error='')
        print(f'Local cooldown cleared for #{args.reset_issue}; confirm GitHub is requeued separately.')
        return
    rows = local_status(root)
    if not rows:
        print('No local worker state for this repository.')
    for state in rows:
        now = int(time.time())
        last = now - int(state.get('saved_unix') or now)
        print(f"#{state.get('issue')} phase={state.get('phase','unknown')} "
              f"last_update={last}s ago provider_calls={state.get('provider_calls',0)} "
              f"last_exit={state.get('provider_exit','?')} "
              f"failures={state.get('failure_count',0)} "
              f"retry_in={max(0,int(state.get('retry_after',0))-now)}s "
              f"reason={state.get('failure_code','-')}")


if __name__ == '__main__':
    main()
