"""GitHub Actions single-writer task claim coordinator; never executes issue content."""
from __future__ import annotations
import json, os, re, sys
from datetime import timedelta
from pathlib import Path
from .common import GitHub, lease_active, lease_get, lease_put, iso, now, label_names, replace_label

COMMAND = re.compile(r'^/nova (claim|heartbeat|release|submitted) ([a-f0-9-]{32,36})(?: (https://github\.com/[\w.-]+/[\w.-]+/pull/\d+))?\s*$', re.I)
QUEUED='nova:queued'; CLAIMED='nova:claimed'; REVIEW='nova:review'


def respond(gh, n, status, token, info=''):
    gh.comment(n, f'<!-- NOVA3_CLAIM_RESULT -->\n/nova result {status} {token} {info}'.strip())


def process_comment(gh, event):
    c=event.get('comment') or {}
    actor=(c.get('user') or {}).get('login','')
    cmd=COMMAND.fullmatch((c.get('body') or '').strip())
    if not cmd or actor.endswith('[bot]'): return
    action, token, pr = cmd.groups()
    n=event['issue']['number']; issue=gh.issue(n)
    if not any(x.startswith('nova:') for x in label_names(issue)): return
    lease=lease_get(issue.get('body'))
    active=lease_active(lease)
    labels=label_names(issue)
    if action=='claim':
        if REVIEW in labels or (active and lease['owner']!=actor):
            respond(gh,n,'busy',token)
            return
        if active and lease['owner']==actor:
            respond(gh,n,'busy',token,'Already held by this worker')
            return
        if QUEUED not in labels and CLAIMED not in labels:
            respond(gh,n,'busy',token,'Not queued')
            return
        new={'owner':actor,'token':token,'expires_at':iso(now()+timedelta(minutes=90))}
        gh.patch(f'issues/{n}', {'body':lease_put(issue['body'],new), 'labels':replace_label(issue,[QUEUED,CLAIMED],CLAIMED)})
        respond(gh,n,'claimed',token, f'owner={actor} expires={new["expires_at"]}')
    elif action in ('heartbeat','release','submitted'):
        if not active or not lease or lease.get('token')!=token or lease.get('owner')!=actor:
            respond(gh,n,'denied',token)
            return
        if action=='heartbeat':
            lease['expires_at']=iso(now()+timedelta(minutes=90))
            gh.patch(f'issues/{n}',{'body':lease_put(issue['body'],lease)})
            respond(gh,n,'renewed',token)
        elif action=='release':
            gh.patch(f'issues/{n}',{'body':lease_put(issue['body'],None),'labels':replace_label(issue,[CLAIMED],QUEUED)})
            respond(gh,n,'released',token)
        elif action=='submitted':
            if not pr or not pr.startswith(f'https://github.com/{gh.repo}/pull/'):
                respond(gh,n,'denied',token,'Expected upstream pull request URL')
                return
            gh.patch(f'issues/{n}',{'body':lease_put(issue['body'],None),'labels':replace_label(issue,[CLAIMED],REVIEW)})
            respond(gh,n,'submitted',token,pr)


def sweep(gh):
    for issue in gh.issues():
        labels=label_names(issue)
        if CLAIMED not in labels: continue
        lease=lease_get(issue.get('body'))
        if not lease_active(lease):
            gh.patch(f'issues/{issue["number"]}',{
                'body':lease_put(issue.get('body'),None),
                'labels':replace_label(issue,[CLAIMED],QUEUED)})
            gh.comment(issue['number'],'Lease expired; task returned to the available queue.')


def main():
    gh=GitHub(os.environ['GITHUB_REPOSITORY'])
    event_name=os.environ.get('GITHUB_EVENT_NAME','')
    if event_name=='issue_comment':
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        if event.get('action')=='created': process_comment(gh,event)
    else:
        sweep(gh)

if __name__=='__main__':main()
