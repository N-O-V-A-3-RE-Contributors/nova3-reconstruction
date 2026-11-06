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
        'auto_merge': data.get('auto_merge', True) is True,
        'source_first': data.get('source_first', True) is not False,
        'delivery_version': 'acceptance-gated/1',
        'require_worker_version': data.get('require_worker_version', 'acceptance-gated/1'),
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
        if grant.get('source_first'):
            from .review import allowed_paths
            paths = allowed_paths(body)
            production = [path for path in paths if path.startswith('reconstruction/')
                          and not any(part in ('tests', 'test', 'docs', 'fixtures') for part in pathlib.PurePosixPath(path).parts)
                          and pathlib.PurePosixPath(path).suffix.lower() in ('.cpp', '.c', '.cc', '.h', '.hpp', '.java', '.kt', '.py', '.rs')]
            if not production:
                refused.append((number, 'source_first_requires_game_implementation'))
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
                    max_this_run: int = 2, merge_method: str = 'squash',
                    deadline_s: float | None = None) -> dict:
    """Advance drafts through bounded acceptance/repair and controlled delivery.

    Squash uses a non-closing commit message so only explicit verified issue
    finalization can close the task. No branch deletion or history rewrite is requested.
    Every decision is appended to the private merge audit.
    """
    if merge_method != 'squash':
        return {'merged': [], 'refused': [{'pr': None, 'why': 'controlled_squash_required'}]}
    from . import delivery
    from .delivery_backend import RuntimeBackend
    import fcntl
    now = int(time.time()) if now is None else int(now)
    started = time.monotonic()
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    merged, refused, outcomes = [], [], []
    # CLI, slash commands and the timer share the same serialized delivery lane.
    # No second reviewer/repair session may race a live attempt for the same PR.
    with (folder / 'delivery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'merged': [], 'refused': [{'pr': None, 'why': 'delivery_busy'}]}
        def authorized():
            current = load_grant(folder)
            return bool(grant_live(current) and current.get('repo') == repo and current.get('auto_merge'))
        def authorize_write():
            current = load_grant(folder)
            if not authorized():
                raise RuntimeError('Owner delivery authority is off or belongs to another repository')
            actor = autoadmit._api('user')
            if actor.get('id') != current.get('actor_id') or str(actor.get('login', '')).casefold() != str(current.get('actor', '')).casefold():
                raise RuntimeError('Authenticated actor differs from owner delivery grant')
            role = autoadmit._api(f'repos/{repo}/collaborators/{actor["login"]}/permission')
            if role.get('permission') != 'admin' and role.get('role_name') != 'admin':
                raise RuntimeError('Delivery actor no longer has repository admin authority')
            latest = load_grant(folder)
            if (not grant_live(latest) or latest.get('repo') != repo or not latest.get('auto_merge')
                    or latest.get('actor_id') != actor.get('id')
                    or str(latest.get('actor', '')).casefold() != str(actor.get('login', '')).casefold()):
                raise RuntimeError('Owner delivery authority was revoked or replaced')
        def authorized_api(path, *, method='GET', payload=None):
            if method.upper() != 'GET':
                authorize_write()
            return autoadmit._api(path, method=method, payload=payload)
        backend = RuntimeBackend(pathlib.Path(__file__).resolve().parents[1], folder,
                                 api=authorized_api, authorized=authorized, authorize_write=authorize_write)
        pulls = autoadmit._api(f'repos/{repo}/pulls?state=open&per_page=100')
        pulls = sorted(pulls, key=lambda pr: int(pr.get('number') or 0))
        cursor_file = folder / 'delivery-cursor.json'
        try:
            cursor = int(json.loads(cursor_file.read_text()).get('last_pr', 0))
        except (OSError, ValueError, AttributeError):
            cursor = 0
        pulls = [pr for pr in pulls if int(pr['number']) > cursor] + [pr for pr in pulls if int(pr['number']) <= cursor]
        def progress_priority(pr):
            try:
                file = folder / 'delivery' / (delivery._digest([repo, int(pr['number'])]) + '.json')
                last = json.loads(file.read_text()).get('last') or {}
                if (last.get('state') in ('repaired', 'updating_base', 'waiting_ci', 'merged_issue_open')
                        and (last.get('state') in ('waiting_ci', 'merged_issue_open')
                             or last.get('head') != (pr.get('head') or {}).get('sha'))):
                    return 0
            except (OSError, ValueError, AttributeError):
                pass
            return 1
        pulls.sort(key=progress_priority)  # stable: cursor fairness within each lane
        for pr in pulls:
            if len(merged) >= max_this_run:
                break
            if deadline_s is not None and time.monotonic() - started >= deadline_s:
                refused.append({'pr': int(pr['number']), 'why': 'deferred_deadline'})
                break
            number = int(pr['number'])
            result = delivery.process_pr(repo, number, folder, api=authorized_api,
                                         reviewer=backend.reviewer, repairer=backend.repairer,
                                         max_repairs=2)
            outcomes.append(result)
            from .model_agents import private_json
            private_json(cursor_file, {'last_pr': number})
            with (folder / MERGE_AUDIT).open('a') as handle:
                handle.write(json.dumps(dict(result, at=now, mode='acceptance_delivery'), sort_keys=True) + '\n')
            (folder / MERGE_AUDIT).chmod(0o600)
            if result['state'] == 'delivered':
                merged.append({'pr': number, 'sha': result.get('head'),
                               'title': pr.get('title') or ''})
            else:
                refused.append({'pr': number, 'why': result['reason'], 'state': result['state']})
            # One PR involving model work per timer pass (at most one fresh
            # reviewer + one repair). Cached/static refusals need no model.
            if backend.sessions_started or result['state'] == 'updating_base':
                break
    return {'merged': merged, 'refused': refused, 'delivery': outcomes}


# ---------------------------------------------------------------------------
# One background pass
# ---------------------------------------------------------------------------

def worker_orchestration_ready(repo: str, version: str = 'acceptance-gated/1') -> bool:
    """An old running daemon cannot attest that newly written code is loaded."""
    try:
        from . import worker
        paths = worker.slot_paths(worker.state_root(repo), 'dsh')
        state = json.loads((pathlib.Path(paths.state) / 'supervisor.json').read_text())
        cap = state.get('orchestration') or {}
        pid = state.get('supervisor_pid')
        if type(pid) is not int or pid <= 0 or cap.get('pid') != pid or cap.get('version') != version:
            return False
        ticks = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]
        return str(cap.get('process_start_ticks')) == ticks
    except (OSError, ValueError, TypeError, IndexError, AttributeError):
        return False


def delivery_summary(folder: pathlib.Path) -> dict:
    counts, recent = {}, []
    for file in sorted((pathlib.Path(folder) / 'delivery').glob('*.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        try:
            last = json.loads(file.read_text()).get('last') or {}
            state = str(last.get('state') or 'unknown')
            counts[state] = counts.get(state, 0) + 1
            if len(recent) < 5:
                recent.append({key: last.get(key) for key in ('pr', 'state', 'reason')})
        except (OSError, ValueError, AttributeError):
            counts['unreadable'] = counts.get('unreadable', 0) + 1
    return {'counts': counts, 'recent': recent}


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
    if grant.get('repo') != repo:
        return {'schema': SCHEMA, 'state': 'wrong_repository', 'approved': [], 'merged': []}
    paused = credit_paused(repo)
    if paused is not None:
        # Temporary: the switch stays on and the next tick checks credit again.
        return {'schema': SCHEMA, 'state': 'paused_credits', 'paused': paused,
                'approved': [], 'merged': []}
    issues = autoadmit._api(f'repos/{repo}/issues?state=open&per_page=100')
    accepted, refused = select_candidates(issues, grant, now=now)
    approved = []
    runtime_ready = worker_orchestration_ready(repo, grant.get('require_worker_version', 'acceptance-gated/1'))
    if not approve_new or not runtime_ready:
        # Delivery can run in this fresh timer process. Don't feed new tasks to
        # an old daemon that has not loaded bounded children/planning controls.
        if accepted and not runtime_ready:
            refused += [(item['number'], 'worker_restart_required') for item in accepted]
        accepted = []
    for issue in accepted[:max_this_run]:
        current_grant = load_grant(folder)
        if not grant_live(current_grant) or current_grant.get('repo') != repo:
            refused.append((issue['number'], 'approval_authority_revoked'))
            break
        actor = autoadmit._api('user')
        permission = autoadmit._api(f'repos/{repo}/collaborators/{actor["login"]}/permission')
        if (actor.get('id') != current_grant.get('actor_id')
                or str(actor.get('login', '')).casefold() != str(current_grant.get('actor', '')).casefold()
                or (permission.get('permission') != 'admin' and permission.get('role_name') != 'admin')):
            refused.append((issue['number'], 'approval_actor_not_authorized'))
            continue
        record = approve_proposal(repo=repo, issue=issue, grant=current_grant, folder=folder,
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
             'source_first': True, 'delivery_version': 'acceptance-gated/1',
             'require_worker_version': 'acceptance-gated/1',
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
            'grant': grant, 'paused': paused, 'path': str(folder / GRANT),
            'worker_runtime_ready': worker_orchestration_ready(repo),
            'delivery': delivery_summary(folder)}


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
