#!/usr/bin/env python3
"""Resumable, unattended batch decompilation driver for a reference ELF.

This is the "start it and walk away" entry point the reconstruction workflow was
missing: `tools/index/inventory.py` builds the function index and
`tools/ghidra/export_batch.py` exports one bounded batch, but nothing chained
them, nothing checkpointed between batches, and a missing `analysis_db/` aborted
the first command on a clean checkout.

What it does, in order:

  1. hashes the reference ELF and refuses to proceed when `--expected-sha256`
     is supplied and does not match (a moved/changed binary invalidates every
     export keyed on the old hash);
  2. bootstraps `analysis_db/index.sqlite` by running `tools/index/inventory.py`
     when the index is missing or has no rows for that binary;
  3. repeatedly runs `tools/ghidra/export_batch.py` with a bounded batch size,
     counting newly written `generated/ghidra/exports/<sha>/<addr>.json` files,
     with a per-batch timeout and a JSON checkpoint after every batch;
  4. stops when no further progress is possible and prints a coverage summary.

Every batch is a separate process, so a crash, a timeout, or a killed session
loses at most one batch; re-running resumes from the on-disk export directory.
No analysed bytes are emitted to stdout and nothing proprietary is written
outside the gitignored `generated/` and `.cache/` trees.

Examples:
    .venv/bin/python tools/pipeline/auto_decompile.py --status
    .venv/bin/python tools/pipeline/auto_decompile.py --max-functions 8 --batch-size 4
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
DB = ROOT / "analysis_db/index.sqlite"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def index_counts(digest: str) -> tuple[int, int]:
    """(functions indexed, exports written) for one binary hash."""
    if not DB.exists():
        return 0, 0
    db = sqlite3.connect(DB)
    try:
        total = db.execute(
            "SELECT COUNT(*) FROM functions WHERE binary_hash=?", (digest,)
        ).fetchone()[0]
    finally:
        db.close()
    exported = len(list((ROOT / "generated/ghidra/exports" / digest).glob("*.json")))
    return total, exported


def ensure_index(binary: Path, log) -> None:
    digest = sha256(binary)
    total, _ = index_counts(digest)
    if total:
        return
    log("index: no rows for %s; running tools/index/inventory.py" % digest[:12])
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/index/inventory.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index bootstrap failed (exit %d):\n%s" % (proc.returncode, proc.stderr[-2000:])
        )
    total, _ = index_counts(digest)
    if not total:
        raise SystemExit("index bootstrap produced no functions for %s" % digest)


def run_batch(binary: Path, batch_size: int, timeout: int, log, project_dir: str) -> tuple[int, str]:
    """Run one export batch; return (new exports, status)."""
    before = len(list((ROOT / "generated/ghidra/exports" / sha256(binary)).glob("*.json")))
    cmd = [
        sys.executable, str(ROOT / "tools/ghidra/export_batch.py"),
        "--binary", str(binary), "--start", "0", "--limit", str(batch_size),
        "--all-symbols", "--project-dir", project_dir,
    ]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("batch: TIMEOUT after %ds (limit=%d)" % (timeout, batch_size))
        return 0, "timeout"
    elapsed = time.time() - started
    after = len(list((ROOT / "generated/ghidra/exports" / sha256(binary)).glob("*.json")))
    new = after - before
    log("batch: %+d exports in %.1fs (rc=%d)" % (new, elapsed, proc.returncode))
    if proc.returncode != 0:
        tail = (proc.stdout[-800:] + proc.stderr[-800:]).strip()
        log("batch: rc=%d tail=%r" % (proc.returncode, tail))
        return new, "error"
    if new == 0:
        tail = (proc.stdout[-800:] + proc.stderr[-800:]).strip()
        log("batch: no progress; tail=%r" % (tail,))
        return 0, "stalled"
    return new, "ok"


def lock_path() -> Path:
    return ROOT / ".cache/pipeline/auto_decompile.lock"


def acquire_lock(binary_label: str) -> bool:
    """Take the single-writer lock; False when another live run holds it.

    Two concurrent runs would share the SQLite index and the Ghidra project, and
    a killed session leaves a lock behind — so a holder whose PID is gone is
    treated as stale and replaced instead of blocking the next run forever.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = None
    if path.exists():
        try:
            holder = int(json.loads(path.read_text()).get("pid"))
        except (OSError, ValueError, TypeError, AttributeError):
            holder = None
        if holder and holder != os.getpid():
            try:
                os.kill(holder, 0)
                return False
            except OSError:
                holder = None  # stale: the process that owned it is gone
    path.write_text(json.dumps({"pid": os.getpid(), "started_at": utcnow(),
                                "binary": binary_label}) + "\n")
    return True


def release_lock() -> None:
    path = lock_path()
    try:
        if int(json.loads(path.read_text()).get("pid")) != os.getpid():
            return
        path.unlink()
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--binary", default=DEFAULT_BINARY)
    p.add_argument("--expected-sha256", default=None,
                   help="abort when the reference hash differs from this value")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--max-functions", type=int, default=0,
                   help="stop after this many new exports (0 = until no progress)")
    p.add_argument("--timeout", type=int, default=900, help="seconds per batch")
    p.add_argument("--project-dir",
                   default=os.environ.get("NOVA3_GHIDRA_PROJECT_DIR")
                   or str(Path.home() / "nova3-analysis" / "ghidra"),
                   help="Ghidra project directory; must not contain a '.'-prefixed path element")
    p.add_argument("--state", default=".cache/pipeline/auto_decompile_state.json")
    p.add_argument("--log", default=".cache/logs/auto_decompile.log")
    p.add_argument("--status", action="store_true", help="report coverage and exit")
    args = p.parse_args()

    binary = (ROOT / args.binary).resolve()
    if not binary.exists():
        print("auto_decompile: missing reference %s" % args.binary, file=sys.stderr)
        return 2
    digest = sha256(binary)
    if args.expected_sha256 and digest != args.expected_sha256:
        print("auto_decompile: reference SHA-256 %s != expected %s"
              % (digest, args.expected_sha256), file=sys.stderr)
        return 2

    log_path = ROOT / args.log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a")

    def log(message: str) -> None:
        line = "%s %s" % (utcnow(), message)
        print(line)
        log_fh.write(line + "\n")
        log_fh.flush()

    state_path = ROOT / args.state
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if args.status:
        total, exported = index_counts(digest)
        print(json.dumps({"binary": args.binary, "sha256": digest, "indexed": total,
                          "exported": exported, "remaining": max(total - exported, 0)},
                         indent=2))
        return 0

    if not acquire_lock(args.binary):
        holder = "unknown"
        try:
            holder = str(json.loads(lock_path().read_text()).get("pid"))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        print("auto_decompile: another run already owns %s (pid %s).\n"
              "  Wait for it, or remove the lock if that process is gone."
              % (lock_path().relative_to(ROOT), holder), file=sys.stderr)
        return 4

    try:
        return _run(binary, digest, args, log, state_path, log_path)
    finally:
        release_lock()


def _run(binary: Path, digest: str, args, log, state_path: Path, log_path: Path) -> int:
    ensure_index(binary, log)
    (ROOT / "generated/ghidra").mkdir(parents=True, exist_ok=True)
    total, exported = index_counts(digest)
    log("start: %s sha256=%s indexed=%d exported=%d project=%s"
        % (args.binary, digest, total, exported, args.project_dir))

    state = {"binary": args.binary, "sha256": digest, "indexed": total,
             "project_dir": args.project_dir, "batches": [], "started_at": utcnow()}
    produced = 0
    while True:
        new, status = run_batch(binary, args.batch_size, args.timeout, log, args.project_dir)
        produced += new
        total, exported = index_counts(digest)
        state.update({"updated_at": utcnow(), "exported": exported, "remaining": max(total - exported, 0)})
        state["batches"].append({"at": utcnow(), "new": new, "status": status, "exported": exported})
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        if status != "ok":
            break
        if args.max_functions and produced >= args.max_functions:
            break
        if total and exported >= total:
            log("complete: all %d indexed functions have exports" % total)
            break

    total, exported = index_counts(digest)
    summary = {"binary": args.binary, "sha256": digest, "indexed": total,
               "exported": exported, "remaining": max(total - exported, 0),
               "new_this_run": produced, "state": str(state_path.relative_to(ROOT)),
               "log": str(log_path.relative_to(ROOT))}
    log("summary: " + json.dumps(summary))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
