"""Small operator CLI: inspect profiles and register their squad natively.

    python -m dsh_runner.cli profiles
    python -m dsh_runner.cli register --project nova3 [--dry-run]
    python -m dsh_runner.cli status --project nova3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import host, profile as profiles

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _profile(name: str):
    for profile in profiles.list_profiles(ROOT):
        if profile.name == name:
            return profile
    raise SystemExit(f'unknown project {name!r}')


def cmd_profiles(_args) -> int:
    print(json.dumps([p.summary() for p in profiles.list_profiles(ROOT)], indent=2))
    return 0


def cmd_register(args) -> int:
    profile = _profile(args.project)
    client = host.HostClient()
    workspace = client.workspace_id(profile.workspace)
    plan = {'project': profile.name, 'workspace': workspace,
            'squad': profile.execution.squad, 'members': sorted(profile.members)}
    if not workspace:
        raise SystemExit(f'{profile.workspace} is not a registered DSH workspace')
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    team = client.ensure_team(profile.execution.squad, workspace_id=workspace,
                              description=f'{profile.name}: {profile.repo}')
    for member in profile.members.values():
        client.ensure_member(team['id'], {
            'name': member.name, 'preset': member.preset,
            'permission': member.permission, 'model': member.model or None,
            'reasoningEffort': member.effort or None, 'brief': member.brief or None})
    team = client.team(profile.execution.squad) or team
    plan.update({'team_id': team.get('id'),
                 'registered': [m.get('name') for m in team.get('members') or []]})
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_status(args) -> int:
    profile = _profile(args.project)
    client = host.HostClient()
    try:
        state = client.state()
    except host.HostUnavailable as exc:
        print(json.dumps({'project': profile.name, 'host': 'unavailable',
                          'detail': str(exc)}, indent=2))
        return 3
    team = next((t for t in state.get('teams') or []
                 if t.get('name') == profile.execution.squad), None)
    runs = [r for r in state.get('runs') or []
            if team and r.get('teamId') == team.get('id')]
    print(json.dumps({'project': profile.name, 'backend': profile.execution.backend,
                      'squad_registered': bool(team), 'runs_recorded': len(runs),
                      'workspace_registered': bool(client.workspace_id(profile.workspace))},
                     indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='dsh_runner', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('profiles').set_defaults(func=cmd_profiles)
    register = sub.add_parser('register')
    register.add_argument('--project', required=True)
    register.add_argument('--dry-run', action='store_true')
    register.set_defaults(func=cmd_register)
    status = sub.add_parser('status')
    status.add_argument('--project', required=True)
    status.set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
