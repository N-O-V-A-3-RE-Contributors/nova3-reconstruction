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


def agent_state(repo: str, slot: str = 'dsh', *, now: float | None = None) -> dict:
    """The LIVE headless agent, not the Web UI's own session list.

    The supervisor's model sessions run as `dsh --profile headless` children of
    the systemd unit, so they never appear as Web-profile agents in the DSH UI.
    This projection is what makes them visible: durable supervisor state, the
    router's selected route, and real activity from the private round log.
    """
    from . import worker
    now = time.time() if now is None else now
    blank = {'running': False, 'pid': None, 'state': None, 'task': None,
             'title': '', 'provider': None, 'model': None, 'purpose': None,
             'router_status': None, 'session_id': None, 'steps': 0,
             'tool_calls': 0, 'last_event_age_s': None, 'log_bytes': 0,
             'cooldowns': {}, 'catalog': [], 'checkout': None}
    try:
        paths = worker.slot_paths(worker.state_root(repo), slot)
    except Exception:
        return blank
    state_dir = pathlib.Path(paths.state)
    supervisor = _read_json(state_dir / 'supervisor.json')
    router = _read_json(state_dir / 'model-router' / 'status.json')
    task = supervisor.get('task') or {}
    pid = supervisor.get('supervisor_pid')
    live = False
    if isinstance(pid, int) and pid > 0:
        try:
            children = pathlib.Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
            for child in children:
                argv = pathlib.Path(f'/proc/{child}/cmdline').read_bytes()
                if b'dsh' in argv and b'--json' in argv:
                    live = True
                    break
        except OSError:
            live = False
    steps = tools = 0
    age = None
    size = 0
    logs = []
    try:
        logs = sorted(state_dir.rglob('round-*.log'),
                      key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        logs = []
    if logs:
        newest = logs[0]
        try:
            stat = newest.stat()
            size = stat.st_size
            age = max(0.0, now - stat.st_mtime)
            with newest.open('rb') as handle:
                if size > 2 * 1024 * 1024:
                    handle.seek(size - 2 * 1024 * 1024)
                for raw in handle:
                    if not raw.startswith(b'{'):
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get('type') == 'tool_call':
                        tools += 1
                    elif event.get('phase') == 'step_end':
                        steps += 1
        except OSError:
            pass
    cooldowns = {}
    for route, until in (router.get('cooldowns') or {}).items():
        try:
            left = int(until) - int(now)
        except (TypeError, ValueError):
            continue
        if left > 0:
            cooldowns[route] = left
    active = router.get('active') or {}
    return {
        'running': live, 'pid': pid if live else None,
        'state': supervisor.get('state'),
        'task': task.get('number'), 'title': str(task.get('title') or ''),
        'provider': active.get('provider'), 'model': active.get('model'),
        'purpose': router.get('purpose'), 'router_status': router.get('status'),
        'session_id': router.get('session_id'),
        'steps': steps, 'tool_calls': tools,
        'last_event_age_s': round(age, 1) if age is not None else None,
        'log_bytes': size,
        'cooldowns': cooldowns,
        'catalog': [f"{c.get('provider')}/{c.get('model')}"
                    for c in router.get('catalog') or [] if isinstance(c, dict)],
        'checkout': (str(paths.state / str(supervisor.get('task_checkout') or ''))
                     if supervisor.get('task_checkout') else None),
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
        'agent': agent_state(repo, slot, now=now),
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
        agent = payload.get('agent') or {}
        print(f"worker {worker['state']} · task {worker['task']} · "
              f"autopilot {payload['autopilot']['mode']}")
        print(f"agent {'RUNNING' if agent.get('running') else 'idle'}"
              + (f" · {agent.get('provider')}/{agent.get('model')}"
                 f" · {agent.get('steps')} steps · "
                 f"last event {agent.get('last_event_age_s')}s ago"
                 if agent.get('running') else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
