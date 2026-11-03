#!/usr/bin/env python3
"""Read-only task-state census over coordination/tasks.json and evidence overlays.

BOOKKEEPING ONLY.  This census counts and cross-references what the committed
JSON files say; it is NOT reconstruction evidence and NOT equivalence evidence.
It never interprets, upgrades or relabels a verification grade.

What it computes from the JSON objects it is handed (no globbing, no ELF, no
network, writes nothing):

* ``task_status``       -- task counts by ``status`` from tasks.json.
* ``tasks_with_overlay``  -- tasks having at least one overlay keyed by
  ``task_key`` or ``task_id`` equal to the task id.
* ``orphans``           -- overlays whose key matches no task (each key once).
* ``distinct_task_keys`` -- distinct task keys the overlays reference (linked
  or orphan).
* ``overlay_buckets``   -- ``{overlay label: grade bucket}``, one entry per
  overlay; ``grade_counts`` gives every bucket including zeros.
  A missing top-level grade field is ``unstated``.  Case-insensitive
  ``unverified`` / ``not matched`` (optionally followed by whitespace, ``:``
  or ``;`` and a note) are ``not_matched``.  A bare ``matched`` is
  ``matched_recording`` -- merely the file's own claim, recorded, not verified
  by this tool.  Anything else, including ``UNKNOWN`` and ``MATCHED: ...``
  notes, is ``other_uninterpreted`` and is never counted as matched.
* ``duplicates``        -- every key referenced by more than one overlay.  Each
  key is still counted once as linked; the list names itself as duplicates.

Usage::

    python -m tools.compare.task_state_census coordination/tasks.json \\
        coordination/evidence/*.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BOOKKEEPING_NOTICE = (
    "Bookkeeping only: this census is not reconstruction and not equivalence evidence."
)

GRADE_BUCKETS = (
    "unstated",
    "not_matched",
    "matched_recording",
    "other_uninterpreted",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

NEGATIVE_RE = re.compile(r"^(?:not matched|unverified)(?:\s*[:;,\s].*)?$", re.DOTALL)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalise_key(value: Any) -> Optional[str]:
    """Return a canonical key string, or None when the value is unusable."""
    if isinstance(value, str):
        return value if value else None
    if _is_int(value):
        return str(value)
    return None


def _task_list(tasks_doc: Any) -> List[Mapping[str, Any]]:
    if isinstance(tasks_doc, Mapping):
        tasks = tasks_doc.get("tasks")
    else:
        tasks = tasks_doc
    if tasks is None:
        return []
    if not isinstance(tasks, (list, tuple)):
        raise TypeError("tasks_doc must be a list of task dicts or a dict with a 'tasks' list")
    out = []
    for item in tasks:
        if not isinstance(item, Mapping):
            raise TypeError("each task must be a mapping")
        out.append(item)
    return out


def _overlay_key(overlay: Mapping[str, Any]) -> Optional[str]:
    """``task_key`` first, then ``task_id``; both shapes are recognised."""
    for field in ("task_key", "task_id"):
        if field in overlay:
            key = _normalise_key(overlay.get(field))
            if key is not None:
                return key
    return None


def _match_key(task: Mapping[str, Any]) -> Optional[str]:
    if "id" not in task:
        return None
    return _normalise_key(task.get("id"))


def classify_grade(grade: Any, present: bool = True) -> str:
    """Bucket a top-level overlay grade without ever upgrading it.

    Only a bare ``matched`` (any case) is ``matched_recording``.  ``UNKNOWN`` and any
    value this tool does not understand land in ``other_uninterpreted``, which
    is not a verified bucket.  The census never maps anything *into* a
    verified bucket on its own authority; ``matched_recording`` merely records
    that the file itself claims a match.
    """
    if not present or grade is None:
        return "unstated"
    if not isinstance(grade, str):
        return "other_uninterpreted"
    text = grade.strip()
    if not text:
        return "unstated"
    lower = text.lower()
    if lower == "matched":
        return "matched_recording"
    # Anything else (including mixed-case "MATCHED ..." notes) is never
    # promoted; only a bare, case-insensitive 'matched' records a claim.
    if lower == "unverified" or NEGATIVE_RE.match(lower):
        return "not_matched"
    return "other_uninterpreted"


def _grade_of(overlay: Mapping[str, Any]) -> Tuple[Any, bool]:
    if "grade" in overlay:
        return overlay.get("grade"), True
    if "verification_grade" in overlay:
        return overlay.get("verification_grade"), True
    return None, False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def census(
    tasks_doc: Any,
    overlays: Iterable[Any],
) -> Dict[str, Any]:
    """Compute the deterministic census from in-memory JSON objects.

    ``tasks_doc`` is either the parsed tasks.json dict (``{"tasks": [...]}``)
    or the bare task list.  ``overlays`` is an iterable of overlay dicts; an
    optional ``__path__`` / ``__name__`` string is used as the overlay's label
    only and never affects matching.

    No file, environment, network or process access happens here.

    The result is identical for any permutation of ``tasks`` or ``overlays``:
    every list is sorted and every dict is key-sorted.
    """
    tasks = _task_list(tasks_doc)
    overlay_list = []
    for item in overlays or ():
        if not isinstance(item, Mapping):
            raise TypeError("each overlay must be a mapping")
        overlay_list.append(item)

    # Task ids -> status.  A repeated id keeps the lexicographically first
    # status so the result never depends on input order.
    status_of: Dict[str, List[str]] = {}
    unkeyed_tasks = 0
    for task in tasks:
        key = _match_key(task)
        if key is None:
            unkeyed_tasks += 1
            continue
        status = task.get("status")
        status = status if isinstance(status, str) and status else "unknown"
        status_of.setdefault(key, [])
        if status not in status_of[key]:
            status_of[key].append(status)
    task_status_table = {
        key: sorted(value)[0] for key, value in status_of.items()
    }
    task_keys = set(status_of)

    linked_keys: set = set()
    overlay_key_counts: Dict[str, int] = {}
    named_overlays: List[Tuple[str, Optional[str]]] = []
    for index, overlay in enumerate(overlay_list):
        key = _overlay_key(overlay)
        if key is not None:
            overlay_key_counts[key] = overlay_key_counts.get(key, 0) + 1
            if key in task_keys:
                linked_keys.add(key)
        name: Optional[str] = None
        for field in ("__path__", "__name__", "path", "file"):
            candidate = overlay.get(field)
            if isinstance(candidate, str) and candidate:
                # Basename only: keeps labels stable and path-free on output.
                name = candidate.replace("\\", "/").rsplit("/", 1)[-1]
                break
        if name is None:
            name = f"overlay[{index}]"
        named_overlays.append((name, key))

    all_referenced = set(overlay_key_counts)
    orphan_keys = sorted(all_referenced - task_keys)

    # Grade buckets: one entry per overlay, with its file basename as label.
    # Distinct overlays that share a basename get an index suffix.
    grade_counts = {bucket: 0 for bucket in GRADE_BUCKETS}
    label_counts: Dict[str, int] = {}
    labels: List[str] = []
    index_buckets: List[Tuple[str, str]] = []
    for index, overlay in enumerate(overlay_list):
        bucket = classify_grade(*_grade_of(overlay))
        grade_counts[bucket] += 1
        base = named_overlays[index][0]
        label_counts[base] = label_counts.get(base, 0) + 1
        label = base if label_counts[base] == 1 else f"{base}#{index}"
        labels.append(label)
        index_buckets.append((label, bucket))
    grade_counts = dict(sorted(grade_counts.items()))

    duplicates: Dict[str, Dict[str, Any]] = {}
    by_key: Dict[str, List[Tuple[str, int]]] = {}
    for index, (name, key) in enumerate(named_overlays):
        if key is not None:
            by_key.setdefault(key, []).append((name, index))
    for key, items in by_key.items():
        if len(items) < 2:
            continue
        duplicates[key] = {
            "count": len(items),
            # One task link for the key; the remaining overlays are duplicates.
            "duplicate_count": len(items) - 1,
            "linked": key in task_keys,
            "overlays": sorted(index_buckets[i][0] for _, i in items),
            # Each duplicate keeps its own recorded grade bucket.
            "buckets": sorted(index_buckets[i][1] for _, i in items),
        }

    status_counts: Dict[str, int] = {}
    for task in tasks:
        status = task.get("status")
        bucket = status if isinstance(status, str) and status else "unknown"
        status_counts[bucket] = status_counts.get(bucket, 0) + 1

    task_table: Dict[str, Any] = {}
    for key in sorted(task_keys):
        task_table[key] = {
            "status": task_status_table[key],
            "overlay_count": overlay_key_counts.get(key, 0),
            "overlays": sorted(
                name for name, k in named_overlays if k == key
            ),
        }

    return {
        "bookkeeping_only": True,
        "notice": BOOKKEEPING_NOTICE,
        "counts": {
            "tasks_total": len(tasks),
            "tasks_without_id": unkeyed_tasks,
            "task_status": dict(sorted(status_counts.items())),
            "overlays_total": len(overlay_list),
            "keyed_overlays": sum(overlay_key_counts.values()),
            "tasks_with_overlay": len(linked_keys),
            "tasks_without_overlay": len(task_keys - linked_keys),
            "tasks_without_overlay_keys": sorted(task_keys - linked_keys),
            "orphans": len(orphan_keys),
            "distinct_task_keys": len(all_referenced),
            "duplicate_keys": len(duplicates),
            "unkeyed_overlays": len(overlay_list) - sum(overlay_key_counts.values()),
        },
        "orphans": orphan_keys,
        "duplicates": dict(sorted(duplicates.items())),
        "overlay_buckets": dict(sorted(index_buckets)),
        "grade_buckets": grade_counts,
        "tasks": task_table,
    }


def render_markdown(result: Mapping[str, Any]) -> str:
    """Render the census as a deterministic Markdown table block."""
    counts = result["counts"]
    rows: List[Tuple[str, int]] = []
    for status, n in counts["task_status"].items():
        rows.append((f"task_status:{status}", n))
    rows.append(("tasks_total", counts["tasks_total"]))
    rows.append(("overlays_total", counts["overlays_total"]))
    rows.append(("tasks_with_overlay", counts["tasks_with_overlay"]))
    rows.append(("tasks_without_overlay", counts["tasks_without_overlay"]))
    rows.append(("orphans", counts["orphans"]))
    rows.append(("unkeyed_overlays", counts["unkeyed_overlays"]))
    rows.append(("distinct_task_keys", counts["distinct_task_keys"]))
    rows.append(("duplicate_keys", counts["duplicate_keys"]))
    for bucket, n in result["grade_buckets"].items():
        rows.append((f"grade:{bucket}", n))

    lines = [
        f"> {BOOKKEEPING_NOTICE}",
        "",
        "| bucket | count |",
        "| --- | ---: |",
    ]
    for name, n in rows:
        lines.append(f"| {name} | {n} |")
    if result["orphans"]:
        lines.append("")
        lines.append("Orphan overlay keys (no matching task):")
        for key in result["orphans"]:
            lines.append(f"- `{key}`")
    if result["duplicates"]:
        lines.append("")
        lines.append("Duplicate overlay keys (counted once as linked):")
        for key, info in result["duplicates"].items():
            # One task link for the key; the extra overlays are the duplicates.
            lines.append(
                f"- `{key}`: {info['count']} overlays ({info['count'] - 1} duplicate), "
                f"linked={str(info['linked']).lower()}"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_document(path: Path) -> Any:
    """Load one JSON document and tag dicts with their file name."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        tagged = dict(data)
        tagged.setdefault("__path__", path.name)
        return tagged
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("__path__", path.name)
            out.append(item)
        return out
    raise ValueError(f"{path}: expected a JSON object or array")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare.task_state_census",
        description="Read-only task-state census (bookkeeping only; no ELF, no network, no writes).",
    )
    parser.add_argument("tasks_json", help="path to coordination/tasks.json")
    parser.add_argument(
        "overlays",
        nargs="*",
        help="evidence overlay JSON files (coordination/evidence/*.json)",
    )
    parser.add_argument("--json", action="store_true", help="print the census as JSON")
    args = parser.parse_args(argv)

    tasks_path = Path(args.tasks_json)
    try:
        tasks_doc = _load_document(tasks_path)
        overlay_docs = []
        for raw in args.overlays:
            data = _load_document(Path(raw))
            if isinstance(data, list):
                overlay_docs.extend(data)
            else:
                overlay_docs.append(data)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = census(tasks_doc, overlay_docs)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
