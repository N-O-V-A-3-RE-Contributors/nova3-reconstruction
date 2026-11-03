"""Owner-enabled autopilot: keep safe work moving while the owner is away.

Authority model
---------------
The owner/admin issues **one persistent switch**, not a timed grant.  While the
switch is on, a background timer may:

* take planner proposals (``NOVA3_DSH_PLANNER_PROPOSAL`` marker) that no human
  has touched and whose ``Allowed paths`` stay inside the safe source trees
  (``--approve-mode off`` limits the pass to merges while that ceiling is
  reached, so delivery keeps draining without authorising new task creation),
* write a normal approval record and the queue labels, exactly like a human
  approval (so the same claim-time gate still verifies it),
* review and merge a pull request only after the read-only compliance review
  passes **and** every GitHub check on the head commit is green,
* append every decision to ``audit.jsonl``.

There is deliberately **no expiry timestamp and no task budget**.  Those turned
"automation" into "automation until an arbitrary moment even though credit was
available", which is the failure this revision removes.  The loop runs until the
owner turns it off (``python -m nova3_team.autopilot off``, the Mission tab in
the Web UI, or stopping the systemd timer).  The one legitimate automatic pause
is **depleted credit**: the supervisor already enters ``WAITING_CREDITS`` and
rechecks by itself, and the autopilot mirrors that state without disabling the
switch, so it resumes on the next tick once credit is back.

It cannot approve outside the grant, touch protected trees, change policy,
extend itself or run when the switch is off.  Merging is limited to PRs that
pass the repository's own read-only reviewer and whose checks are all green.

Grant file (0600, owner-only): ``~/.cache/nova3-team/<owner_repo>/autopilot/grant.json``.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from . import autoadmit

SCHEMA = 'nova3-autopilot/2'
LEGACY_SCHEMAS = ('nova3-autopilot/1',)
GRANT = 'grant.json'
AUDIT = 'audit.jsonl'
MERGE_AUDIT = 'merge-audit.jsonl'
ACTIVE_LABELS = {'nova:blocked', 'nova:claimed', 'nova:review', 'nova:integrated'}
PASSING_CONCLUSIONS = {'success', 'neutral', 'skipped'}


def default_dir(repo: str) -> pathlib.Path:
    return pathlib.Path.home() / '.cache' / 'nova3-team' / repo.replace('/', '_') / 'autopilot'


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------

def load_grant(folder: pathlib.Path) -> dict | None:
    """Read the switch, accepting and migrating the old timed grant.

    A v1 grant is mapped to a v2 switch with the same enabled state.  Its
    ``expires_at``/``max_tasks`` fields are intentionally discarded: they are
    the time-boxing this schema removes.
    """
    try:
        data = json.loads((pathlib.Path(folder) / GRANT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get('schema') not in (SCHEMA, *LEGACY_SCHEMAS):
        return None
    return {
        'schema': SCHEMA,
        'repo': data.get('repo'),
        'actor': data.get('actor'),
        'actor_id': data.get('actor_id'),
        'enabled': data.get('enabled') is True,
        'auto_merge': data.get('auto_merge', True) is not False,
        'enabled_at': data.get('enabled_at') or data.get('created_at'),
        'policy': data.get('policy') or
                  'planner proposals only; safe source paths only; normal approval record; '
                  'merge only reviewed PRs with green checks',
    }


def save_grant(folder: pathlib.Path, grant: dict) -> None:
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / GRANT
    path.write_text(json.dumps(grant, indent=2, sort_keys=True), encoding='utf-8')
    path.chmod(0o600)


def grant_live(grant: dict | None, *, now: int | None = None) -> bool:
    """The switch is on.  `now` is accepted for call-site compatibility.

    There is no expiry and no task counter: a live switch stays live until it is
    explicitly turned off or credit is depleted (a temporary pause, not a
    disable).
    """
    return bool(grant) and grant.get('enabled') is True


def remaining(grant: dict | None, *, now: int | None = None) -> int:
    """No task budget exists.  Kept so old status callers do not crash (-1 = unlimited)."""
    return -1 if grant_live(grant, now=now) else 0


def credit_paused(repo: str, *, slot: str = 'dsh') -> dict | None:
    """Mirror the supervisor's depleted-credit state (a pause, never a disable).

    Reads the supervisor's durable state file only; it does not query a model or
    a provider.  Returns a small dict when the account cannot pay, else None.
    """
    try:
        from . import worker
        paths = worker.slot_paths(worker.state_root(repo), slot)
        state = json.loads((paths.state / 'supervisor.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, AttributeError, ImportError):
        return None
    if not isinstance(state, dict) or state.get('state') != 'WAITING_CREDITS':
        return None
    return {'state': 'WAITING_CREDITS', 'reason': state.get('reason') or '',
            'since_unix': state.get('updated_unix')}


# ---------------------------------------------------------------------------
# Planner proposals
# ---------------------------------------------------------------------------

def select_candidates(issues: list[dict], grant: dict, *,
                      now: int | None = None) -> tuple[list[dict], list[tuple]]:
    """Planner proposals that are safe to auto-approve, and why others were refused."""
    accepted, refused = [], []
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
        accepted.append(issue)
    return accepted, refused


def approve_proposal(*, repo: str, issue: dict, grant: dict, folder: pathlib.Path,
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


# ---------------------------------------------------------------------------
# Merge gate
# ---------------------------------------------------------------------------

def merge_decision(pr: dict, checks: dict, review: dict, *,
                   auto_merge: bool = True) -> tuple[bool, str]:
    """Pure merge gate: reviewed, green, non-draft and explicitly enabled.

    `checks` is ``{'runs': [...], 'combined': 'success'}`` as read from GitHub.
    No network here, so the policy is unit-tested directly.
    """
    if not auto_merge:
        return False, 'auto_merge_disabled'
    if pr.get('draft') or pr.get('isDraft'):
        return False, 'draft'
    if pr.get('state') not in (None, 'open'):
        return False, 'not_open'
    if pr.get('mergeable') is False or pr.get('mergeable_state') in ('dirty', 'blocked'):
        return False, 'not_mergeable'
    if review.get('verdict') != 'pass':
        return False, 'review_not_pass'
    runs = (checks or {}).get('runs') or []
    if not runs:
        return False, 'no_checks'
    for run in runs:
        if run.get('status') != 'completed':
            return False, 'checks_pending'
        if run.get('conclusion') not in PASSING_CONCLUSIONS:
            return False, f"check_failed:{run.get('name') or 'unknown'}"
    combined = (checks or {}).get('combined')
    if combined not in (None, 'success'):
        return False, f'combined_status:{combined}'
    return True, 'reviewed_and_green'


def _checks_for(repo: str, sha: str) -> dict:
    runs = autoadmit._api(
        f'repos/{repo}/commits/{sha}/check-runs?per_page=100').get('check_runs') or []
    try:
        status = autoadmit._api(f'repos/{repo}/commits/{sha}/status') or {}
    except Exception:
        status = {}
    # GitHub reports a combined state of "pending" when there are NO legacy
    # commit statuses at all. That is "not applicable", not "still running".
    combined = None if int(status.get('total_count') or 0) == 0 else status.get('state')
    return {'runs': runs, 'combined': combined}


def merge_validated(repo: str, folder: pathlib.Path, *, now: int | None = None,
                    max_this_run: int = 2, merge_method: str = 'merge',
                    deadline_s: float | None = None) -> dict:
    """Merge PRs that pass the read-only reviewer and have green checks.

    Every decision (merge or refusal) is appended to ``merge-audit.jsonl`` so the
    owner can reconstruct why a PR did or did not land.
    """
    from . import review as review_mod
    now = int(time.time()) if now is None else int(now)
    started = time.monotonic()
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only ready PRs can merge, and GitHub lists newest first. Asking for ready
    # PRs directly means each pass reaches fresh candidates instead of re-reviewing
    # the same full-page of drafts that will be refused anyway.
    pulls = autoadmit._api(f'repos/{repo}/pulls?state=open&per_page=100')
    others = [pr for pr in pulls if pr.get('draft')]
    # Oldest first: the backlog drains in the order it was created, and the
    # per-run merge cap is spent on PRs that have waited longest.
    pulls = sorted((pr for pr in pulls if not pr.get('draft')),
                   key=lambda pr: int(pr.get('number') or 0))
    merged, refused = [], []
    for pr in pulls:
        # A long queue must not get the unit killed mid-review: stop cleanly and
        # leave the rest to the next tick, which resumes from the same list.
        if deadline_s is not None and time.monotonic() - started > deadline_s:
            refused.append({'pr': int(pr['number']), 'why': 'deferred_deadline'})
            continue
        number = int(pr['number'])
        head = (pr.get('head') or {}).get('sha') or ''
        try:
            verdict = review_mod.review(repo, number)
            checks = _checks_for(repo, head) if head else {'runs': [], 'combined': None}
        except Exception as error:      # network/CLI failure: never merge blind
            refused.append({'pr': number, 'why': f'review_error:{str(error)[:120]}'})
            continue
        ok, why = merge_decision(pr, checks, verdict, auto_merge=True)
        record = {'at': now, 'pr': number, 'head': head, 'merge': ok, 'why': why,
                  'review': verdict.get('verdict'), 'checks': checks.get('combined'),
                  'mode': 'autopilot'}
        audit = folder / MERGE_AUDIT
        with audit.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, sort_keys=True) + '\n')
        audit.chmod(0o600)
        if not ok:
            refused.append({'pr': number, 'why': why})
            continue
        if len(merged) >= max_this_run:
            refused.append({'pr': number, 'why': 'merge_cap_this_run'})
            continue
        try:
            result = autoadmit._api(
                f'repos/{repo}/pulls/{number}/merge', method='PUT',
                payload={'merge_method': merge_method, 'sha': head})
        except Exception as error:
            refused.append({'pr': number, 'why': f'merge_error:{str(error)[:120]}'})
            continue
        merged.append({'pr': number, 'sha': (result or {}).get('sha') or head,
                       'title': pr.get('title') or ''})
    # Drafts were never candidates; report them as one line, not 30 entries.
    if others:
        refused.append({'pr': 'drafts', 'why': f'{len(others)}_draft_prs_not_mergeable'})
    return {'merged': merged, 'refused': refused}


# ---------------------------------------------------------------------------
# One background pass
# ---------------------------------------------------------------------------

def tick(repo: str, folder: pathlib.Path, *, now: int | None = None,
         max_this_run: int = 2, merge: bool | None = None,
         deadline_s: float | None = None, approve_new: bool = True) -> dict:
    """One background pass. Does nothing without an enabled switch."""
    now = int(time.time()) if now is None else int(now)
    folder = pathlib.Path(folder)
    grant = load_grant(folder)
    if not grant_live(grant, now=now):
        return {'schema': SCHEMA, 'state': 'disabled' if not grant else 'off',
                'approved': [], 'merged': []}
    paused = credit_paused(repo)
    if paused is not None:
        # Temporary: the switch stays on and the next tick checks credit again.
        return {'schema': SCHEMA, 'state': 'paused_credits', 'paused': paused,
                'approved': [], 'merged': []}
    issues = autoadmit._api(f'repos/{repo}/issues?state=open&per_page=100')
    accepted, refused = select_candidates(issues, grant, now=now)
    approved = []
    if not approve_new:
        # Merge-only pass: proposals stay unapproved for the owner.
        accepted = []
    for issue in accepted[:max_this_run]:
        record = approve_proposal(repo=repo, issue=issue, grant=grant, folder=folder,
                         post_comment=lambda n, b: autoadmit._api(
                             f'repos/{repo}/issues/{n}/comments', method='POST',
                             payload={'body': b}),
                         patch_labels=lambda n, labels: autoadmit._api(
                             f'repos/{repo}/issues/{n}', method='PATCH',
                             payload={'labels': labels}),
                         repository=autoadmit._repo_payload(repo), now=now)
        approved.append(record)
    result = {'schema': SCHEMA, 'state': 'active', 'approved': approved,
              'refused': refused, 'merged': []}
    if grant.get('auto_merge', True) and merge is not False:
        try:
            merge_result = merge_validated(repo, folder, now=now,
                                           max_this_run=max_this_run,
                                           deadline_s=deadline_s)
            result['merged'] = merge_result['merged']
            result['merge_refused'] = merge_result['refused']
        except Exception as error:
            result['merge_error'] = str(error)[:200]
    return result


def enable(repo: str, folder: pathlib.Path, *, actor: str, actor_id: int,
           auto_merge: bool = True, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else int(now)
    grant = {'schema': SCHEMA, 'repo': repo, 'actor': actor, 'actor_id': int(actor_id),
             'enabled': True, 'enabled_at': now, 'auto_merge': bool(auto_merge),
             'policy': 'planner proposals only; safe source paths only; normal approval '
                       'record; merge only reviewed PRs with green checks'}
    save_grant(folder, grant)
    return grant


def disable(folder: pathlib.Path) -> dict:
    folder = pathlib.Path(folder)
    grant = load_grant(folder) or {'schema': SCHEMA}
    grant['enabled'] = False
    grant.setdefault('schema', SCHEMA)
    save_grant(folder, grant)
    return grant


def set_auto_merge(folder: pathlib.Path, enabled: bool) -> dict:
    """Change only the merge capability; the on/off switch is untouched."""
    folder = pathlib.Path(folder)
    grant = load_grant(folder)
    if grant is None:
        raise SystemExit('autopilot: no switch configured yet; enable it first')
    grant['auto_merge'] = bool(enabled)
    save_grant(folder, grant)
    return grant


def state(repo: str, folder: pathlib.Path | None = None, *, now: int | None = None) -> dict:
    """A small JSON-safe status object used by the CLI and the Mission tab."""
    folder = pathlib.Path(folder) if folder else default_dir(repo)
    grant = load_grant(folder)
    paused = credit_paused(repo) if grant_live(grant, now=now) else None
    if grant is None:
        mode = 'unset'
    elif not grant_live(grant, now=now):
        mode = 'off'
    elif paused is not None:
        mode = 'paused_credits'
    else:
        mode = 'on'
    return {'schema': SCHEMA, 'mode': mode, 'enabled': grant_live(grant, now=now),
            'auto_merge': bool(grant.get('auto_merge', True)) if grant else False,
            'grant': grant, 'paused': paused, 'path': str(folder / GRANT)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 persistent autopilot')
    parser.add_argument('command', choices=('enable', 'status', 'off', 'run', 'configure'))
    parser.add_argument('--repo', default='N-O-V-A-3-RE-Contributors/nova3-reconstruction')
    parser.add_argument('--merge-mode', choices=('on', 'off'), default=None,
                        help='auto-merge reviewed green PRs (enable/configure)')
    parser.add_argument('--no-auto-merge', action='store_true',
                        help='deprecated alias for --merge-mode off')
    parser.add_argument('--no-merge', action='store_true', help='skip merge in this run')
    parser.add_argument('--approve-mode', choices=('on', 'off'), default='on',
                        help='off: merge only, never approve new proposals')
    parser.add_argument('--state', default=None)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--deadline-s', type=float, default=120.0,
                        help='stop the merge scan before systemd\'s TimeoutStartSec '
                             '(the next tick resumes); 0 disables')
    args = parser.parse_args(argv)
    folder = pathlib.Path(args.state) if args.state else default_dir(args.repo)
    auto_merge = None
    if args.merge_mode is not None:
        auto_merge = args.merge_mode == 'on'
    elif args.no_auto_merge:
        auto_merge = False
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
        grant = enable(args.repo, folder, actor=login, actor_id=actor['id'],
                       auto_merge=True if auto_merge is None else auto_merge)
        print(json.dumps(grant, indent=2))
        return 0
    if args.command == 'configure':
        if auto_merge is None:
            print('configure needs --merge-mode on|off')
            return 2
        print(json.dumps(set_auto_merge(folder, auto_merge), indent=2))
        return 0
    if args.command == 'off':
        disable(folder)
        print('autopilot disabled')
        return 0
    if args.command == 'status':
        report = state(args.repo, folder)
        print(json.dumps(report, indent=2) if args.json else
              f"autopilot: {report['mode'].upper()} · auto-merge "
              f"{'on' if report['auto_merge'] else 'off'} · {report['path']}")
        return 0
    report = tick(args.repo, folder, merge=not args.no_merge,
                  deadline_s=args.deadline_s or None,
                  approve_new=args.approve_mode == 'on')
    print(json.dumps(report, indent=2) if args.json else
          f"autopilot {report['state']}: approved {len(report['approved'])} "
          f"merged {len(report.get('merged') or [])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
