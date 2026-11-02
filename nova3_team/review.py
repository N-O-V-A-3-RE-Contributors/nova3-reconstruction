"""Owner/admin compliance review for contributor pull requests.

A contributor (slave) worker may claim a task, edit source, run tests and open a
PR. It must never approve, merge or decide that its own work is acceptable.
This module is the **read-only** half of the owner/admin gate: it checks a PR
against the issue it claims to close and reports findings. A human owner/admin
(or the owner's own agent acting for them) makes the decision.

Checks (each produces a stable finding id):

``missing_task_scope``      the linked issue has no ``Allowed paths:`` line
``outside_allowed_paths``   a changed path is not inside the issue's scope
``protected_path``          a protected tree was touched even if the scope says so
``binary_artifact``         an APK/OBB/SO/DEX/RAR/archive/image was added
``possible_secret``         an added line looks like a credential
``oversized_change``        an unusually large diff needs a human look

The analysis never writes to GitHub and never executes repository code. Wiring
it into CI only *reports*; merge authority stays with the owner/admin.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

SCHEMA = 'nova3-pr-review/1'
PROTECTED = ('.github/', 'originals/', 'analysis/', 'research/', 'generated/',
             'Archive/', 'materials/', '.venv/', '.local/', 'coordination/',
             'tools/systemd/')
BINARY_SUFFIXES = ('.apk', '.obb', '.so', '.dex', '.rar', '.elf', '.bin', '.zip',
                   '.tar', '.gz', '.7z', '.exe', '.dll', '.dylib', '.a', '.o',
                   '.png', '.jpg', '.jpeg', '.webp', '.ktx', '.pvr', '.etc')
SECRET_RE = re.compile(
    r'(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
    r'sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|'
    r'-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----|'
    r'(?i:api[_-]?key|secret|password|token)\s*[:=]\s*["\'][A-Za-z0-9_\-]{16,}["\'])')
MAX_ADDED_LINES = 2500


def allowed_paths(issue_body: str) -> list[str]:
    """The issue's declared edit scope, one entry per Allowed paths line."""
    paths: list[str] = []
    for line in str(issue_body or '').splitlines():
        if line.strip().startswith('Allowed paths:'):
            for piece in line.split(':', 1)[1].split(','):
                piece = piece.strip().strip('`').strip()
                if piece:
                    paths.append(piece)
    return paths


def _inside(path: str, allowed: list[str]) -> bool:
    for entry in allowed:
        if path == entry:
            return True
        root = entry.rstrip('/')
        if entry.endswith('/') and (path == root or path.startswith(root + '/')):
            return True
        if path.startswith(root + '/'):
            return True
    return False


def findings_for(*, files: list[dict], diff: str, issue_body: str,
                 issue_number: int | None = None) -> list[dict]:
    """Pure check: no network, no filesystem, no GitHub writes."""
    findings: list[dict] = []
    scope = allowed_paths(issue_body)
    if not scope:
        findings.append({'id': 'missing_task_scope', 'severity': 'blocker',
                         'problem': f'issue #{issue_number or "?"} has no Allowed paths line',
                         'fix': 'ask an owner/admin to scope the task before review'})
        return findings
    added = [line[1:] for line in str(diff or '').splitlines()
             if line.startswith('+') and not line.startswith('+++')]
    for entry in files:
        path = str(entry.get('path') or '')
        if not path:
            continue
        if not _inside(path, scope):
            findings.append({'id': 'outside_allowed_paths', 'severity': 'blocker',
                             'problem': f'{path} is outside the allowed paths of the task',
                             'fix': f'remove the change or have an owner extend the scope: {scope}'})
        if path.startswith(PROTECTED):
            findings.append({'id': 'protected_path', 'severity': 'blocker',
                             'problem': f'{path} touches a protected tree',
                             'fix': 'protected trees (CI, credentials, private references, '
                                    'services, queue state) are owner/admin-only'})
        if path.lower().endswith(BINARY_SUFFIXES):
            findings.append({'id': 'binary_artifact', 'severity': 'blocker',
                             'problem': f'{path} looks like a binary or game asset',
                             'fix': 'rebuild from source; never commit original game binaries'})
    for number, line in enumerate(added, 1):
        match = SECRET_RE.search(line)
        if match:
            findings.append({'id': 'possible_secret', 'severity': 'blocker',
                             'problem': f'added line {number} looks like a credential',
                             'fix': 'remove it, rotate the credential, and use a local store'})
            break
    if len(added) > MAX_ADDED_LINES:
        findings.append({'id': 'oversized_change', 'severity': 'medium',
                         'problem': f'{len(added)} added lines is unusually large for one task',
                         'fix': 'split the task or ask for an owner review of the size'})
    return findings


def _gh(args: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(['gh', *args], input=stdin, capture_output=True,
                            text=True, timeout=90, check=False)
    if result.returncode:
        raise RuntimeError(f'gh {" ".join(args[:3])} failed: {result.stderr[:300]}')
    return result.stdout


def _linked_issue(body: str) -> int | None:
    for pattern in (r'(?i)\bcloses\s+#(\d+)', r'(?i)\bfixes\s+#(\d+)',
                    r'(?i)\bresolves\s+#(\d+)'):
        match = re.search(pattern, str(body or ''))
        if match:
            return int(match.group(1))
    return None


def review(repo: str, pr: int, *, owner_override: bool = False) -> dict:
    """Fetch one PR + its linked issue and return a verdict. Read-only."""
    payload = json.loads(_gh(['pr', 'view', str(pr), '--repo', repo, '--json',
                              'number,title,body,author,files,additions,deletions,isDraft']))
    files = payload.get('files') or []
    if not files:
        files = json.loads(_gh(['api', f'repos/{repo}/pulls/{pr}/files?per_page=100']))
    number = _linked_issue(payload.get('body') or '')
    issue_body = ''
    if number:
        issue = json.loads(_gh(['api', f'repos/{repo}/issues/{number}']))
        issue_body = issue.get('body') or ''
    diff = _gh(['pr', 'diff', str(pr), '--repo', repo]) if not owner_override else ''
    if number is None:
        findings = [{'id': 'missing_linked_task', 'severity': 'blocker',
                     'problem': 'the PR does not say which task it closes '
                                '(Closes/Fixes/Resolves #N)',
                     'fix': 'link the scoped issue the work belongs to'}]
    else:
        findings = findings_for(files=files, diff=diff, issue_body=issue_body,
                                issue_number=number)
    if owner_override:
        findings = [f for f in findings if f['id'] not in ('outside_allowed_paths',)]
    verdict = 'pass' if not findings else 'needs_changes'
    return {'schema': SCHEMA, 'repo': repo, 'pr': pr, 'issue': number,
            'author': (payload.get('author') or {}).get('login'),
            'files': [f.get('path') for f in files],
            'verdict': verdict, 'findings': findings,
            'note': 'Read-only. Merge authority stays with an owner/admin.'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only owner/admin compliance review')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--owner-override', action='store_true',
                        help='owner/admin own PR: relax the scope check, keep safety checks')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = review(args.repo, args.pr, owner_override=args.owner_override)
    except Exception as exc:
        print(json.dumps({'schema': SCHEMA, 'verdict': 'unavailable',
                          'error': str(exc)[:300]}, indent=2))
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"PR #{report['pr']} ({report['author']}): {report['verdict'].upper()}")
        print(f"task: #{report['issue']}; files: {len(report['files'])}")
        for finding in report['findings']:
            print(f"  [{finding['severity']}] {finding['id']}: {finding['problem']}")
            print(f"      fix: {finding['fix']}")
        print(report['note'])
    return 0 if report['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
