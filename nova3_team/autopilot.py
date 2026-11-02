"""Owner-granted autopilot: approve safe planner proposals without intervention.

The authority model does not change. An **owner/admin issues one grant** (the
CLI verifies the live GitHub role), and while that grant is live a background
timer may:

* take planner proposals (marker in the body) that no human has touched,
* accept only tasks whose ``Allowed paths`` stay inside the safe source trees,
* write a normal approval record and the queue labels, exactly like a human
  approval (so the same gate still verifies it at claim time),
* stop at a task budget, an expiry, the first unsafe task, or a kill switch,
* append every decision to ``audit.jsonl``.

It cannot approve outside the grant, touch protected trees, merge, change
policy, extend itself, or run when the grant is absent/expired. Disable with
``python -m nova3_team.autopilot off`` (or stop the timer).

Grant file (0600, owner-only): ``~/.cache/nova3-team/<owner_repo>/autopilot/grant.json``.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from . import autoadmit

SCHEMA = 'nova3-autopilot/1'
GRANT = 'grant.json'
AUDIT = 'audit.jsonl'
ACTIVE_LABELS = {'nova:blocked', 'nova:claimed', 'nova:review', 'nova:integrated'}


def default_dir(repo: str) -> pathlib.Path:
    return pathlib.Path.home() / '.cache' / 'nova3-team' / repo.replace('/', '_') / 'autopilot'


def load_grant(folder: pathlib.Path) -> dict | None:
    try:
        data = json.loads((pathlib.Path(folder) / GRANT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('schema') == SCHEMA else None


def save_grant(folder: pathlib.Path, grant: dict) -> None:
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / GRANT
    path.write_text(json.dumps(grant, indent=2, sort_keys=True), encoding='utf-8')
    path.chmod(0o600)


def grant_live(grant: dict | None, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else int(now)
    return bool(grant) and grant.get('enabled') is True \
        and int(grant.get('expires_at') or 0) > now \
        and int(grant.get('tasks_approved') or 0) < int(grant.get('max_tasks') or 0)


def remaining(grant: dict | None, *, now: int | None = None) -> int:
    if not grant_live(grant, now=now):
        return 0
    return max(0, int(grant['max_tasks']) - int(grant.get('tasks_approved') or 0))


def select_candidates(issues: list[dict], grant: dict,
                      *, now: int | None = None) -> tuple[list[dict], list[tuple]]:
    """Planner proposals that are safe to auto-approve, and why others were refused."""
    accepted, refused = [], []
    deadline = int(grant.get('expires_at') or 0)
    for issue in issues:
        number = issue.get('number')
        body = issue.get('body') or ''
        labels = set(autoadmit.label_names(issue))
        if (issue.get('state') or 'open') != 'open':
            continue
        if 'NOVA3_DSH_PLANNER_PROPOSAL' not in body:
            continue
        if ACTIVE_LABELS & labels:
            continue
        if any(label.startswith('nova:approved:') for label in labels):
            continue
        blockers = autoadmit.safety_blockers(issue)
        if blockers:
            refused.append((number, ','.join(blockers)))
            continue
        if deadline <= (now or int(time.time())):
            refused.append((number, 'grant_expired'))
            continue
        accepted.append(issue)
    return accepted, refused


def approve(*, repo: str, issue: dict, grant: dict, folder: pathlib.Path,
            post_comment, patch_labels, repository: dict,
            now: int | None = None, ttl_seconds: int = 3600) -> dict:
    """Write one approval record + queue labels for a selected proposal."""
    now = int(time.time()) if now is None else int(now)
    number = int(issue['number'])
    spec = autoadmit.task_spec_sha256(issue)
    waiver = autoadmit.Waiver(0, int(grant['actor_id']), str(grant['actor']),
                              (number,), now, now + ttl_seconds, {str(number): spec})
    body = autoadmit.approval_record(issue=issue, waiver=waiver, repository=repository,
                                     created_at=now, ttl_seconds=ttl_seconds)
    comment = post_comment(number, body)
    if not isinstance(comment, dict) or not comment.get('id'):
        raise RuntimeError('approval comment was not created')
    comment_id = int(comment['id'])
    labels = sorted(set(autoadmit.label_names(issue)) | {'nova:queued',
                                                        f'nova:approved:{comment_id}'})
    patch_labels(number, labels)
    record = {'at': now, 'issue': number, 'comment_id': comment_id,
              'task_spec_sha256': spec, 'actor': grant['actor'], 'mode': 'autopilot'}
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    audit = folder / AUDIT
    with audit.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, sort_keys=True) + '\n')
    audit.chmod(0o600)
    return record


def tick(repo: str, folder: pathlib.Path, *, now: int | None = None,
         max_this_run: int = 2) -> dict:
    """One background pass. Returns a report; does nothing without a live grant."""
    now = int(time.time()) if now is None else int(now)
    folder = pathlib.Path(folder)
    grant = load_grant(folder)
    if not grant_live(grant, now=now):
        return {'schema': SCHEMA, 'state': 'disabled' if not grant else 'expired',
                'approved': []}
    issues = autoadmit._api(f'repos/{repo}/issues?state=open&per_page=100')
    accepted, refused = select_candidates(issues, grant, now=now)
    approved = []
    for issue in accepted[:min(max_this_run, remaining(grant, now=now))]:
        record = approve(repo=repo, issue=issue, grant=grant, folder=folder,
                         post_comment=lambda n, b: autoadmit._api(
                             f'repos/{repo}/issues/{n}/comments', method='POST',
                             payload={'body': b}),
                         patch_labels=lambda n, labels: autoadmit._api(
                             f'repos/{repo}/issues/{n}', method='PATCH',
                             payload={'labels': labels}),
                         repository=autoadmit._repo_payload(repo), now=now)
        approved.append(record)
        grant['tasks_approved'] = int(grant.get('tasks_approved') or 0) + 1
        save_grant(folder, grant)
    return {'schema': SCHEMA, 'state': 'active', 'approved': approved,
            'refused': refused, 'remaining': remaining(grant, now=now)}


def enable(repo: str, folder: pathlib.Path, *, hours: float, max_tasks: int,
           actor: str, actor_id: int, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else int(now)
    grant = {'schema': SCHEMA, 'repo': repo, 'actor': actor, 'actor_id': int(actor_id),
             'enabled': True, 'enabled_at': now,
             'expires_at': now + int(hours * 3600), 'max_tasks': int(max_tasks),
             'tasks_approved': 0,
             'policy': 'planner proposals only; safe source paths only; normal approval record'}
    save_grant(folder, grant)
    return grant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 owner-granted autopilot')
    parser.add_argument('command', choices=('enable', 'status', 'off', 'run'))
    parser.add_argument('--repo', default='N-O-V-A-3-RE-Contributors/nova3-reconstruction')
    parser.add_argument('--hours', type=float, default=24.0)
    parser.add_argument('--max-tasks', type=int, default=3)
    parser.add_argument('--state', default=None)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    folder = pathlib.Path(args.state) if args.state else default_dir(args.repo)
    if args.command == 'enable':
        actor = autoadmit._api('user')
        login = str(actor.get('login') or '')
        permission = autoadmit._api(
            f'repos/{args.repo}/collaborators/{login}/permission')
        admin = str(permission.get('permission') or '').lower() == 'admin' or \
            str(permission.get('role_name') or '').lower() == 'admin'
        if not admin:
            print('refused: the authenticated account is not a repository ADMIN')
            return 1
        grant = enable(args.repo, folder, hours=args.hours, max_tasks=args.max_tasks,
                       actor=login, actor_id=actor['id'])
        print(json.dumps(grant, indent=2))
        return 0
    if args.command == 'off':
        grant = load_grant(folder) or {}
        grant['enabled'] = False
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        (folder / GRANT).write_text(json.dumps(grant, indent=2), encoding='utf-8')
        print('autopilot disabled')
        return 0
    if args.command == 'status':
        grant = load_grant(folder)
        report = {'grant': grant, 'live': grant_live(grant), 'remaining': remaining(grant)}
        print(json.dumps(report, indent=2) if args.json else
              f"autopilot: {'LIVE' if report['live'] else 'off'} · "
              f"remaining {report['remaining']} · grant {folder / GRANT}")
        return 0
    report = tick(args.repo, folder)
    print(json.dumps(report, indent=2) if args.json else
          f"autopilot {report['state']}: approved {len(report['approved'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
