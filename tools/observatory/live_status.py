"""Truthful live-worker status for the NOVA3 Control Center.

Four independent signals are kept separate so a quiet structured feed can
never be reported as a stopped, failed or idle worker:

* systemd process state (ActiveState / SubState / MainPID / elapsed / cap);
* GitHub claim/lease freshness (task labels plus optional presence heartbeat);
* provider-log byte heartbeat (local journal bytes, last increase);
* structured observation age (sanitized worker summary files).

No raw provider output, prompt text, credential or chain-of-thought is read.
Everything here is a pure function over already-collected metadata so it can be
tested offline with fake systemd, GitHub and file data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

AI_SESSION_CAP_SECONDS = 20 * 60
STRUCTURED_QUIET_SECONDS = 60
LEASE_FRESH_SECONDS = 10 * 60
LOG_QUIET_SECONDS = 5 * 60
PROCESS_RUNNING = frozenset({'active', 'activating', 'reloading'})
SUCCESS_EXIT = '0'


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _age(now: datetime, value: Any) -> int | None:
    stamp = _parse(value)
    if stamp is None:
        return None
    return max(0, int((now - stamp).total_seconds()))


def _fmt_age(seconds: int | None) -> str:
    if seconds is None:
        return 'UNKNOWN'
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m'
    if seconds < 86400:
        return f'{seconds // 3600}h{(seconds % 3600) // 60:02d}m'
    return f'{seconds // 86400}d'


def process_status(service: dict | None, now: datetime, *,
                   ai_cap: int = AI_SESSION_CAP_SECONDS) -> dict:
    """Derive process truth from systemd metadata only.

    ``active`` is not inferred from log activity. A failed unit keeps its real
    exit status and is never silently labelled idle.
    """
    service = service if isinstance(service, dict) else {}
    active = str(service.get('active') or 'unknown').lower()
    sub = str(service.get('sub') or 'unknown')
    pid = str(service.get('pid') or '0')
    running = active in PROCESS_RUNNING and pid not in ('', '0')
    started = _parse(service.get('active_enter'))
    elapsed = int((now - started).total_seconds()) if started and now >= started else None
    exit_status = str(service.get('exit_status') or 'UNKNOWN')
    result = str(service.get('result') or '').lower()
    if running:
        status = 'RUNNING'
        detail = 'systemd reports the worker process alive'
    elif active in ('failed', 'degraded') or (result not in ('', 'success') and active not in PROCESS_RUNNING):
        status = 'FAILED'
        detail = f'unit {active}; original exit/status {exit_status}'
    elif active in ('inactive', 'stopped'):
        status = 'STOPPED'
        detail = 'unit inactive (not a structured-feed judgement)'
    else:
        status = 'UNKNOWN'
        detail = 'systemd state could not be read'
    return {
        'status': status, 'active': active, 'sub': sub, 'pid': pid,
        'started_at': started.isoformat() if started else None,
        'elapsed_seconds': elapsed, 'elapsed': _fmt_age(elapsed),
        'exit_status': exit_status, 'result': result,
        'ai_cap_seconds': ai_cap,
        'ai_cap_remaining_seconds': (max(0, ai_cap - elapsed)
                                     if running and elapsed is not None else None),
        'source': 'systemd --user show',
        'detail': detail,
    }


def lease_status(issues: list[dict] | None, presence: list[dict] | None,
                 now: datetime, *, fresh_seconds: int = LEASE_FRESH_SECONDS) -> dict:
    """Read the GitHub task lease and optional presence heartbeat.

    A claim is a *lease*, not proof of progress. Presence is reported separately
    and may be missing on older workers without implying they are offline.
    """
    claimed = []
    for issue in issues or []:
        labels = {str(item.get('name', '')) for item in issue.get('labels', [])
                  if isinstance(item, dict)}
        if 'nova:claimed' not in labels:
            continue
        stamp = issue.get('updatedAt') or issue.get('createdAt')
        age = _age(now, stamp)
        claimed.append({
            'issue': issue.get('number'),
            'age_seconds': age,
            'fresh': age is not None and age <= fresh_seconds,
            'source': 'GitHub issue labels/updatedAt',
        })
    claimed.sort(key=lambda item: (item['issue'] is None, item['issue'] or 0))
    active = claimed[0] if claimed else None
    beats = []
    for record in presence or []:
        if not isinstance(record, dict):
            continue
        age = _age(now, record.get('updated'))
        beats.append({'owner': str(record.get('owner') or 'UNKNOWN')[:80],
                      'state': str(record.get('state') or 'UNKNOWN')[:16],
                      'issue': record.get('issue'), 'age_seconds': age,
                      'age': _fmt_age(age),
                      'fresh': age is not None and age <= fresh_seconds,
                      'source': 'GitHub presence comment'})
    beats.sort(key=lambda item: (item['age_seconds'] is None, item['age_seconds'] or 0))
    beat = beats[0] if beats else None
    if active is None:
        state = 'NO CLAIM'
    elif active['fresh']:
        state = 'CLAIMED'
    else:
        state = 'CLAIM STALE'
    return {
        'state': state,
        'issue': active['issue'] if active else None,
        'age_seconds': active['age_seconds'] if active else None,
        'age': _fmt_age(active['age_seconds']) if active else 'UNKNOWN',
        'fresh': bool(active and active['fresh']),
        'presence': beat,
        'presence_label': (f"{beat['state']} {beat['age']} ago" if beat else
                           'UNKNOWN (older worker or not reported)'),
        'source': 'GitHub labels + presence comment',
    }


def log_status(log_health: dict | None, now: datetime, *,
               quiet_seconds: int = LOG_QUIET_SECONDS) -> dict:
    """Provider journal byte heartbeat, independent of structured summaries."""
    log_health = log_health if isinstance(log_health, dict) else {}
    size = log_health.get('bytes')
    size = size if type(size) is int and size >= 0 else None
    age = _age(now, log_health.get('changed_at'))
    delta = log_health.get('delta')
    delta = delta if type(delta) is int else None
    if size is None:
        state = 'MISSING'
    elif age is None:
        state = 'UNKNOWN'
    elif age <= quiet_seconds:
        state = 'GROWING'
    else:
        state = 'NO GROWTH'
    return {
        'state': state, 'bytes': size, 'delta': delta,
        'changed_at': log_health.get('changed_at'),
        'age_seconds': age, 'age': _fmt_age(age),
        'quiet': state in ('MISSING', 'NO GROWTH'),
        'source': str(log_health.get('source') or 'local systemd journal bytes'),
    }


def feed_status(activity: dict | None, now: datetime, *,
                quiet_seconds: int = STRUCTURED_QUIET_SECONDS) -> dict:
    """Age of the newest sanitized structured event, never a health verdict."""
    activity = activity if isinstance(activity, dict) else {}
    newest = None
    for record in activity.get('records', []) or []:
        stamp = _parse(record.get('timestamp_utc')) if isinstance(record, dict) else None
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    age = max(0, int((now - newest).total_seconds())) if newest else None
    if age is None:
        state = 'MISSING'
    elif age <= quiet_seconds:
        state = 'LIVE'
    else:
        state = 'QUIET'
    return {
        'state': state, 'age_seconds': age, 'age': _fmt_age(age),
        'quiet': age is not None and age > quiet_seconds,
        'missing': age is None,
        'threshold_seconds': quiet_seconds,
        'source': 'sanitized round-*.activity.json',
    }


def live_health(process: dict, lease: dict, provider: dict, feed: dict,
                now: datetime | None = None) -> dict:
    """Combine independent signals without conflating quiet output and failure.

    Rule: while systemd proves the process alive the overall verdict stays
    RUNNING. A >=60s structured-event gap becomes a ``STRUCTURED-EVENT QUIET``
    sub-badge with an explanatory sentence, not STOPPED/IDLE/FAILED.
    """
    now = now or datetime.now(timezone.utc)
    badges = []
    notes = []
    if process['status'] == 'RUNNING':
        verdict = 'RUNNING'
        if feed['quiet'] and not feed['missing']:
            badges.append('STRUCTURED-EVENT QUIET')
            notes.append(f"No new structured event for {feed['age']} — process still running.")
        elif feed['missing']:
            badges.append('NO STRUCTURED FEED')
            notes.append('Process is running; no compatible structured event has been observed.')
        if provider['state'] in ('NO GROWTH', 'MISSING'):
            badges.append('LOG-BYTE QUIET' if provider['state'] == 'NO GROWTH' else 'LOG MISSING')
            notes.append('Journal byte count is not increasing; this alone does not prove a stall.')
        if lease['state'] == 'CLAIM STALE':
            badges.append('CLAIM AGE STALE')
            notes.append('The GitHub claim is older than its freshness window; check the worker.')
        elif lease['state'] == 'NO CLAIM' and lease.get('presence') is None:
            notes.append('No current GitHub claim or presence heartbeat is visible.')
    else:
        verdict = process['status']
        badges.append(verdict)
        if process['status'] == 'FAILED':
            notes.append(f"Original unit result: exit {process['exit_status']} "
                         f"({process['result'] or process['active']}). Review the journal before restart.")
        elif process['status'] == 'STOPPED':
            notes.append('systemd says the process is not running; this is not inferred from log silence.')
        else:
            notes.append('Process state is UNKNOWN; no restart or stop will be inferred from activity.')
        if feed['quiet'] and not feed['missing']:
            badges.append('FEED QUIET')
    stalled = (process['status'] == 'RUNNING' and provider['state'] == 'NO GROWTH'
               and feed['quiet'] and lease['state'] == 'CLAIM STALE')
    if stalled:
        badges.append('POSSIBLE STALL')
        notes.append('No structured event, no log-byte growth and a stale claim: check the journal.')
    return {
        'verdict': verdict,
        'badges': badges,
        'badge': ' · '.join(badges) if badges else 'LIVE',
        'notes': notes,
        'stalled': stalled,
        'structured_quiet': bool(process['status'] == 'RUNNING' and feed['quiet']),
        'checked_at': now.isoformat(),
    }


def collect_live_status(snapshot, now: datetime | None = None, *,
                        ai_cap: int = AI_SESSION_CAP_SECONDS) -> dict:
    """Build the four-signal model from a Snapshot-like object."""
    now = now or datetime.now(timezone.utc)
    process = process_status(getattr(snapshot, 'service', {}), now, ai_cap=ai_cap)
    lease = lease_status(getattr(snapshot, 'issues', []),
                         getattr(snapshot, 'worker_presence', []), now)
    provider = log_status(getattr(snapshot, 'log_health', {}), now)
    feed = feed_status(getattr(snapshot, 'activity', {}), now)
    return {'process': process, 'lease': lease, 'provider': provider,
            'feed': feed, 'health': live_health(process, lease, provider, feed, now)}


def summary_rows(model: dict, *, provenance: bool = True) -> list[tuple[str, str]]:
    """Compact card rows used by PULSE / ACTIVITY without raw log content."""
    p, lease, prov, feed = (model['process'], model['lease'],
                            model['provider'], model['feed'])
    health = model['health']
    rows: list[tuple[str, str]] = [
        (f"PROCESS {p['status']} | {p['active']}/{p['sub']} | PID {p['pid']}", 'green' if p['status'] == 'RUNNING' else 'red'),
        (f"Elapsed {p['elapsed']} | AI session cap {p['ai_cap_seconds'] // 60}m"
         + (f" | {p['ai_cap_remaining_seconds']}s left" if p.get('ai_cap_remaining_seconds') is not None else ''),
         'muted'),
        (f"LEASE {lease['state']}"
         + (f" #{lease['issue']} age {lease['age']}" if lease.get('issue') else '')
         + f" | presence {lease['presence_label']}", 'cyan' if lease['fresh'] else 'yellow'),
        (f"PROVIDER LOG {prov['state']} | {prov['bytes'] if prov['bytes'] is not None else 'UNKNOWN'} bytes"
         f" | last increase {prov['age']}" + (f" (+{prov['delta']})" if prov['delta'] else ''), 'cyan' if not prov['quiet'] else 'yellow'),
        (f"STRUCTURED FEED {feed['state']} | newest event {feed['age']} ago", 'cyan' if not feed['quiet'] else 'yellow'),
    ]
    if provenance:
        rows.append((f"Sources: {p['source']} | {lease['source']} | {prov['source']} | {feed['source']}", 'muted'))
    for note in health['notes'][:4]:
        rows.append((note, 'yellow' if health['verdict'] != 'RUNNING' else 'muted'))
    return rows
