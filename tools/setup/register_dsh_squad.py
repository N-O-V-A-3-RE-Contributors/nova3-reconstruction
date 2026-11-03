#!/usr/bin/env python3
"""Register the NOVA3 reconstruction worker in DSH's native Agents tab.

The tab is the `@linxin666/dsh-agents` squad registry: `teams` (definitions with
members pinned to a preset, permission, model route and reasoning effort) and
`runs` (sessions launched from that tab). It can only show things the DSH Host
owns, so a systemd-managed headless worker cannot appear there as a live row.
What this script does is give the agent a *native, runnable definition*: the same
workspace, model route, effort and guardrails the durable supervisor uses, so it
can be inspected and launched from the Agents tab.

It talks to the plugin's own guarded HTTP API on this machine, never to the
ledger file directly (the Host owns that file's locking and revision). Re-running
is safe: an existing squad/member with the same name is left untouched.

    python tools/setup/register_dsh_squad.py --dry-run
    python tools/setup/register_dsh_squad.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:3080'
ORIGIN = BASE
SQUAD = 'NOVA3 Reconstruction'
WORKSPACE_PATH = '/home/jason/Projects/nova3_re'
DESCRIPTION = (
    'Durable source-reconstruction worker for N.O.V.A. 3. The systemd unit '
    'nova3-dsh-worker.service claims tasks from the GitHub queue, runs one '
    'bounded headless DSH session per task with task-aware model routing, then '
    'independently verifies, commits and opens a pull request. Live status: the '
    'Mission tab or `python -m nova3_team.mission`. This squad is the native, '
    'runnable definition; the supervisor itself is a service, not a Host session.'
)
MEMBERS = (
    {
        'name': 'reconstruction-worker',
        'preset': 'standard',
        'permission': 'workspace-write',
        'model': 'openrouter/deepseek/deepseek-v4.1-flash-1017',
        'reasoningEffort': 'high',
        'brief': (
            'You are the NOVA3 reconstruction worker for /home/jason/Projects/nova3_re. '
            'Work only inside the task Allowed paths. Never upload original APKs, ELFs '
            'or extracted game data to a third party, and never commit the private '
            'analysis trees. Prefer evidence: compile and test real source, mark '
            'unverified claims NOT MATCHED, and update docs/progress so the next '
            'session can resume without chat history.'
        ),
    },
    {
        'name': 'evidence-verifier',
        'preset': 'minimal',
        'permission': 'read-only',
        'model': 'openai-codex/gpt-6-astra',
        'reasoningEffort': 'high',
        'brief': (
            'Independently review submitted NOVA3 reconstruction work: re-run the '
            'tests the task declares, compare every claim against the recorded '
            'evidence, reject fabricated matches, and report only concrete findings '
            'with file and command evidence. Never modify source.'
        ),
    },
)


def call(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        BASE + path, data=data,
        headers={'Origin': ORIGIN, 'content-type': 'application/json'} if data
        else {'Origin': ORIGIN},
        method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        raise SystemExit(f'{path}: HTTP {error.code} {error.read()[:200]!r}')
    except urllib.error.URLError as error:
        raise SystemExit(f'{path}: cannot reach the DSH Web host ({error.reason})')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    state = call('/dsh-agents/api/state')
    workspaces = {item.get('path'): item.get('id') for item in state.get('workspaces') or []}
    workspace = workspaces.get(WORKSPACE_PATH)
    if not workspace:
        raise SystemExit(f'workspace {WORKSPACE_PATH} is not registered in DSH')
    team = next((item for item in state.get('teams') or []
                 if item.get('name') == SQUAD), None)
    plan = {'workspace': workspace, 'team': team.get('id') if team else None,
            'create_team': team is None}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    if team is None:
        created = call('/dsh-agents/api/team', {'team': {
            'name': SQUAD, 'description': DESCRIPTION,
            'workspaceId': workspace, 'leadPreset': 'standard', 'order': 0}})
        team = created.get('team') or {}
        plan['team'] = team.get('id')
    team_id = team.get('id')
    existing = {member.get('name') for member in team.get('members') or []}
    added = []
    for member in MEMBERS:
        if member['name'] in existing:
            continue
        call('/dsh-agents/api/member', {'teamId': team_id, 'member': member})
        added.append(member['name'])
    plan['added_members'] = added
    final = call('/dsh-agents/api/state')
    squad = next((item for item in final.get('teams') or []
                  if item.get('id') == team_id), {})
    plan['members'] = [{'name': m.get('name'), 'model': m.get('model'),
                        'reasoningEffort': m.get('reasoningEffort'),
                        'permission': m.get('permission')}
                       for m in squad.get('members') or []]
    plan['runs'] = len(final.get('runs') or [])
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
