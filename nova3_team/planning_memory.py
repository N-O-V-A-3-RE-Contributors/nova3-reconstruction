"""Persistent, provider-neutral planning memory with automatic compaction (#139).

Design rules (see ``docs/progress/PLANNING_MEMORY.md``):

* Memory is **local and private** by default (``$XDG_STATE_HOME``/``~/.local/state``)
  and refuses to live inside the public repository unless the path is already
  git-ignored. Raw conversations are never committed.
* Two separate stores of truth: an ordered, compactable **turn log** and a
  structured **memory ledger** of goals, plans, decisions, facts, hypotheses,
  evidence, dependencies, blocked questions and next actions. Prose is never
  promoted into the ledger automatically.
* Accepted decisions and verified facts are never silently overwritten. A
  correction creates a *new* ledger entry that ``supersedes`` the old one; the
  old entry keeps its bytes and gains ``superseded_by``. Checkpoint rows are
  append-only enforced by SQL triggers.
* Model output is **untrusted**. A model cannot mark a claim ``verified`` or a
  decision ``accepted``; such attempts are recorded with a downgrade flag and
  remain ``unverified``/``proposed``. Only a human (or an explicit tool
  verification with digest evidence) promotes an entry, and promotion is
  itself logged.
* Context is budgeted **before** the provider window is exhausted. Every turn
  produces an incremental checkpoint; older turns are compacted into bounded
  summaries; retrieval considers only the recent window plus scored ledger
  entries. No code path replays the full transcript to build context.
* The continuation envelope is deterministic, checksummed, provider-neutral and
  importable by any CLI (Hermes, OpenCode, Codex, Claude, Gemini, human).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence

from . import planning_bridge as bridge

SCHEMA_VERSION = 1
CONTEXT_SCHEMA = "nova3.planning.context/v1"
ENVELOPE_SCHEMA = "nova3.planning.continuation/v1"
SNAPSHOT_SCHEMA = "nova3.planning.snapshot/v1"
DEFAULT_STATE_DIR = Path(".local") / "state" / "nova3" / "planning_memory"
MAX_TEXT_CHARS = 16000
MAX_ENTRY_CHARS = 4000
MAX_EVIDENCE = 20
MAX_TAGS = 12

KINDS = frozenset({
    "goal", "plan", "decision", "fact", "hypothesis", "evidence",
    "dependency", "blocked_question", "next_action",
})
DEFAULT_STATUS = {
    "goal": "active",
    "plan": "proposed",
    "decision": "proposed",
    "fact": "unverified",
    "hypothesis": "open",
    "evidence": "recorded",
    "dependency": "open",
    "blocked_question": "open",
    "next_action": "pending",
}
STATUS_BY_KIND = {
    "goal": frozenset({"active", "achieved", "abandoned"}),
    "plan": frozenset({"proposed", "accepted", "rejected", "superseded"}),
    "decision": frozenset({"proposed", "accepted", "rejected", "superseded"}),
    "fact": frozenset({"unverified", "verified", "hypothesis", "refuted", "superseded"}),
    "hypothesis": frozenset({"open", "supported", "refuted", "superseded"}),
    "evidence": frozenset({"recorded"}),
    "dependency": frozenset({"open", "satisfied", "blocked"}),
    "blocked_question": frozenset({"open", "answered", "withdrawn"}),
    "next_action": frozenset({"pending", "done", "dropped"}),
}
# Statuses a model writer may never self-award.
MODEL_INELIGIBLE = {
    "decision": frozenset({"accepted"}),
    "plan": frozenset({"accepted"}),
    "fact": frozenset({"verified"}),
}
ROLES = frozenset({"user", "assistant", "system", "tool"})
TRUSTED_WRITERS = frozenset({"human", "tool"})
EXCLUDED_CONTENT = (
    "original APKs/ELF bytes", "game assets", "decompiler output",
    "credentials", "absolute local paths", "raw chat transcripts",
)

# Prompt-injection heuristics. These are *flags and downgrades*, never a filter
# that silently drops a user's own words: a model turn is stored (sanitised) with
# the flag so the audit trail shows what happened.
INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions?|messages?|rules)", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|developer)\s+(prompt|message|instructions?)", re.I),
    re.compile(r"you\s+are\s+now\s+(the\s+)?(system|admin|root|owner)", re.I),
    re.compile(r"^\s*(system|assistant|developer)\s*:", re.I | re.M),
    re.compile(r"</?\s*(system|developer|tool)\b", re.I),
    re.compile(r"BEGIN\s+(SYSTEM|ADMIN)\b"),
)
RESERVED_MARKERS = tuple(bridge.RESERVED_MARKERS) + ("SYSTEM OVERRIDE",)
PRIVATE_REF = re.compile(
    r"\b(?:originals|analysis|analysis_db|archive|generated|research)/[A-Za-z0-9._/\-]*",
    re.I,
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"[a-z0-9_]{3,}")
STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "then", "than", "have", "has", "are", "was", "were", "will", "would",
    "should", "must", "not", "but", "you", "your", "our", "its", "it's",
    "can", "could", "may", "might", "all", "any", "each", "turn", "seq",
    "turn", "user", "assistant", "system", "provider", "session",
})


class PlanningMemoryError(ValueError):
    """Deterministic, secret-free failure with a stable machine code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _checksum_body(payload: Mapping[str, object]) -> dict:
    """Fields covered by the envelope checksum.

    ``provider_hint`` is deliberately excluded: switching CLI/backend must not
    change the identity of the same planning context.
    """
    return {key: value for key, value in payload.items()
            if key not in {"checksum", "provider_hint"}}


def _checksum(payload: Mapping[str, object]) -> str:
    return _digest(_checksum_body(payload))


def seal(payload: Mapping[str, object]) -> dict:
    sealed = dict(payload)
    sealed["checksum"] = _checksum(sealed)
    return sealed


def unseal(payload: object, *, code: str) -> dict:
    if not isinstance(payload, Mapping):
        raise PlanningMemoryError(code, "payload must be a JSON object")
    body = dict(payload)
    expected = body.pop("checksum", None)
    if not isinstance(expected, str) or not _HEX64.match(expected):
        raise PlanningMemoryError(code, "missing or malformed checksum")
    hint = body.get("provider_hint")
    actual = _digest(_checksum_body(body))
    if actual != expected:
        raise PlanningMemoryError(code, "checksum mismatch (payload was modified or truncated)")
    body["checksum"] = expected
    if hint is not None:
        body["provider_hint"] = hint
    return body


def estimate_tokens(text: str) -> int:
    """Deterministic, provider-independent budget unit (~4 chars per token)."""
    return max(1, (len(text) + 3) // 4)


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))


def _now_iso() -> str:
    return _iso(int(time.time()))


def _tokens(text: str) -> set[str]:
    return {word for word in _TOKEN.findall(text.lower()) if word not in STOPWORDS}


def scan_injection(text: str) -> tuple[str, ...]:
    return tuple(str(index) for index, pattern in enumerate(INJECTION_PATTERNS)
                 if pattern.search(text))


def sanitize(value: object, *, limit: int = MAX_TEXT_CHARS) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return ``(clean_text, redactions, flags)``.

    Credentials and absolute home paths are redacted by the shared planning
    bridge filter; references to private project trees and reserved
    authorization markers are neutralised here. The caller records the flags so
    redaction is auditable instead of silent.
    """
    if not isinstance(value, str):
        raise PlanningMemoryError("text", "must be a string")
    if "\x00" in value:
        raise PlanningMemoryError("text", "contains NUL")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    flags: list[str] = []
    redactions: list[str] = []
    clean = bridge.sanitize_text(text)
    if clean != text:
        redactions.append("credential-or-local-path")
    for marker in RESERVED_MARKERS:
        if marker in clean:
            clean = clean.replace(marker, "[reserved-marker-removed]")
            redactions.append("reserved-marker")
    cleaned = PRIVATE_REF.sub("[private-ref]", clean)
    if cleaned != clean:
        redactions.append("private-project-reference")
    clean = cleaned
    clean = "".join(ch if ch == "\n" or ch == "\t" or ord(ch) >= 32 else " " for ch in clean)
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
        flags.append("truncated")
    injection = scan_injection(clean)
    if injection:
        flags.append("prompt-injection")
    return clean, tuple(dict.fromkeys(redactions)), tuple(flags)


def normalize_provider(provider: object) -> str:
    if provider is None:
        return "neutral"
    if not isinstance(provider, str):
        raise PlanningMemoryError("provider", "must be a string")
    value = provider.strip().lower()
    if not value or len(value) > 40 or not re.match(r"^[a-z0-9][a-z0-9._-]*$", value):
        raise PlanningMemoryError("provider", "must be a short lowercase slug")
    return value


@dataclass(frozen=True)
class Budget:
    """Context budget with explicit reserved headroom."""

    max_tokens: int = 4000
    reserve_tokens: int = 800
    recent_turns: int = 10
    max_summaries: int = 6
    summary_tokens: int = 240
    max_ledger_items: int = 24

    def __post_init__(self) -> None:
        if self.max_tokens < 200:
            raise PlanningMemoryError("budget", "max_tokens must be >= 200")
        if not 0 <= self.reserve_tokens < self.max_tokens:
            raise PlanningMemoryError("budget", "reserve_tokens must be < max_tokens")
        if not 1 <= self.recent_turns <= 200:
            raise PlanningMemoryError("budget", "recent_turns out of range")
        if not 1 <= self.max_summaries <= 64:
            raise PlanningMemoryError("budget", "max_summaries out of range")
        if self.summary_tokens < 40:
            raise PlanningMemoryError("budget", "summary_tokens too small")
        if self.max_ledger_items < 4:
            raise PlanningMemoryError("budget", "max_ledger_items too small")

    @property
    def available(self) -> int:
        return self.max_tokens - self.reserve_tokens

    def as_dict(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "reserve_tokens": self.reserve_tokens,
            "available_tokens": self.available,
            "recent_turns": self.recent_turns,
            "max_summaries": self.max_summaries,
            "summary_tokens": self.summary_tokens,
            "max_ledger_items": self.max_ledger_items,
        }


DEFAULT_BUDGET = Budget()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL UNIQUE,
    project TEXT NOT NULL,
    repo_revision TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0,
    checkpoint_id TEXT,
    compaction_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    writer_kind TEXT NOT NULL,
    text TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    checkpoint_id TEXT,
    compacted INTEGER NOT NULL DEFAULT 0,
    redactions TEXT NOT NULL DEFAULT '[]',
    flags TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    seq_high INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    parent_id TEXT,
    envelope TEXT NOT NULL,
    checksum TEXT NOT NULL,
    budget TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS checkpoints_session_idx
    ON checkpoints(session_id, seq_high);
CREATE TABLE IF NOT EXISTS ledger (
    entry_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 50,
    confidence REAL NOT NULL DEFAULT 0.5,
    writer TEXT NOT NULL,
    writer_kind TEXT NOT NULL,
    trust TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    supersedes TEXT,
    superseded_by TEXT,
    evidence TEXT NOT NULL DEFAULT '[]',
    digest TEXT NOT NULL,
    flags TEXT NOT NULL DEFAULT '[]',
    verified_by TEXT,
    verified_at INTEGER,
    stale_after INTEGER,
    tags TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ledger_session_idx
    ON ledger(session_id, kind, status);
CREATE TABLE IF NOT EXISTS compactions (
    compaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    from_seq INTEGER NOT NULL,
    to_seq INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    summary TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS compactions_session_idx
    ON compactions(session_id, to_seq);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS imports (
    envelope_digest TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    imported_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS checkpoints_immutable_update
BEFORE UPDATE ON checkpoints
BEGIN
    SELECT RAISE(ABORT, 'checkpoints are immutable');
END;
CREATE TRIGGER IF NOT EXISTS checkpoints_immutable_delete
BEFORE DELETE ON checkpoints
BEGIN
    SELECT RAISE(ABORT, 'checkpoints are immutable');
END;
"""


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def _worktree_root(path: Path) -> Path | None:
    """Nearest ancestor that looks like a Git worktree (has a .git entry)."""
    for candidate in (path.resolve(), *path.resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _gitignored(relative: str, root: Path) -> bool:
    ignore = root / ".gitignore"
    if not ignore.is_file():
        return False
    rel = relative.strip("/")
    for raw in ignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        pattern = line.lstrip("/").rstrip("/")
        if not pattern:
            continue
        if rel == pattern or rel.startswith(pattern + "/"):
            return True
        if pattern.startswith("**/") and (rel == pattern[3:] or rel.startswith(pattern[3:] + "/")):
            return True
    return False


def default_store_dir() -> Path:
    override = os.environ.get("NOVA3_PLANNING_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "nova3" / "planning_memory"
    return Path.home() / DEFAULT_STATE_DIR


class PlanningMemory:
    """SQLite-backed planning memory. One instance is safe to share across threads."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        budget: Budget = DEFAULT_BUDGET,
        repo_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], float] | None = None,
        allow_repo_path: bool = False,
    ) -> None:
        self.budget = budget
        self._clock = clock or time.time
        self._snapshot_lock = threading.Lock()
        root = Path(repo_root) if repo_root is not None else _repo_root_default()
        target = Path(path).expanduser() if path is not None else default_store_dir()
        if _is_within(target, root):
            relative = str(target.resolve().relative_to(root.resolve())).replace(os.sep, "/")
            if not (allow_repo_path and _gitignored(relative, root)):
                raise PlanningMemoryError(
                    "store-in-repo",
                    "planning memory must live outside the public repository or in a git-ignored path",
                )
        # Module-location detection misses an installed copy writing into a real
        # checkout. Also discover containment from the target's own worktree.
        toplevel = _worktree_root(target)
        if toplevel is not None and toplevel != root.resolve():
            relative = str(target.resolve().relative_to(toplevel)).replace(os.sep, "/")
            if not (allow_repo_path and _gitignored(relative, toplevel)):
                raise PlanningMemoryError(
                    "store-in-repo",
                    "planning memory must live outside any Git worktree or in a git-ignored path",
                )
        self.root = target
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "memory.sqlite3"
        self.snapshot_path = self.root / "continuation.json"
        self._initialised = False
        self._init_lock = threading.Lock()
        with self._init_lock:
            if not self._initialised:
                self._initialise()
                self._initialised = True

    # ------------------------------------------------------------------
    # connection / schema plumbing
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _initialise(self) -> None:
        try:
            with self._connect() as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError("quick_check failed")
                self._migrate(conn)
        except sqlite3.DatabaseError:
            self._recover_database()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_SQL)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0
        if current > SCHEMA_VERSION:
            raise PlanningMemoryError(
                "schema-too-new",
                f"store schema {current} is newer than supported {SCHEMA_VERSION}",
            )
        migrations: dict[int, Sequence[str]] = {}
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in migrations.get(version, ()):  # pragma: no cover - future use
                conn.execute(statement)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('created_by', 'nova3_team.planning_memory') "
            "ON CONFLICT(key) DO NOTHING"
        )

    def _recover_database(self) -> None:
        """A corrupt DB is set aside and rebuilt from the last valid snapshot."""
        stamp = int(self._clock())
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                candidate.replace(Path(f"{candidate}.corrupt-{stamp}"))
        snapshot = None
        try:
            snapshot = self._read_snapshot_file(raise_on_error=False)
        except PlanningMemoryError:
            snapshot = None
        with self._connect() as conn:
            self._migrate(conn)
            if snapshot:
                for envelope in snapshot.get("sessions", []):
                    # One savepoint per envelope: a failure must not leave a
                    # partially imported session committed.
                    conn.execute("SAVEPOINT envelope")
                    try:
                        self._import_conn(conn, envelope, now=stamp, trust_checksum=True)
                    except (PlanningMemoryError, sqlite3.Error, ValueError, TypeError):
                        conn.execute("ROLLBACK TO envelope")
                        conn.execute("RELEASE envelope")
                        continue
                    conn.execute("RELEASE envelope")
            self._event_conn(conn, None, "database-recovered", {
                "snapshot_restored": bool(snapshot),
                "corrupt_backup": f"memory.sqlite3.corrupt-{stamp}",
            })

    def close(self) -> None:
        """No pooled connections are held; present for API symmetry and tests."""
        return None

    def __enter__(self) -> "PlanningMemory":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextlib.contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # events / snapshot
    # ------------------------------------------------------------------
    def _event_conn(self, conn: sqlite3.Connection, session_id: str | None,
                    kind: str, detail: Mapping[str, object]) -> None:
        conn.execute(
            "INSERT INTO events(session_id, at, kind, detail) VALUES(?,?,?,?)",
            (session_id, int(self._clock()), kind, _canonical(dict(detail))),
        )

    def events(self, session_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._read() as conn:
            if session_id is None:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY event_id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events WHERE session_id=? ORDER BY event_id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
        return [dict(row, detail=json.loads(row["detail"])) for row in reversed(rows)]

    def _snapshot_payload_conn(self, conn: sqlite3.Connection) -> dict:
        envelopes = []
        for row in conn.execute("SELECT session_id FROM sessions ORDER BY created_at, session_id"):
            envelopes.append(self._export_envelope_conn(conn, row["session_id"]))
        return seal({
            "schema": SNAPSHOT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "exported_at": int(self._clock()),
            "sessions": envelopes,
            "excluded": list(EXCLUDED_CONTENT),
        })

    def _write_snapshot_file(self, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=True)
        suffix = f".{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp"
        tmp = self.snapshot_path.with_name(f".{self.snapshot_path.name}{suffix}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.snapshot_path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def save_snapshot(self) -> dict:
        with self._snapshot_lock:
            with self._read() as conn:
                payload = self._snapshot_payload_conn(conn)
            self._write_snapshot_file(payload)
        return {"path": str(self.snapshot_path), "checksum": payload["checksum"],
                "sessions": len(payload["sessions"])}

    def _read_snapshot_file(self, *, raise_on_error: bool = True) -> dict | None:
        if not self.snapshot_path.exists():
            return None
        try:
            raw = self.snapshot_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            body = unseal(payload, code="snapshot-invalid")
            if body.get("schema") != SNAPSHOT_SCHEMA:
                raise PlanningMemoryError("snapshot-invalid", "unknown snapshot schema")
            if not isinstance(body.get("sessions"), list):
                raise PlanningMemoryError("snapshot-invalid", "sessions must be a list")
            return body
        except (OSError, ValueError, PlanningMemoryError) as exc:
            if raise_on_error:
                if isinstance(exc, PlanningMemoryError):
                    raise
                raise PlanningMemoryError("snapshot-invalid", "snapshot is corrupt or truncated") from None
            return None

    def load_snapshot(self, *, rebuild_if_invalid: bool = True) -> dict:
        """Load the JSON export, rebuilding it from SQLite when corrupt/truncated."""
        try:
            payload = self._read_snapshot_file()
            if payload is None and rebuild_if_invalid:
                with self._read() as conn:
                    payload = self._snapshot_payload_conn(conn)
                self._write_snapshot_file(payload)
                with self._write() as conn:
                    self._event_conn(conn, None, "snapshot-rebuilt", {"reason": "missing"})
            if payload is None:
                raise PlanningMemoryError("snapshot-missing", "no snapshot exists")
            return payload
        except PlanningMemoryError:
            if not rebuild_if_invalid:
                raise
            # Preserve the corrupt/truncated handoff before overwriting it; it
            # may be the only copy a user has of an imported continuation.
            if self.snapshot_path.exists():
                backup = self.snapshot_path.with_name(
                    f"{self.snapshot_path.name}.corrupt-{time.time_ns()}")
                try:
                    os.replace(self.snapshot_path, backup)
                except OSError:
                    pass
            with self._read() as conn:
                payload = self._snapshot_payload_conn(conn)
            self._write_snapshot_file(payload)
            with self._write() as conn:
                self._event_conn(conn, None, "snapshot-recovered", {"reason": "corrupt-or-truncated"})
            return payload

    def snapshot_info(self) -> dict:
        info = {"path": str(self.snapshot_path), "exists": self.snapshot_path.exists()}
        try:
            payload = self._read_snapshot_file()
        except PlanningMemoryError as exc:
            info.update({"valid": False, "error": exc.code})
            return info
        info.update({
            "valid": payload is not None,
            "checksum": payload.get("checksum") if payload else None,
            "sessions": len(payload.get("sessions", [])) if payload else 0,
        })
        return info

    # ------------------------------------------------------------------
    # sessions and turns
    # ------------------------------------------------------------------
    def create_session(
        self,
        project: str,
        *,
        thread_id: str | None = None,
        provider: str = "neutral",
        repo_revision: str = "",
        session_id: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        clean_project, _, _ = sanitize(project, limit=200)
        if not clean_project:
            raise PlanningMemoryError("project", "must not be empty")
        clean_rev, _, _ = sanitize(repo_revision, limit=200)
        provider = normalize_provider(provider)
        now = int(created_at if created_at is not None else self._clock())
        sid = session_id or self._new_id("s")
        tid = thread_id or self._new_id("th")
        if not re.match(r"^[A-Za-z0-9_-]{1,80}$", sid):
            raise PlanningMemoryError("session-id", "must be 1-80 chars of [A-Za-z0-9_-]")
        if not re.match(r"^[A-Za-z0-9_-]{1,80}$", tid):
            raise PlanningMemoryError("thread-id", "must be 1-80 chars of [A-Za-z0-9_-]")
        with self._write() as conn:
            try:
                conn.execute(
                    "INSERT INTO sessions(session_id, thread_id, project, repo_revision, provider,"
                    " created_at, updated_at, last_seq, checkpoint_id, compaction_count)"
                    " VALUES(?,?,?,?,?,?,?,0,NULL,0)",
                    (sid, tid, clean_project, clean_rev, provider, now, now),
                )
            except sqlite3.IntegrityError as exc:
                code = "thread-exists" if "thread_id" in str(exc) else "session-exists"
                raise PlanningMemoryError(code, "a session with that identity already exists") from None
            self._event_conn(conn, sid, "session-created", {"provider": provider})
        self.save_snapshot()
        return self.get_session(sid)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"

    def _session_row(self, conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise PlanningMemoryError("unknown-session", session_id[:80])
        return row

    def get_session(self, session_id: str) -> dict:
        with self._read() as conn:
            row = self._session_row(conn, session_id)
            turn_count = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        return self._session_dict(row, turn_count)

    def _session_dict(self, row: sqlite3.Row, turn_count: int | None = None) -> dict:
        return {
            "session_id": row["session_id"],
            "thread_id": row["thread_id"],
            "project": row["project"],
            "repo_revision": row["repo_revision"],
            "provider": row["provider"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "created_at_iso": _iso(row["created_at"]),
            "last_seq": row["last_seq"],
            "turn_count": turn_count if turn_count is not None else row["last_seq"],
            "checkpoint_id": row["checkpoint_id"],
            "compaction_count": row["compaction_count"],
        }

    def list_sessions(self) -> list[dict]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY created_at, session_id").fetchall()
            result = []
            for row in rows:
                count = conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE session_id=?", (row["session_id"],)
                ).fetchone()[0]
                result.append(self._session_dict(row, count))
            return result

    def append_turn(
        self,
        session_id: str,
        text: str,
        *,
        role: str = "user",
        provider: str | None = None,
        writer_kind: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        """Append one ordered turn and produce an incremental checkpoint.

        Model roles default to ``writer_kind='model'`` and are marked untrusted.
        Compaction runs *before* the checkpoint is sealed whenever the projected
        context would violate the budget's reserved headroom.
        """
        if role not in ROLES:
            raise PlanningMemoryError("role", f"must be one of {sorted(ROLES)}")
        kind = writer_kind or ("model" if role in {"assistant", "tool"} else "human")
        if kind not in {"model", "human", "tool"}:
            raise PlanningMemoryError("writer-kind", "must be model, human or tool")
        clean, redactions, flags = sanitize(text)
        if not clean.strip():
            raise PlanningMemoryError("turn", "text must not be empty")
        digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        now = int(created_at if created_at is not None else self._clock())
        with self._write() as conn:
            session = self._session_row(conn, session_id)
            provider_name = normalize_provider(provider or session["provider"])
            seq = int(session["last_seq"]) + 1
            conn.execute(
                "INSERT INTO turns(session_id, seq, role, provider, writer_kind, text, digest,"
                " created_at, checkpoint_id, compacted, redactions, flags)"
                " VALUES(?,?,?,?,?,?,?,?,NULL,0,?,?)",
                (session_id, seq, role, provider_name, kind, clean, digest, now,
                 _canonical(list(redactions)), _canonical(list(flags))),
            )
            conn.execute(
                "UPDATE sessions SET last_seq=?, updated_at=? WHERE session_id=?",
                (seq, now, session_id),
            )
            compacted_before = int(session["compaction_count"])
            projected = seal(self._render_at_level(
                conn, self._session_row(conn, session_id), self.budget, 0, provider_name
            ))
            over = (estimate_tokens(_canonical(projected)) > self.budget.available
                    or self._compaction_backlog_conn(conn, session_id, self.budget) > self.budget.recent_turns)
            if over:
                self._compact_conn(conn, session_id, self.budget)
            checkpoint = self._checkpoint_conn(
                conn, session_id, reason="turn-compacted" if over else "turn",
                budget=self.budget, provider=provider_name,
            )
            conn.execute(
                "UPDATE turns SET checkpoint_id=? WHERE session_id=? AND seq=?",
                (checkpoint["checkpoint_id"], session_id, seq),
            )
            session_after = self._session_row(conn, session_id)
        self.save_snapshot()
        return {
            "session_id": session_id,
            "seq": seq,
            "role": role,
            "provider": provider_name,
            "writer_kind": kind,
            "untrusted": kind == "model",
            "digest": digest,
            "redactions": list(redactions),
            "flags": list(flags),
            "compacted": bool(session_after["compaction_count"] > compacted_before),
            "checkpoint_id": checkpoint["checkpoint_id"],
            "context_tokens": checkpoint["tokens"],
            "context_mode": checkpoint["mode"],
        }

    def turns(self, session_id: str, *, limit: int = 200, since_seq: int = 0,
              include_compacted: bool = True) -> list[dict]:
        with self._read() as conn:
            self._session_row(conn, session_id)
            sql = "SELECT * FROM turns WHERE session_id=? AND seq>?"
            params: list[object] = [session_id, since_seq]
            if not include_compacted:
                sql += " AND compacted=0"
            sql += " ORDER BY seq ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return [self._turn_dict(row) for row in rows]

    @staticmethod
    def _turn_dict(row: sqlite3.Row) -> dict:
        return {
            "seq": row["seq"],
            "role": row["role"],
            "provider": row["provider"],
            "writer_kind": row["writer_kind"],
            "untrusted": row["writer_kind"] == "model",
            "text": row["text"],
            "digest": row["digest"],
            "created_at": row["created_at"],
            "created_at_iso": _iso(row["created_at"]),
            "checkpoint_id": row["checkpoint_id"],
            "compacted": bool(row["compacted"]),
            "redactions": json.loads(row["redactions"]),
            "flags": json.loads(row["flags"]),
        }

    # ------------------------------------------------------------------
    # structured ledger
    # ------------------------------------------------------------------
    def remember(
        self,
        session_id: str,
        kind: str,
        text: str,
        *,
        status: str | None = None,
        rationale: str = "",
        evidence: Sequence[Mapping[str, object]] = (),
        importance: int = 50,
        confidence: float = 0.5,
        writer: str = "planner",
        writer_kind: str = "model",
        provider: str | None = None,
        turn_seq: int | None = None,
        tags: Sequence[str] = (),
        stale_after: int | None = None,
        supersedes: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        if kind not in KINDS:
            raise PlanningMemoryError("kind", f"must be one of {sorted(KINDS)}")
        if writer_kind not in {"model", "human", "tool"}:
            raise PlanningMemoryError("writer-kind", "must be model, human or tool")
        resolved = status or DEFAULT_STATUS[kind]
        if resolved not in STATUS_BY_KIND[kind]:
            raise PlanningMemoryError("status", f"invalid status {resolved!r} for {kind}")
        flags: list[str] = []
        downgraded_from = None
        if writer_kind == "model" and resolved in MODEL_INELIGIBLE.get(kind, frozenset()):
            downgraded_from = resolved
            resolved = "unverified" if kind == "fact" else "proposed"
            flags.append("downgraded:model-cannot-assert-" + downgraded_from)
        clean, redactions, text_flags = sanitize(text, limit=MAX_ENTRY_CHARS)
        if not clean.strip():
            raise PlanningMemoryError("text", "must not be empty")
        clean_rationale, rationale_redactions, rationale_flags = sanitize(
            rationale, limit=MAX_ENTRY_CHARS
        )
        flags.extend(text_flags)
        flags.extend(rationale_flags)
        redactions = tuple(dict.fromkeys(redactions + rationale_redactions))
        flags.extend(redactions)
        if not 0 <= int(importance) <= 100:
            raise PlanningMemoryError("importance", "must be 0..100")
        if not 0.0 <= float(confidence) <= 1.0:
            raise PlanningMemoryError("confidence", "must be 0..1")
        clean_tags = []
        for tag in list(tags)[:MAX_TAGS]:
            tag_text, _, _ = sanitize(str(tag), limit=40)
            if tag_text:
                clean_tags.append(tag_text)
        normalised_evidence = self._normalise_evidence(evidence)
        now = int(created_at if created_at is not None else self._clock())
        trust = "untrusted" if writer_kind == "model" else "trusted"
        if "prompt-injection" in flags and writer_kind == "model":
            trust = "untrusted"
        provenance = {
            "provider": self._safe_provider(provider, writer),
            "writer_kind": writer_kind,
            "turn_seq": turn_seq,
            "session": session_id,
        }
        with self._write() as conn:
            self._session_row(conn, session_id)
            entry_id = self._entry_id(conn, session_id, kind, clean, now)
            digest = hashlib.sha256(
                _canonical({"kind": kind, "text": clean, "rationale": clean_rationale,
                            "evidence": normalised_evidence, "status": resolved})
                .encode("utf-8")
            ).hexdigest()
            conn.execute(
                "INSERT INTO ledger(entry_id, session_id, kind, status, text, rationale,"
                " importance, confidence, writer, writer_kind, trust, provenance, created_at,"
                " updated_at, supersedes, superseded_by, evidence, digest, flags, verified_by,"
                " verified_at, stale_after, tags)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,NULL,NULL,?,?)",
                (entry_id, session_id, kind, resolved, clean, clean_rationale,
                 int(importance), float(confidence), writer, writer_kind, trust,
                 _canonical(provenance), now, now, supersedes, _canonical(normalised_evidence),
                 digest, _canonical(list(dict.fromkeys(flags))), stale_after,
                 _canonical(clean_tags)),
            )
            self._event_conn(conn, session_id, "ledger-added", {
                "entry_id": entry_id, "kind": kind, "status": resolved,
                "writer_kind": writer_kind, "downgraded_from": downgraded_from,
                "redactions": list(redactions),
            })
        self.save_snapshot()
        result = self.get_entry(entry_id)
        result["downgraded_from"] = downgraded_from
        result["redactions"] = list(redactions)
        return result

    @staticmethod
    def _safe_provider(provider: object, fallback: str = "neutral") -> str:
        for candidate in (provider, fallback, "neutral"):
            try:
                return normalize_provider(candidate)
            except PlanningMemoryError:
                continue
        return "neutral"

    def _entry_id(self, conn: sqlite3.Connection, session_id: str, kind: str,
                  text: str, now: int) -> str:
        base = _digest({"session": session_id, "kind": kind, "text": text, "at": now})[:18]
        candidate = f"mem_{base}"
        counter = 1
        while conn.execute("SELECT 1 FROM ledger WHERE entry_id=?", (candidate,)).fetchone():
            counter += 1
            candidate = f"mem_{base}_{counter}"
        return candidate

    def _normalise_evidence(self, evidence: Sequence[Mapping[str, object]]) -> list[dict]:
        if not isinstance(evidence, (list, tuple)):
            raise PlanningMemoryError("evidence", "must be a list")
        if len(evidence) > MAX_EVIDENCE:
            raise PlanningMemoryError("evidence", f"at most {MAX_EVIDENCE} items")
        result = []
        for item in evidence:
            if not isinstance(item, Mapping):
                raise PlanningMemoryError("evidence", "items must be objects")
            locator = item.get("locator", item.get("path"))
            if not isinstance(locator, str) or not locator.strip():
                raise PlanningMemoryError("evidence", "locator is required")
            clean_locator, _, _ = sanitize(locator, limit=500)
            digest = item.get("digest", item.get("sha256", ""))
            if digest in (None, ""):
                digest = ""
            if not isinstance(digest, str) or (digest and not _HEX64.match(digest)):
                raise PlanningMemoryError("evidence", "digest must be a sha256 hex string")
            result.append({"locator": clean_locator, "digest": digest})
        return result

    def get_entry(self, entry_id: str) -> dict:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM ledger WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise PlanningMemoryError("unknown-entry", entry_id[:80])
        return self._entry_dict(row)

    def _entry_dict(self, row: sqlite3.Row, *, now: int | None = None) -> dict:
        now = int(self._clock()) if now is None else now
        stale = bool(row["stale_after"]) and (now - int(row["updated_at"])) > int(row["stale_after"])
        return {
            "entry_id": row["entry_id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "status": row["status"],
            "text": row["text"],
            "rationale": row["rationale"],
            "importance": row["importance"],
            "confidence": row["confidence"],
            "writer": row["writer"],
            "writer_kind": row["writer_kind"],
            "trust": row["trust"],
            "provenance": json.loads(row["provenance"]),
            "created_at": row["created_at"],
            "created_at_iso": _iso(row["created_at"]),
            "updated_at": row["updated_at"],
            "supersedes": row["supersedes"],
            "superseded_by": row["superseded_by"],
            "evidence": json.loads(row["evidence"]),
            "digest": row["digest"],
            "flags": json.loads(row["flags"]),
            "verified_by": row["verified_by"],
            "verified_at": row["verified_at"],
            "stale_after": row["stale_after"],
            "stale": stale,
            "tags": json.loads(row["tags"]),
        }

    def ledger(self, session_id: str, *, kind: str | None = None,
               status: str | None = None, include_superseded: bool = False) -> list[dict]:
        sql = "SELECT * FROM ledger WHERE session_id=?"
        params: list[object] = [session_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if status:
            sql += " AND status=?"
            params.append(status)
        if not include_superseded:
            sql += " AND status!='superseded'"
        sql += " ORDER BY created_at ASC, entry_id ASC"
        with self._read() as conn:
            self._session_row(conn, session_id)
            rows = conn.execute(sql, params).fetchall()
        now = int(self._clock())
        return [self._entry_dict(row, now=now) for row in rows]

    def record_decision(
        self,
        session_id: str,
        text: str,
        *,
        decision: str = "accepted",
        rationale: str = "",
        writer: str = "owner",
        # Fail closed: an omitted kind is untrusted model output. A human/tool
        # caller must assert writer_kind explicitly.
        writer_kind: str = "model",
        importance: int = 80,
        **kwargs: object,
    ) -> dict:
        return self.remember(
            session_id, "decision", text, status=decision, rationale=rationale,
            writer=writer, writer_kind=writer_kind, importance=importance, **kwargs,
        )

    def correct_entry(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        rationale: str | None = None,
        status: str | None = None,
        reason: str = "",
        writer: str = "owner",
        writer_kind: str = "human",
        created_at: int | None = None,
    ) -> dict:
        """Append a correction that supersedes ``entry_id``; never overwrite it."""
        old = self.get_entry(entry_id)
        if writer_kind == "model" and (old["status"] in {"accepted", "verified"}
                                       or old["kind"] in {"decision", "fact"}):
            raise PlanningMemoryError(
                "model-cannot-supersede",
                "model output cannot replace an accepted decision or verified fact",
            )
        if old["superseded_by"]:
            raise PlanningMemoryError("already-superseded", old["superseded_by"])
        new_status = status or old["status"]
        rationale_text = rationale if rationale is not None else old["rationale"]
        reason_text, _, reason_flags = sanitize(reason, limit=500)
        new = self.remember(
            old["session_id"], old["kind"], text if text is not None else old["text"],
            status=new_status,
            rationale=(rationale_text + (" | correction: " + reason_text if reason_text else "")),
            evidence=old["evidence"], importance=old["importance"],
            confidence=old["confidence"], writer=writer, writer_kind=writer_kind,
            provider=old["provenance"].get("provider"),
            tags=old["tags"], created_at=created_at, supersedes=entry_id,
        )
        now = int(created_at if created_at is not None else self._clock())
        with self._write() as conn:
            conn.execute(
                "UPDATE ledger SET superseded_by=?, status='superseded', updated_at=?"
                " WHERE entry_id=?",
                (new["entry_id"], now, entry_id),
            )
            self._event_conn(conn, old["session_id"], "entry-corrected", {
                "old": entry_id, "new": new["entry_id"], "prior_status": old["status"],
                "flags": list(reason_flags),
            })
            if old["kind"] in {"decision", "fact"}:
                conn.execute(
                    "UPDATE ledger SET flags=? WHERE entry_id=?",
                    (_canonical(list(dict.fromkeys(old["flags"] + ["corrected"]))), entry_id),
                )
        self.save_snapshot()
        return {"replaces": entry_id, "entry": self.get_entry(new["entry_id"])}

    def verify_entry(
        self,
        entry_id: str,
        *,
        evidence: Sequence[Mapping[str, object]],
        verifier: str = "owner",
        # Fail closed: promotion requires an explicit human/tool assertion.
        verifier_kind: str = "model",
        note: str = "",
    ) -> dict:
        """Promote a claim to verified. Models may never do this."""
        if verifier_kind not in TRUSTED_WRITERS:
            raise PlanningMemoryError("model-cannot-verify", "verification requires a human or tool")
        entry = self.get_entry(entry_id)
        if entry["kind"] not in {"fact", "hypothesis"}:
            raise PlanningMemoryError("not-verifiable", "only facts and hypotheses are verifiable")
        if entry["superseded_by"]:
            raise PlanningMemoryError("superseded-entry", entry["superseded_by"])
        normalised = self._normalise_evidence(evidence)
        if not any(item["digest"] for item in normalised):
            raise PlanningMemoryError("evidence-required", "at least one digest-bearing evidence item")
        now = int(self._clock())
        status = "verified" if entry["kind"] == "fact" else "supported"
        with self._write() as conn:
            conn.execute(
                "UPDATE ledger SET status=?, evidence=?, verified_by=?, verified_at=?,"
                " updated_at=?, trust='trusted', flags=? WHERE entry_id=?",
                (status, _canonical(normalised), verifier, now, now,
                 _canonical(list(dict.fromkeys(entry["flags"] + ["verified"]))), entry_id),
            )
            self._event_conn(conn, entry["session_id"], "entry-verified", {
                "entry_id": entry_id, "verifier": verifier, "verifier_kind": verifier_kind,
                "note": sanitize(note, limit=300)[0],
                "evidence_digests": [item["digest"] for item in normalised if item["digest"]],
            })
        self.save_snapshot()
        return self.get_entry(entry_id)

    def explain(self, entry_id: str) -> dict:
        chain = []
        seen: set[str] = set()
        current = self.get_entry(entry_id)
        while current["supersedes"] and current["supersedes"] not in seen:
            seen.add(current["supersedes"])
            previous = self.get_entry(current["supersedes"])
            chain.append(previous)
            current = previous
        successor = None
        latest = self.get_entry(entry_id)
        if latest["superseded_by"]:
            successor = self.get_entry(latest["superseded_by"])
        return {"entry": self.get_entry(entry_id), "predecessors": chain[::-1],
                "successor": successor}

    # ------------------------------------------------------------------
    # compaction
    # ------------------------------------------------------------------
    def _compaction_backlog_conn(self, conn: sqlite3.Connection, session_id: str,
                                 budget: Budget) -> int:
        """Turns older than the recent window that are still uncompacted."""
        session = self._session_row(conn, session_id)
        cutoff = int(session["last_seq"]) - budget.recent_turns
        if cutoff <= 0:
            return 0
        return int(conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id=? AND compacted=0 AND seq<=?",
            (session_id, cutoff),
        ).fetchone()[0])

    def needs_compaction(self, session_id: str, *, budget: Budget | None = None) -> bool:
        budget = budget or self.budget
        with self._read() as conn:
            session = self._session_row(conn, session_id)
            level_zero = seal(self._render_at_level(conn, session, budget, 0, None))
            return (estimate_tokens(_canonical(level_zero)) > budget.available
                    or self._compaction_backlog_conn(conn, session_id, budget) > budget.recent_turns)

    def _compaction_summary(self, rows: Sequence[sqlite3.Row], budget: Budget) -> str:
        limit_chars = max(160, budget.summary_tokens * 4)
        lines = [f"[compacted {len(rows)} turns seq {rows[0]['seq']}..{rows[-1]['seq']}]"]
        for row in rows:
            snippet = " ".join(row["text"].split())
            lines.append(f"seq{row['seq']} {row['role']}: {snippet[:160]}")
        text = "\n".join(lines)
        if len(text) > limit_chars:
            text = text[: limit_chars - 3].rstrip() + "..."
        return text

    def _compact_conn(self, conn: sqlite3.Connection, session_id: str, budget: Budget) -> dict | None:
        session = self._session_row(conn, session_id)
        last_seq = int(session["last_seq"])
        cutoff = last_seq - budget.recent_turns
        if cutoff <= 0:
            return None
        rows = conn.execute(
            "SELECT * FROM turns WHERE session_id=? AND compacted=0 AND seq<=?"
            " ORDER BY seq ASC LIMIT 40",
            (session_id, cutoff),
        ).fetchall()
        if not rows:
            return None
        summary = self._compaction_summary(rows, budget)
        now = int(self._clock())
        compaction_id = "cmp_" + _digest({
            "session": session_id, "from": rows[0]["seq"], "to": rows[-1]["seq"],
            "at": now, "summary": summary,
        })[:18]
        conn.execute(
            "INSERT INTO compactions(compaction_id, session_id, from_seq, to_seq, turn_count,"
            " summary, digest, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (compaction_id, session_id, rows[0]["seq"], rows[-1]["seq"], len(rows),
             summary, hashlib.sha256(summary.encode("utf-8")).hexdigest(), now),
        )
        seqs = [row["seq"] for row in rows]
        conn.executemany(
            "UPDATE turns SET compacted=1 WHERE session_id=? AND seq=?",
            [(session_id, seq) for seq in seqs],
        )
        conn.execute(
            "UPDATE sessions SET compaction_count=compaction_count+1 WHERE session_id=?",
            (session_id,),
        )
        self._event_conn(conn, session_id, "context-compacted", {
            "compaction_id": compaction_id, "from_seq": seqs[0], "to_seq": seqs[-1],
            "turns": len(seqs), "budget_tokens": budget.max_tokens,
        })
        return {"compaction_id": compaction_id, "turns": len(seqs),
                "from_seq": seqs[0], "to_seq": seqs[-1]}

    def compact(self, session_id: str) -> dict | None:
        with self._write() as conn:
            result = self._compact_conn(conn, session_id, self.budget)
        if result:
            self.save_snapshot()
        return result

    def compactions(self, session_id: str) -> list[dict]:
        with self._read() as conn:
            self._session_row(conn, session_id)
            rows = conn.execute(
                "SELECT * FROM compactions WHERE session_id=? ORDER BY to_seq ASC",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # context envelope
    # ------------------------------------------------------------------
    _LEVELS = (
        {"turn_chars": 1200, "decisions": 8, "facts": 8, "hypotheses": 4,
         "claims": 3, "goals": 3, "actions": 6, "blockers": 4, "deps": 4,
         "plans": 3, "evidence": 4, "summaries": 1.0, "summary_chars": 900,
         "recent": 1.0},
        {"turn_chars": 500, "decisions": 6, "facts": 5, "hypotheses": 3,
         "claims": 2, "goals": 2, "actions": 4, "blockers": 3, "deps": 3,
         "plans": 2, "evidence": 3, "summaries": 0.7, "summary_chars": 500,
         "recent": 1.0},
        {"turn_chars": 220, "decisions": 4, "facts": 3, "hypotheses": 2,
         "claims": 1, "goals": 2, "actions": 3, "blockers": 2, "deps": 2,
         "plans": 1, "evidence": 2, "summaries": 0.5, "summary_chars": 260,
         "recent": 0.7},
        {"turn_chars": 100, "decisions": 2, "facts": 2, "hypotheses": 1,
         "claims": 1, "goals": 1, "actions": 2, "blockers": 1, "deps": 1,
         "plans": 1, "evidence": 1, "summaries": 0.34, "summary_chars": 120,
         "recent": 0.4},
    )

    def context(self, session_id: str, *, provider: str | None = None,
                budget: Budget | None = None) -> dict:
        budget = budget or self.budget
        with self._read() as conn:
            payload = self._render_conn(conn, session_id, budget, provider=provider)
        return payload

    def _render_conn(self, conn: sqlite3.Connection, session_id: str, budget: Budget,
                     *, provider: str | None = None) -> dict:
        session = self._session_row(conn, session_id)
        level = 0
        payload = self._render_at_level(conn, session, budget, level, provider)
        while (estimate_tokens(_canonical(payload)) > budget.available
               and level < len(self._LEVELS) - 1):
            level += 1
            payload = self._render_at_level(conn, session, budget, level, provider)
        if estimate_tokens(_canonical(payload)) > budget.available:
            payload = self._hard_fit(payload, budget)
        return seal(payload)

    def _render_at_level(self, conn: sqlite3.Connection, session: sqlite3.Row,
                         budget: Budget, level: int, provider: str | None) -> dict:
        cfg = self._LEVELS[level]
        session_id = session["session_id"]
        recent_limit = max(1, int(round(budget.recent_turns * cfg["recent"])))
        recent_rows = conn.execute(
            "SELECT * FROM turns WHERE session_id=? AND compacted=0"
            " ORDER BY seq DESC LIMIT ?",
            (session_id, recent_limit),
        ).fetchall()
        recent_rows = list(reversed(recent_rows))
        summary_rows = conn.execute(
            "SELECT * FROM compactions WHERE session_id=? ORDER BY to_seq DESC LIMIT ?",
            (session_id, max(1, int(round(budget.max_summaries * cfg["summaries"])))),
        ).fetchall()
        summary_rows = list(reversed(summary_rows))
        query_text = " ".join(row["text"] for row in recent_rows[-4:])
        query = _tokens(query_text)
        entries = self._load_entries(conn, session_id)
        selected, omitted_ledger = self._select_entries(entries, query, cfg, budget)
        now = int(self._clock())

        def turn_view(row: sqlite3.Row) -> dict:
            text = row["text"]
            truncated = len(text) > cfg["turn_chars"]
            return {
                "seq": row["seq"],
                "role": row["role"],
                "provider": row["provider"],
                "writer_kind": row["writer_kind"],
                "untrusted": row["writer_kind"] == "model",
                "text": text[: cfg["turn_chars"]] + ("..." if truncated else ""),
                "digest": row["digest"],
                "truncated": truncated,
            }

        def entry_view(entry: Mapping[str, object]) -> dict:
            text = str(entry["text"])
            rationale = str(entry["rationale"])
            truncated = len(text) > cfg["turn_chars"]
            return {
                "entry_id": entry["entry_id"],
                "kind": entry["kind"],
                "status": entry["status"],
                "text": text[: cfg["turn_chars"]] + ("..." if truncated else ""),
                "rationale": rationale[: max(80, cfg["turn_chars"] // 2)],
                "importance": entry["importance"],
                "confidence": entry["confidence"],
                "writer": entry["writer"],
                "writer_kind": entry["writer_kind"],
                "trust": entry["trust"],
                "digest": entry["digest"],
                "evidence": entry["evidence"][: cfg["evidence"]],
                "stale": entry["stale"],
                "created_at": entry["created_at"],
                "supersedes": entry["supersedes"],
            }

        grouped: dict[str, list[dict]] = {key: [] for key in selected}
        for bucket, rows in selected.items():
            grouped[bucket] = [entry_view(entry) for entry in rows]
        turn_total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,)
        ).fetchone()[0]
        compacted_total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id=? AND compacted=1", (session_id,)
        ).fetchone()[0]
        payload = {
            "schema": CONTEXT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "thread_id": session["thread_id"],
            "project": session["project"],
            "repo_revision": session["repo_revision"],
            "session_provider": session["provider"],
            "provider_hint": normalize_provider(provider or session["provider"]),
            "generated_at": now,
            "generated_at_iso": _iso(now),
            "last_turn_seq": session["last_seq"],
            "checkpoint": {
                "checkpoint_id": session["checkpoint_id"],
                "compaction_count": session["compaction_count"],
            },
            "budget": budget.as_dict(),
            "mode": ("full" if level == 0 else "degraded") if level < 3 else "fallback",
            "fallback": level >= 3,
            "recent_turns": [turn_view(row) for row in recent_rows],
            "summaries": [
                {
                    "compaction_id": row["compaction_id"],
                    "from_seq": row["from_seq"],
                    "to_seq": row["to_seq"],
                    "turn_count": row["turn_count"],
                    "summary": row["summary"][: cfg["summary_chars"]],
                    "digest": row["digest"],
                }
                for row in summary_rows
            ],
            "summaries_omitted": max(0, int(session["compaction_count"]) - len(summary_rows)),
            "ledger": grouped,
            "retrieval": {
                "strategy": "pinned-importance+keyword-overlap+recency",
                "query_digest": hashlib.sha256(
                    _canonical(sorted(query)).encode("utf-8")
                ).hexdigest(),
                "ledger_total": len(entries),
                "ledger_in_context": sum(len(rows) for rows in selected.values()),
                "ledger_omitted": omitted_ledger,
            },
            "turns": {
                "total": turn_total,
                "in_context": len(recent_rows),
                "compacted": compacted_total,
                "omitted": max(0, turn_total - len(recent_rows)),
            },
            "excluded": list(EXCLUDED_CONTENT),
        }
        return payload

    def _load_entries(self, conn: sqlite3.Connection, session_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT * FROM ledger WHERE session_id=? AND status!='superseded'"
            " ORDER BY created_at ASC, entry_id ASC",
            (session_id,),
        ).fetchall()
        now = int(self._clock())
        return [self._entry_dict(row, now=now) for row in rows]

    def _select_entries(self, entries: Sequence[dict], query: set[str],
                        cfg: Mapping[str, object], budget: Budget) -> tuple[dict, int]:
        buckets = {
            "goals": ("goal", cfg["goals"]),
            "accepted_decisions": ("decision", cfg["decisions"]),
            "rejected_decisions": ("decision", cfg["decisions"]),
            "verified_facts": ("fact", cfg["facts"]),
            "hypotheses": ("hypothesis", cfg["hypotheses"]),
            "unverified_claims": ("fact", cfg["claims"]),
            "plans": ("plan", cfg["plans"]),
            "dependencies": ("dependency", cfg["deps"]),
            "blocked_questions": ("blocked_question", cfg["blockers"]),
            "next_actions": ("next_action", cfg["actions"]),
            "evidence": ("evidence", cfg["evidence"]),
            "stale_items": (None, 3),
        }
        selected: dict[str, list[dict]] = {key: [] for key in buckets}
        total_limit = budget.max_ledger_items

        def score(entry: Mapping[str, object]) -> tuple:
            overlap = len(query & _tokens(str(entry["text"]) + " " + str(entry["rationale"])))
            pinned = int(entry["importance"]) >= 80 and entry["status"] in {"accepted", "verified"}
            return (
                -int(pinned),
                -overlap,
                -int(entry["importance"]),
                -int(entry["created_at"]),
                entry["entry_id"],
            )

        def bucket_for(entry: Mapping[str, object]) -> str | None:
            kind, status = entry["kind"], entry["status"]
            if entry["stale"]:
                return "stale_items"
            if kind == "decision":
                return "accepted_decisions" if status == "accepted" else (
                    "rejected_decisions" if status == "rejected" else None)
            if kind == "fact":
                return "verified_facts" if status == "verified" else (
                    "unverified_claims" if status in {"unverified", "refuted"} else None)
            return {
                "goal": "goals", "plan": "plans", "hypothesis": "hypotheses",
                "evidence": "evidence", "dependency": "dependencies",
                "blocked_question": "blocked_questions", "next_action": "next_actions",
            }.get(kind)

        used = 0
        for entry in sorted(entries, key=score):
            bucket = bucket_for(entry)
            if bucket is None:
                continue
            if len(selected[bucket]) >= int(buckets[bucket][1]):
                continue
            if used >= total_limit:
                continue
            selected[bucket].append(entry)
            used += 1
        omitted = max(0, len(entries) - used)
        return selected, omitted

    def _hard_fit(self, payload: Mapping[str, object], budget: Budget) -> dict:
        """Deterministic last resort: last turns + highest-signal ledger only."""
        payload = json.loads(json.dumps(payload))
        payload["mode"] = "fallback"
        payload["fallback"] = True
        payload["summaries"] = payload["summaries"][-1:]
        for summary in payload["summaries"]:
            summary["summary"] = summary["summary"][:120]
        payload["recent_turns"] = payload["recent_turns"][-3:]
        for turn in payload["recent_turns"]:
            turn["text"] = turn["text"][:100]
            turn["truncated"] = True
        keep = {"goals": 1, "accepted_decisions": 2, "rejected_decisions": 1,
                "verified_facts": 2, "hypotheses": 1, "unverified_claims": 1,
                "plans": 1, "dependencies": 1, "blocked_questions": 1,
                "next_actions": 2, "evidence": 1, "stale_items": 0}
        for bucket, limit in keep.items():
            items = payload["ledger"].get(bucket, [])[:limit]
            for item in items:
                item["text"] = item["text"][:120]
                item["rationale"] = item["rationale"][:80]
                item["evidence"] = item["evidence"][:1]
            payload["ledger"][bucket] = items
        payload["retrieval"]["ledger_in_context"] = sum(
            len(items) for items in payload["ledger"].values()
        )
        # If even the skeleton is too large, shrink prose fields further.
        guard = 0
        while estimate_tokens(_canonical(payload)) > budget.max_tokens and guard < 20:
            guard += 1
            for turn in payload["recent_turns"]:
                if len(turn["text"]) > 24:
                    turn["text"] = turn["text"][: max(24, len(turn["text"]) // 2)]
            for items in payload["ledger"].values():
                for item in items:
                    if len(item["text"]) > 24:
                        item["text"] = item["text"][: max(24, len(item["text"]) // 2)]
                    item["rationale"] = ""
            for summary in payload["summaries"]:
                summary["summary"] = summary["summary"][:60]
        return payload

    # ------------------------------------------------------------------
    # checkpoints
    # ------------------------------------------------------------------
    def _checkpoint_conn(self, conn: sqlite3.Connection, session_id: str, *, reason: str,
                         budget: Budget, provider: str | None = None) -> dict:
        session = self._session_row(conn, session_id)
        payload = self._render_conn(conn, session_id, budget, provider=provider)
        last_id = session["checkpoint_id"]
        if last_id and int(session["last_seq"]) > 0:
            existing = conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (last_id,)
            ).fetchone()
            if existing and int(existing["seq_high"]) == int(session["last_seq"]):
                return self._checkpoint_dict(existing, created=False)
        now = int(self._clock())
        checkpoint_id = "ck_" + _digest({
            "session": session_id, "seq": session["last_seq"],
            "checksum": payload["checksum"], "parent": last_id, "at": now,
        })[:20]
        mode = payload["mode"]
        conn.execute(
            "INSERT INTO checkpoints(checkpoint_id, session_id, thread_id, seq_high,"
            " turn_count, parent_id, envelope, checksum, budget, mode, reason, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (checkpoint_id, session_id, session["thread_id"], session["last_seq"],
             session["last_seq"], last_id, _canonical(payload), payload["checksum"],
             _canonical(budget.as_dict()), mode, reason, now),
        )
        conn.execute("UPDATE sessions SET checkpoint_id=? WHERE session_id=?",
                     (checkpoint_id, session_id))
        self._event_conn(conn, session_id, "checkpoint-created", {
            "checkpoint_id": checkpoint_id, "seq": session["last_seq"],
            "reason": reason, "mode": mode,
        })
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
        ).fetchone()
        return self._checkpoint_dict(row, created=True)

    def _checkpoint_dict(self, row: sqlite3.Row, *, created: bool) -> dict:
        envelope = json.loads(row["envelope"])
        return {
            "checkpoint_id": row["checkpoint_id"],
            "session_id": row["session_id"],
            "thread_id": row["thread_id"],
            "seq_high": row["seq_high"],
            "turn_count": row["turn_count"],
            "parent_id": row["parent_id"],
            "checksum": row["checksum"],
            "mode": row["mode"],
            "reason": row["reason"],
            "created_at": row["created_at"],
            "created": created,
            "tokens": estimate_tokens(_canonical(envelope)),
            "budget": json.loads(row["budget"]),
            "envelope": envelope,
        }

    def checkpoint(self, session_id: str, *, reason: str = "manual",
                   provider: str | None = None) -> dict:
        with self._write() as conn:
            result = self._checkpoint_conn(
                conn, session_id, reason=reason, budget=self.budget, provider=provider
            )
        if result["created"]:
            self.save_snapshot()
        return result

    def checkpoints(self, session_id: str) -> list[dict]:
        with self._read() as conn:
            self._session_row(conn, session_id)
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id=? ORDER BY seq_high ASC, created_at ASC",
                (session_id,),
            ).fetchall()
            return [self._checkpoint_dict(row, created=False) for row in rows]

    # ------------------------------------------------------------------
    # resume / continuation
    # ------------------------------------------------------------------
    def resume(self, session_id: str, *, provider: str | None = None,
               budget: Budget | None = None) -> dict:
        budget = budget or self.budget
        provider_hint = normalize_provider(provider) if provider else None
        envelope = self.context(session_id, provider=provider_hint, budget=budget)
        session = self.get_session(session_id)
        text = self.continuation_text(envelope, session=session)
        return {
            "schema": ENVELOPE_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "thread_id": session["thread_id"],
            "provider_hint": envelope["provider_hint"],
            "session_provider": session["provider"],
            "continuation": text,
            "envelope": envelope,
            "stats": {
                "turns_in_context": envelope["turns"]["in_context"],
                "turns_total": envelope["turns"]["total"],
                "compactions": session["compaction_count"],
                "checkpoints": len(self.checkpoints(session_id)),
                "tokens": estimate_tokens(_canonical(envelope)),
                "mode": envelope["mode"],
            },
        }

    @staticmethod
    def continuation_text(envelope: Mapping[str, object], *,
                          session: Mapping[str, object] | None = None) -> str:
        ledger = envelope["ledger"]
        lines = [
            "[NOVA3 planning continuation v1]",
            f"Session {envelope['session_id']} thread {envelope['thread_id']}",
            f"Project {envelope['project']}"
            + (f" @ {envelope['repo_revision']}" if envelope.get("repo_revision") else ""),
            f"Provider: {envelope['session_provider']} -> {envelope['provider_hint']}",
            f"Turns: {envelope['turns']['total']} total, "
            f"{envelope['turns']['in_context']} in window, "
            f"{envelope['turns']['compacted']} compacted",
        ]
        if session and session.get("last_seq"):
            lines.append(f"Last turn seq: {session['last_seq']}")
        if ledger["goals"]:
            lines.append("Goals:")
            lines.extend(f"- {item['text']}" for item in ledger["goals"][:3])
        if ledger["accepted_decisions"]:
            lines.append("Accepted decisions:")
            for item in ledger["accepted_decisions"][:6]:
                rationale = f" (rationale: {item['rationale']})" if item["rationale"] else ""
                lines.append(f"- [{item['entry_id']}] {item['text']}{rationale}")
        if ledger["rejected_decisions"]:
            lines.append("Rejected decisions:")
            for item in ledger["rejected_decisions"][:3]:
                rationale = f" (rationale: {item['rationale']})" if item["rationale"] else ""
                lines.append(f"- [{item['entry_id']}] {item['text']}{rationale}")
        if ledger["verified_facts"]:
            lines.append("Verified facts:")
            lines.extend(f"- {item['text']}" for item in ledger["verified_facts"][:6])
        if ledger["unverified_claims"]:
            lines.append("Unverified claims (model output is not evidence):")
            lines.extend(f"- {item['text']}" for item in ledger["unverified_claims"][:3])
        if ledger["blocked_questions"]:
            lines.append("Blocked questions:")
            lines.extend(f"- {item['text']}" for item in ledger["blocked_questions"][:4])
        if ledger["next_actions"]:
            lines.append("Next actions:")
            lines.extend(f"- {item['text']}" for item in ledger["next_actions"][:5])
        if envelope["summaries"]:
            lines.append(f"Compacted history ({envelope['summaries_omitted']} summaries omitted):")
            lines.append(envelope["summaries"][-1]["summary"][:600])
        if envelope["recent_turns"]:
            lines.append("Recent turns:")
            for turn in envelope["recent_turns"][-4:]:
                lines.append(f"- seq{turn['seq']} {turn['role']}: {turn['text'][:200]}")
        if envelope.get("fallback"):
            lines.append("NOTE: context was degraded to fit the reserved budget.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # portable import / export
    # ------------------------------------------------------------------
    def _export_envelope_conn(self, conn: sqlite3.Connection, session_id: str) -> dict:
        session = self._session_row(conn, session_id)
        turns = conn.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY seq ASC", (session_id,)
        ).fetchall()
        entries = conn.execute(
            "SELECT * FROM ledger WHERE session_id=? ORDER BY created_at ASC, entry_id ASC",
            (session_id,),
        ).fetchall()
        compactions = conn.execute(
            "SELECT * FROM compactions WHERE session_id=? ORDER BY to_seq ASC", (session_id,)
        ).fetchall()
        checkpoints = conn.execute(
            "SELECT * FROM checkpoints WHERE session_id=? ORDER BY created_at ASC, seq_high ASC",
            (session_id,),
        ).fetchall()
        providers = sorted({row["provider"] for row in turns} | {session["provider"]})
        return seal({
            "schema": ENVELOPE_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "exported_at": int(self._clock()),
            "session": self._session_dict(session, len(turns)),
            "providers": providers,
            "turns": [self._turn_dict(row) for row in turns],
            "ledger": [self._entry_dict(row) for row in entries],
            "compactions": [dict(row) for row in compactions],
            "checkpoints": [
                {
                    "checkpoint_id": row["checkpoint_id"],
                    "seq_high": row["seq_high"],
                    "parent_id": row["parent_id"],
                    "checksum": row["checksum"],
                    "mode": row["mode"],
                    "reason": row["reason"],
                    "budget": json.loads(row["budget"]),
                    "created_at": row["created_at"],
                    "envelope": json.loads(row["envelope"]),
                }
                for row in checkpoints
            ],
        })

    def export_envelope(self, session_id: str, path: str | os.PathLike[str] | None = None) -> dict:
        with self._read() as conn:
            self._session_row(conn, session_id)
            payload = self._export_envelope_conn(conn, session_id)
        if path is not None:
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, target)
        return payload

    def import_envelope(self, source: str | os.PathLike[str] | Mapping[str, object],
                        *, provider: str | None = None) -> dict:
        if isinstance(source, Mapping):
            payload = dict(source)
        else:
            path = Path(source).expanduser()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PlanningMemoryError("envelope-invalid", "cannot read envelope") from exc
        body = unseal(payload, code="envelope-invalid")
        if body.get("schema") != ENVELOPE_SCHEMA:
            raise PlanningMemoryError("envelope-invalid", "unknown envelope schema")
        if not isinstance(body.get("turns"), list) or not isinstance(body.get("ledger"), list):
            raise PlanningMemoryError("envelope-invalid", "turns/ledger must be lists")
        digest = body["checksum"]
        now = int(self._clock())
        with self._write() as conn:
            row = conn.execute(
                "SELECT session_id FROM imports WHERE envelope_digest=?", (digest,)
            ).fetchone()
            if row:
                return {"session_id": row["session_id"], "imported": False,
                        "reason": "already-imported"}
            session_id = self._import_conn(conn, body, now=now, trust_checksum=True,
                                           provider=provider)
            conn.execute(
                "INSERT INTO imports(envelope_digest, session_id, imported_at) VALUES(?,?,?)",
                (digest, session_id, now),
            )
        self.save_snapshot()
        return {"session_id": session_id, "imported": True,
                "provider": provider or body.get("session", {}).get("provider", "neutral")}

    def _import_conn(self, conn: sqlite3.Connection, body: Mapping[str, object], *,
                     now: int, trust_checksum: bool = False,
                     provider: str | None = None) -> str:
        session = body.get("session")
        if not isinstance(session, Mapping):
            raise PlanningMemoryError("envelope-invalid", "session metadata missing")
        original_id = str(session.get("session_id", ""))
        target_id = original_id
        existing = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id=?", (target_id,)
        ).fetchone()
        if existing:
            target_id = "s_" + _digest({"import": original_id, "checksum": body.get("checksum"),
                                        "at": now})[:16]
        thread_id = str(session.get("thread_id", "")) or self._new_id("th")
        if conn.execute("SELECT 1 FROM sessions WHERE thread_id=?", (thread_id,)).fetchone():
            thread_id = self._new_id("th")
        created = int(session.get("created_at", now))
        provider_name = normalize_provider(provider or session.get("provider") or "neutral")
        conn.execute(
            "INSERT INTO sessions(session_id, thread_id, project, repo_revision, provider,"
            " created_at, updated_at, last_seq, checkpoint_id, compaction_count)"
            " VALUES(?,?,?,?,?,?,?,?,NULL,?)",
            (target_id, thread_id, sanitize(str(session.get("project", "imported")), limit=200)[0],
             sanitize(str(session.get("repo_revision", "")), limit=200)[0],
             provider_name, created, int(session.get("updated_at", now)),
             int(session.get("last_seq", len(body.get("turns", [])))),
             int(session.get("compaction_count", len(body.get("compactions", []))))),
        )
        entry_map: dict[str, str] = {}
        for entry in body.get("ledger", []):
            if not isinstance(entry, Mapping):
                continue
            # Imported envelopes are untrusted input even when their checksum is
            # valid (the checksum is unkeyed). Re-validate every field instead
            # of copying whatever the file claims.
            kind = entry.get("kind", "fact")
            if kind not in KINDS:
                raise PlanningMemoryError("envelope-invalid", "unknown ledger kind")
            status = entry.get("status", DEFAULT_STATUS[kind])
            if status not in STATUS_BY_KIND[kind]:
                raise PlanningMemoryError("envelope-invalid", "invalid ledger status")
            writer_kind = str(entry.get("writer_kind", "model"))
            if writer_kind not in ("human", "model", "tool", "imported"):
                writer_kind = "model"
            if writer_kind == "model" and status in MODEL_INELIGIBLE.get(kind, frozenset()):
                # Model-supplied verification/acceptance never survives import.
                status = DEFAULT_STATUS[kind]
            trust = "trusted" if writer_kind in TRUSTED_WRITERS else "untrusted"
            text, redactions, flags = sanitize(entry.get("text", ""))
            rationale, r_redactions, r_flags = sanitize(entry.get("rationale", ""))
            evidence_text, e_redactions, e_flags = sanitize(
                _canonical(entry.get("evidence", [])))
            new_id = str(entry.get("entry_id"))
            if conn.execute("SELECT 1 FROM ledger WHERE entry_id=?", (new_id,)).fetchone():
                new_id = "mem_import_" + _digest({"id": new_id, "s": target_id})[:16]
            entry_map[str(entry.get("entry_id"))] = new_id
            conn.execute(
                "INSERT INTO ledger(entry_id, session_id, kind, status, text, rationale,"
                " importance, confidence, writer, writer_kind, trust, provenance, created_at,"
                " updated_at, supersedes, superseded_by, evidence, digest, flags, verified_by,"
                " verified_at, stale_after, tags)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id, target_id, kind, status,
                 text, rationale,
                 max(0, min(100, int(entry.get("importance", 50)))),
                 max(0.0, min(1.0, float(entry.get("confidence", 0.5)))),
                 sanitize(str(entry.get("writer", "imported")), limit=80)[0], writer_kind,
                 trust,
                 _canonical(entry.get("provenance", {})), int(entry.get("created_at", now)),
                 int(entry.get("updated_at", now)), None, None,
                 evidence_text, str(entry.get("digest", "")),
                 _canonical(sorted(set(
                     tuple(flags) + tuple(r_flags) + tuple(e_flags)
                     + tuple(str(f) for f in entry.get("flags", []) if isinstance(f, str))))),
                 entry.get("verified_by") if writer_kind != "model" else None,
                 entry.get("verified_at") if writer_kind != "model" else None,
                 entry.get("stale_after"),
                 _canonical([str(t) for t in entry.get("tags", []) if isinstance(t, str)])),
            )
        # link supersession chains after all entries exist
        for entry in body.get("ledger", []):
            if not isinstance(entry, Mapping):
                continue
            old_id = str(entry.get("entry_id"))
            if old_id not in entry_map:
                continue
            supersedes = entry.get("supersedes")
            superseded_by = entry.get("superseded_by")
            conn.execute(
                "UPDATE ledger SET supersedes=?, superseded_by=? WHERE entry_id=?",
                (entry_map.get(str(supersedes)) if supersedes else None,
                 entry_map.get(str(superseded_by)) if superseded_by else None,
                 entry_map[old_id]),
            )
        for turn in body.get("turns", []):
            if not isinstance(turn, Mapping):
                continue
            seq = int(turn.get("seq", 0))
            if seq <= 0:
                raise PlanningMemoryError("envelope-invalid", "turn seq must be positive")
            conn.execute(
                "INSERT OR REPLACE INTO turns(session_id, seq, role, provider, writer_kind,"
                " text, digest, created_at, checkpoint_id, compacted, redactions, flags)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (target_id, seq, str(turn.get("role", "user")),
                 normalize_provider(turn.get("provider", provider_name)),
                 str(turn.get("writer_kind", "model")), str(turn.get("text", "")),
                 str(turn.get("digest", "")), int(turn.get("created_at", now)),
                 None, int(bool(turn.get("compacted", False))),
                 _canonical(turn.get("redactions", [])), _canonical(turn.get("flags", []))),
            )
        checkpoint_map: dict[str, str] = {}
        for checkpoint in body.get("checkpoints", []):
            if not isinstance(checkpoint, Mapping):
                continue
            old_id = str(checkpoint.get("checkpoint_id"))
            checkpoint_map[old_id] = "ck_import_" + _digest({"id": old_id, "s": target_id})[:20]
        for checkpoint in body.get("checkpoints", []):
            if not isinstance(checkpoint, Mapping):
                continue
            old_id = str(checkpoint.get("checkpoint_id"))
            parent = checkpoint.get("parent_id")
            envelope = checkpoint.get("envelope", {})
            conn.execute(
                "INSERT INTO checkpoints(checkpoint_id, session_id, thread_id, seq_high,"
                " turn_count, parent_id, envelope, checksum, budget, mode, reason, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (checkpoint_map[old_id], target_id, thread_id, int(checkpoint.get("seq_high", 0)),
                 int(checkpoint.get("seq_high", 0)),
                 checkpoint_map.get(str(parent)) if parent else None,
                 _canonical(envelope), str(checkpoint.get("checksum", "")),
                 _canonical(checkpoint.get("budget", {})), str(checkpoint.get("mode", "full")),
                 str(checkpoint.get("reason", "imported")),
                 int(checkpoint.get("created_at", now))),
            )
        last_ck = None
        for turn in body.get("turns", []):
            if isinstance(turn, Mapping) and turn.get("checkpoint_id"):
                last_ck = checkpoint_map.get(str(turn.get("checkpoint_id")), last_ck)
        if last_ck is None and checkpoint_map:
            last_ck = list(checkpoint_map.values())[-1]
        for turn in body.get("turns", []):
            if not isinstance(turn, Mapping):
                continue
            ck = checkpoint_map.get(str(turn.get("checkpoint_id"))) if turn.get("checkpoint_id") else None
            if ck:
                conn.execute(
                    "UPDATE turns SET checkpoint_id=? WHERE session_id=? AND seq=?",
                    (ck, target_id, int(turn.get("seq", 0))),
                )
        conn.execute("UPDATE sessions SET checkpoint_id=? WHERE session_id=?", (last_ck, target_id))
        for compaction in body.get("compactions", []):
            if not isinstance(compaction, Mapping):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO compactions(compaction_id, session_id, from_seq, to_seq,"
                " turn_count, summary, digest, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (f"cmp_import_{_digest({'id': compaction.get('compaction_id'), 's': target_id})[:18]}",
                 target_id, int(compaction.get("from_seq", 0)), int(compaction.get("to_seq", 0)),
                 int(compaction.get("turn_count", 0)), str(compaction.get("summary", "")),
                 str(compaction.get("digest", "")), int(compaction.get("created_at", now))),
            )
        self._event_conn(conn, target_id, "session-imported", {
            "original_session_id": original_id,
            "checksum_verified": trust_checksum,
            "turns": len(body.get("turns", [])),
            "ledger": len(body.get("ledger", [])),
        })
        return target_id

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status(self) -> dict:
        with self._read() as conn:
            version = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            ledger = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
            checkpoints = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        return {
            "schema_version": int(version["value"]) if version else None,
            "supported_schema_version": SCHEMA_VERSION,
            "store": str(self.root),
            "sessions": sessions,
            "turns": turns,
            "ledger_entries": ledger,
            "checkpoints": checkpoints,
            "snapshot": self.snapshot_info(),
            "budget": self.budget.as_dict(),
        }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _read_text(value: str | None, file_path: str | None) -> str:
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise PlanningMemoryError("io", "cannot read text file") from exc
    if value is None:
        raise PlanningMemoryError("text", "provide --text or --text-file")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 tools/planning_memory.py",
        description="Offline persistent planning memory (no network, no model calls).",
    )
    parser.add_argument("--store", default=None, help="private store directory")
    parser.add_argument("--repo-root", default=None,
                        help="repository root used for containment checks")
    sub = parser.add_subparsers(dest="command", required=True)

    def store_args(p: "argparse.ArgumentParser") -> None:
        p.add_argument("--session", required=True)
        p.add_argument("--provider", default=None)

    init = sub.add_parser("init", help="create a planning session")
    init.add_argument("--project", required=True)
    init.add_argument("--provider", default="neutral")
    init.add_argument("--repo-revision", default="")

    turn = sub.add_parser("turn", help="append a turn and seal a checkpoint")
    store_args(turn)
    turn.add_argument("--role", default="user", choices=sorted(ROLES))
    turn.add_argument("--writer-kind", default=None, choices=["model", "human", "tool"])
    turn.add_argument("--text")
    turn.add_argument("--text-file")

    remember = sub.add_parser("remember", help="add a structured ledger entry")
    store_args(remember)
    remember.add_argument("--kind", required=True, choices=sorted(KINDS))
    remember.add_argument("--status", default=None)
    remember.add_argument("--rationale", default="")
    remember.add_argument("--importance", type=int, default=50)
    remember.add_argument("--confidence", type=float, default=0.5)
    remember.add_argument("--writer", default="planner")
    remember.add_argument("--writer-kind", default="model", choices=["model", "human", "tool"])
    remember.add_argument("--evidence", action="append", default=[],
                          help="locator or locator=sha256")
    remember.add_argument("--text")
    remember.add_argument("--text-file")

    decision = sub.add_parser("decision", help="record a decision")
    store_args(decision)
    decision.add_argument("--text", required=True)
    decision.add_argument("--decision", default="accepted",
                          choices=["accepted", "rejected", "proposed"])
    decision.add_argument("--rationale", default="")
    decision.add_argument("--writer", default="owner")
    decision.add_argument("--writer-kind", default="model", choices=["model", "human", "tool"])

    ctx = sub.add_parser("context", help="render the deterministic context envelope")
    store_args(ctx)
    ctx.add_argument("--out", default=None)

    resume = sub.add_parser("resume", help="print continuation context only")
    store_args(resume)

    checkpoint = sub.add_parser("checkpoint", help="seal an explicit checkpoint")
    store_args(checkpoint)

    ledger = sub.add_parser("ledger", help="list ledger entries")
    store_args(ledger)
    ledger.add_argument("--kind", default=None)

    export = sub.add_parser("export", help="write a portable continuation envelope")
    store_args(export)
    export.add_argument("--out", required=True)

    imp = sub.add_parser("import", help="import a portable continuation envelope")
    imp.add_argument("file")
    imp.add_argument("--provider", default=None)

    verify = sub.add_parser("verify", help="human/tool verification of a claim")
    verify.add_argument("--entry", required=True)
    verify.add_argument("--evidence", action="append", required=True,
                        help="locator=sha256")
    verify.add_argument("--verifier", default="owner")
    verify.add_argument("--verifier-kind", required=True, choices=["human", "tool"])

    sub.add_parser("status", help="store status and schema version")
    sub.add_parser("doctor", help="validate store, snapshot and containment")

    args = parser.parse_args(argv)
    try:
        memory = PlanningMemory(args.store, repo_root=args.repo_root)
        if args.command == "init":
            _emit(memory.create_session(args.project, provider=args.provider,
                                        repo_revision=args.repo_revision))
            return 0
        if args.command == "turn":
            _emit(memory.append_turn(
                args.session, _read_text(args.text, args.text_file), role=args.role,
                provider=args.provider, writer_kind=args.writer_kind,
            ))
            return 0
        if args.command == "remember":
            evidence = []
            for item in args.evidence:
                locator, _, digest = item.partition("=")
                evidence.append({"locator": locator, "digest": digest})
            _emit(memory.remember(
                args.session, args.kind, _read_text(args.text, args.text_file),
                status=args.status, rationale=args.rationale,
                evidence=evidence, importance=args.importance,
                confidence=args.confidence, writer=args.writer,
                writer_kind=args.writer_kind, provider=args.provider,
            ))
            return 0
        if args.command == "decision":
            _emit(memory.record_decision(
                args.session, args.text, decision=args.decision,
                rationale=args.rationale, writer=args.writer,
                writer_kind=args.writer_kind,
            ))
            return 0
        if args.command == "context":
            payload = memory.context(args.session, provider=args.provider)
            if args.out:
                Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True),
                                          encoding="utf-8")
                _emit({"written": args.out, "checksum": payload["checksum"],
                       "tokens": estimate_tokens(_canonical(payload))})
            else:
                _emit(payload)
            return 0
        if args.command == "resume":
            result = memory.resume(args.session, provider=args.provider)
            print(result["continuation"])
            _emit({"stats": result["stats"], "checksum": result["envelope"]["checksum"]})
            return 0
        if args.command == "checkpoint":
            _emit(memory.checkpoint(args.session, provider=args.provider))
            return 0
        if args.command == "ledger":
            _emit(memory.ledger(args.session, kind=args.kind))
            return 0
        if args.command == "export":
            payload = memory.export_envelope(args.session, path=args.out)
            _emit({"written": args.out, "checksum": payload["checksum"],
                   "turns": len(payload["turns"]), "ledger": len(payload["ledger"])})
            return 0
        if args.command == "import":
            _emit(memory.import_envelope(args.file, provider=args.provider))
            return 0
        if args.command == "verify":
            evidence = []
            for item in args.evidence:
                locator, _, digest = item.partition("=")
                evidence.append({"locator": locator, "digest": digest})
            _emit(memory.verify_entry(args.entry, evidence=evidence,
                                      verifier=args.verifier,
                                      verifier_kind=args.verifier_kind))
            return 0
        if args.command == "status":
            _emit(memory.status())
            return 0
        if args.command == "doctor":
            info = memory.status()
            try:
                # Diagnostic only: never rewrite/replace a corrupt handoff here.
                snapshot = memory.load_snapshot(rebuild_if_invalid=False)
                info["snapshot_valid"] = True
                info["snapshot_checksum"] = snapshot["checksum"]
            except PlanningMemoryError as exc:
                info["snapshot_valid"] = False
                info["snapshot_error"] = exc.code
            _emit(info)
            return 0 if info["snapshot_valid"] else 2
    except PlanningMemoryError as exc:
        _emit({"error": exc.code, "detail": str(exc)})
        return 2
    return 2
