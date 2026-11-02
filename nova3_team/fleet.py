"""Model/host routing: use each AI for what it is good at, within the host.

This is a *policy* layer, not a live switcher. It answers one question:
"given this task and this machine, which configured model should run it, and
how many heavy jobs may run at once?" A supervisor or planner can use the
answer; nothing here contacts a provider or changes a running session.

Inputs are the host (OS, cores, RAM), the models the operator actually
configured, and current load. Unknown models are never invented: an operator
must name models that exist in their own DSH credential store.
"""
from __future__ import annotations

import argparse
import json
import os
import platform

# Strengths are deliberately coarse and operational, not benchmark claims.
MODELS = {
    'deepseek': {'strengths': {'code', 'repo', 'cheap'}, 'context': 128_000,
                 'cost': 1, 'speed': 3},
    'codex': {'strengths': {'code', 'review', 'long-context'}, 'context': 200_000,
              'cost': 3, 'speed': 2},
    'claude': {'strengths': {'review', 'docs', 'long-context'}, 'context': 200_000,
               'cost': 3, 'speed': 2},
    'gemini': {'strengths': {'long-context', 'docs', 'vision'}, 'context': 1_000_000,
               'cost': 2, 'speed': 3},
    'local': {'strengths': {'cheap', 'offline'}, 'context': 32_000,
              'cost': 0, 'speed': 1},
}

TASK_NEEDS = {
    'recon': {'code'},
    'analysis': {'long-context'},
    'review': {'review'},
    'docs': {'docs'},
    'test': {'code'},
    'infra': {'code', 'repo'},
}


def host_profile(*, os_name: str | None = None, cpu: int | None = None,
                 ram_gb: float | None = None) -> dict:
    os_name = (os_name or platform.system() or 'unknown').lower()
    cpu = int(cpu or os.cpu_count() or 1)
    if ram_gb is None:
        ram_gb = _ram_gb()
    heavy = cpu >= 4 and ram_gb >= 8
    # One heavyweight Ghidra/IDA analysis at a time on a small laptop; scale
    # slowly, because each job is memory-bound before it is CPU-bound.
    max_parallel = 1 if not heavy else max(1, min(4, cpu // 4))
    return {'os': os_name, 'cpu': cpu, 'ram_gb': round(float(ram_gb), 1),
            'heavy_ok': heavy, 'max_parallel': max_parallel}


def _ram_gb() -> float:
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        size = os.sysconf('SC_PAGE_SIZE')
        return pages * size / (1024 ** 3)
    except (AttributeError, OSError, ValueError):
        return 16.0  # unknown: assume a normal desktop rather than a tiny one


def recommend(kind: str, *, host: dict, models: list[str],
              load: dict[str, int] | None = None) -> dict:
    """Pick a configured model for a task kind, and a safe parallelism."""
    if kind not in TASK_NEEDS:
        raise ValueError(f'unknown task kind: {kind}')
    if not models:
        raise ValueError('no configured models to choose from')
    needs = TASK_NEEDS[kind]
    load = load or {}
    ranked = []
    for name in models:
        profile = MODELS.get(name.lower())
        if profile is None:
            # Unknown route: eligible (the operator configured it) but unscored,
            # so it loses ties instead of silently winning.
            ranked.append((-1.0, name, 'unrecognised model; lowest confidence'))
            continue
        score = 3.0 * len(needs & profile['strengths'])
        score -= 0.4 * profile['cost']
        score += 0.2 * profile['speed']
        score -= 0.5 * int(load.get(name, 0))
        if kind in ('test', 'docs'):
            score += 0.5 if profile['cost'] <= 1 else 0
        ranked.append((score, name, f"strengths={sorted(profile['strengths'])}"))
    ranked.sort(reverse=True)
    score, model, reason = ranked[0]
    heavy = kind in ('recon', 'analysis')
    return {
        'kind': kind, 'model': model, 'score': round(score, 2),
        'reason': reason,
        'max_parallel': host['max_parallel'] if not heavy else 1,
        'heavy_ok': host['heavy_ok'],
        'warning': ('' if not heavy or host['heavy_ok']
                    else 'this host is below the 4-core / 8 GiB heavy-analysis floor; '
                         'run analysis elsewhere rather than thrash this machine'),
        'host': host,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 model/host routing recommendation')
    parser.add_argument('--task', default='recon', choices=sorted(TASK_NEEDS))
    parser.add_argument('--models', default='deepseek,codex,claude,gemini')
    parser.add_argument('--os', default=None)
    parser.add_argument('--cpu', type=int, default=None)
    parser.add_argument('--ram-gb', type=float, default=None)
    parser.add_argument('--load', default='', help='model=count,model=count')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    host = host_profile(os_name=args.os, cpu=args.cpu, ram_gb=args.ram_gb)
    models = [item.strip() for item in args.models.split(',') if item.strip()]
    load = {}
    for piece in args.load.split(','):
        if '=' in piece:
            key, _, value = piece.partition('=')
            try:
                load[key.strip()] = int(value)
            except ValueError:
                pass
    result = recommend(args.task, host=host, models=models, load=load)
    print(json.dumps(result, indent=2) if args.json
          else f"{result['model']} for {result['kind']} (max parallel "
               f"{result['max_parallel']}, heavy_ok={result['heavy_ok']})")
    if result['warning']:
        print(result['warning'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
