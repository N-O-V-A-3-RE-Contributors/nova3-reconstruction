"""NOVA3 task coordinator: quiet single-writer GitHub Actions state machine.

Issue comments are *requests*, not evidence of progress. Successful operations
only update issue metadata; do not generate a reply comment to every heartbeat.
"""
from __future__ import annotations

import json
import os
import re
from datetime import timedelta, datetime
from pathlib import Path

from .common import GitHub, lease_active, lease_get, lease_put, iso, now, label_names, replace_label

COMMAND = re.compile(
    r'^/nova (claim|heartbeat|release|submitted|block) ([a-f0-9-]{32,36})'
    r'(?: (https://github\.com/[\w.-]+/[\w.-]+/pull/\d+|[A-Z_]{3,40}))?\s*$', re.I
)
QUEUED = 'nova:queued'
CLAIMED = 'nova:claimed'
REVIEW = 'nova:review'
BLOCKED = 'nova:blocked'
ATTENTION = 'nova:attention'
INTEGRATED = 'nova:integrated'


def alert(message):
    login=os.environ.get('NOVA3_ALERT_USER','').strip()
    return (f'@{login} ' if re.fullmatch(r'[A-Za-z0-9-]{1,39}',login) else '') + message


def process_comment(gh, event):
    c = event.get('comment') or {}
    actor = (c.get('user') or {}).get('login', '')
    parsed = COMMAND.fullmatch((c.get('body') or '').strip())
    if not parsed or actor.endswith('[bot]'):
        return
    action, token, payload = parsed.groups()
    number = event['issue']['number']
    issue = gh.issue(number)
    labels = label_names(issue)
    if not any(label.startswith('nova:') for label in labels) or BLOCKED in labels or INTEGRATED in labels:
        return
    lease = lease_get(issue.get('body'))
    active = lease_active(lease)
    if action == 'claim':
        if REVIEW in labels or (active and lease and lease.get('owner') != actor):
            return
        if active or not ({QUEUED, CLAIMED} & set(labels)):
            return
        latest = {'owner': actor, 'token': token, 'expires_at': iso(now() + timedelta(minutes=90))}
        gh.patch(f'issues/{number}', {
            'body': lease_put(issue.get('body'), latest),
            'labels': replace_label(issue, [QUEUED, CLAIMED, ATTENTION], CLAIMED),
        })
        return
    if not active or not lease or lease.get('owner') != actor or lease.get('token') != token:
        return
    if action == 'heartbeat':
        lease['expires_at'] = iso(now() + timedelta(minutes=90))
        lease['last_heartbeat_at'] = iso(now())
        if payload in ('AI_RUNNING','AI_FINISHED','VALIDATING','PUSHING'):
            lease['stage'] = payload
        patch={'body': lease_put(issue.get('body'), lease)}
        if payload == 'AI_FINISHED' and ATTENTION in labels:
            patch['labels']=[x for x in labels if x != ATTENTION]
        gh.patch(f'issues/{number}',patch)
    elif action == 'release':
        gh.patch(f'issues/{number}', {
            'body': lease_put(issue.get('body'), None),
            'labels': replace_label(issue, [CLAIMED], QUEUED),
        })
    elif action == 'submitted':
        expected = f'https://github.com/{gh.repo}/pull/'
        if not payload or not payload.startswith(expected) or not payload[len(expected):].isdigit():
            return
        gh.patch(f'issues/{number}', {
            'body': lease_put(issue.get('body'), None),
            'labels': replace_label(issue, [CLAIMED, QUEUED, ATTENTION], REVIEW),
        })
    elif action == 'block':
        reason = payload if payload and re.fullmatch(r'[A-Z_]{3,40}', payload) else 'WORKER_FAILURE'
        gh.patch(f'issues/{number}', {
            'body': lease_put(issue.get('body'), None),
            'labels': replace_label(issue, [QUEUED, CLAIMED, REVIEW, ATTENTION], BLOCKED),
        })
        # One diagnostic comment per transition, not one comment per retry.
        gh.comment(number, alert(
            f'⚠️ NOVA3 worker circuit breaker: `{reason}`. This task is quarantined; '
            f'it cannot be automatically claimed until the cause is fixed and a maintainer '
            f'replaces `nova:blocked` with `nova:queued`. Existing local source changes are retained.'))


def sweep(gh):
    """Flag stale worker signals once; never generate per-poll comment floods."""
    for issue in gh.issues():
        labels = label_names(issue)
        if CLAIMED not in labels:
            continue
        lease=lease_get(issue.get('body'))
        if not lease_active(lease):
            updated = [lab for lab in labels if lab != CLAIMED]
            for lab in (QUEUED, ATTENTION):
                if lab not in updated:
                    updated.append(lab)
            gh.patch(f'issues/{issue["number"]}', {
                'body': lease_put(issue.get('body'), None), 'labels': updated,
            })
            gh.comment(issue['number'], alert('⚠️ NOVA3 claim expired without a new heartbeat. '
                                            'The task was requeued; this is not evidence of completed code.'))
            continue
        # Provider sessions are capped at 30 minutes. 45 minutes without a fresh
        # stage signal means a worker may be hung, disconnected or offline.
        if ATTENTION not in labels and lease and lease.get('stage') == 'AI_RUNNING':
            try:
                stamp=datetime.fromisoformat(
                    lease['last_heartbeat_at'].replace('Z','+00:00'))
                stalled=(now()-stamp).total_seconds()>45*60
            except (KeyError,TypeError,ValueError):
                stalled=True
            if stalled:
                gh.patch(f'issues/{issue["number"]}', {
                    'labels':labels+[ATTENTION]})
                gh.comment(issue['number'], alert('⚠️ NOVA3 AI_RUNNING was last reported over '
                                                 '45 minutes ago. Worker may be stalled.'))


TASK_REFERENCE = re.compile(r'(?im)\b(?:refs|closes|fixes|resolves)\s+#(\d+)\b')


def process_merged_pr(gh, event):
    """Integrated means merged into main; it does NOT mean byte matched."""
    pr = event.get('pull_request') or {}
    if not pr.get('merged') or pr.get('base', {}).get('ref') != 'main':
        return
    repo_name = ((pr.get('base') or {}).get('repo') or {}).get('full_name')
    if repo_name and repo_name.casefold() != gh.repo.casefold():
        return
    text = pr.get('body') or ''
    # Only branches produced by our task worker may auto-integrate an issue.
    branch=((pr.get('head') or {}).get('ref') or '')
    m=re.fullmatch(r'nova/issue-(\d+)-[a-f0-9]+', branch)
    if not m:
        return
    number=int(m.group(1))
    if number not in {int(x) for x in TASK_REFERENCE.findall(text)}:
        return
    for number in (number,):
        try:
            issue = gh.issue(number)
        except Exception:
            continue
        labels = label_names(issue)
        if not any(lab in labels for lab in (QUEUED, CLAIMED, REVIEW, BLOCKED, INTEGRATED)):
            continue
        remaining = [lab for lab in labels if lab not in (QUEUED, CLAIMED, REVIEW, BLOCKED, ATTENTION)]
        if INTEGRATED not in remaining:
            remaining.append(INTEGRATED)
        gh.patch(f'issues/{number}', {
            'state': 'closed',
            'body': lease_put(issue.get('body'), None),
            'labels': remaining,
        })


def main():
    gh = GitHub(os.environ['GITHUB_REPOSITORY'])
    name = os.environ.get('GITHUB_EVENT_NAME', '')
    if name in ('issue_comment', 'pull_request'):
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        if name == 'issue_comment' and event.get('action') == 'created':
            process_comment(gh, event)
        elif name == 'pull_request' and event.get('action') == 'closed':
            process_merged_pr(gh, event)
    else:
        sweep(gh)


if __name__ == '__main__':
    main()
