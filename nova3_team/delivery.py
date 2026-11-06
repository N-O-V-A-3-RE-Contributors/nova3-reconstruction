"""Bounded owner-side PR delivery. No model, subprocess, or service dependencies.

``api`` has autoadmit._api's JSON interface. ``reviewer(context)`` is a trusted
adapter returning {'review': <JSON object or string>, 'execution': {...}}.
Execution metadata MUST be attached by the adapter, not copied from model JSON:
role='reviewer', session_id, read_only=True, independent=True. Independent means
an isolated review context that did not implement this PR. repairer(context,
findings) returns {'repaired': bool, 'execution': {'role': 'repairer',
'session_id': ...}} and must preserve branch and task scope. After a confirmed
push the trusted adapter may additionally attach published_head (git HEAD's
40-hex SHA); this MUST NOT be copied from model output. It only permits waiting
for GitHub propagation, never acceptance of unseen code. Neither callback
may approve its own implementation. This module never posts GitHub approvals.

One invocation reviews at most one head and runs at most one repair. Repair
budgets span head changes (maximum two per PR); unchanged semantic outcomes are
cached. Transient exceptions are retryable after a cooldown, not rejections.
The caller owns authorization, runtime timeouts, and serialization per PR.
Only sanitized state (identifiers, hashes, verdicts) is persisted, never prompts,
diffs, findings text, provider output, tokens, or proprietary data.

Legacy closing/ref metadata is normalized in a separate cheap preparation tick.
Delivery targets the freshly verified repository default branch, with explicitly
allowed squash and fixed non-closing commit metadata. Original PR branch history
is retained. Only the controller's post-merge, full-contract verification may
explicitly close the issue and apply the integrated lifecycle metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import time
from types import MappingProxyType

from . import review as compliance_review
from . import access_policy as policy
from . import coordinator

SCHEMA = 'nova3-delivery/1'
DEFAULT_SOURCE_CHECKS = ('source-and-tests',)
RETRY_SECONDS = 60
MAX_PATCH_BYTES = 128 * 1024
MAX_CONTEXT_BYTES = 384 * 1024
MAX_TASK_BODY_BYTES = 64 * 1024
MAX_OBJECTIVE_BYTES = 8 * 1024
MAX_ACCEPTANCE_BYTES = 16 * 1024


def _json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8'))


class GateError(ValueError):
    """Stable local validation failure, distinct from transport JSON errors."""


class CIFailure(GateError):
    """Known terminal CI failure, not missing or pending test evidence."""

    def __init__(self, context):
        super().__init__('ci_not_successful')
        self.context = context


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _section(body, name):
    match = re.search(r'(?im)^\s*(?:#{1,6}\s*)?' + name + r'\s*:?\s*$', body)
    if not match:
        return ''
    rest = body[match.end():]
    stop = re.search(r'(?m)^\s*(?:#{1,6}\s+|Allowed paths:)', rest)
    return rest[:stop.start() if stop else len(rest)].strip()


def extract_task(issue):
    """Extract actual explicit Markdown task contract; never infer acceptance."""
    body = str(issue.get('body') or '')
    if len(body.encode('utf-8')) > MAX_TASK_BODY_BYTES:
        raise GateError('task_body_too_large')
    body = coordinator.task_text(body)
    objective = _section(body, 'Objective')
    if not objective:
        match = re.search(r'(?im)^\s*Objective:\s*(\S[^\n]*)$', body)
        objective = match.group(1).strip() if match else ''
    acceptance = _section(body, r'Acceptance(?: criteria)?')
    criteria = []
    for line in acceptance.splitlines():
        match = re.match(r'^\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+)', line)
        if match:
            criteria.append(match.group(1).strip())
        elif line.strip() and criteria:
            criteria[-1] += ' ' + line.strip()
    if not objective or not criteria or len(criteria) != len(set(criteria)):
        raise GateError('missing_task_contract')
    if (len(objective.encode('utf-8')) > MAX_OBJECTIVE_BYTES or len(criteria) > 32
            or _json_bytes(criteria) > MAX_ACCEPTANCE_BYTES):
        raise GateError('task_contract_too_large')
    return {'number': issue['number'], 'title': issue.get('title', ''),
            'body': coordinator.task_text(body),
            'objective': objective, 'acceptance': criteria,
            'allowed_paths': compliance_review.allowed_paths(body)}


CLOSING_RE = re.compile(
    r'(?i)\b(?:refs|close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*'
    r'(?:#(\d+)|https://github\.com/([^/]+/[^/]+)/issues/(\d+)'
    r'|([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+))')
RELATED_RE = re.compile(r'(?im)^\s*Related\s+to\s+#(\d+)\s*$')


def _linked_issue(body, repo):
    # Closing directives are read only for migration. Final delivery uses one
    # explicit non-closing related link and verifies issue resolution itself.
    matches = CLOSING_RE.findall(body or '')
    related = RELATED_RE.findall(body or '')
    if (not matches and not related) or any(
            (other or qualified) and (other or qualified).casefold() != repo.casefold()
            for _, other, _, qualified, _ in matches):
        raise GateError('missing_or_external_linked_issue')
    numbers = {int(short or full or qualified_number)
               for short, _, full, _, qualified_number in matches} | {int(n) for n in related}
    if len(numbers) != 1 or len(related) > 1:
        raise GateError('ambiguous_linked_issue')
    return numbers.pop()


def _nonclosing_body(body, issue_number):
    remaining = RELATED_RE.sub('', CLOSING_RE.sub('', body or '')).strip()
    return (remaining + '\n\n' if remaining else '') + f'Related to #{issue_number}'


def _files(api, root, pr):
    count = pr.get('changed_files')
    if not isinstance(count, int) or not 0 < count <= 300:
        raise GateError('unbounded_or_empty_diff')
    result = []
    for page in range(1, (count + 99) // 100 + 1):
        items = api(f'{root}/files?per_page=100&page={page}')
        if not isinstance(items, list):
            raise GateError('invalid_files')
        result.extend(items)
    if len(result) != count:
        raise GateError('incomplete_files')
    normalized = []
    patch_bytes = 0
    for item in result:
        path = item.get('filename') or item.get('path') or ''
        if not path or path.startswith('/') or '..' in path.split('/') or '\\' in path:
            raise GateError('unsafe_file_path')
        # GitHub may omit oversized patches; a substantive review cannot pass
        # unseen changes. Binary files fail compliance as well.
        if not isinstance(item.get('patch'), str) or not item['patch'].strip():
            raise GateError('incomplete_diff')
        patch_bytes += len(item['patch'].encode('utf-8'))
        if patch_bytes > MAX_PATCH_BYTES:
            raise GateError('review_diff_too_large')
        # A nonempty but truncated patch is also insufficient. GitHub's file
        # counters let us detect omitted added/deleted lines without fetching
        # raw proprietary blobs or accepting a partial substantive review.
        added = sum(line.startswith('+') for line in item['patch'].splitlines())
        deleted = sum(line.startswith('-') for line in item['patch'].splitlines())
        if (item.get('additions') != added or item.get('deletions') != deleted
                or not item['patch'].startswith('@@')):
            raise GateError('incomplete_diff')
        normalized.append({**item, 'path': path})
        old = item.get('previous_filename')
        if old and (old.startswith('/') or '..' in old.split('/') or '\\' in old):
            raise GateError('unsafe_previous_path')
    return normalized


def _implementation_added(patch, suffix):
    """Conservative lexical filter, not a claim of behavioral implementation.

    Read added and retained hunk lines to track comment blocks; deleted lines
    cannot provide implementation. Obvious includes/comments/docstrings alone
    never satisfy the source delta gate. Semantic correctness remains review's.
    """
    block = None
    for line in patch.splitlines():
        if not line or line[0] not in ('+', ' '):
            continue
        added = line[0] == '+'
        code = line[1:]
        out = []
        index = 0
        quote = None
        while index < len(code):
            if block:
                end = code.find(block, index)
                if end < 0:
                    break
                index = end + len(block)
                block = None
                continue
            if quote:
                out.append(code[index])
                if code[index] == '\\' and index + 1 < len(code):
                    index += 1
                    out.append(code[index])
                elif code[index] == quote:
                    quote = None
                index += 1
                continue
            if suffix == '.py':
                if code.startswith(('\"\"\"', "'''"), index):
                    block = code[index:index + 3]
                    index += 3
                    continue
                if code[index] == '#':
                    break
            else:
                if code.startswith('//', index):
                    break
                if code.startswith('/*', index):
                    block = '*/'
                    index += 2
                    continue
            if code[index] in ('\"', "'"):
                quote = code[index]
            out.append(code[index])
            index += 1
        text = ''.join(out).strip()
        if (added and text and not re.match(r'^#\s*(?:include|import)\b', text)
                and not re.match(r'^\*(?:\s|/|$)', text)):
            return True
    return False


def _source_changed(files):
    for item in files:
        path = pathlib.PurePosixPath(item['path'])
        if any(part.lower() in ('test', 'tests', 'docs', 'fixtures', 'testdata')
               for part in path.parts) or re.match(r'(?:test_|.*_test\.)', path.name):
            continue
        if path.suffix.lower() not in ('.c', '.cc', '.cpp', '.cxx', '.h', '.hpp',
                                      '.py', '.java', '.kt', '.rs', '.js', '.ts'):
            continue
        if item.get('status') == 'removed':
            continue
        if _implementation_added(item['patch'], path.suffix.lower()):
            return True
    return False


def _needs_source(task):
    # Scope, not a title mentioning tests, determines a test/docs-only task.
    # A production allowlist always requires actual production source changes.
    def test_or_docs(path):
        path = pathlib.PurePosixPath(path.rstrip('/'))
        return (any(part.lower() in ('test', 'tests', 'docs', 'documentation',
                                     'fixtures', 'testdata') for part in path.parts)
                or bool(re.match(r'(?:test_|.*_test\.)', path.name)))

    scope = task.get('allowed_paths') or []
    if not scope or not all(test_or_docs(path) for path in scope):
        return True
    text = task['objective'].lower()
    # Do not silently reinterpret a contradictory source implementation task as
    # tests-only merely because somebody scoped it to tests by mistake.
    modifies = re.search(r'\b(?:implement|reconstruct|repair|fix|modify|change|extend|port)\b', text)
    source = re.search(r'\b(?:source|production|engine|renderer|parser|runtime|jni|native)\b', text)
    return bool(modifies and source)


def _ci(api, repo, head, required):
    data = api(f'repos/{repo}/commits/{head}/check-runs?per_page=100')
    runs = data.get('check_runs') or []
    if not runs or data.get('total_count') != len(runs):
        raise GateError('ci_missing_or_incomplete')
    if not required or not set(required) <= {r.get('name') for r in runs}:
        raise GateError('required_source_tests_missing')
    failed = []
    for run in runs:
        if run.get('head_sha') != head:
            raise GateError('ci_stale_head')
        if run.get('status') != 'completed':
            raise GateError('ci_not_successful')
        passing = ({'success'} if run.get('name') in required
                   else {'success', 'neutral', 'skipped'})
        conclusion = run.get('conclusion')
        if conclusion not in passing:
            if conclusion not in ('failure', 'cancelled', 'timed_out'):
                # Skipped required tests, startup/approval problems, unknown
                # conclusions and incomplete evidence do not prove bad code.
                raise GateError('ci_not_successful')
            name = re.sub(r'[^A-Za-z0-9_. /()-]', '?', str(run.get('name') or 'unknown'))[:120]
            failed.append({'name': name, 'conclusion': conclusion})
    status = api(f'repos/{repo}/commits/{head}/status')
    if int(status.get('total_count') or 0) and status.get('state') != 'success':
        if status.get('state') in ('failure', 'error'):
            failed.append({'name': 'commit-status', 'conclusion': status['state']})
        else:
            raise GateError('commit_status_not_successful')
    if failed:
        # Pass only bounded metadata to repair; never logs, output, or an
        # assertion that the implementation's actual acceptance passed.
        raise CIFailure({'head': head, 'failed_checks': failed})
    return {'head': head, 'runs': runs, 'status': status}


def _review(raw, task, head, repair_sessions):
    if not isinstance(raw, dict):
        raise GateError('invalid_review_envelope')
    execution = raw.get('execution') or {}
    session = execution.get('session_id')
    if (execution.get('role') != 'reviewer' or execution.get('read_only') is not True
            or execution.get('independent') is not True or not isinstance(session, str)
            or not session or session in repair_sessions):
        raise GateError('review_not_independent')
    result = raw.get('review')
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or result.get('verdict') not in (
            'pass', 'needs_revision', 'blocked') or result.get('reviewed_head') != head:
        raise GateError('invalid_review_verdict_or_head')
    rows = result.get('acceptanceResults')
    criteria = task['acceptance']
    if (not isinstance(rows, list) or len(rows) != len(criteria)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get('criterion') for row in rows} != set(criteria)
            or any(row.get('status') not in ('passed', 'failed', 'blocked')
                   or not isinstance(row.get('evidence'), str) or not row['evidence'].strip()
                   for row in rows)):
        raise GateError('invalid_acceptance_coverage')
    findings = result.get('findings')
    if not isinstance(findings, list) or any(
            not isinstance(f, dict) or not all(isinstance(f.get(k), str) and f[k].strip()
                                              for k in ('id', 'problem', 'requiredFix'))
            for f in findings):
        raise GateError('invalid_findings')
    if result['verdict'] == 'pass' and (findings or any(r['status'] != 'passed' for r in rows)):
        raise GateError('contradictory_pass')
    if result['verdict'] == 'needs_revision' and not findings:
        raise GateError('missing_revision_findings')
    return result, session


def _task_provenance(api, repo, issue, base_ref, *, allow_closed=False):
    """Historical contract provenance, NOT current queue-admission authority.

    The caller must hold the owner's CURRENT final-delivery authorization.
    A previously valid approval may be expired now, but it must have bound this
    exact lease-stripped task when its immutable comment was posted. This does
    not renew an approval, weaken claim-time policy, or authorize new work.
    """
    if issue.get('state') != 'open' and not (allow_closed and issue.get('state') == 'closed'):
        raise GateError('linked_task_not_open')
    labels = []
    for label in issue.get('labels') or []:
        name = label.get('name') if isinstance(label, dict) else label
        if not isinstance(name, str):
            raise GateError('task_approval_labels_invalid')
        labels.append(name)
    locator_issue = {**issue, 'labels': [{'name': name} for name in labels]}
    if ('nova:claimed' in labels
            or coordinator.lease_active(coordinator.lease_get(issue.get('body')))):
        raise GateError('task_implementation_active')
    locator = coordinator.approval_comment_id(locator_issue)
    if locator is None:
        raise GateError('task_approval_locator_required')
    repository = api(f'repos/{repo}')
    comment = api(f'repos/{repo}/issues/comments/{locator}')
    endpoint = f'{policy.API_ORIGIN}/repos/{repo}'
    number = issue.get('number')
    if (not isinstance(repository, dict)
            or str(repository.get('full_name', '')).casefold() != repo.casefold()
            or type(repository.get('id')) is not int or repository['id'] <= 0
            or not isinstance(repository.get('owner'), dict)
            or type(repository['owner'].get('id')) is not int or repository['owner']['id'] <= 0
            or str(repository['owner'].get('login', '')).casefold() != repo.split('/')[0].casefold()
            or type(issue.get('id')) is not int or issue['id'] <= 0
            or type(number) is not int or number <= 0
            or issue.get('url') != f'{endpoint}/issues/{number}'
            or issue.get('repository_url') != endpoint or 'pull_request' in issue):
        raise GateError('task_approval_identity_mismatch')
    if (not isinstance(comment, dict) or type(comment.get('id')) is not int
            or comment['id'] != locator or comment.get('issue_url') != f'{endpoint}/issues/{number}'
            or comment.get('created_at') != comment.get('updated_at')):
        raise GateError('task_approval_comment_invalid_or_edited')
    default_branch = repository.get('default_branch')
    if not isinstance(default_branch, str) or not default_branch or base_ref != default_branch:
        raise GateError('delivery_target_not_default_branch')
    if repository.get('allow_squash_merge') is not True:
        raise GateError('delivery_safe_squash_not_allowed')
    body = comment.get('body')
    if not isinstance(body, str) or len(body.encode('utf-8')) > 8192:
        raise GateError('task_approval_record_invalid')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GateError('task_approval_record_invalid')
            result[key] = value
        return result

    try:
        record = json.loads(body, object_pairs_hook=unique)
    except (ValueError, TypeError):
        raise GateError('task_approval_record_invalid') from None
    if not isinstance(record, dict):
        raise GateError('task_approval_record_invalid')
    task = {key: issue.get(key) for key in ('id', 'number', 'title', 'body', 'created_at')}
    if not isinstance(task['title'], str) or not isinstance(task['body'], str):
        raise GateError('task_approval_identity_mismatch')
    task['body'] = coordinator.task_text(task['body'])
    spec = policy.operation_digest(task)
    expected = {
        'version': policy.POLICY_VERSION, 'origin': policy.API_ORIGIN,
        'repository_id': repository['id'], 'owner_id': repository['owner']['id'],
        'issue_id': issue['id'], 'issue_number': number, 'task_spec_sha256': spec,
        'kind': 'nova3.approval', 'action': policy.Action.APPROVE_QUEUE.value,
        'operation_arguments': {'transition': 'queue'},
        'operation_sha256': policy.operation_digest({'transition': 'queue'}),
        'executor_user_id': None}
    extras = {'nonce', 'actor_id', 'actor_login', 'issued_at', 'expires_at'}
    if set(record) != set(expected) | extras:
        raise GateError('task_approval_record_invalid')
    if any(json.dumps(record.get(key), sort_keys=True) != json.dumps(value, sort_keys=True)
           for key, value in expected.items()):
        raise GateError('task_approval_contract_mismatch')
    if not isinstance(record['nonce'], str) or not re.fullmatch(r'[0-9a-f]{64}', record['nonce']):
        raise GateError('task_approval_record_invalid')
    author = comment.get('user')
    if (not isinstance(author, dict) or type(author.get('id')) is not int or author['id'] <= 0
            or not isinstance(author.get('login'), str)
            or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', author['login'])
            or type(record['actor_id']) is not int or record['actor_id'] != author['id']
            or record['actor_login'] != author['login'] or author.get('type') != 'User'):
        raise GateError('task_approval_author_mismatch')
    try:
        created = policy._time(comment['created_at'])
        issued = policy._time(record['issued_at'])
        expires = policy._time(record['expires_at'])
        task_created = policy._time(task['created_at'])
    except Exception:
        raise GateError('task_approval_timestamp_invalid') from None
    # Expired NOW is deliberately allowed only for this historical binding.
    if not (task_created <= created <= time.time() and issued <= created < expires
            and 0 < expires - issued <= policy.MAX_APPROVAL_AGE
            and created - issued <= policy.MAX_CONSENT_AGE):
        raise GateError('task_approval_timestamp_invalid')
    permission = api(f"repos/{repo}/collaborators/{author['login']}/permission")
    person = permission.get('user', {}) if isinstance(permission, dict) else {}
    if (not isinstance(permission, dict) or permission.get('permission') != 'admin'
            or not isinstance(person, dict)
            or type(person.get('id')) is not int or person['id'] != author['id']
            or person.get('login') != author['login']):
        raise GateError('task_approval_current_admin_required')
    return {'task_spec_sha256': spec, 'approval_id': locator, 'default_branch': default_branch}


def _close_payload(issue):
    """Verified post-merge lifecycle cleanup, preserving approval locators."""
    labels = [label.get('name') if isinstance(label, dict) else label
              for label in issue.get('labels') or []]
    active = {'nova:queued', 'nova:claimed', 'nova:review', 'nova:blocked', 'nova:attention'}
    labels = [name for name in labels if name not in active]
    if 'nova:integrated' not in labels:
        labels.append('nova:integrated')
    return {'state': 'closed', 'body': coordinator.task_text(issue.get('body')),
            'labels': labels}


def _closure_complete(issue):
    payload = _close_payload(issue)
    labels = [label.get('name') if isinstance(label, dict) else label
              for label in issue.get('labels') or []]
    return (issue.get('state') == 'closed' and issue.get('body') == payload['body']
            and set(labels) == set(payload['labels']))


def _base_behind(api, repo, pr):
    """Positive ancestry evidence for this exact base/head, never CI inference."""
    base = pr.get('base', {}).get('sha')
    head = pr.get('head', {}).get('sha')
    if not all(isinstance(sha, str) and re.fullmatch(r'[A-Za-z0-9_-]+', sha)
               for sha in (base, head)):
        raise GateError('base_comparison_missing_sha')
    comparison = api(f'repos/{repo}/compare/{base}...{head}')
    if not isinstance(comparison, dict):
        raise GateError('base_comparison_invalid_response')
    behind = comparison.get('behind_by')
    if type(behind) is not int or behind < 0:
        raise GateError('base_comparison_invalid_response')
    if ('base_commit' in comparison
            and (not isinstance(comparison['base_commit'], dict)
                 or comparison['base_commit'].get('sha') != base)):
        raise GateError('base_comparison_wrong_base')
    status = comparison.get('status')
    if status is not None and (status not in ('ahead', 'behind', 'diverged', 'identical')
                               or (behind == 0 and status not in ('ahead', 'identical'))
                               or (behind > 0 and status not in ('behind', 'diverged'))):
        raise GateError('base_comparison_invalid_response')
    return behind


def _branch(pr):
    return (pr.get('head', {}).get('ref'), pr.get('head', {}).get('repo', {}).get('full_name'),
            pr.get('base', {}).get('ref'), pr.get('base', {}).get('sha'))


def process_pr(repo, number, folder, *, api, reviewer, repairer=None,
               max_repairs=2, compliance=None, required_source_checks=None):
    """Review/repair/deliver one PR, fail closed, using only injected execution.

    Returns schema/repo/pr/head/state/reason/repairs and optionally cached=True.
    States: blocked, retryable, preparing_delivery, waiting_ci, updating_base,
    reviewed, repaired, escalated, stale_head,
    merge_refused, merged_issue_open, delivered, not_open. No raw review content
    is returned or logged. Required check names are owner configuration, never
    taken from PR text. ``compliance`` defaults to review.findings_for's pure
    keyword interface and must return a list of findings.
    """
    folder = pathlib.Path(folder) / 'delivery'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    path = folder / (_digest([repo, int(number)]) + '.json')
    try:
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or state.get('schema') != SCHEMA
                or not isinstance(state.get('heads'), dict)
                or not isinstance(state.get('repairs'), list)
                or not isinstance(state.get('repair_sessions'), list)
                or not isinstance(state.get('base_updates', {}), dict)
                or not isinstance(state.get('awaiting_publication', {}), dict)):
            raise GateError('invalid_cache_schema')
    except FileNotFoundError:
        state = {'schema': SCHEMA, 'heads': {}, 'repairs': [], 'repair_sessions': []}
    except (OSError, ValueError, AttributeError):
        # Losing the budget ledger must not silently grant two more repairs.
        return {'schema': SCHEMA, 'repo': repo, 'pr': number,
                'state': 'escalated', 'reason': 'cache_unreadable'}
    head = None
    key = None
    root = f'repos/{repo}/pulls/{int(number)}'
    limit = max(0, min(2, int(max_repairs)))

    def finish(status, reason, *, cache=False, cached=False):
        report = {'schema': SCHEMA, 'repo': repo, 'pr': number, 'head': head,
                  'state': status, 'reason': reason, 'repairs': len(state['repairs'])}
        if cached:
            report['cached'] = True
        if cache and key:
            state['heads'].setdefault(key, {}).update({'state': status, 'reason': reason})
        state['last'] = report
        temp = path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(state, handle, sort_keys=True)
        temp.chmod(0o600)
        os.replace(temp, path)
        return report

    try:
        pr = api(root)
        head = pr.get('head', {}).get('sha')
        if not head:
            return finish('blocked', 'missing_head')
        issue_number = _linked_issue(pr.get('body'), repo)
        issue_root = f'repos/{repo}/issues/{issue_number}'
        issue = api(issue_root)
        # Contract-size validation precedes extraction; retain a digest-only
        # cache key even when that validation rejects the task.
        key = _digest(['contract', head, issue.get('body'), pr.get('body'), _branch(pr)])
        provenance = _task_provenance(
            api, repo, issue, pr.get('base', {}).get('ref'),
            allow_closed=pr.get('merged') is True and state.get('confirmed_merge') == head)
        if state['heads'].get(key, {}).get('reason') in (
                'task_body_too_large', 'task_contract_too_large'):
            previous = state['heads'][key]
            return finish('blocked', previous['reason'], cached=True)
        task = extract_task(issue)
        key = _digest([head, task, pr.get('body'), _branch(pr),
                       required_source_checks or DEFAULT_SOURCE_CHECKS])
        # A confirmed delivery can finish issue closure after an API interruption.
        if pr.get('merged') is True:
            if (state.get('confirmed_merge') != head
                    or state.get('resolved_task') != _digest(task)
                    or state.get('resolved_spec') != provenance['task_spec_sha256']):
                return finish('blocked', 'merged_without_delivery_evidence')
            if not _closure_complete(issue):
                api(issue_root, method='PATCH', payload=_close_payload(issue))
                issue = api(issue_root)
            recovered = _task_provenance(
                api, repo, issue, pr.get('base', {}).get('ref'), allow_closed=True)
            complete = (_closure_complete(issue)
                        and recovered['task_spec_sha256'] == state['resolved_spec'])
            return finish('delivered' if complete else 'merged_issue_open',
                          'confirmed_merge_and_resolved_task')
        if pr.get('state') != 'open':
            return finish('not_open', 'pr_not_open')
        if CLOSING_RE.search(pr.get('body') or ''):
            # GitHub (and the legacy coordinator webhook) would otherwise
            # close the issue DURING merge, before resolved-task verification.
            latest = api(root)
            if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                    or latest.get('body') != pr.get('body') or latest.get('state') != 'open'):
                return finish('stale_head', 'pr_changed_before_closing_metadata_migration')
            if _task_provenance(api, repo, api(issue_root), latest.get('base', {}).get('ref')) != provenance:
                return finish('stale_head', 'task_changed_before_closing_metadata_migration')
            normalized = _nonclosing_body(pr.get('body'), issue_number)
            updated = api(root, method='PATCH', payload={'body': normalized})
            if not isinstance(updated, dict) or updated.get('body') != normalized:
                state['retry_after'] = time.time() + RETRY_SECONDS
                return finish('retryable', 'closing_metadata_migration_unconfirmed')
            return finish('preparing_delivery', 'automatic_issue_closure_disabled')
        if state.get('repair_violation'):
            return finish('escalated', 'repair_contract_violated')
        pending_publication = state.get('awaiting_publication')
        if pending_publication:
            if list(_branch(pr)[:3]) != pending_publication.get('branch'):
                state['repair_violation'] = True
                return finish('escalated', 'repair_changed_branch_or_base', cache=True)
            if head == pending_publication.get('previous_head'):
                # This is PR/head-bound, not base-SHA-bound: an ordinary target
                # advance must not trigger another paid repair while GitHub's
                # read API still reports the already-repaired old commit.
                return finish('repaired', 'awaiting_published_head', cache=True, cached=True)
            state.pop('awaiting_publication', None)
        old = state['heads'].get(key, {})
        if (old.get('state') in ('blocked', 'escalated', 'repaired')
                and not (old.get('reason') == 'source_unchanged' and repairer is not None)):
            return finish(old['state'], old['reason'], cached=True)
        if state.get('retry_after', 0) > time.time():
            return finish('retryable', 'transient_cooldown', cached=True)
        # A green old head is not evidence against a newer target branch. Do
        # this before reading the diff or invoking even a cached-pass callback.
        try:
            behind = _base_behind(api, repo, pr)
        except GateError as exc:
            state['retry_after'] = time.time() + RETRY_SECONDS
            return finish('retryable', str(exc))
        if behind:
            update_key = _digest([head, _branch(pr)])
            updates = state.setdefault('base_updates', {})
            pending = updates.get(update_key)
            if pending:
                if pending == 'accepted':
                    return finish('updating_base', 'base_update_pending', cached=True)
                return finish('blocked', 'base_update_unconfirmed', cached=True)
            latest = api(root)
            if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                    or latest.get('state') != 'open'):
                return finish('stale_head', 'head_or_base_changed_before_update')
            # Persist before the API call. Do not repeatedly request updates of
            # an unchanged head when GitHub is async, rejects, or times out.
            updates[update_key] = 'unconfirmed'
            finish('updating_base', 'base_update_requested')
            try:
                updated = api(root + '/update-branch', method='PUT',
                              payload={'expected_head_sha': head})
            except Exception:
                try:
                    raced = api(root)
                except Exception:
                    raced = None
                if raced is not None and (raced.get('head', {}).get('sha') != head
                                          or _branch(raced) != _branch(pr)):
                    return finish('stale_head', 'head_or_base_changed_during_update')
                state['retry_after'] = time.time() + RETRY_SECONDS
                return finish('retryable', 'base_update_unconfirmed')
            if (not isinstance(updated, dict)
                    or updated.get('message') != 'Updating pull request branch.'):
                return finish('blocked', 'base_update_not_accepted')
            updates[update_key] = 'accepted'
            return finish('updating_base', 'base_update_accepted_requires_new_head')
        files = _files(api, root, pr)
        diff = '\n'.join(f"diff --git a/{f['path']} b/{f['path']}\n{f['patch']}" for f in files)
        check_files = files + [{'path': f['previous_filename']} for f in files
                               if f.get('previous_filename')]
        findings = (compliance or compliance_review.findings_for)(
            files=check_files, diff=diff, issue_body=task['body'],
            issue_number=issue_number)
        if not isinstance(findings, list) or findings:
            return finish('blocked', 'compliance_failed', cache=True)
        source_unchanged = _needs_source(task) and not _source_changed(files)
        if source_unchanged and repairer is None:
            return finish('blocked', 'source_unchanged', cache=True)
        if api(root).get('head', {}).get('sha') != head:
            return finish('stale_head', 'head_changed_during_fetch')
        try:
            ci = _ci(api, repo, head, required_source_checks or DEFAULT_SOURCE_CHECKS)
            ci_error = None
        except CIFailure as exc:
            ci, ci_error = exc.context, 'ci_failed'
            if repairer is None:
                return finish('waiting_ci', str(exc))
        except GateError as exc:
            # Pending/missing/stale/unknown evidence is not a substantive
            # review verdict. Never spend a model or freeze this head on it.
            return finish('waiting_ci', str(exc))
        context_data = {'repo': repo, 'pr': pr, 'task': task, 'files': files,
                        'diff': diff, 'ci': ci, 'ci_error': ci_error}
        if _json_bytes(context_data) > MAX_CONTEXT_BYTES:
            return finish('blocked', 'review_context_too_large', cache=True)
        context = _freeze(context_data)
        if source_unchanged or ci_error == 'ci_failed':
            # Trusted deterministic gates only request repair, NEVER pass.
            # Actual acceptance is unknown until a fresh green repaired head
            # has also passed a real independent substantive review.
            session = ('deterministic:source-delta/v1' if source_unchanged
                       else 'deterministic:ci-failure/v1')
            deterministic_findings = []
            if source_unchanged:
                deterministic_findings.append({
                    'id': 'source_unchanged',
                    'problem': 'Source implementation objective has no production source change.',
                    'requiredFix': 'Implement meaningful production source behavior within the '
                                   'approved task scope and existing PR branch, with honest '
                                   'acceptance evidence. If evidence or permitted source scope is '
                                   'missing, report blocked rather than fabricating an implementation.'})
            if ci_error == 'ci_failed':
                deterministic_findings.append({
                    'id': 'ci_failed',
                    'problem': 'Current-head CI has terminal unsuccessful checks: '
                               + json.dumps(ci['failed_checks'], sort_keys=True),
                    'requiredFix': 'Investigate the failed checks and repair genuine source/test '
                                   'defects within the approved scope on this PR branch. Do not '
                                   'weaken tests, skip checks, or fabricate success. If failure is '
                                   'infrastructure-only or evidence is unavailable, report blocked. '
                                   'A repaired head needs fresh successful CI and independent review.'})
            review = {
                'verdict': 'needs_revision', 'reviewed_head': head,
                'acceptanceResults': [
                    {'criterion': criterion, 'status': 'blocked',
                     'evidence': 'Acceptance not established: deterministic delivery gate failed.'}
                    for criterion in task['acceptance']],
                'findings': deterministic_findings}
            state['heads'][key] = {
                'verdict': 'needs_revision', 'session': session,
                'review_digest': _digest(review),
                'acceptance_count': len(task['acceptance'])}
        elif old.get('verdict') == 'pass':
            review, session = {'verdict': 'pass'}, old['session']
        else:
            try:
                raw = reviewer(context)
            except Exception:
                state['retry_after'] = time.time() + RETRY_SECONDS
                return finish('retryable', 'review_provider_unavailable')
            try:
                review, session = _review(raw, task, head, state['repair_sessions'])
            except (ValueError, TypeError, KeyError):
                return finish('escalated', 'invalid_review', cache=True)
            state['heads'][key] = {
                'verdict': review['verdict'], 'session': session,
                'review_digest': _digest(review),
                'acceptance_count': len(review['acceptanceResults'])}
        if review['verdict'] != 'pass':
            if review['verdict'] == 'blocked' or repairer is None:
                return finish('blocked', 'substantive_review_' + review['verdict'], cache=True)
            if len(state['repairs']) >= limit:
                return finish('escalated', 'repair_budget_exhausted', cache=True)
            if api(root).get('head', {}).get('sha') != head:
                return finish('stale_head', 'head_changed_before_repair')
            # Persist the spend BEFORE executing: crashes cannot reset a budget.
            state['repairs'].append(head)
            finish('escalated', 'repair_interrupted', cache=True)
            try:
                repaired = repairer(context, _freeze(review['findings']))
            except Exception:
                # A provider/auth outage is not a semantic rejection. Keep its
                # spent invocation in the bounded budget, but permit recovery
                # after cooldown rather than pinning a permanent failed verdict.
                state['heads'].pop(key, None)
                state['retry_after'] = time.time() + RETRY_SECONDS
                return finish('retryable', 'repair_provider_unavailable')
            execution = repaired.get('execution', {}) if isinstance(repaired, dict) else {}
            repair_session = execution.get('session_id')
            if (execution.get('role') != 'repairer' or not isinstance(repair_session, str)
                    or not repair_session or repair_session == session):
                return finish('escalated', 'repair_not_independent', cache=True)
            state['repair_sessions'].append(repair_session)
            latest = api(root)
            # Branch identity is head ref/repository + base ref. A target
            # branch's SHA naturally advances while repair runs; normal fresh
            # base/CI/review gates handle that on the next invocation.
            if _branch(latest)[:3] != _branch(pr)[:3]:
                state['repair_violation'] = True
                return finish('escalated', 'repair_changed_branch_or_base', cache=True)
            if repaired.get('repaired') is not True:
                return finish('escalated', 'repair_failed_or_no_new_head', cache=True)
            if latest.get('head', {}).get('sha') == head:
                published = repaired.get('published_head')
                if (isinstance(published, str) and re.fullmatch(r'[0-9a-fA-F]{40}', published)
                        and published.casefold() != str(head).casefold()):
                    state['awaiting_publication'] = {
                        'previous_head': head, 'published_head': published.lower(),
                        'branch': list(_branch(pr)[:3])}
                    # A trusted successful push is progress, not review/scope
                    # acceptance. Never inspect old files as the new commit,
                    # mark ready, or merge until a fresh GET exposes a new head.
                    return finish('repaired', 'awaiting_published_head', cache=True)
                return finish('escalated', 'repair_failed_or_no_new_head', cache=True)
            repaired_files = _files(api, root, latest)
            scope_findings = (compliance or compliance_review.findings_for)(
                files=repaired_files + [{'path': f['previous_filename']} for f in repaired_files
                                        if f.get('previous_filename')],
                diff='\n'.join(f['patch'] for f in repaired_files),
                issue_body=task['body'], issue_number=issue_number)
            if not isinstance(scope_findings, list) or scope_findings:
                return finish('escalated', 'repair_outside_scope', cache=True)
            return finish('repaired', 'new_head_requires_independent_review', cache=True)
        if ci_error:
            return finish('waiting_ci', ci_error)
        latest = api(root)
        if latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr):
            return finish('stale_head', 'head_or_branch_changed_before_ready')
        if latest.get('state') != 'open' or latest.get('mergeable') is False:
            return finish('blocked', 'pr_not_mergeable')
        latest_issue = api(issue_root)
        if (_task_provenance(api, repo, latest_issue, latest.get('base', {}).get('ref')) != provenance
                or _digest(extract_task(latest_issue)) != _digest(task)):
            return finish('stale_head', 'task_changed_before_merge')
        # Publish a genuine independent pass as COMMENT, never APPROVE: the
        # owner's GitHub actor may also be the PR author. Branch protection is
        # still authoritative; this evidence cannot substitute for its approval.
        latest = api(root)
        if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                or latest.get('body') != pr.get('body') or latest.get('state') != 'open'):
            return finish('stale_head', 'head_or_task_link_changed_before_review_comment')
        review_record = state['heads'][key]
        if not review_record.get('published_review_id'):
            review_digest = review_record.get('review_digest')
            if (not isinstance(review_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', review_digest)
                    or review_record.get('acceptance_count') != len(task['acceptance'])):
                return finish('escalated', 'cached_review_evidence_invalid', cache=True)
            public_session = (session if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session)
                              else 'sha256:' + _digest(session))
            summary = (
                'Independent substantive review evidence (COMMENT only; not GitHub approval).\n'
                f'Session: {public_session}\n'
                f"Acceptance: {len(task['acceptance'])}/{len(task['acceptance'])} passed.\n"
                'Gates: historical task provenance, source/scope compliance, and current-head '
                'required CI passed.\n'
                f'Review SHA256: {review_digest}\n'
                f"Task SHA256: {provenance['task_spec_sha256']}\n"
                f'Head SHA256: {_digest(head)}')
            published = api(root + '/reviews', method='POST', payload={
                'event': 'COMMENT', 'commit_id': head, 'body': summary})
            if (not isinstance(published, dict) or type(published.get('id')) is not int
                    or published['id'] <= 0 or published.get('commit_id') != head
                    or published.get('state') != 'COMMENTED'):
                return finish('blocked', 'review_comment_not_confirmed')
            review_record['published_review_id'] = published['id']
            finish('reviewed', 'independent_review_comment_published')
        latest = api(root)
        if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                or latest.get('body') != pr.get('body') or latest.get('state') != 'open'):
            return finish('stale_head', 'head_changed_after_review_comment')
        if latest.get('draft'):
            if _task_provenance(api, repo, api(issue_root), latest.get('base', {}).get('ref')) != provenance:
                return finish('stale_head', 'task_approval_changed_before_ready')
            node = latest.get('node_id')
            if not node:
                return finish('blocked', 'missing_pr_node_id')
            ready = api('graphql', method='POST', payload={
                'query': 'mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id})'
                         '{pullRequest{isDraft}}}', 'variables': {'id': node}})
            if ready.get('errors') or ready.get('data', {}).get(
                    'markPullRequestReadyForReview', {}).get('pullRequest', {}).get('isDraft') is not False:
                return finish('blocked', 'ready_not_confirmed')
        latest = api(root)
        if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                or latest.get('body') != pr.get('body')):
            return finish('stale_head', 'head_or_task_link_changed_before_merge')
        if latest.get('draft') or latest.get('state') != 'open':
            return finish('blocked', 'not_ready')
        try:
            _ci(api, repo, head, required_source_checks or DEFAULT_SOURCE_CHECKS)
        except GateError as exc:
            return finish('waiting_ci', str(exc))
        # Read authority again after readiness/CI API calls. An approval comment
        # can be removed, edited, or its author demoted while a review runs.
        if _task_provenance(api, repo, api(issue_root), latest.get('base', {}).get('ref')) != provenance:
            return finish('stale_head', 'task_approval_changed_before_merge')
        latest = api(root)
        if (latest.get('head', {}).get('sha') != head or _branch(latest) != _branch(pr)
                or latest.get('body') != pr.get('body') or CLOSING_RE.search(latest.get('body') or '')
                or latest.get('state') != 'open' or latest.get('draft')):
            return finish('stale_head', 'pr_changed_at_merge_boundary')
        # Squash with controller-owned metadata: original branch commits may
        # themselves contain closing keywords. Do not inherit their messages or
        # the editable PR title/body into the default-branch delivery commit.
        # This preserves the PR branch/history; no rewrite or deletion is used.
        merged = api(root + '/merge', method='PUT', payload={
            'sha': head, 'merge_method': 'squash',
            'commit_title': f'Verified task #{issue_number} delivery',
            'commit_message': f'Related to #{issue_number}\n\n'
                              'Independent review and required source CI passed.'})
        if not isinstance(merged, dict) or merged.get('merged') is not True:
            return finish('merge_refused', 'github_did_not_merge')
        # Persist confirmed merge before attempting issue delivery. All actual
        # acceptance criteria passed at this head, so the linked task is resolved.
        state['confirmed_merge'] = head
        state['resolved_task'] = _digest(task)
        state['resolved_spec'] = provenance['task_spec_sha256']
        finish('merged_issue_open', 'confirmed_merge')
        issue = api(issue_root)
        try:
            final_provenance = _task_provenance(
                api, repo, issue, pr.get('base', {}).get('ref'), allow_closed=True)
        except GateError:
            return finish('merged_issue_open', 'issue_task_changed_after_merge')
        if (final_provenance['task_spec_sha256'] != state['resolved_spec']
                or _digest(extract_task(issue)) != state['resolved_task']):
            return finish('merged_issue_open', 'issue_task_changed_after_merge')
        if not _closure_complete(issue):
            api(issue_root, method='PATCH', payload=_close_payload(issue))
            issue = api(issue_root)
        if not _closure_complete(issue):
            return finish('merged_issue_open', 'issue_close_not_confirmed')
        verified = _task_provenance(
            api, repo, issue, pr.get('base', {}).get('ref'), allow_closed=True)
        if verified['task_spec_sha256'] != state['resolved_spec']:
            return finish('merged_issue_open', 'issue_task_changed_after_merge')
        return finish('delivered', 'confirmed_merge_and_closed_issue')
    except GateError as exc:
        # Only our stable validation codes are recorded, never external text.
        reason = str(exc)
        safe = reason if re.fullmatch(r'[a-z_]+', reason) else 'invalid_response'
        return finish('blocked', safe, cache=True)
    except Exception:
        state['retry_after'] = time.time() + RETRY_SECONDS
        return finish('retryable', 'api_or_execution_unavailable')
