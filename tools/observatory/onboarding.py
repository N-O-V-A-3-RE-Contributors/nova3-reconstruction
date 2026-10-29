"""Guided, role-aware onboarding for the NOVA3 Control Center.

Contributors get a read-mostly path: status, their own provider install/login,
owner-approved work, evidence/PR submission and read-only suggestions.
Administration steps appear only for an authenticated organization owner or
repository ADMIN. No step collects a password/token, runs sudo, pipes a remote
script, installs licensed software or mutates GitHub without the #135 gate.

Statuses are deliberately limited to OBSERVED / MISSING / UNKNOWN / NEEDS ACTION
so an offline or unverifiable check never becomes a fabricated "ready".
"""
from __future__ import annotations

CONTRIBUTOR = 'CONTRIBUTOR'
ADMIN = 'ADMIN'
REVOKED = 'REVOKED'
UNKNOWN = 'UNKNOWN'

OBSERVED = 'OBSERVED'
MISSING = 'MISSING'
NEEDS_ACTION = 'NEEDS ACTION'
UNVERIFIED = 'UNKNOWN'


def role_of(owner_status: dict | None, repo_role: str = 'UNKNOWN') -> str:
    """Derive the onboarding role; revoked/unknown fail closed."""
    status = owner_status if isinstance(owner_status, dict) else {}
    if status.get('allowed'):
        return ADMIN
    role = str(status.get('role') or repo_role or 'UNKNOWN').upper()
    if role in ('ADMIN',):
        return ADMIN
    if role in ('MAINTAIN', 'WRITE', 'TRIAGE', 'READ'):
        return CONTRIBUTOR
    if role in ('REVOKED', 'SUSPENDED'):
        return REVOKED
    return UNKNOWN


def _step(ident, title, detail, status=UNVERIFIED, required=False, action=''):
    return {'id': ident, 'title': title, 'detail': detail, 'status': status,
            'required': required, 'action': action}


def contributor_steps(context: dict | None = None) -> list[dict]:
    context = context or {}
    gh = context.get('gh_auth')
    repo = context.get('repo_access')
    provider_ready = context.get('provider_ready')
    tools = context.get('toolchain')
    steps = [
        _step('identity', 'Authenticate GitHub CLI locally',
              'Run gh auth login yourself; the Control Center never asks for a token.',
              OBSERVED if gh is True else NEEDS_ACTION if gh is False else UNVERIFIED,
              True, 'gh auth login'),
        _step('membership', 'Confirm repository access',
              'Membership is read from GitHub; no copy of private repository data is uploaded.',
              OBSERVED if repo is True else NEEDS_ACTION if repo is False else UNVERIFIED,
              True, 'gh repo view --json viewerPermission'),
        _step('providers', 'Choose and sign in to your own AI provider',
              'Accounts page (U): install/test/login guidance for Codex, Claude, Gemini, Kimi, '
              'Qwen, OpenCode, OpenRouter, DeepSeek or Hermes. Keys stay with the vendor.',
              OBSERVED if provider_ready else UNVERIFIED, False, 'open ACCOUNTS and press R'),
        _step('work', 'Open owner-approved work only',
              'Approved issues appear on TASKS. Planner drafts are suggestions and are never '
              'auto-queued; do not mutate the global queue.',
              UNVERIFIED, False, 'open TASKS'),
        _step('evidence', 'Submit evidence with your pull request',
              'Record source-present, host-test, reference-comparison and byte-match claims '
              'separately. UNKNOWN/NOT MATCHED stay visible.',
              UNVERIFIED, False, 'open PULL REQUESTS'),
        _step('suggest', 'Send suggestions read-only',
              'Open an issue/discussion for a proposed task; contributors cannot approve, '
              'reprioritise or merge it.',
              UNVERIFIED, False, 'open GitHub issue'),
    ]
    if tools is True:
        steps.insert(3, _step('toolchain', 'Check local toolchain', 'Python, curses and '
                              'optional analysis tools are verified locally.', OBSERVED, False,
                              'H TOOLCHAIN'))
    return steps


def admin_steps(context: dict | None = None) -> list[dict]:
    context = context or {}
    queue = context.get('queue_reviewed')
    approval = context.get('approval_gate')
    journal = context.get('journal_available')
    return contributor_steps(context) + [
        _step('planning', 'Review planner drafts and dependency graph',
              'Owner-only planning workspace. Drafts remain suggestions until explicitly approved.',
              UNVERIFIED, False, 'open ADMINISTRATION'),
        _step('queue', 'Approve queue order and budgets',
              'Priority never bypasses #124/#125/#134/#135 dependencies. No active worker is interrupted.',
              OBSERVED if queue else UNVERIFIED, False, 'post approval review'),
        _step('gate', 'Use the separate #135 approval gate',
              'Merges, restarts and policy changes require the gate plus a fresh server-side role check.',
              OBSERVED if approval is True else NEEDS_ACTION if approval is False else UNVERIFIED,
              True, 'wait for #135 gate'),
        _step('journal', 'Inspect local journal only when needed',
              'ACTIVITY page J: explicit confirmation, bounded round file, redacted, local-only.',
              OBSERVED if journal else UNVERIFIED, False, 'open ACTIVITY and press J'),
    ]


def steps_for(role: str, context: dict | None = None) -> list[dict]:
    """Only ADMIN gets administration steps; revoked/unknown get none."""
    if role == ADMIN:
        return admin_steps(context)
    if role == CONTRIBUTOR:
        return contributor_steps(context)
    return []


def summary_lines(role: str, context: dict | None = None, *, width: int = 80) -> list[tuple[str, str]]:
    """Compact onboarding rows for HELP, with honest status words."""
    steps = steps_for(role, context)
    if not steps:
        return [(f"Onboarding {role}: no guided steps. Owner actions fail closed.", 'red'),
                ('Verify gh auth and repository membership; reinstall if this persists.', 'yellow')]
    lines = [(f"{role} ONBOARDING: {sum(s['status'] == OBSERVED for s in steps)}/{len(steps)} observed",
              'cyan')]
    for index, step in enumerate(steps, 1):
        detail = step['detail']
        if len(detail) > 78:
            detail = detail[:77] + '…'
        lines.append((f"{index}. [{step['status']}] {step['title']}", 'green' if step['status'] == OBSERVED else 'yellow'))
        lines.append((f"   {detail}", 'muted'))
        if step['action']:
            lines.append((f"   Next: {step['action']}", 'cyan'))
    return lines


def owner_action_allowed(role: str, owner_status: dict | None = None, *,
                         recheck: object = None) -> bool:
    """Server boundaries must pass a fresh recheck callable, never this alone.

    The UI may hide controls based on ``role``; execution must call
    ``operator_ops.require_owner_access`` (or ``owner_access``) again.
    """
    if role != ADMIN:
        return False
    status = owner_status if isinstance(owner_status, dict) else {}
    if status and not status.get('allowed'):
        return False
    if callable(recheck):
        try:
            result = recheck()
        except Exception:
            return False
        if isinstance(result, dict):
            return bool(result.get('allowed'))
        return bool(result)
    return True
