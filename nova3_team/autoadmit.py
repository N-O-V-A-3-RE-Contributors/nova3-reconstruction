"""Owner-authorized auto-admission of SAFE reconstruction tasks (#135 gate intact).

NOVA3_AUTOADMIT_V1. The problem this solves: every queued task needs a
handwritten owner approval record bound to its exact revision, so an autonomous
loop can create candidate tasks but nothing it makes can ever be claimed
without the owner typing JSON. That is the bottleneck, not model capacity.

The narrow fix: a **waiver** the owner writes once, in one comment (today on
the holder issue, #16), listing which specific issues they authorise an
automated approver to approve -- and only those. For each waived issue the
approver writes an *approval record* in the format
`nova3_team.access_policy` documents, then applies the queue labels
(`nova:queued` plus the `nova:approved:<comment-id>` locator), so the existing
coordinator gate verifies it exactly like a hand-written approval. Nothing in
the gate, the coordinator or the worker is bypassed or weakened:

* the owner's waiver is itself checked against live evidence (current owner or
  ADMIN, fresh, unexpired) by the same `_role` machinery the gate uses;
* `admit()` refuses any issue outside the waiver, any issue whose text changed
  since the waiver was written, any pull request, closed or locked issue, and
  any issue whose body or allowed paths are not confined to
  `grant.SAFE_PATHS`;
* the produced record passes `access_policy._record` (schema, actor identity,
  freshness windows) and is then verified by the real gate in the self-test;
* **merge authority is untouched** (nothing here can merge), and every approval
  this module writes is recorded locally and reported to the owner.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import re
import time
import uuid
from dataclasses import dataclass

from . import access_policy as policy
from .common import lease_put

MARKER = 'NOVA3_AUTOADMIT_WAIVER'
WAIVER_RE = re.compile(r'<!--\s*NOVA3_AUTOADMIT_WAIVER:\s*(\{.*?\})\s*-->', re.S)
POLICY_VERSION = 'NOVA3_AUTOADMIT_V1'
# The gate refuses any approval whose window exceeds MAX_APPROVAL_AGE, and the
# waiver is anchored at its comment's creation time. Advertising a longer TTL
# would be a lie the gate would silently truncate, so the protocol maximum IS
# the default: one unattended run, never a standing grant.
DEFAULT_TTL_S = policy.MAX_APPROVAL_AGE
MAX_TTL_S = policy.MAX_APPROVAL_AGE
# Paths an auto-admitted task may touch. Everything else (CI, services,
# credentials, proprietary trees, the queue itself, docs of record) needs a
# human. Enforced by BOTH the grant (owner) and this module (approver).
SAFE_PATHS = ('reconstruction/', 'nova3_team/', 'tools/', 'docs/')
DENIED_PATHS = ('originals/', 'analysis/', 'research/', 'generated/', 'Archive/',
                'build/', 'materials/', '.github/', '.codex/', 'coordination/',
                '.venv/', '.local/', 'analysis_db/')
LOOP_ACTION = policy.Action.APPROVE_QUEUE


@dataclass(frozen=True)
class Waiver:
    """The owner's written authorisation, parsed from one issue comment.

    ``issued_at`` is the comment's own ``created_at`` (the human act), never a
    number the waiver text chooses; ``expires_at`` may shorten the protocol
    window but never extend it beyond MAX_APPROVAL_AGE.
    """

    comment_id: int
    actor_id: int
    actor_login: str
    issues: tuple
    issued_at: int
    expires_at: int
    task_spec: dict            # "163" -> task_spec_sha256 bound at waiver time
    standing: bool = False     # owner wrote "standing": true (autopilot-gated)
    renew_autopilot: bool = False  # owner allows renewing autopilot-approved tasks

    def is_live(self, now: int | None = None, *, standing_enabled: bool = False) -> bool:
        """A one-run waiver is clock-bound; a standing one needs the owner switch.

        The standing form does not extend the *approval record* window: the gate
        still refuses any record older than MAX_APPROVAL_AGE, so the automated
        approver must renew records.  What it removes is the need for the owner
        to re-post a waiver every hour while the persistent autopilot switch is
        on.  Turning the switch off makes every standing waiver dead at once.
        """
        now = int(time.time()) if now is None else int(now)
        if self.standing:
            return bool(self.issued_at) and bool(standing_enabled) \
                and 0 < self.issued_at <= now
        return (bool(self.issued_at) and self.issued_at <= now < self.expires_at
                and 0 < self.expires_at - self.issued_at <= policy.MAX_APPROVAL_AGE)

    @property
    def live(self) -> bool:
        return self.is_live()

    def covers(self, number: int, now: int | None = None, *,
               standing_enabled: bool = False) -> bool:
        return int(number) in set(self.issues) and self.is_live(
            now, standing_enabled=standing_enabled)

    def as_dict(self) -> dict:
        return {'policy': POLICY_VERSION, 'comment_id': self.comment_id,
                'issues': list(self.issues), 'issued_at': self.issued_at,
                'expires_at': self.expires_at, 'standing': self.standing,
                'renew_autopilot': self.renew_autopilot,
                'task_spec': dict(self.task_spec)}


def task_spec_sha256(issue: dict) -> str:
    """The exact digest the gate binds: id, number, title, body, created_at."""
    task = {key: issue[key] for key in ('id', 'number', 'title', 'body', 'created_at')}
    # The claim gate binds task text without the coordinator lease/trailing
    # whitespace. Approval publication must hash that exact same canonical text.
    task['body'] = lease_put(task['body'], None)
    return policy.operation_digest(task)


def _canonical(message: dict) -> str:
    return json.dumps(message, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def label_names(issue: dict) -> list:
    """Label names, tolerating both GitHub label shapes ({'name': ...} or str)."""
    names = []
    for label in issue.get('labels') or ():
        name = label.get('name') if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            names.append(name)
    return names


def waiver_text(issues, *, ttl_seconds: int = DEFAULT_TTL_S, now: int | None = None,
                logins: dict | None = None, standing: bool = False,
                renew_autopilot: bool = False) -> str:
    """The comment the OWNER posts once (copy from the report, paste into GitHub).

    It carries only issue numbers, digests and a short expiry -- no secrets. The
    author of the comment is the approver of record; this module never pretends
    to be that author.
    """
    now = int(time.time()) if now is None else int(now)
    ttl = max(60, min(MAX_TTL_S, int(ttl_seconds)))
    numbers = sorted({int(item['number']) for item in issues})
    spec = {str(item['number']): task_spec_sha256(item) for item in issues}
    if standing:
        body = {'policy': POLICY_VERSION, 'issues': numbers,
                'task_spec': {str(n): spec[str(n)] for n in numbers},
                'standing': True,
                **({'renew_autopilot': True} if renew_autopilot else {}),
                'note': 'Standing authorisation: while the persistent autopilot switch is '
                        'ON the automated approver may (re)issue short-lived approvals for '
                        'ONLY these issue numbers, at EXACTLY these task texts. Turning '
                        'the switch off revokes this immediately; editing a task text '
                        'requires a new waiver.' + (
                            ' Issues the autopilot already approved (local audit log, '
                            'exact task text) may also be renewed.' if renew_autopilot
                            else '')}
    else:
        body = {'policy': POLICY_VERSION, 'issues': numbers,
                'task_spec': {str(n): spec[str(n)] for n in numbers},
                'expires_at': now + ttl,
                'note': 'One-run authorisation: the automated approver may approve ONLY '
                        'these issue numbers, at EXACTLY these task texts, before '
                        'expires_at.'}
    return ('<!-- ' + MARKER + ': ' + _canonical(body) + ' -->')


def parse_waiver(text: str, *, comment_id: int = 0, actor_id: int = 0,
                 actor_login: str = '', created_at: int = 0) -> Waiver | None:
    """Parse the owner's waiver comment. Fails closed on anything unexpected."""
    match = WAIVER_RE.search(text or '')
    if not match:
        return None
    try:
        body = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get('policy') != POLICY_VERSION:
        return None
    try:
        issues = tuple(sorted({int(n) for n in body.get('issues') or []}))
        expires = int(body.get('expires_at') or 0)
        spec = {str(k): str(v) for k, v in (body.get('task_spec') or {}).items()}
    except (TypeError, ValueError):
        return None
    standing = body.get('standing') is True
    renew_autopilot = body.get('renew_autopilot') is True and standing
    if not issues or any(n <= 0 for n in issues):
        return None
    if len(issues) > 20 or any(not re.fullmatch(r'[0-9a-f]{64}', v) for v in spec.values()):
        return None
    # The waiver names its own expiry; the window is anchored at the comment's
    # creation time and can never exceed the protocol maximum, so a waiver
    # cannot extend itself.
    issued = int(created_at or 0)
    if standing:
        return Waiver(int(comment_id), int(actor_id), str(actor_login), issues,
                      issued, 0, spec, True, renew_autopilot)
    bound = issued + policy.MAX_APPROVAL_AGE if issued else expires
    return Waiver(int(comment_id), int(actor_id), str(actor_login), issues,
                  issued, min(expires, bound) if issued else expires, spec, False)


def safety_blockers(issue: dict) -> tuple:
    """Fixed reasons an issue is NOT safe to auto-admit (empty tuple = safe)."""
    problems = []
    body = issue.get('body') or ''
    if issue.get('pull_request'):
        # A pull request carries no issue identity, so the gate could never
        # verify an approval against it; refuse before judging its scope.
        return ('is_pull_request',)
    if (issue.get('state') or 'open') != 'open':
        problems.append('not_open')
    if issue.get('locked'):
        problems.append('locked')
    # Never re-open a task that a human or another worker already parked or
    # owns: auto-admission adds work, it never overrides a live state.
    active = {'nova:blocked', 'nova:claimed', 'nova:review', 'nova:integrated'}
    if active & set(label_names(issue)):
        problems.append('already_active_or_closed')
    for line in body.splitlines():
        if not line.startswith('Allowed paths:'):
            continue
        paths = [p.strip() for p in line.split(':', 1)[1].split(',') if p.strip()]
        if not paths:
            problems.append('no_allowed_paths')
        for path in paths:
            if '..' in path.split('/') or path.startswith('/') or '\\' in path:
                problems.append('unsafe_path')
            elif any(path.startswith(root) for root in DENIED_PATHS):
                problems.append('denied_path')
            elif not any(path.startswith(root) for root in SAFE_PATHS) \
                    and path != 'CMakeLists.txt':
                problems.append('outside_safe_paths')
    if 'Allowed paths:' not in body:
        problems.append('missing_allowed_paths')
    lowered = body.lower()
    for action in ('merge', 'force-push', 'deploy', 'sudo', 'gateway install',
                   'self-hosted runner'):
        if re.search(rf'\b{re.escape(action)}\b', lowered):
            problems.append('privileged_action_in_scope')
            break
    return tuple(sorted(set(problems)))


def approval_record(*, issue: dict, waiver: Waiver, repository: dict,
                    created_at: int, ttl_seconds: int = DEFAULT_TTL_S) -> str:
    """Build the approval comment body, exactly as the gate re-reads it.

    Window arithmetic is the validator's, so the caller cannot create a record
    that would be refused: issued <= created <= now < expires, a lifetime of
    at most MAX_APPROVAL_AGE and issued within MAX_CONSENT_AGE of creation.
    """
    arguments = {'transition': 'queue'}
    operation_sha256 = policy.operation_digest(arguments)
    # access_policy._digest() requires exactly 256 bits of hex; a UUID4 hex
    # (128 bits) is refused by the gate as invalid evidence.
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()
    task = {key: issue[key] for key in ('id', 'number', 'title', 'body', 'created_at')}
    # The claim gate binds task text without the coordinator lease/trailing
    # whitespace. Approval publication must hash that exact same canonical text.
    task['body'] = lease_put(task['body'], None)
    binding = {
        'version': policy.POLICY_VERSION, 'origin': policy.API_ORIGIN,
        'repository_id': int(repository['id']), 'owner_id': int(repository['owner']['id']),
        'issue_id': issue['id'], 'issue_number': issue['number'],
        'task_spec_sha256': policy.operation_digest(task),
        'action': LOOP_ACTION.value, 'operation_sha256': operation_sha256,
        'operation_arguments': arguments, 'nonce': nonce, 'executor_user_id': None,
    }
    ttl = max(60, min(policy.MAX_APPROVAL_AGE, int(ttl_seconds)))
    issued = int(created_at)
    record = dict(binding)
    # Timestamps are GitHub timestamps (strict ISO-8601 UTC), not epoch ints:
    # _time() compares them against the comment's own created_at string.
    record.update({'kind': 'nova3.approval', 'actor_id': int(waiver.actor_id),
                   'actor_login': waiver.actor_login, 'issued_at': _github_time(issued),
                   'expires_at': _github_time(issued + ttl)})
    return _canonical(record)


def _github_time(seconds: int) -> str:
    return datetime.datetime.fromtimestamp(int(seconds), datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def admit(*, issues, repository: dict, reserve, verify_role, post_comment,
          fetch_issue=None, now: int | None = None,
          ttl_seconds: int = DEFAULT_TTL_S, waiver_source: dict | None = None,
          standing_enabled: bool = False, previous=None,
          audit_specs: dict | None = None) -> dict:
    """Approve only waiver-covered, unchanged, safe issues; write one record each.

    `reserve(number) -> bool` takes a durable one-shot slot for an issue and
    returns False when one was already taken, so a crash or a second approver
    can never write two approvals for one issue.
    `verify_role(login) -> (allowed, actor_id)` re-reads the waiver author's
    CURRENT role through live evidence; a waiver from someone who is no longer
    an owner/ADMIN is ignored.
    `fetch_issue(number) -> dict | None` returns the live issue payload; a
    missing fetch is a refusal, never an assumption.
    `waiver_source` is the comment that carries the waiver:
    ``{'text', 'comment_id', 'actor_login', 'created_at'}``. It is what
    ``run()`` reads from the holder issue's COMMENTS, and passing it here is the
    only production path: the holder's body is a status board, not the waiver.
    The older body-scan remains for callers that keep the waiver in a body, but
    exactly one source is allowed.
    """
    now = int(time.time()) if now is None else int(now)
    fetch = fetch_issue or _fetch_issue
    report = {'policy': POLICY_VERSION, 'approved': [], 'skipped': [], 'waiver': None}
    sources = []
    if waiver_source is not None:
        sources.append(dict(waiver_source))
    for issue in issues:
        if WAIVER_RE.search(issue.get('body') or ''):
            sources.append({'text': issue.get('body') or '',
                            'comment_id': int(issue.get('id') or 0),
                            'actor_login': str(issue.get('_waiver_author') or ''),
                            'created_at': int(issue.get('_waiver_created') or 0)})
    if not sources:
        report['reason'] = 'no_waiver'
        return report
    # Exactly one waiver may exist: two would make "who authorised this"
    # ambiguous, and the gate's own locator rule rejects ambiguity too.
    if len(sources) > 1:
        report['reason'] = 'ambiguous_waiver'
        return report
    source = sources[0]
    waiver = parse_waiver(str(source.get('text') or ''),
                          comment_id=int(source.get('comment_id') or 0),
                          actor_login=str(source.get('actor_login') or ''),
                          created_at=int(source.get('created_at') or 0))
    if waiver is None:
        report['reason'] = 'unparseable_waiver'
        return report
    allowed, actor_id = verify_role(waiver.actor_login)
    if not allowed:
        report['reason'] = 'waiver_author_not_owner'
        return report
    waiver = Waiver(waiver.comment_id, actor_id, waiver.actor_login, waiver.issues,
                    waiver.issued_at, waiver.expires_at, waiver.task_spec,
                    waiver.standing, waiver.renew_autopilot)
    report['waiver'] = waiver.as_dict()
    if not waiver.is_live(now, standing_enabled=standing_enabled):
        report['reason'] = ('standing_waiver_needs_autopilot' if waiver.standing
                            else 'waiver_expired')
        return report
    targets = list(waiver.issues)
    if waiver.standing and waiver.renew_autopilot and audit_specs:
        # The owner's standing waiver may also renew issues the AUTOPILOT already
        # approved: the local audit log is the authorization, not a GitHub label
        # (labels are contributor-writable), and the audited task-spec digest is
        # rechecked against the live issue, so an edited task is still refused.
        targets += sorted(int(number) for number in audit_specs
                          if int(number) not in set(waiver.issues))
    if targets != list(waiver.issues):
        report['audit_coverage'] = sorted(int(number) for number in targets
                                          if int(number) not in set(waiver.issues))
    for target in targets:
        issue = fetch(target)
        if issue is None:
            report['skipped'].append((target, 'issue_missing'))
            continue
        blockers = safety_blockers(issue)
        if blockers:
            report['skipped'].append((target, ','.join(blockers)))
            continue
        if waiver.task_spec.get(str(target)) not in (None, task_spec_sha256(issue)):
            report['skipped'].append((target, 'task_text_changed_since_waiver'))
            continue
        audited = (audit_specs or {}).get(str(target)) or (audit_specs or {}).get(target)
        if audited and audited != task_spec_sha256(issue):
            report['skipped'].append((target, 'task_text_changed_since_autopilot_approval'))
            continue
        # A reservation whose recorded expiry has passed is renewed: the gate
        # refuses records older than MAX_APPROVAL_AGE, so continuity requires a
        # fresh record.  A reservation with no recorded expiry is permanent and
        # never re-issued (the legacy one-shot behaviour).
        prior = previous(target) if previous is not None else None
        if isinstance(prior, dict) and prior:
            prior_expiry = int(prior.get('expires_at') or 0)
            if prior_expiry == 0 or prior_expiry > now:
                report['skipped'].append((target, 'already_approved'))
                continue
        if not reserve(target):
            report['skipped'].append((target, 'already_approved'))
            continue
        renewal = isinstance(prior, dict) and bool(prior)
        body = approval_record(issue=issue, waiver=waiver, repository=repository,
                               created_at=now, ttl_seconds=ttl_seconds)
        try:
            comment = post_comment(target, body)
            if not isinstance(comment, dict) or not comment.get('id'):
                raise ValueError('no comment id')
        except Exception:
            report['skipped'].append((target, 'comment_failed'))
            continue
        report['approved'].append({'issue': target, 'comment_id': comment.get('id'),
                                   'renewal': renewal,
                                   'task_spec_sha256': task_spec_sha256(issue)})
    return report


def standing_enabled_for(repo: str) -> bool:
    """True only while the owner's persistent autopilot switch is enabled.

    Standing waivers are live exactly as long as this is true, so turning the
    switch off revokes every one of them without touching GitHub.
    """
    try:
        from . import autopilot
        grant = autopilot.load_grant(autopilot.default_dir(repo))
        return bool(grant) and grant.get('enabled') is True
    except Exception:
        return False


def autopilot_audit_specs(repo: str) -> dict:
    """Issue number -> task-spec digest for tasks the AUTOPILOT already approved.

    Read from the local, owner-owned audit log only (never a GitHub label, which
    any contributor can write).  A missing or unreadable log yields no coverage.
    """
    specs: dict = {}
    try:
        from . import autopilot
        folder = autopilot.default_dir(repo)
        path = folder / autopilot.AUDIT
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            number = record.get('issue')
            spec = record.get('task_spec_sha256')
            if isinstance(number, int) and isinstance(spec, str) and spec:
                specs[number] = spec
    except Exception:
        return {}
    return specs


def _fetch_issue(number: int):
    """Default fetcher: none. Callers must supply one; silence is a refusal."""
    return None


# ---------------------------------------------------------------------------
# Owner CLI: issue a waiver request, or approve what the owner waived
# ---------------------------------------------------------------------------

def _gh(args: list[str], payload: dict | None = None):
    import subprocess
    raw = None if payload is None else json.dumps(payload)
    return subprocess.run(['gh', *args], input=raw, capture_output=True, text=True,
                          timeout=60, check=False)


def _api(path: str, *, method: str = 'GET', payload: dict | None = None):
    args = ['api', '-X', method, path]
    if payload is not None:
        args += ['--input', '-']
    result = _gh(args, payload)
    if result.returncode:
        raise RuntimeError(f'gh api {method} {path} failed: {result.stderr[:300]}')
    return json.loads(result.stdout or '{}')


def _repo_payload(repo: str) -> dict:
    return _api(f'repos/{repo}')


def _issue_payload(repo: str, number: int) -> dict:
    return _api(f'repos/{repo}/issues/{number}')


def request_waiver(repo: str, issues: list, *, ttl_seconds: int = DEFAULT_TTL_S,
                   standing: bool = False, renew_autopilot: bool = False) -> str:
    """Print the waiver comment for the OWNER to post. Writes nothing."""
    payloads = [_issue_payload(repo, n) for n in issues]
    return waiver_text(payloads, ttl_seconds=ttl_seconds, standing=standing,
                       renew_autopilot=renew_autopilot)


def reserve_factory(path: pathlib.Path, *, ttl_seconds: int = DEFAULT_TTL_S):
    """Durable reservations, outside any model conversation.

    A reservation records ``expires_at`` so a renewal is a deliberate, auditable
    replacement of an *expired* approval record rather than a second approval of
    a live one.  A reservation without ``expires_at`` (or with 0) is permanent
    and stays single-use, which is what the legacy tests assert.
    """
    path = pathlib.Path(path)
    ttl = max(60, min(policy.MAX_APPROVAL_AGE, int(ttl_seconds)))

    def reserve(number: int) -> bool:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = path / f'{int(number)}.approved'
        lock = path / f'{int(number)}.lock'
        now = int(time.time())
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                stale = now - int(lock.stat().st_mtime) > 120
            except OSError:
                stale = True
            if not stale:
                return False
            try:
                lock.unlink()
            except OSError:
                return False
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return False
        try:
            current = {}
            try:
                current = json.loads(target.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                current = {}
            if isinstance(current, dict) and current:
                expiry = int(current.get('expires_at') or 0)
                if expiry == 0 or expiry > now:
                    return False
            temporary = path / f'.{int(number)}.pending'
            with open(temporary, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps({'at': now, 'expires_at': now + ttl}))
            os.replace(temporary, target)
            return True
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                lock.unlink()
            except OSError:
                pass

    return reserve


def _parse_github_time(value) -> int:
    """Parse a GitHub ISO-8601 timestamp; 0 means unusable (never 'now')."""
    text = str(value or '').strip()
    if not text:
        return 0
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(moment.timestamp())


def _write_report(state_dir: pathlib.Path, report: dict) -> None:
    """The audit record must exist even on a refusal; the dir is ours to make."""
    state_dir = pathlib.Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (state_dir / 'last-admit.json').write_text(json.dumps(report, indent=2))


def _update_reservation(directory: pathlib.Path, number: int, **fields) -> None:
    target = pathlib.Path(directory) / f'{int(number)}.approved'
    try:
        current = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        current = {}
    current.update(fields)
    try:
        target.write_text(json.dumps(current), encoding='utf-8')
    except OSError:
        pass


def _queue_labels(repo: str, number: int, comment_id: int) -> dict:
    """Add `nova:queued` and the approval locator, keeping every other label.

    This is the step that makes an approval *actionable*: the supervisor only
    considers `nova:queued` issues, and `coordinator.approval_comment_id` only
    finds the record through `nova:approved:<comment-id>`.
    """
    current = _issue_payload(repo, number)
    names = set(label_names(current))
    if {'nova:blocked', 'nova:claimed', 'nova:review'} & names:
        raise RuntimeError(f'issue #{number} moved to {sorted(names)} state; not queueing')
    # Replace, never accumulate, approval locators: the gate refuses an issue
    # that names two approval comments (ambiguous), which is exactly what a
    # renewal would produce if the superseded locator were kept.
    wanted = {name for name in names if not name.startswith('nova:approved:')}
    wanted |= {'nova:queued', f'nova:approved:{int(comment_id)}'}
    if wanted == names and f'nova:approved:{int(comment_id)}' in names:
        return current                       # already correct; no write
    return _api(f'repos/{repo}/issues/{number}', method='PATCH',
                payload={'labels': sorted(wanted)})


def run(repo: str, *, holder: int, state_dir: pathlib.Path,
        ttl_seconds: int = DEFAULT_TTL_S, now: int | None = None) -> dict:
    """Approve the issues the owner waived, exactly as the gate will re-read them.

    The waiver lives in a **comment on the holder issue**, not in its body: the
    holder body is a live status board. Reading only the body was the defect
    that made this command report ``no_waiver`` while a valid waiver existed.
    """
    state_dir = pathlib.Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'policy': POLICY_VERSION, 'approved': [], 'skipped': [],
              'waiver': None, 'finalized': []}
    try:
        comments = _api(f'repos/{repo}/issues/{holder}/comments?per_page=100')
    except RuntimeError as exc:
        report.update(reason='github_unavailable', detail=str(exc)[:300])
        _write_report(state_dir, report)
        return report
    candidates = [comment for comment in comments or []
                  if isinstance(comment, dict) and MARKER in (comment.get('body') or '')]
    if not candidates:
        report['reason'] = 'no_waiver_comment'
        _write_report(state_dir, report)
        return report
    # Newest author intent wins; ties break on the comment id, deterministically.
    waiver_comment = max(candidates, key=lambda c: (_parse_github_time(c.get('created_at')),
                                                    int(c.get('id') or 0)))
    created = _parse_github_time(waiver_comment.get('created_at'))
    if created <= 0:
        report['reason'] = 'waiver_timestamp_unusable'
        _write_report(state_dir, report)
        return report
    waiver_source = {'text': waiver_comment.get('body') or '',
                     'comment_id': int(waiver_comment.get('id') or 0),
                     'actor_login': (waiver_comment.get('user') or {}).get('login', ''),
                     'created_at': created}
    assert WAIVER_RE.search(waiver_source['text']), 'selected comment lost its marker'

    def verify_role(login: str):
        # Live role evidence through the same reads the gate trusts.
        if not login:
            return False, 0
        try:
            user = _api(f'users/{login}')
            permission = _api(f'repos/{repo}/collaborators/{login}/permission')
        except RuntimeError:
            return False, 0
        admin = str((permission.get('permission') or '')).lower() == 'admin' or \
            str((permission.get('role_name') or '')).lower() == 'admin'
        if not admin:
            # Optional DSH-team admins: logins the owner added to the merged
            # coordination/admin_team.json. A contributor cannot self-add,
            # because only an owner/admin can merge that file.
            try:
                from . import authority
                admin = login.lower() in {name.lower()
                                          for name in authority.team_allowlist()}
            except Exception:
                admin = False
        return (admin, int(user.get('id') or 0))

    def post_comment(number: int, body: str):
        return _api(f'repos/{repo}/issues/{number}/comments', method='POST',
                    payload={'body': body})

    reserved_dir = state_dir / 'reserved'

    def previous(number: int):
        try:
            return json.loads((reserved_dir / f'{int(number)}.approved')
                              .read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    report = admit(issues=[], repository=_repo_payload(repo),
                   reserve=reserve_factory(reserved_dir, ttl_seconds=ttl_seconds),
                   verify_role=verify_role, post_comment=post_comment,
                   fetch_issue=lambda n: _issue_payload(repo, n),
                   ttl_seconds=ttl_seconds, waiver_source=waiver_source, now=now,
                   standing_enabled=standing_enabled_for(repo), previous=previous,
                   audit_specs=autopilot_audit_specs(repo))
    report.setdefault('finalized', [])
    reserved = state_dir / 'reserved'
    # A failed POST must not burn the one-shot slot forever.
    for number, why in report.get('skipped') or []:
        if why == 'comment_failed':
            (reserved / f'{int(number)}.approved').unlink(missing_ok=True)
    for entry in report.get('approved') or []:
        _update_reservation(reserved, entry['issue'],
                            comment_id=entry['comment_id'], queued=False)
        try:
            _queue_labels(repo, entry['issue'], entry['comment_id'])
            entry['queued'] = True
            _update_reservation(reserved, entry['issue'], queued=True,
                                label_comment_id=entry['comment_id'])
        except Exception as exc:                 # keep the comment; retry labels
            entry['queued'] = False
            entry['label_error'] = str(exc)[:200]
    # Recover approvals whose comment landed but whose labels did not, without
    # posting a second approval comment.
    # Label repair for EVERY recorded approval, not only the ones whose first
    # label write failed: a renewal supersedes the old locator, and an issue that
    # carries two locators is refused by the gate as ambiguous. _queue_labels is
    # idempotent and writes nothing when the label set is already correct.
    for path in sorted(reserved.glob('*.approved')) if reserved.is_dir() else []:
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        number, comment_id = int(path.stem), int(state.get('comment_id') or 0)
        if not comment_id:
            continue
        if (state.get('queued')
                and int(state.get('label_comment_id') or 0) == comment_id):
            continue                        # labels already name this record
        try:
            _queue_labels(repo, number, comment_id)
        except Exception:
            continue
        _update_reservation(reserved, number, queued=True, label_comment_id=comment_id)
        report['finalized'].append({'issue': number, 'comment_id': comment_id})
    _write_report(state_dir, report)
    return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='NOVA3 auto-admission (owner-authorized)')
    parser.add_argument('command', choices=('waiver', 'admit'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--issues', default='', help='comma-separated issue numbers (waiver)')
    parser.add_argument('--holder', type=int, default=16,
                        help='issue that carries the owner waiver comment')
    parser.add_argument('--state', default=None)
    parser.add_argument('--ttl', type=int, default=DEFAULT_TTL_S)
    parser.add_argument('--renew-autopilot', action='store_true',
                        help='waiver command: also allow renewing issues the autopilot '
                             'already approved (local audit log, exact task text)')
    parser.add_argument('--standing', action='store_true',
                        help='waiver command: emit a standing waiver (live while the '
                             'autopilot switch is ON) instead of a one-run waiver')
    args = parser.parse_args(argv)
    state = pathlib.Path(args.state or (pathlib.Path.home() / '.cache' / 'nova3-team'
                                        / args.repo.replace('/', '_') / 'autoadmit'))
    if args.command == 'waiver':
        numbers = [int(x) for x in args.issues.split(',') if x.strip()]
        if not numbers:
            parser.error('--issues is required for waiver')
        print(request_waiver(args.repo, numbers, ttl_seconds=args.ttl,
                             standing=args.standing,
                             renew_autopilot=args.renew_autopilot))
        return 0
    report = run(args.repo, holder=args.holder, state_dir=state, ttl_seconds=args.ttl)
    print(json.dumps(report, indent=2))
    # A timer run with nothing to do is SUCCESS, not failure: returning 3 for an
    # idle tick made systemd mark the unit failed every ten minutes. Exit 3 is
    # reserved for an approval that was written but could not be queued.
    unqueued = [e for e in report.get('approved') or [] if not e.get('queued')]
    return 3 if unqueued else 0


if __name__ == '__main__':
    raise SystemExit(main())
