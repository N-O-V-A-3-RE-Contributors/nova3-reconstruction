"""Operator status surface: one readable page for the phone (DSH Web) or a terminal.

The supervisor keeps its truth in durable JSON files; this module only *renders*
them. It never queries a model, never reads a credential value and never writes
to the supervisor's state, so it is safe to run at any time, from any checkout,
while the worker is mid-session.

    python -m nova3_team.dsh_status            # human page (default)
    python -m nova3_team.dsh_status --json     # same data, machine-readable
    python -m nova3_team.dsh_status --write ISSUE_NUMBER  # also refresh a GitHub issue

The issue mode is how the phone sees it without a tunnel: the same page is
written to one pinned status issue (default: the existing live-status issue) on
a cadence, exactly like the project's other status publishers.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

from . import dsh_policy

STATE_EMOJI = {
    dsh_policy.RUNNING: '🟢', dsh_policy.BACKOFF: '🟡', dsh_policy.WAITING_CREDITS: '🔴',
    dsh_policy.WAITING_APPROVAL: '🔵', dsh_policy.BLOCKED: '⛔', dsh_policy.COMPLETE: '✅',
}
STATUS_NOTE = {
    dsh_policy.RUNNING: 'sessions may start now',
    dsh_policy.BACKOFF: 'temporary provider problem; bounded exponential retry',
    dsh_policy.WAITING_CREDITS: 'no further sessions can succeed until the balance is topped up '
                                '(rechecked automatically; no restart needed)',
    dsh_policy.WAITING_APPROVAL: 'a human decision is needed; other work continues where safe',
    dsh_policy.BLOCKED: 'this task needs repair before it is retried; the worker moves on',
    dsh_policy.COMPLETE: 'nothing queued or plannable remains',
}


def _read_json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _ago(seconds: float) -> str:
    seconds = int(max(0, seconds))
    for unit, size in (('d', 86400), ('h', 3600), ('m', 60)):
        if seconds >= size:
            return f'{seconds // size}{unit}'
    return f'{seconds}s'


def collect(repo: str, slot: str = 'dsh', *, now: float | None = None) -> dict:
    import time
    from . import worker
    now = time.time() if now is None else now
    root = worker.state_root(repo)
    paths = worker.slot_paths(root, slot)
    state_file = paths.state / 'supervisor.json'
    state = _read_json(state_file)
    ledger = dsh_policy.SpendLedger(paths.state / 'spend.jsonl')
    balance = _openrouter_balance()
    open_path = paths.state / 'requests.open.json'
    requests = _read_list(open_path)
    if not open_path.exists():
        requests = _requests(paths.state / 'requests.jsonl')
    last = state.get('last_outcome') or {}
    task = state.get('task') or {}
    try:
        from . import analysis as project_analysis
        project_snapshot = project_analysis.snapshot(pathlib.Path.cwd())
        project_estimate = project_analysis.estimate(project_snapshot)
    except Exception:
        project_snapshot, project_estimate = None, None
    return {
        'slot': slot,
        'state': state.get('state') or 'not started',
        'reason': state.get('reason') or '',
        'updated_unix': state.get('updated_unix'),
        'updated_ago': _ago(now - state['updated_unix']) if state.get('updated_unix') else None,
        'next_check_in': (max(0, int(state['next_check_unix'] - now))
                          if state.get('next_check_unix') else None),
        'task': task.get('number'),
        'task_title': task.get('title'),
        'last_outcome': last,
        'endpoint': state.get('endpoint') or {},
        'budget': state.get('budget') or {},
        'sessions_today': ledger.sessions_today(now=now),
        'tokens_today': ledger.tokens_today(now=now),
        'sessions_last_hour': ledger.sessions_last_hour(now=now),
        'assessment': state.get('assessment') or {},
        'open_requests': requests,
        'balance': balance,
        'state_file': str(state_file),
        'project': {'snapshot': project_snapshot, 'estimate': project_estimate},
    }


def _read_list(path: pathlib.Path) -> list:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _requests(path: pathlib.Path, limit: int = 6) -> list:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    seen, out = set(), []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        key = (record.get('kind'), record.get('detail'))
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
        if len(out) >= limit:
            break
    return out


def _openrouter_balance() -> dict | None:
    """Read-only account balance, only when the operator exported the key.

    Uses the free ``GET /api/v1/credits`` endpoint. The key never enters this
    process's argv or the state files: it is passed to ``curl`` on stdin via
    ``-H @-``. Without ``OPENROUTER_API_KEY`` in the launching environment the
    balance is reported as unknown (this module never reads DSH's store).
    """
    import os
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        return None
    try:
        proc = subprocess.run(
            ['curl', '-sS', '--max-time', '10', 'https://openrouter.ai/api/v1/credits',
             '-H', '@-'], input=f'Authorization: Bearer {key}\n', capture_output=True,
            text=True, timeout=15, check=False,
            env={k: v for k, v in os.environ.items() if k != 'OPENROUTER_API_KEY'})
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        data = json.loads(proc.stdout or '{}').get('data') or {}
        total, used = float(data.get('total_credits', 0)), float(data.get('total_usage', 0))
    except (ValueError, TypeError, AttributeError):
        return None
    return {'remaining_usd': round(total - used, 2), 'total_usd': round(total, 2)}


def render(data: dict) -> str:
    state = data.get('state') or 'not started'
    lines = [f'## N.O.V.A. 3 — DSH worker: {STATE_EMOJI.get(state, "⚪")} {state}',
             '', f'_slot `{data["slot"]}` · {STATUS_NOTE.get(state, "")}_']
    if data.get('reason'):
        lines.append(f'\n**Why:** {data["reason"]}')
    endpoint = data.get('endpoint') or {}
    if endpoint:
        ok = 'reachable' if endpoint.get('ok') else f'unusable ({endpoint.get("reason")})'
        balance = data.get('balance')
        money = (f' · balance ≈ ${balance["remaining_usd"]:.2f} of ${balance["total_usd"]:.2f}'
                 if balance else ' · balance unknown here')
        lines.append(f'\n**Model route:** `{endpoint.get("route")}` / `{endpoint.get("model")}` '
                     f'via DSH {endpoint.get("version")} — {ok}{money}')
    if data.get('task'):
        lines.append(f'\n**Task:** #{data["task"]} {data.get("task_title") or ""}')
    last = data.get('last_outcome') or {}
    if last:
        lines.append(f'\n**Last session:** {last.get("status")} · steps {last.get("steps")} · '
                     f'{last.get("input_tokens")}+{last.get("output_tokens")} tokens · '
                     f'{last.get("elapsed_s")}s'
                     + (f' · `{last.get("error_code")}`' if last.get('error_code') else ''))
    if data.get('updated_ago'):
        lines.append(f'\n**Updated:** {data["updated_ago"]} ago'
                     + (f' · next check in {data["next_check_in"]}s'
                        if data.get('next_check_in') else ''))
        lines.append(f'**Sessions today:** {data.get("sessions_today")} · '
                     f'last hour {data.get("sessions_last_hour")} · '
                     f'tokens {data.get("tokens_today"):,}')
    requests = data.get('open_requests') or []
    if requests:
        lines += ['', '### Action needed'] + [
            f'- **{r.get("kind")}** {("(#" + str(r["task"]) + ") ") if r.get("task") else ""}\n'
            f'  - what: {r.get("detail")}\n  - do: {r.get("action")}'
            for r in requests]
    else:
        lines += ['', '**Action needed:** none']
    assessment = data.get('assessment') or {}
    queue = assessment.get('queue') or {}
    if queue:
        lines.append(f'\n**Queue:** {queue.get("runnable")} runnable · '
                     f'{queue.get("claimed")} claimed · {queue.get("review")} in review · '
                     f'{queue.get("blocked")} blocked · {queue.get("open_prs")} open PRs')
    project = data.get('project') or {}
    snapshot, estimate = project.get('snapshot'), project.get('estimate')
    if snapshot and estimate:
        inv = snapshot.get('inventory') or {}
        prog = snapshot.get('progress') or {}
        lines += ['', '### Project']
        coverage = inv.get('inventory_coverage')
        lines.append(
            f"Inventory: {inv.get('functions_recorded')} of {inv.get('elf_functions')} "
            f"ELF functions"
            + (f" ({coverage:.1%})" if isinstance(coverage, (int, float)) else '')
            + f" · complete: {'yes' if inv.get('inventory_complete') else 'NO'}")
        if prog.get('available'):
            lines.append(f"Mappings: {prog.get('mappings_implemented')} · matched ARM "
                         f"{prog.get('mappings_matched_arm')} · updated {prog.get('updated_at')}")
        scenarios = estimate.get('scenarios') or {}
        recorded = scenarios.get('recorded_inventory')
        if estimate.get('state') == 'unavailable':
            lines.append('ETA: unavailable — ' + '; '.join(estimate.get('reasons') or [])[:300])
        elif recorded:
            low, likely, high = recorded['days']
            lines.append(f"ETA (provisional): recorded inventory ~{likely:.0f} days "
                         f"({low:.0f}-{high:.0f}) at the measured mapping rate; re-baseline "
                         'after the pre-release source is indexed.')
    lines += ['', f'_state file: `{data["state_file"]}`_']
    return '\n'.join(lines)


def _gh(args: list[str], *, body: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(['gh', *args], input=body, capture_output=True, text=True,
                          timeout=60, check=False)


def publish(repo: str, body: str, number: int | None) -> int:
    """Write the page to a status issue (creating nothing unless asked)."""
    if number is None:
        listing = _gh(['issue', 'list', '-R', repo, '--state', 'open', '--label',
                       'nova:dashboard', '--limit', '20', '--json', 'number,title'])
        try:
            issues = json.loads(listing.stdout or '[]')
        except ValueError:
            issues = []
        for issue in issues:
            if 'live reconstruction status' in (issue.get('title') or '').lower():
                number = issue.get('number')
                break
    if number is None:
        print('no live status issue found; pass --issue NUMBER', file=sys.stderr)
        return 2
    result = _gh(['issue', 'comment', str(number), '-R', repo, '--body-file', '-'], body=body)
    if result.returncode:
        print(f'comment failed: {result.stderr[:300]}', file=sys.stderr)
        return 1
    print(f'published status to #{number}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='N.O.V.A. 3 DSH worker status')
    parser.add_argument('--repo', default=None)
    parser.add_argument('--slot', default='dsh')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--publish', action='store_true',
                        help='also post the page to the project status issue')
    parser.add_argument('--issue', type=int, default=None)
    args = parser.parse_args(argv)
    repo = args.repo
    if not repo:
        proc = subprocess.run(['gh', 'repo', 'view', '--json', 'nameWithOwner', '--jq',
                               '.nameWithOwner'], capture_output=True, text=True, check=False)
        repo = (proc.stdout or '').strip()
        if not repo:
            print('cannot determine the repository; pass --repo OWNER/REPO', file=sys.stderr)
            return 2
    data = collect(repo, args.slot)
    page = render(data)
    print(json.dumps(data, indent=2) if args.json else page)
    if args.publish:
        return publish(repo, page, args.issue)
    return 0


if __name__ == '__main__':
    sys.exit(main())
