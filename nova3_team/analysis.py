"""Evidence-based project analysis and dynamic completion ETA.

Design rules:

* Every number is read from a recorded source (the function ledger, the
  progress snapshot, or the filesystem). Missing evidence produces an explicit
  ``unavailable`` state, never a guessed figure.
* An ETA is a *scenario*, not a promise. It carries the rate it used, the
  window it was measured over, a range and a confidence, and it names what
  would invalidate it.
* Recorded-inventory burn-down and full-ELF burn-down are reported separately:
  the inventory is NOT complete (1,165 of 39,092 functions), so treating the
  recorded inventory as the whole game would be the exact false claim this
  project forbids.
* The 2026-11 pre-release source the owner is supplying will change the
  inputs; the report says so instead of pretending the current baseline is
  final.

CLI::

    python -m nova3_team.analysis [--root .] [--json]
"""
from __future__ import annotations

from datetime import date, datetime
import json
import pathlib
import sys

SCHEMA = 'nova3-project-analysis/1'
PROGRESS = pathlib.Path('coordination/progress_state.json')
LEDGER = pathlib.Path('coordination/function_ledger.json')
EVIDENCE = pathlib.Path('coordination/evidence')
RECOVERED = pathlib.Path('reconstruction/recovered')
TESTS = pathlib.Path('reconstruction/tests')


def _load_json(path: pathlib.Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _count_glob(root: pathlib.Path, pattern: str) -> int:
    try:
        return sum(1 for _ in root.rglob(pattern))
    except OSError:
        return 0


def _day(value: object) -> date | None:
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def inventory(root: pathlib.Path) -> dict:
    """What is recorded, and how much of the binary it covers."""
    root = pathlib.Path(root)
    ledger = _load_json(root / LEDGER) or {}
    stats = ledger.get('statistics') or {}
    functions = ledger.get('functions') or []
    recorded = len(functions) if functions else int(stats.get('boundaries_extracted') or 0)
    elf_total = int(stats.get('elf_function_count') or 0)
    exported = int(stats.get('functions_with_ghidra_exports') or 0)
    complete = bool(stats.get('inventory_complete'))
    coverage = (recorded / elf_total) if elf_total else None
    return {
        'functions_recorded': recorded,
        'elf_functions': elf_total,
        'functions_with_exports': exported,
        'inventory_complete': complete,
        'inventory_coverage': coverage,
        'unrecorded_functions': max(0, elf_total - recorded) if elf_total else None,
    }


def progress(root: pathlib.Path) -> dict:
    """Recorded reconstruction progress, or its absence."""
    root = pathlib.Path(root)
    state = _load_json(root / PROGRESS) or {}
    return {
        'available': bool(state),
        'schema': state.get('schema'),
        'updated_at': state.get('updated_at'),
        'work_started': state.get('work_started'),
        'provenance': state.get('provenance'),
        'mappings_implemented': int(state.get('mappings_implemented') or 0),
        'mappings_matched_arm': int(state.get('mappings_matched_arm') or 0),
        'mappings_behavioural': int(state.get('mappings_behavioural') or 0),
        'utilities_verified': int(state.get('utilities_verified') or 0),
        'source_modules': int(state.get('source_modules') or 0),
        'notes': list(state.get('notes') or ()),
    }


def assets(root: pathlib.Path) -> dict:
    root = pathlib.Path(root)
    return {
        'evidence_files': _count_glob(root / EVIDENCE, '*.json'),
        'recovered_sources': _count_glob(root / RECOVERED, '*.cpp'),
        'recovered_headers': _count_glob(root / RECOVERED, '*.h'),
        'test_files': _count_glob(root / TESTS, 'test_*.py') + _count_glob(root / TESTS, '*test*.cpp'),
    }


def snapshot(root: pathlib.Path = '.') -> dict:
    root = pathlib.Path(root)
    inv, prog, files = inventory(root), progress(root), assets(root)
    return {'schema': SCHEMA, 'inventory': inv, 'progress': prog, 'assets': files}


def _rate(prog: dict) -> dict | None:
    """Mappings per day over the recorded window. None when unmeasurable."""
    if not prog.get('available') or not prog.get('mappings_implemented'):
        return None
    start, end = _day(prog.get('work_started')), _day(prog.get('updated_at'))
    if start is None or end is None or end <= start:
        return None
    days = (end - start).days
    implemented = prog['mappings_implemented']
    return {'mappings_per_day': implemented / days, 'days': days,
            'value': implemented, 'window': f'{start}..{end}'}


def _days(remaining: int, per_day: float) -> tuple[float, float, float] | None:
    if remaining <= 0 or per_day <= 0:
        return None
    likely = remaining / per_day
    # A rate measured over a short window is uncertain; show a band rather than
    # a single authoritative number.
    return (remaining / (per_day * 1.5), likely, remaining / (per_day * 0.5))


def estimate(snap: dict) -> dict:
    inv, prog = snap['inventory'], snap['progress']
    rate = _rate(prog)
    implemented = prog['mappings_implemented'] if prog.get('available') else None
    result = {
        'schema': SCHEMA, 'confidence': 'low', 'state': 'unavailable',
        'reasons': [], 'assumptions': [], 'scenarios': {},
        'rebaseline': 'The owner is supplying pre-release source; re-run after it is indexed.',
    }
    if not prog.get('available'):
        result['reasons'].append('coordination/progress_state.json is missing or unreadable')
    if not inv.get('functions_recorded'):
        result['reasons'].append('no function ledger entries were read')
    if inv.get('inventory_complete') is not True:
        result['reasons'].append(
            f"inventory is NOT complete: {inv.get('functions_recorded')} recorded of "
            f"{inv.get('elf_functions')} ELF functions")
    if rate is None:
        result['reasons'].append(
            'no measurable mapping rate yet: the progress snapshot needs a start date, '
            'an update date and a non-zero implemented count')
    if implemented is not None and inv.get('functions_recorded'):
        result['coverage_of_recorded'] = implemented / inv['functions_recorded']
    if inv.get('inventory_coverage') is not None:
        result['coverage_of_elf'] = inv['inventory_coverage']
    if rate is None:
        return result
    result['rate'] = rate
    known_remaining = max(0, int(inv.get('functions_recorded') or 0) - int(implemented or 0))
    if known_remaining:
        band = _days(known_remaining, rate['mappings_per_day'])
        if band:
            result['scenarios']['recorded_inventory'] = {
                'remaining': known_remaining, 'days': band,
                'label': 'finish only the functions already recorded',
            }
    unrecorded = int(inv.get('unrecorded_functions') or 0)
    if unrecorded:
        band = _days(unrecorded, rate['mappings_per_day'])
        if band:
            result['scenarios']['full_elf_at_recorded_rate'] = {
                'remaining': unrecorded, 'days': band,
                'label': ('naive full-ELF floor: assumes every unrecorded function needs the '
                          'same effort as a recorded mapping; the inventory itself is incomplete'),
            }
    result['assumptions'] = [
        'the mapping rate is assumed constant; it was measured over a short window',
        'one mapping is treated as one unit of completion; matching versus behavioural work differs',
        'inventory extraction continues in parallel and is not costed here',
    ]
    if inv.get('inventory_complete') is not True:
        result['state'] = 'provisional'
    else:
        result['state'] = 'measured'
    return result


def _human(days: float) -> str:
    if days < 45:
        return f'{days:.0f} days'
    if days < 730:
        return f'{days / 30.44:.1f} months'
    return f'{days / 365.25:.1f} years'


def render(snap: dict, est: dict) -> str:
    inv, prog = snap['inventory'], snap['progress']
    lines = ['NOVA3 project analysis', '=' * 22]
    lines.append(
        f"Inventory: {inv.get('functions_recorded')} recorded of {inv.get('elf_functions')} "
        f"ELF functions"
        + (f" ({inv['inventory_coverage']:.1%})" if inv.get('inventory_coverage') is not None else '')
        + f"; exports: {inv.get('functions_with_exports')}; complete: "
        + ('yes' if inv.get('inventory_complete') else 'NO'))
    if prog.get('available'):
        lines.append(
            f"Recorded progress ({prog.get('updated_at')}): {prog.get('mappings_implemented')} "
            f"mappings ({prog.get('mappings_matched_arm')} matched ARM, "
            f"{prog.get('mappings_behavioural')} behavioural, "
            f"{prog.get('utilities_verified')} utilities); source modules: "
            f"{prog.get('source_modules')}")
        if est.get('coverage_of_recorded') is not None:
            lines.append(
                f"Coverage: {est['coverage_of_recorded']:.1%} of recorded inventory, "
                f"{est.get('coverage_of_elf', 0):.2%} of the ELF function count")
    files = snap['assets']
    lines.append(
        f"Evidence files: {files['evidence_files']}; recovered sources: "
        f"{files['recovered_sources']}; tests: {files['test_files']}")
    lines.append('')
    if est['state'] == 'unavailable':
        lines.append('ETA: UNAVAILABLE')
        for reason in est['reasons']:
            lines.append(f"  - {reason}")
        return '\n'.join(lines)
    rate = est['rate']
    lines.append(
        f"Throughput ({est['state']}, confidence {est['confidence']}): "
        f"{rate['mappings_per_day']:.2f} mappings/day measured over {rate['days']} days "
        f"({rate['window']}). This is a rate, not an ETA: the deliverable is a game,")
    lines.append("not a count of mappings, and the inventory itself is incomplete.")
    for key, label in (('recorded_inventory', 'finish the recorded inventory only'),
                       ('full_elf_at_recorded_rate',
                        'NOT AN ESTIMATE: unrecorded functions at recorded rate')):
        scenario = est['scenarios'].get(key)
        if not scenario:
            continue
        low, likely, high = scenario['days']
        lines.append(
            f"  - {label}: {scenario['remaining']} remaining -> "
            f"{_human(likely)} likely ({_human(low)}-{_human(high)})")
    lines.append('Assumptions:')
    for item in est['assumptions']:
        lines.append(f"  - {item}")
    if est['reasons']:
        lines.append('Caveats:')
        for reason in est['reasons']:
            lines.append(f"  - {reason}")
    lines.append(f"Re-baseline: {est['rebaseline']}")
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='NOVA3 evidence-based project analysis and ETA')
    parser.add_argument('--root', default='.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    snap = snapshot(pathlib.Path(args.root))
    est = estimate(snap)
    if args.json:
        print(json.dumps({'snapshot': snap, 'estimate': est}, indent=2, sort_keys=True))
    else:
        print(render(snap, est))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
