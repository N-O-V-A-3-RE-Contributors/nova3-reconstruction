"""NOVA3 task coordinator: quiet single-writer GitHub Actions state machine.

Issue comments are *requests*, not evidence of progress. Successful operations
only update issue metadata; do not generate a reply comment to every heartbeat.

Owner/admin queue admission (issue #135) is enforced in `handle_event`, the only
entry point used by `main` for GitHub events. The lower-level `process_comment`
state machine is retained for offline tests and MUST NOT be wired to untrusted
input without a QueueApprovalGate decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, datetime
from pathlib import Path
from typing import Mapping, Protocol

from . import access_policy as policy
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

API_ORIGIN = 'https://api.github.com'
APPROVAL_LABEL = re.compile(r'nova:approved:([1-9][0-9]*)')
APPROVAL_MARKER = re.compile(r'<!--\s*NOVA3_APPROVAL_COMMENT:\s*([1-9][0-9]*)\s*-->')
QUEUE_ACTION = policy.Action.APPROVE_QUEUE


def alert(message):
    login=os.environ.get('NOVA3_ALERT_USER','').strip()
    return (f'@{login} ' if re.fullmatch(r'[A-Za-z0-9-]{1,39}',login) else '') + message


# ---------------------------------------------------------------------------
# Owner/admin queue-admission gate (issue #135)
# ---------------------------------------------------------------------------

class EvidenceUnavailable(RuntimeError):
    """Trusted transport could not produce fresh authenticated evidence."""


class EvidenceCollector(Protocol):
    """Trusted collector interface; implementations must do authenticated reads.

    Every method returns an unmodified GitHub REST payload. Implementations must
    never accept a token, API base URL or role from issue text, environment
    supplied by a task, or a model. Any error must raise EvidenceUnavailable so
    the gate fails closed.
    """

    def identity(self) -> int: ...
    def repository(self) -> Mapping[str, object]: ...
    def issue(self, number: int) -> Mapping[str, object]: ...
    def comment(self, comment_id: int) -> Mapping[str, object]: ...
    def user(self, login: str) -> Mapping[str, object]: ...
    def permission(self, login: str) -> Mapping[str, object]: ...
    def membership(self, login: str) -> Mapping[str, object] | None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)


class GitHubEvidenceCollector:
    """Minimal read-only REST collector for queue admission.

    Uses the coordinator/worker credential supplied by the trusted process.
    Tokens are never logged or copied into policy objects. Global endpoints
    (`/user`, `/app`, `/users/...`, `/orgs/...`) cannot be reached through
    `common.GitHub` (which prefixes `/repos/{repo}`), so this uses a fixed-origin
    urllib request with redirects refused and a bounded timeout.
    """

    def __init__(self, token: str, repository: policy.Repository, *, timeout: float = 20.0):
        if not isinstance(token, str) or not token:
            raise EvidenceUnavailable('missing credential')
        self._token = token
        self.target = repository
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _get(self, path: str, *, statuses: tuple[int, ...] = (200,)) -> Mapping[str, object]:
        if not path.startswith('/') or '..' in path:
            raise EvidenceUnavailable('invalid endpoint')
        url = API_ORIGIN + path
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {self._token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'nova3-teamkit/0.1',
        })
        try:
            with self._opener.open(req, timeout=self._timeout) as response:
                if response.status not in statuses:
                    raise EvidenceUnavailable('unexpected status')
                payload = json.loads(response.read() or b'{}')
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, TimeoutError):
            raise EvidenceUnavailable('GitHub read unavailable') from None
        if not isinstance(payload, Mapping):
            raise EvidenceUnavailable('GitHub payload is not an object')
        return payload

    def identity(self) -> int:
        try:
            payload = self._get('/user')
        except EvidenceUnavailable:
            payload = self._get('/app')  # GitHub App installation tokens cannot read /user
        value = payload.get('id')
        if type(value) is not int or value <= 0:
            raise EvidenceUnavailable('no authenticated identity')
        return value

    def repository(self) -> Mapping[str, object]:
        return self._get(f'/repos/{self.target.owner}/{self.target.name}')

    def issue(self, number: int) -> Mapping[str, object]:
        return self._get(f'/repos/{self.target.owner}/{self.target.name}/issues/{int(number)}')

    def comment(self, comment_id: int) -> Mapping[str, object]:
        return self._get(f'/repos/{self.target.owner}/{self.target.name}/issues/comments/{int(comment_id)}')

    def user(self, login: str) -> Mapping[str, object]:
        return self._get(f'/users/{login}')

    def permission(self, login: str) -> Mapping[str, object]:
        return self._get(f'/repos/{self.target.owner}/{self.target.name}/collaborators/{login}/permission')

    def membership(self, login: str) -> Mapping[str, object] | None:
        try:
            return self._get(f'/orgs/{self.target.owner}/memberships/{login}')
        except EvidenceUnavailable:
            # Unreadable membership is not proof of organization ownership. A
            # repository ADMIN still passes via the permission endpoint; an
            # organization-only owner fails closed until the credential can read
            # the membership endpoint.
            return None


@dataclass(frozen=True)
class QueueDecision:
    allowed: bool
    reason: str
    approval_id: int | None = None
    binding: dict | None = None
    task_text_sha256: str | None = None
    approved_updated_at: str | None = None


def approval_comment_id(issue: Mapping[str, object]) -> int | None:
    """Return the single approval-comment locator, or None if absent/ambiguous.

    A locator is not authorization: it only names which comment to verify. Both
    labels and issue text are contributor-editable, so a MAINTAIN can remove the
    locator (denial) or point at another owner comment for this same task
    revision (equivalent authorization), but cannot forge an owner approval.
    """
    found: set[int] = set()
    for label in label_names(issue):
        match = APPROVAL_LABEL.fullmatch(label)
        if match:
            found.add(int(match.group(1)))
    body = issue.get('body') or ''
    if isinstance(body, str):
        for match in APPROVAL_MARKER.finditer(body):
            found.add(int(match.group(1)))
    return found.pop() if len(found) == 1 else None


def task_text(body: object) -> str:
    """Task text with the coordinator lease envelope removed, for tamper checks."""
    return lease_put(body if isinstance(body, str) else '', None)


def task_text_sha256(body: object) -> str:
    return hashlib.sha256(task_text(body).encode('utf-8')).hexdigest()


class QueueApprovalGate:
    """Verify owner/admin queue admission against live GitHub evidence."""

    def __init__(self, repository: policy.Repository, collector: EvidenceCollector, *, clock=time.time):
        self.repository = repository
        self.collector = collector
        self.clock = clock

    def _now(self) -> int:
        value = self.clock()
        return int(value) if isinstance(value, (int, float)) else 0

    def _receipt(self, path: str, payload: Mapping[str, object], nonce: str, now_s: int, reader: int):
        return policy.GitHubRead(path, reader, nonce, now_s, 200, payload)

    def _author(self, comment: Mapping[str, object], nonce: str, now_s: int, reader: int) -> policy.AccessReads:
        user = comment.get('user')
        if not isinstance(user, Mapping):
            raise EvidenceUnavailable('comment has no authenticated author')
        login = user.get('login')
        if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
            raise EvidenceUnavailable('invalid comment author')
        base = f'/repos/{self.repository.owner}/{self.repository.name}'
        membership = self.collector.membership(login)
        return policy.AccessReads(
            user=self._receipt(f'/users/{login}', self.collector.user(login), nonce, now_s, reader),
            permission=self._receipt(f'{base}/collaborators/{login}/permission',
                                     self.collector.permission(login), nonce, now_s, reader),
            membership=(self._receipt(f'/orgs/{self.repository.owner}/memberships/{login}',
                                      membership, nonce, now_s, reader)
                        if membership is not None else None),
        )

    def _evidence(self, issue_number: int, comment: Mapping[str, object], nonce: str,
                  now_s: int, reader: int, *, include_issue: bool = True) -> policy.Evidence:
        base = f'/repos/{self.repository.owner}/{self.repository.name}'
        repository = self._receipt(base, self.collector.repository(), nonce, now_s, reader)
        issue = (self._receipt(f'{base}/issues/{issue_number}', self.collector.issue(issue_number),
                               nonce, now_s, reader) if include_issue else None)
        comment_id = comment.get('id')
        if type(comment_id) is not int or comment_id <= 0:
            raise EvidenceUnavailable('invalid approval comment')
        approval = policy.ApprovalEvidence(
            comment=self._receipt(f'{base}/issues/comments/{comment_id}', comment, nonce, now_s, reader),
            author=self._author(comment, nonce, now_s, reader),
        )
        return policy.Evidence(
            repository=repository, issue=issue, approvals=(approval,),
            replay=policy.ReplayState(nonce, now_s),
        )

    def _request(self, issue_number: int, approval_record: Mapping[str, object]) -> policy.Request:
        arguments = approval_record.get('operation_arguments')
        nonce = approval_record.get('nonce')
        if type(arguments) is not dict or not isinstance(nonce, str):
            raise EvidenceUnavailable('approval record is malformed')
        return policy.Request(
            self.repository, QUEUE_ACTION, issue_number,
            policy.operation_digest(arguments), arguments, nonce, None)

    def verify(self, issue: Mapping[str, object]) -> QueueDecision:
        """Full verification for an unclaimed (queued) task revision."""
        try:
            number = issue.get('number')
            if type(number) is not int or number <= 0:
                return QueueDecision(False, 'invalid_issue')
            comment_id = approval_comment_id(issue)
            if comment_id is None:
                return QueueDecision(False, 'owner_approval_locator_required')
            comment = self.collector.comment(comment_id)
            record = json.loads(comment.get('body') or '')
            if not isinstance(record, dict):
                return QueueDecision(False, policy.Reason.INVALID_EVIDENCE.value, comment_id)
            request = self._request(number, record)
            now_s = self._now()
            reader = self.collector.identity()
            issue_payload = self.collector.issue(number)
            decision = policy.verify_owner_approval(
                request, self._evidence(number, comment, request.nonce, now_s, reader), now=now_s)
            if not decision.allowed:
                return QueueDecision(False, decision.reason.value, comment_id)
            binding = policy.request_binding(request, issue_payload)
            updated = issue_payload.get('updated_at')
            return QueueDecision(
                True, policy.Reason.ALLOWED.value, comment_id, binding,
                task_text_sha256(issue_payload.get('body')),
                updated if isinstance(updated, str) else None,
            )
        except Exception:
            # Fixed reason only: never echo a payload, task text or credential.
            return QueueDecision(False, policy.Reason.INVALID_EVIDENCE.value)

    def revalidate(self, issue: Mapping[str, object], lease: Mapping[str, object] | None,
                   *, allow_legacy_publish: bool = False) -> QueueDecision:
        """Pre-publish check using the binding captured at admission time.

        The coordinator lease changes the issue body/updated_at, so the original
        task-spec hash cannot be recomputed. The captured binding, the approval
        comment body, the author's current role and a lease-time digest of the
        task text (lease envelope removed) are checked instead. ``allow_legacy_publish``
        is the explicit one-release migration path for work committed before
        this gate shipped; it never launches new AI work.
        """
        try:
            number = issue.get('number')
            if type(number) is not int or number <= 0:
                return QueueDecision(False, 'invalid_issue')
            if not isinstance(lease, Mapping):
                return QueueDecision(False, 'owner_approval_required')
            binding = lease.get('approval_binding')
            comment_id = lease.get('approval_id')
            text_sha = lease.get('task_text_sha256')
            approved_updated_at = lease.get('approved_updated_at')
            if not isinstance(binding, dict) or type(comment_id) is not int or comment_id <= 0 \
                    or not isinstance(text_sha, str):
                if allow_legacy_publish:
                    return QueueDecision(True, 'legacy_in_flight_publish_only')
                return QueueDecision(False, 'owner_approval_required')
            if task_text_sha256(issue.get('body')) != text_sha:
                return QueueDecision(False, 'task_text_edited', comment_id)
            comment = self.collector.comment(comment_id)
            request = policy.Request(
                self.repository, QUEUE_ACTION, number,
                binding['operation_sha256'], binding['operation_arguments'], binding['nonce'], None)
            now_s = self._now()
            reader = self.collector.identity()
            evidence = self._evidence(number, comment, request.nonce, now_s, reader, include_issue=False)
            decision = policy.verify_approval_record(
                binding, evidence.approvals[0], request, reader=reader, now=now_s,
                organization=self._organization(), replay=evidence.replay,
                issue_updated_at=approved_updated_at if isinstance(approved_updated_at, str) else None)
            return QueueDecision(decision.allowed, decision.reason.value, comment_id)
        except Exception:
            # Fixed reason only: never echo a payload, task text or credential.
            return QueueDecision(False, policy.Reason.INVALID_EVIDENCE.value)

    def _organization(self) -> bool:
        owner = self.collector.repository().get('owner')
        return isinstance(owner, Mapping) and owner.get('type') == 'Organization'


def pinned_repository(owner: str, name: str) -> policy.Repository:
    """Reviewed deployment identity; numeric IDs are mandatory (fail closed).

    Name-only configuration would permit repository-name reuse to transfer a
    stale approval. Set NOVA3_REPOSITORY_ID and NOVA3_OWNER_ID in the reviewed
    workflow/environment.
    """
    def _env_id(key: str) -> int:
        raw = os.environ.get(key, '').strip()
        if not re.fullmatch(r'[1-9][0-9]*', raw):
            raise EvidenceUnavailable(f'{key} must be pinned to a positive numeric GitHub ID')
        return int(raw)
    return policy.Repository(_env_id('NOVA3_REPOSITORY_ID'), _env_id('NOVA3_OWNER_ID'), owner, name)


def build_gate(gh: GitHub) -> QueueApprovalGate:
    owner, _, name = gh.repo.partition('/')
    if not owner or not name:
        raise EvidenceUnavailable('repository must be OWNER/REPO')
    repository = pinned_repository(owner, name)
    return QueueApprovalGate(repository, GitHubEvidenceCollector(gh.token, repository))


def quarantine(gh, issue: Mapping[str, object], reason: str, *, detail: str = '') -> None:
    """Move an unapproved task out of the steerable queue with one diagnostic.

    Never marks work complete, never re-queues silently, never retries in a
    loop. ``reason`` is a fixed policy code; no task text or credential is echoed.
    """
    number = issue['number']
    labels = [lab for lab in label_names(issue) if lab not in (QUEUED, CLAIMED)]
    if ATTENTION not in labels:
        labels.append(ATTENTION)
    gh.patch(f'issues/{number}', {'body': lease_put(issue.get('body'), None), 'labels': labels})
    suffix = f' ({detail})' if detail else ''
    gh.comment(number, alert(
        f'⚠️ NOVA3 queue admission denied{suffix}: `{reason}`. This task is quarantined in '
        f'`{ATTENTION}`; no AI session will start. An owner or repository ADMIN must approve '
        f'the current task revision (see docs/progress/OWNER_APPROVAL_GATE.md) before it can be requeued.'))


def handle_event(gh, event, gate) -> bool:
    """Production entry point: authorize the transition, then mutate.

    Returns True when the state machine ran (or the request was a no-op),
    False when the transition was denied and nothing else was mutated.
    """
    c = event.get('comment') or {}
    actor = (c.get('user') or {}).get('login', '')
    parsed = COMMAND.fullmatch((c.get('body') or '').strip())
    if not parsed or actor.endswith('[bot]'):
        return True
    action = parsed.group(1).lower()
    if action not in ('claim', 'release'):
        process_comment(gh, event)
        return True
    number = event['issue']['number']
    issue = gh.issue(number)
    lease = lease_get(issue.get('body'))
    if action == 'claim':
        if lease_active(lease):
            # No state transition: duplicate claim/replay is handled atomically
            # by the lease check inside process_comment.
            process_comment(gh, event)
            return True
        decision = gate.verify(issue)
        if not decision.allowed:
            sys.stderr.write(f'NOVA3 queue admission denied #{number}: {decision.reason}\n')
            # One idempotent, actionable signal; no comment storm and no claim.
            labels = label_names(issue)
            if ATTENTION not in labels:
                gh.patch(f'issues/{number}', {'labels': labels + [ATTENTION]})
            return False
        process_comment(gh, event, admission=decision)
        return True
    decision = gate.revalidate(issue, lease)
    if not decision.allowed:
        sys.stderr.write(f'NOVA3 queue release denied #{number}: {decision.reason}\n')
        quarantine(gh, issue, decision.reason)
        return False
    process_comment(gh, event, admission=decision)
    return True


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def process_comment(gh, event, *, admission=None):
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
        if admission is not None and admission.allowed:
            latest.update({
                'approval_id': admission.approval_id,
                'approval_binding': admission.binding,
                'task_text_sha256': admission.task_text_sha256,
                'approved_updated_at': admission.approved_updated_at,
            })
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


def sweep(gh, gate=None):
    """Flag stale worker signals once; never generate per-poll comment floods.

    When a gate is supplied (production), an expired claim is only requeued if
    the recorded owner/admin approval still revalidates. Legacy claims without a
    recorded approval are quarantined in `nova:attention` instead of being
    silently grandfathered back into the paid queue.
    """
    for issue in gh.issues():
        labels = label_names(issue)
        if CLAIMED not in labels:
            continue
        lease=lease_get(issue.get('body'))
        if not lease_active(lease):
            if gate is not None:
                decision = gate.revalidate(issue, lease)
                if not decision.allowed:
                    quarantine(gh, issue, decision.reason)
                    continue
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
    try:
        gate = build_gate(gh)
    except EvidenceUnavailable as exc:
        # Deliberate, reported failure: never fall back to an ungated queue.
        sys.stderr.write(f'NOVA3 queue admission gate unavailable: {exc}\n')
        raise SystemExit(6)
    if name in ('issue_comment', 'pull_request'):
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        if name == 'issue_comment' and event.get('action') == 'created':
            handle_event(gh, event, gate)
        elif name == 'pull_request' and event.get('action') == 'closed':
            process_merged_pr(gh, event)
    else:
        sweep(gh, gate)


if __name__ == '__main__':
    main()
