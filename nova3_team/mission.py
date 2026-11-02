"""One JSON payload for the NOVA3 Mission tab in the DSH Web UI.

The tab replaced the `/nova` slash command.  It is a *read-only projection* of
the supervisor's durable state, the evidence-based project analysis, the
autopilot switch and the pre-release source reference.  It never starts a model,
never writes supervisor state and never reads a credential value; the only
mutation it can trigger is the explicit automation toggle, which the host route
performs by calling the audited ``nova3_team.autopilot`` CLI.

    python -m nova3_team.mission --root . --json
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

SCHEMA = 'nova3-mission/1'
SOURCE_ROOT = pathlib.Path('reference/nova3_source/NOVA3')
SOURCE_INDEX = pathlib.Path('coordination/source_index.json')


def _read_json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _next_task(root: pathlib.Path) -> str:
    """The first actionable line of NEXT.md, as a one-line banner."""
    try:
        lines = (root / 'docs/progress/NEXT.md').read_text(encoding='utf-8').splitlines()
    except OSError:
        return ''
    for line in lines:
        text = line.strip()
        if not text or text.startswith('#') or text.startswith('---'):
            continue
        if text.lower().startswith(('done:', 'next, in order:', 'next:')):
            continue
        return text[:240]
    return ''


def source_reference(root: pathlib.Path) -> dict:
    """Describe the extracted pre-release source reference without walking it on every poll."""
    base = root / SOURCE_ROOT
    index = _read_json(root / SOURCE_INDEX)
    present = base.is_dir()
    return {
        'present': present,
        'path': str(base),
        'indexed': bool(index),
        'index': index or None,
        'note': ('pre-release source is present and is a REFERENCE, not a build input; '
                 'index and re-baseline before trusting any ETA'
                 if present else 'pre-release source not materialised under reference/'),
    }


def collect(repo: str, root: pathlib.Path = '.', *, slot: str = 'dsh',
            now: float | None = None) -> dict:
    import subprocess
    from . import analysis, autopilot, dsh_status
    now = time.time() if now is None else now
    root = pathlib.Path(root).resolve()
    status = dsh_status.collect(repo, slot, now=now)
    snapshot = status.get('project', {}).get('snapshot') or {}
    estimate = status.get('project', {}).get('estimate') or {}
    progress = snapshot.get('progress') or {}
    inventory = snapshot.get('inventory') or {}
    autopilot_state = autopilot.state(repo, now=int(now))
    work_started = progress.get('work_started')
    elapsed_days = None
    try:
        started = datetime.date.fromisoformat(str(work_started))
        elapsed_days = (datetime.date.fromtimestamp(now) - started).days
    except (TypeError, ValueError):
        pass
    return {
        'schema': SCHEMA,
        'generated_unix': int(now),
        'generated_at': datetime.datetime.fromtimestamp(now).astimezone().isoformat(
            timespec='seconds'),
        'repo': repo,
        'worker': {
            'state': status.get('state'),
            'reason': status.get('reason'),
            'task': status.get('task'),
            'task_title': status.get('task_title'),
            'updated_unix': status.get('updated_unix'),
            'updated_ago': status.get('updated_ago'),
            'next_check_in': status.get('next_check_in'),
            'sessions_today': status.get('sessions_today'),
            'tokens_today': status.get('tokens_today'),
            'sessions_last_hour': status.get('sessions_last_hour'),
            'balance': status.get('balance'),
            'budget': status.get('budget'),
            'last_outcome': status.get('last_outcome'),
            'assessment': status.get('assessment'),
            'open_requests': status.get('open_requests'),
        },
        'autopilot': autopilot_state,
        'project': {
            'work_started': work_started,
            'elapsed_days': elapsed_days,
            'inventory': inventory,
            'progress': {key: value for key, value in progress.items() if key != 'notes'},
            'estimate': estimate,
            'completion': {
                'recorded_of_elf': inventory.get('inventory_coverage'),
                'mappings_implemented': progress.get('mappings_implemented'),
                'matched_arm': progress.get('mappings_matched_arm'),
                'behavioural': progress.get('mappings_behavioural'),
                'utilities_verified': progress.get('utilities_verified'),
                'source_modules': progress.get('source_modules'),
            },
        },
        'source_reference': source_reference(root),
        'next_task': _next_task(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 Mission tab payload')
    parser.add_argument('--root', default='.')
    parser.add_argument('--repo', default='N-O-V-A-3-RE-Contributors/nova3-reconstruction')
    parser.add_argument('--slot', default='dsh')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    payload = collect(args.repo, pathlib.Path(args.root), slot=args.slot)
    if args.json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
    else:
        worker = payload['worker']
        print(f"worker {worker['state']} · task {worker['task']} · "
              f"autopilot {payload['autopilot']['mode']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
