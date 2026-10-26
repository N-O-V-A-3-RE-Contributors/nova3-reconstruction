"""Actual GitHub task/PR status; byte-match and completion estimates are never inferred."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from .common import GitHub, lease_active, lease_get, label_names, now
from .coordinator import TASK_REFERENCE, QUEUED, CLAIMED, REVIEW, BLOCKED, ATTENTION, INTEGRATED

DASHBOARD = 'nova:dashboard'
STATES = (QUEUED, CLAIMED, REVIEW, BLOCKED, INTEGRATED)


def pull_requests(gh):
    all_pr = []
    for page in range(1, 11):
        part = gh.get(f'pulls?state=all&per_page=100&page={page}')
        all_pr.extend(part)
        if len(part) < 100:
            break
    return all_pr


def linked_issues(pr):
    found = {int(n) for n in TASK_REFERENCE.findall(pr.get('body') or '')}
    branch = (pr.get('head') or {}).get('ref', '')
    m = re.match(r'^nova/issue-(\d+)-[a-f0-9]+$', branch)
    if m:
        found.add(int(m.group(1)))
    return found


def collect(gh):
    issues = gh.issues(state='all')
    prs = pull_requests(gh)
    tasks = [i for i in issues if any(l in label_names(i) for l in STATES)]
    task_prs = {}
    for pr in prs:
        for n in linked_issues(pr):
            task_prs.setdefault(n, []).append(pr)
    counts = Counter()
    alerts = []
    items = []
    for item in sorted(tasks, key=lambda x: x['number']):
        labels = set(label_names(item))
        links = task_prs.get(item['number'], [])
        merged = [p for p in links if p.get('merged_at')]
        if merged or INTEGRATED in labels:
            stage = 'integrated'
        elif BLOCKED in labels:
            stage = 'blocked'
        elif REVIEW in labels:
            stage = 'review'
        elif CLAIMED in labels:
            stage = 'claimed'
        elif QUEUED in labels:
            stage = 'queued'
        else:
            stage = 'unclassified'
        counts[stage] += 1
        claim=lease_get(item.get('body'))
        if stage == 'claimed' and not lease_active(claim):
            alerts.append(f'#{item["number"]}: claimed lease expired; next sweep should return it to queue')
        if stage == 'claimed' and claim and claim.get('stage') == 'AI_RUNNING':
            try:
                last=datetime.fromisoformat(claim['last_heartbeat_at'].replace('Z','+00:00'))
                if (now()-last).total_seconds()>45*60:
                    alerts.append(f'#{item["number"]}: AI_RUNNING claim without check-in for over 45 minutes; may be stalled')
            except (KeyError,ValueError,TypeError):
                alerts.append(f'#{item["number"]}: claimed AI_RUNNING with missing heartbeat timestamp')
        if ATTENTION in labels:
            alerts.append(f'#{item["number"]}: attention label set (previous expired worker claim)')
        if stage == 'blocked':
            alerts.append(f'#{item["number"]}: blocked; maintainer intervention required')
        if stage == 'review' and not links:
            alerts.append(f'#{item["number"]}: review label present but no linked pull request found')
        # Hundreds of comments indicate earlier control-loop noise, not productivity.
        if int(item.get('comments') or 0) >= 100:
            alerts.append(f'#{item["number"]}: {item["comments"]} issue comments; check for old worker retry floods')
        items.append(dict(number=item['number'],title=item.get('title',''),stage=stage,
                          comments=item.get('comments',0),
                          worker_stage=(claim or {}).get('stage','unknown') if stage=='claimed' else '—',
                          url=item.get('html_url',f'https://github.com/{gh.repo}/issues/{item["number"]}'),
                          prs=[{'url':p.get('html_url',''), 'merged':bool(p.get('merged_at')),
                                'state':p.get('state','')} for p in links]))
    return {'repo':gh.repo,'generated_at':now().isoformat(timespec='seconds'),
            'tasks':items,'counts':{s:counts[s] for s in ('queued','claimed','review','blocked','integrated')},
            'alerts':list(dict.fromkeys(alerts)),
            'open_prs':sum(1 for p in prs if p.get('state') == 'open'),
            'linked_merged_prs':sum(1 for p in prs if p.get('merged_at') and linked_issues(p))}


def format_markdown(state):
    c=state['counts']
    n=len(state['tasks'])
    finished=c['integrated']
    lines=[
        '# NOVA3 reconstruction — observable workflow status',
        '',
        f'Updated: `{state["generated_at"]}` (UTC). This page is **automatically overwritten**, not appended to.',
        '',
        f'**Task ledger:** {n} discovered GitHub tasks · **{finished} integrated** · '
        f'**{c["review"]} review** · **{c["claimed"]} claimed** · '
        f'**{c["queued"]} queued** · **{c["blocked"]} blocked**',
        '',
        f'**Open pull requests:** {state["open_prs"]} · **Linked merged PRs:** {state["linked_merged_prs"]}',
        '',
        '**Overall game reconstruction: UNKNOWN.** These tasks mostly cover infrastructure and resource formats. '
        'Closing an issue, merging a PR, or counting source files **does not prove byte-for-byte equivalence**.',
        '',
        '**Project completion ETA: UNKNOWN** until a verified function inventory and measured matching throughput exist.',
        '',
        '### Problems requiring attention',
    ]
    if state['alerts']:
        lines += [f'- {a}' for a in state['alerts'][:30]]
    else:
        lines.append('- No problems detected from issue labels and PR state (local AI activity cannot be seen by GitHub).')
    lines += ['', '### Tasks', '', '| Issue | Stage | Worker signal | Pull requests |', '|---|---|---|---|']
    for row in state['tasks'][:200]:
        title=row['title'].replace('|','\\|').replace('\n',' ')
        links=', '.join(f'[PR]({p["url"]})'+(' (merged)' if p['merged'] else '') for p in row['prs']) or '—'
        lines.append(f'| [#{row["number"]}: {title}]({row["url"]}) | **{row["stage"].upper()}** | {row["worker_stage"]} | {links} |')
    if len(state['tasks'])>200:
        lines.append(f'\nOnly first 200 of {len(state["tasks"])} tasks shown.')
    lines += ['', '### What each stage means',
              '- **Queued:** available for a contributor to claim.',
              '- **Claimed:** leased to a contributor; **not proof Codex is active**.',
              '- **Review:** PR submitted; **not yet integrated or verified**.',
              '- **Blocked:** a circuit breaker or maintainer stopped automated retry.',
              '- **Integrated:** a PR was merged or the task carries the integrated label; **not proof of matching source**.',
              '', 'Source match coverage requires independently audited reference hashes, compiler provenance and comparison output.',
              '']
    return '\n'.join(lines)


def publish(gh, content):
    # Label creation is idempotent; GitHub reports HTTP 422 for an existing label.
    try:
        gh.post('labels', {'name':DASHBOARD,'color':'0969da','description':'NOVA3 automatically refreshed project status'})
    except RuntimeError as exc:
        if 'HTTP 422' not in str(exc):
            raise
    dashboards=[i for i in gh.issues(state='all') if DASHBOARD in label_names(i)]
    if dashboards:
        existing=dashboards[0]
        if existing.get('body') != content or existing.get('state') != 'open':
            gh.patch(f'issues/{existing["number"]}', {'body':content,'state':'open'})
        return existing.get('html_url', f'https://github.com/{gh.repo}/issues/{existing["number"]}')
    new=gh.post('issues', {'title':'NOVA3 — live reconstruction status',
                           'body':content,'labels':[DASHBOARD]})
    return new['html_url']


def main():
    p=argparse.ArgumentParser(description='NOVA3 project status from actual GitHub issue and PR state')
    p.add_argument('action', choices=('show','publish'))
    p.add_argument('--repo',required=True)
    p.add_argument('--json',action='store_true')
    a=p.parse_args()
    gh=GitHub(a.repo)
    state=collect(gh)
    if a.action=='publish':
        url=publish(gh, format_markdown(state))
        print('Updated:',url)
    elif a.json:
        print(json.dumps(state, indent=2))
    else:
        print(format_markdown(state))


if __name__=='__main__':
    main()
