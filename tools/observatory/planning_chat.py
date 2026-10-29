"""Owner/admin Plan & Chat workspace for the NOVA3 Control Center.

This module is deliberately UI-agnostic: :class:`ChatWorkspace` owns the durable
planning thread, provider selection, context health, decisions, checkpoints,
private export and task-proposal drafts.  ``observer.py`` only paints it and
forwards keys, which keeps every privacy and authorization rule testable
offline with fake adapters and no curses.

Hard rules (issue #140):

* The planning store is the #139 per-user private ledger.  A contributor never
  opens it: role is resolved from a fresh ``operator_ops.owner_access`` call on
  every workspace entry and every privileged action, and denial fails closed.
* Model output is untrusted prose.  It can never accept a decision, verify a
  fact or publish a task; the owner must explicitly pin or publish.
* Provider switching, compaction and restarts never touch a worker.  This
  module has no worker/coordinator import or endpoint.
* No transcript, prompt, secret or absolute private path is written to the
  public repository.  Planning memory refuses in-repo stores; exports and
  drafts are written 0600 and are refused inside the checkout.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import types
from typing import Callable, Iterable, Mapping, Protocol, Sequence, cast

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_planning_modules():
    """Load #139/#132 as a private package.

    Using the real ``nova3_team`` name would make the Control Center import the
    project package into unrelated unittest processes and leak that package's
    ``__path__`` into fixtures.  A private package name keeps the same relative
    imports (``planning_memory -> planning_bridge -> access_policy``) without
    registering a public top-level module.
    """
    package_name = "nova3_private_control_planning"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(_REPO_ROOT / "nova3_team")]
        sys.modules[package_name] = package
    memory = importlib.import_module(package_name + ".planning_memory")
    bridge = importlib.import_module(package_name + ".planning_bridge")
    return memory, bridge


planning_memory, planning_bridge = _load_planning_modules()

MAX_TITLE = 80
MAX_PROMPT = 8000
MAX_TRANSCRIPT_VIEW = 40
MAX_TOOL_EVENTS = 60
PHRASE_PREFIX = "PUBLISH "


class ChatError(RuntimeError):
    """Deterministic user-facing failure.  Never echoes credentials."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail[:300]
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


@dataclass(frozen=True)
class ProviderInfo:
    pid: str
    label: str
    models: tuple[str, ...]
    kind: str
    note: str


# Only providers the Control Center can genuinely drive through an injected
# adapter are listed.  "not connected" is displayed honestly; there is no
# silent shell fallback and no pretence that a login is an API key.
PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo("hermes", "Hermes (native planner)", ("hermes-planner",), "native",
                 "Native planner adapter; a worker session is never reused."),
    ProviderInfo("opencode", "OpenCode", ("default",), "cli",
                 "Interactive CLI adapter; separate from worker claims."),
    ProviderInfo("openrouter", "OpenRouter (API)", ("planner-default",), "api",
                 "Separately billed API adapter; no ChatGPT login reuse."),
    ProviderInfo("codex", "Codex", ("default",), "cli",
                 "Interactive CLI adapter; never the worker's session."),
    ProviderInfo("claude", "Claude", ("default",), "cli",
                 "Interactive CLI adapter; separate token wallet."),
    ProviderInfo("gemini", "Gemini", ("default",), "cli",
                 "Interactive CLI adapter; separate token wallet."),
)
PROVIDER_BY_ID = {row.pid: row for row in PROVIDERS}
DEFAULT_PROVIDER = "hermes"

CONTRIBUTOR_HELP: tuple[str, ...] = (
    "PLAN & CHAT is owner/admin only.",
    "Contributors can work approved issues and keep a private local help note;",
    "the shared project planning memory is never opened for this account.",
    "Nothing here can publish, queue, merge, or start/stop a worker.",
    "",
    "Ask a maintainer to publish a reviewed task when planning is needed.",
    "Keys: ? help, Q quit, U accounts, G sync. Role is rechecked every action.",
)


class ProviderAdapter(Protocol):
    """Minimal interactive adapter seam, injected by the host or tests."""

    def stream(
        self,
        prompt: str,
        context: Mapping[str, object],
        *,
        emit: Callable[[Mapping[str, object]], None],
        cancelled: threading.Event,
        provider: str,
        model: str,
    ) -> str: ...


class ScriptedProvider:
    """Offline fake provider used by tests, previews and demos.

    Emits only already-public activity labels ("tool event"), never reasoning
    text.  ``chunks`` controls streaming granularity; ``crash`` raises a real
    exception so error paths are exercised instead of described.
    """

    def __init__(self, reply: str = "Synthetic planning reply.", *, chunks: int = 3,
                 crash: str | None = None, tool_events: Sequence[str] = ("context",)) -> None:
        self.reply = reply
        self.chunks = max(1, chunks)
        self.crash = crash
        self.tool_events = tuple(tool_events)
        self.calls: list[dict] = []
        self.cancelled_observed = False

    def stream(self, prompt, context, *, emit, cancelled, provider, model):
        self.calls.append({"prompt": prompt, "provider": provider, "model": model,
                           "tokens": context.get("stats", {}).get("tokens")})
        for name in self.tool_events:
            emit({"kind": "tool", "name": str(name), "state": "ok",
                  "summary": "local fixture activity"})
        if self.crash:
            raise RuntimeError(self.crash)
        size = max(1, len(self.reply) // self.chunks + 1)
        sent = ""
        for index in range(0, len(self.reply), size):
            if cancelled.is_set():
                self.cancelled_observed = True
                raise Cancelled()
            piece = self.reply[index:index + size]
            sent += piece
            emit({"kind": "delta", "text": piece})
            time.sleep(0)
        return sent


class Cancelled(Exception):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _user_key(user: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@-]", "", str(user or "unknown"))[:64] or "unknown"
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def _iso(epoch: float | None) -> str:
    if not epoch:
        return "UNKNOWN"
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _private_write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, json.dumps(payload, indent=1, sort_keys=True).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def provider_state(pid: str, adapters: Mapping[str, object] | None) -> str:
    return "READY" if adapters and adapters.get(pid) else "NOT CONNECTED"


class ChatWorkspace:
    """One signed-in user's private planning workspace."""

    def __init__(
        self,
        *,
        store_root: str | os.PathLike[str],
        repo: str = "N-O-V-A-3-RE-Contributors/nova3-reconstruction",
        user: str = "owner",
        role_provider: Callable[[], Mapping[str, object]] | None = None,
        adapters: Mapping[str, ProviderAdapter] | None = None,
        budget: planning_memory.Budget | None = None,
        clock: Callable[[], float] | None = None,
        repo_root: str | os.PathLike[str] | None = None,
        allow_repo_path: bool = False,
    ) -> None:
        self.repo = repo
        self.user = re.sub(r"[^A-Za-z0-9_.@-]", "", str(user or "unknown"))[:64] or "unknown"
        self._role_provider = role_provider
        self.adapters = dict(adapters or {})
        self._budget = budget or planning_memory.DEFAULT_BUDGET
        self._clock = clock or time.time
        self._repo_root = Path(repo_root).expanduser().resolve() if repo_root else _REPO_ROOT
        self._allow_repo_path = allow_repo_path
        base = Path(store_root).expanduser()
        self.user_dir = base / "users" / _user_key(self.user)
        self.memory_dir = self.user_dir / "memory"
        self.meta_path = self.user_dir / "threads.json"
        self.drafts_dir = self.user_dir / "drafts"
        self.exports_dir = self.user_dir / "exports"
        self._lock = threading.RLock()
        self._memory: planning_memory.PlanningMemory | None = None
        self._memory_error: str | None = None
        self._meta = self._load_meta()
        self._session_id: str | None = self._meta.get("active")
        self._role: dict = self._deny("Role has not been resolved yet")
        self._autosave = "IDLE"
        self._error: str | None = None
        self._context_refreshed = False
        self._streaming = False
        self._cancel_event: threading.Event | None = None
        self._events: list[dict] = []
        self._last_prompt: str | None = None
        self._last_reply: str | None = None
        self._proposal_error: str | None = None
        self._ckpt_count: int | None = None
        # Entry check: the workspace is only useful after a fresh role decision.
        self.resolve_role()

    # ------------------------------------------------------------------
    # authorization
    # ------------------------------------------------------------------
    @staticmethod
    def _deny(reason: str, role: str = "UNKNOWN") -> dict:
        return {"allowed": False, "role": role, "reason": reason,
                "account": "UNKNOWN", "organization_owner": False,
                "source": "planning chat"}

    def resolve_role(self) -> dict:
        """Re-resolve authority from the injected operator seam, fail closed."""
        provider = self._role_provider
        if provider is None:
            status = self._deny("No authorization seam configured")
        else:
            try:
                raw = provider()
            except Exception as exc:  # a transport failure must deny, not crash
                status = self._deny("Authorization recheck failed: " + str(exc)[:120])
            else:
                if not isinstance(raw, Mapping):
                    status = self._deny("Authorization response was malformed")
                else:
                    role = str(raw.get("role") or "UNKNOWN").upper()
                    allowed = bool(raw.get("allowed")) and (
                        role == "ADMIN" or bool(raw.get("organization_owner")))
                    status = {
                        "allowed": allowed,
                        "role": role if role in
                        {"ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ", "UNKNOWN"} else "UNKNOWN",
                        "reason": str(raw.get("reason") or ("repository ADMIN" if allowed else
                                                            "owner/admin required"))[:200],
                        "account": str(raw.get("account") or "UNKNOWN")[:64],
                        "organization_owner": bool(raw.get("organization_owner")),
                        "source": str(raw.get("source") or "operator_ops.owner_access")[:80],
                    }
        with self._lock:
            self._role = status
        return dict(status)

    @property
    def role(self) -> dict:
        return dict(self._role)

    def owner(self) -> bool:
        return bool(self._role.get("allowed"))

    def guard(self, action: str) -> dict:
        """Fresh check at every privileged boundary (entry and each action)."""
        status = self.resolve_role()
        if not status["allowed"]:
            with self._lock:
                self._error = None
            raise ChatError("owner-required",
                            f"{action} requires a verified organization owner or repository ADMIN"
                            + (f" ({status['role']})" if status["role"] != "UNKNOWN" else ""))
        return status

    # ------------------------------------------------------------------
    # private metadata (titles, provider/model, pins, archive)
    # ------------------------------------------------------------------
    def _load_meta(self) -> dict:
        data = _read_json(self.meta_path)
        threads = data.get("threads") if isinstance(data, dict) else None
        return {"schema": 1,
                "threads": threads if isinstance(threads, dict) else {},
                "active": data.get("active") if isinstance(data, dict) else None}

    def _thread_meta(self, session_id: str) -> dict:
        entry = self._meta["threads"].get(session_id)
        return entry if isinstance(entry, dict) else {}

    def _save_meta(self) -> None:
        try:
            _private_write(self.meta_path, self._meta)
        except OSError:
            # Metadata is convenience only; the ledger remains authoritative.
            pass

    def _touch_thread(self, session_id: str, **fields) -> None:
        with self._lock:
            entry = self._meta["threads"].setdefault(session_id, {})
            entry.update(fields)
            entry.setdefault("created_at", int(self._clock()))
            self._meta["active"] = session_id
            self._save_meta()

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------
    def _open_memory(self) -> planning_memory.PlanningMemory:
        with self._lock:
            if self._memory is not None:
                return self._memory
            if self._memory_error:
                raise ChatError("ledger", self._memory_error)
            try:
                self._memory = planning_memory.PlanningMemory(
                    self.memory_dir, budget=self._budget, clock=self._clock,
                    repo_root=self._repo_root, allow_repo_path=self._allow_repo_path)
            except planning_memory.PlanningMemoryError as exc:
                self._memory_error = getattr(exc, "code", "ledger")
                raise ChatError("ledger", str(exc)[:200]) from None
            except (OSError, ValueError) as exc:
                self._memory_error = "io"
                raise ChatError("ledger", str(exc)[:200]) from None
            self._remember_threads()
            return self._memory

    def _remember_threads(self) -> None:
        """Import sessions created by a previous run/CLI into local metadata."""
        if self._memory is None:
            return
        for session in self._memory.list_sessions():
            self._meta["threads"].setdefault(session["session_id"], {
                "created_at": session["created_at"],
                "provider": session["provider"],
                "model": PROVIDER_BY_ID.get(session["provider"],
                                            PROVIDER_BY_ID[DEFAULT_PROVIDER]).models[0],
            })

    def close(self) -> None:
        memory, self._memory = self._memory, None
        if memory is not None:
            try:
                memory.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # threads
    # ------------------------------------------------------------------
    def _provider_default_model(self, pid: str) -> str:
        return PROVIDER_BY_ID[pid].models[0]

    def current_session(self) -> str | None:
        if self._session_id and self._session_id in self._meta["threads"]:
            return self._session_id
        self._session_id = None
        return None

    def thread_rows(self) -> list[dict]:
        rows: list[dict] = []
        if not self.owner():
            return rows
        try:
            sessions = {s["session_id"]: s for s in self._open_memory().list_sessions()}
        except ChatError:
            return rows
        for session_id, meta in self._meta["threads"].items():
            if not isinstance(meta, dict) or meta.get("archived"):
                continue
            session = sessions.get(session_id)
            if session is None:
                continue
            rows.append({
                "session_id": session_id,
                "title": str(meta.get("title") or "Untitled plan")[:MAX_TITLE],
                "provider": str(meta.get("provider") or session["provider"]),
                "model": str(meta.get("model") or self._provider_default_model(session["provider"])),
                "turns": session["turn_count"],
                "compactions": session["compaction_count"],
                "active": session_id == self.current_session(),
                "updated": _iso(session["updated_at"]),
            })
        rows.sort(key=lambda row: row["updated"], reverse=True)
        return rows

    def create_thread(self, title: str = "", *, provider: str = DEFAULT_PROVIDER) -> dict:
        self.guard("create planning thread")
        if provider not in PROVIDER_BY_ID:
            raise ChatError("provider", "unknown provider")
        clean_title = self._clean_title(title) or "New plan"
        memory = self._open_memory()
        session = memory.create_session(self.repo.split("/")[-1], provider=provider,
                                        repo_revision=self._repo_revision())
        self._touch_thread(session["session_id"], title=clean_title, provider=provider,
                           model=self._provider_default_model(provider))
        self._session_id = session["session_id"]
        self._ckpt_count = 0
        self._autosave = "SAVED · new thread"
        self._context_refreshed = False
        self._error = None
        self._last_prompt = None
        self._last_reply = None
        return session

    def select_thread(self, session_id: str) -> dict:
        self.guard("open planning thread")
        if session_id not in self._meta["threads"]:
            raise ChatError("thread", "unknown thread")
        self._session_id = session_id
        self._ckpt_count = None
        self._touch_thread(session_id)
        self._error = None
        self._context_refreshed = False
        return self.state()

    def select_offset(self, delta: int) -> dict | None:
        rows = self.thread_rows()
        if not rows:
            return None
        current = self.current_session()
        ids = [row["session_id"] for row in rows]
        index = ids.index(current) if current in ids else 0
        return self.select_thread(ids[(index + int(delta)) % len(ids)])

    def rename_thread(self, title: str) -> None:
        self.guard("rename planning thread")
        session = self._require_session()
        self._touch_thread(session, title=self._clean_title(title) or "Untitled plan")

    def archive_thread(self) -> dict:
        """Hide a thread from history.  Sealed checkpoints stay local by design."""
        self.guard("remove planning thread from history")
        session = self._require_session()
        self._touch_thread(session, archived=True)
        self._meta["active"] = None
        self._session_id = None
        self._ckpt_count = None
        self._save_meta()
        return {"archived": session,
                "note": "thread hidden; sealed local checkpoints are retained"}

    @staticmethod
    def _clean_title(title: str) -> str:
        clean, _, _ = planning_memory.sanitize(str(title or ""), limit=MAX_TITLE)
        return " ".join(clean.split())[:MAX_TITLE]

    def _repo_revision(self) -> str:
        try:
            return os.environ.get("NOVA3_PLANNING_REVISION", "")[:64]
        except Exception:
            return ""

    def _require_session(self) -> str:
        session = self.current_session()
        if not session:
            rows = self.thread_rows()
            if rows:
                session = rows[0]["session_id"]
                self._session_id = session
        if not session:
            raise ChatError("no-thread", "create or select a planning thread first")
        return session

    # ------------------------------------------------------------------
    # provider / model
    # ------------------------------------------------------------------
    def set_provider(self, pid: str) -> dict:
        """Switch the planner provider; never touches a worker or its model."""
        self.guard("switch planning provider")
        if pid not in PROVIDER_BY_ID:
            raise ChatError("provider", "unknown provider")
        session = self._require_session()
        meta = self._thread_meta(session)
        model = (meta.get("model_by_provider") or {}).get(pid) or self._provider_default_model(pid)
        self._touch_thread(session, provider=pid, model=model)
        self._context_refreshed = False
        return {"provider": pid, "model": model,
                "state": provider_state(pid, self.adapters),
                "note": "planning session only; no worker was started, stopped or reassigned"}

    def cycle_provider(self, delta: int = 1) -> dict:
        session = self._require_session()
        ids = [row.pid for row in PROVIDERS]
        current = self._thread_meta(session).get("provider", DEFAULT_PROVIDER)
        index = ids.index(current) if current in ids else 0
        return self.set_provider(ids[(index + int(delta)) % len(ids)])

    def set_model(self, model: str) -> dict:
        self.guard("switch planning model")
        session = self._require_session()
        meta = self._thread_meta(session)
        pid = meta.get("provider", DEFAULT_PROVIDER)
        allowed = PROVIDER_BY_ID[pid].models
        if model not in allowed:
            raise ChatError("model", f"{pid} models: {', '.join(allowed)}")
        per_provider = dict(meta.get("model_by_provider") or {})
        per_provider[pid] = model
        self._touch_thread(session, model=model, model_by_provider=per_provider)
        return {"provider": pid, "model": model}

    def cycle_model(self) -> dict:
        session = self._require_session()
        meta = self._thread_meta(session)
        pid = meta.get("provider", DEFAULT_PROVIDER)
        models = PROVIDER_BY_ID[pid].models
        current = meta.get("model", models[0])
        index = models.index(current) if current in models else 0
        return self.set_model(models[(index + 1) % len(models)])

    def provider_rows(self) -> list[dict]:
        session = self.current_session()
        meta = self._thread_meta(session) if session else {}
        active = meta.get("provider", DEFAULT_PROVIDER)
        return [{"pid": row.pid, "label": row.label, "kind": row.kind,
                 "models": row.models, "note": row.note,
                 "state": provider_state(row.pid, self.adapters),
                 "active": row.pid == active,
                 "model": meta.get("model") if row.pid == active else row.models[0]}
                for row in PROVIDERS]

    # ------------------------------------------------------------------
    # chat turn + automatic memory
    # ------------------------------------------------------------------
    def _emit(self, event: Mapping[str, object], sink: Callable | None) -> None:
        kind = str(event.get("kind") or "status")
        if kind not in {"delta", "tool", "status"}:
            return  # never accept a "reasoning"/chain-of-thought channel
        payload: dict[str, object] = {"kind": kind}
        if kind == "delta":
            clean, _, _ = planning_memory.sanitize(str(event.get("text") or ""), limit=4000)
            if not clean:
                return
            payload["text"] = clean
        else:
            payload["name"] = str(event.get("name") or "activity")[:40]
            payload["state"] = str(event.get("state") or "ok")[:20]
            summary, _, _ = planning_memory.sanitize(str(event.get("summary") or ""), limit=200)
            payload["summary"] = summary
        with self._lock:
            self._events.append(dict(payload))
            del self._events[:-MAX_TOOL_EVENTS]
        if sink is not None:
            sink(dict(payload))

    def run_turn(self, prompt: str, *, on_event: Callable[[dict], None] | None = None,
                 adapter: ProviderAdapter | None = None) -> dict:
        """Send one owner message, streaming already-redacted activity only.

        The user turn is checkpointed before the provider is called.  A provider
        crash or cancellation therefore leaves the durable turn saved and the
        UI shows an explicit error instead of claiming success.
        """
        self.guard("send planning message")
        session = self._require_session()
        text = str(prompt or "").strip()
        if not text:
            raise ChatError("prompt", "message is empty")
        if len(text) > MAX_PROMPT:
            raise ChatError("prompt", f"message exceeds {MAX_PROMPT} characters")
        memory = self._open_memory()
        meta = self._thread_meta(session)
        pid = str(meta.get("provider") or DEFAULT_PROVIDER)
        model = str(meta.get("model") or self._provider_default_model(pid))
        if not meta.get("title") or meta.get("title") == "New plan":
            self._touch_thread(session, title=self._clean_title(text[:MAX_TITLE]) or "New plan")

        with self._lock:
            self._error = None
            self._last_reply = None
            self._events = []
        created = memory.append_turn(session, text, role="user", writer_kind="human",
                                     provider=pid)
        self._ckpt_count = (self._ckpt_count or 0) + 1
        compacted = bool(created["compacted"])
        self._autosave = f"SAVED · seq {created['seq']} · checkpoint {created['checkpoint_id'][:14]}"
        if created["compacted"]:
            self._context_refreshed = True

        chosen = adapter or self.adapters.get(pid)
        if chosen is None:
            self._error = f"{pid}: NOT CONNECTED — no interactive adapter in this build"
            self._autosave = self._autosave + " · reply NOT saved"
            return {"status": "provider-unavailable", "provider": pid,
                    "error": self._error, "seq": created["seq"]}

        context = memory.context(session, provider=pid)
        self._last_prompt = text
        cancel = threading.Event()
        with self._lock:
            self._cancel_event = cancel
            self._streaming = True
        reply = ""
        status = "ok"
        error = None
        try:
            reply = chosen.stream(text, context, emit=lambda event: self._emit(event, on_event),
                                  cancelled=cancel, provider=pid, model=model)
        except Cancelled:
            status, error = "cancelled", "cancelled by owner; partial output kept locally"
        except Exception as exc:
            status = "provider-error"
            error, _, _ = planning_memory.sanitize(
                f"{pid} failed: {type(exc).__name__}: {str(exc)[:160]}", limit=300)
        finally:
            with self._lock:
                self._streaming = False
                self._cancel_event = None

        reply = str(reply or "").strip()
        if reply:
            saved = memory.append_turn(session, reply, role="assistant",
                                       writer_kind="model", provider=pid)
            self._ckpt_count = (self._ckpt_count or 0) + 1
            compacted = compacted or bool(saved["compacted"])
            self._last_reply = reply
            self._autosave = (f"SAVED · seq {saved['seq']} · "
                              f"checkpoint {saved['checkpoint_id'][:14]}")
            if saved["compacted"]:
                self._context_refreshed = True
        elif status == "ok":
            status, error = "provider-error", f"{pid} returned no text; nothing claimed as saved"

        if error:
            self._error = error
            self._autosave += " · reply NOT saved"
        return {"status": status, "provider": pid, "model": model,
                "seq": created["seq"], "error": error,
                "compacted": compacted,
                "context_tokens": created["context_tokens"],
                "context_mode": created["context_mode"]}

    def cancel(self) -> bool:
        with self._lock:
            event = self._cancel_event
        if event is None:
            return False
        event.set()
        return True

    def retry(self) -> dict:
        self.guard("retry planning message")
        if not self._last_prompt:
            raise ChatError("retry", "no previous message in this session")
        return self.run_turn(self._last_prompt)

    # ------------------------------------------------------------------
    # structured ledger
    # ------------------------------------------------------------------
    def _ledger(self, kind: str) -> list[dict]:
        session = self.current_session()
        if not session or not self.owner():
            return []
        try:
            return self._open_memory().ledger(session, kind=kind)
        except ChatError:
            return []

    def objective(self) -> str:
        goals = [row for row in self._ledger("goal") if row["status"] == "active"]
        return goals[-1]["text"] if goals else "Not set"

    def next_actions(self) -> list[str]:
        return [row["text"] for row in self._ledger("next_action")
                if row["status"] in {"pending", "open"}]

    def open_questions(self) -> list[str]:
        return [row["text"] for row in self._ledger("blocked_question")
                if row["status"] == "open"]

    def decisions(self) -> list[dict]:
        return [row for row in self._ledger("decision") if row["status"] == "accepted"]

    def set_objective(self, text: str) -> dict:
        self.guard("set planning objective")
        session = self._require_session()
        clean, _, _ = planning_memory.sanitize(text, limit=1000)
        if not clean.strip():
            raise ChatError("objective", "objective is empty")
        entry = self._open_memory().remember(
            session, "goal", clean, status="active", writer=self.user,
            writer_kind="human", importance=90)
        self._autosave = "SAVED · objective"
        return entry

    def add_next_action(self, text: str) -> dict:
        self.guard("add next action")
        session = self._require_session()
        entry = self._open_memory().remember(
            session, "next_action", text, writer=self.user, writer_kind="human",
            importance=60)
        self._autosave = "SAVED · next action"
        return entry

    def add_question(self, text: str) -> dict:
        self.guard("add open question")
        session = self._require_session()
        entry = self._open_memory().remember(
            session, "blocked_question", text, writer=self.user, writer_kind="human")
        self._autosave = "SAVED · open question"
        return entry

    def pin_decision(self, text: str, rationale: str = "") -> dict:
        """Owner-explicit acceptance.  Model prose can never reach this path."""
        self.guard("pin decision")
        session = self._require_session()
        clean, _, _ = planning_memory.sanitize(text, limit=1000)
        if not clean.strip():
            raise ChatError("decision", "decision text is empty")
        entry = self._open_memory().record_decision(
            session, clean, decision="accepted", rationale=rationale,
            writer=self.user, writer_kind="human", importance=95)
        self._touch_thread(session, pinned=entry["entry_id"])
        self._autosave = "SAVED · decision pinned"
        return entry

    def correct_decision(self, entry_id: str, text: str, *, reason: str = "") -> dict:
        self.guard("correct decision")
        self._require_session()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", entry_id or ""):
            raise ChatError("decision", "invalid entry id")
        try:
            result = self._open_memory().correct_entry(
                entry_id, text=text, reason=reason, writer=self.user,
                writer_kind="human")
        except planning_memory.PlanningMemoryError as exc:
            raise ChatError(getattr(exc, "code", "correct"), str(exc)[:200]) from None
        self._autosave = "SAVED · decision corrected"
        return result

    # ------------------------------------------------------------------
    # checkpoints / export / privacy
    # ------------------------------------------------------------------
    def checkpoints(self) -> list[dict]:
        if not self.owner():
            self.guard("inspect checkpoints")
        session = self.current_session()
        if not session:
            return []
        try:
            return self._open_memory().checkpoints(session)
        except ChatError:
            return []

    def inspect_checkpoint(self, index: int = -1) -> dict:
        self.guard("inspect checkpoint")
        rows = self.checkpoints()
        if not rows:
            raise ChatError("checkpoint", "no checkpoint has been sealed yet")
        row = rows[max(0, min(len(rows) - 1, index if index < 0 else index))]
        return {"checkpoint": row, "turns": self._open_memory().turns(
            self._require_session(), limit=5, since_seq=max(0, int(row["seq_high"]) - 4))}

    def export_private(self, path: str | os.PathLike[str] | None = None) -> dict:
        """Write the checksummed continuation envelope to private local state."""
        self.guard("export planning memory")
        session = self._require_session()
        if path is None:
            stamp = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = self.exports_dir / f"{session}-{stamp}.json"
        else:
            target = Path(path).expanduser()
        try:
            resolved = target.resolve()
            if resolved == self._repo_root or self._repo_root in resolved.parents:
                raise ChatError("private-export",
                                "refusing to export a private transcript inside the repository")
        except OSError:
            raise ChatError("private-export", "invalid export path") from None
        try:
            payload = self._open_memory().export_envelope(session, target)
            os.chmod(target, 0o600)
        except planning_memory.PlanningMemoryError as exc:
            raise ChatError(getattr(exc, "code", "export"), str(exc)[:200]) from None
        except OSError as exc:
            raise ChatError("export", str(exc)[:200]) from None
        return {"path": str(target), "checksum": payload.get("checksum", ""),
                "bytes": target.stat().st_size,
                "privacy": "local private file (0600); never uploaded automatically"}

    # ------------------------------------------------------------------
    # task proposals (planning_bridge drafts)
    # ------------------------------------------------------------------
    def draft_proposal(self, raw: Mapping[str, object]) -> dict:
        """Validate and store a private local draft.  Creates no GitHub issue."""
        self.guard("create task proposal draft")
        self._require_session()
        try:
            spec = planning_bridge.validate_proposal(dict(raw))
        except planning_bridge.ProposalError as exc:
            raise ChatError("proposal", str(exc)[:240]) from None
        target = self.drafts_dir / f"{spec.fingerprint[:16]}.json"
        payload = {"fingerprint": spec.fingerprint, "created_at": int(self._clock()),
                   "session_id": self.current_session(), "proposal": spec.payload(),
                   "state": "LOCAL DRAFT — not published, not queued"}
        _private_write(target, payload)
        return {"fingerprint": spec.fingerprint, "title": spec.title,
                "path": str(target), "state": "LOCAL DRAFT",
                "prerequisites": list(spec.prerequisites),
                "allowed_paths": list(spec.allowed_paths)}

    def draft_from_reply(self, text: str | None = None) -> dict:
        """Accept only a complete JSON proposal embedded in the last reply.

        There is no heuristic task generation: prose that is not a valid
        proposal produces an explicit refusal.
        """
        self.guard("create task proposal draft")
        source = (text if text is not None else self._last_reply) or ""
        try:
            raw = json.loads(source)
        except (TypeError, ValueError):
            raise ChatError("proposal",
                            "no structured proposal JSON in the last reply; nothing created") from None
        if not isinstance(raw, dict):
            raise ChatError("proposal", "proposal must be a JSON object")
        return self.draft_proposal(raw)

    def proposals(self) -> list[dict]:
        if not self.owner():
            return []
        rows = []
        try:
            files = sorted(self.drafts_dir.glob("*.json"))
        except OSError:
            return []
        for path in files:
            data = _read_json(path)
            if not data or not isinstance(data.get("proposal"), dict):
                continue
            proposal = data["proposal"]
            title = str(proposal.get("title") or "Untitled draft")[:120]
            rows.append({"fingerprint": str(data.get("fingerprint") or ""),
                         "title": title,
                         "state": str(data.get("state") or "LOCAL DRAFT"),
                         "path": str(path),
                         "prerequisites": proposal.get("prerequisites") or [],
                         "evidence": proposal.get("evidence") or []})
        return rows

    def publish_phrase(self, fingerprint: str) -> str:
        return PHRASE_PREFIX + (fingerprint or "")[:12]

    def publish_proposal(self, fingerprint: str, *, confirmation: str,
                         transport: object | None = None) -> dict:
        """Publish a NON-QUEUED draft with fresh authorization + typed consent."""
        status = self.guard("publish task proposal")
        expected = self.publish_phrase(fingerprint)
        if str(confirmation or "").strip() != expected:
            raise ChatError("confirmation", "typed confirmation did not match; nothing published")
        safe = re.sub(r"[^0-9a-f]", "", fingerprint or "")[:64]
        data = _read_json(self.drafts_dir / f"{safe[:16]}.json") if len(safe) >= 16 else None
        if not data or data.get("fingerprint") != fingerprint:
            raise ChatError("proposal", "draft not found; nothing published")
        try:
            spec = planning_bridge.validate_proposal(data["proposal"])
        except planning_bridge.ProposalError as exc:
            raise ChatError("proposal", str(exc)[:200]) from None
        owner, _, name = self.repo.partition("/")
        if transport is None:
            try:
                transport = planning_bridge.GhTransport()
            except Exception as exc:
                raise ChatError("github-unavailable", str(exc)[:120]) from None
        try:
            result = planning_bridge.publish_draft(
                cast(planning_bridge.Transport, transport), owner, name, spec, apply=True)
        except planning_bridge.ProposalError as exc:
            raise ChatError("github", str(exc)[:200]) from None
        except Exception as exc:  # transport/shell failure, offline gh, rate limit
            raise ChatError("github-offline",
                            f"GitHub draft was not published: {type(exc).__name__}: "
                            f"{str(exc)[:140]}") from None
        return {"action": result.get("action"), "number": result.get("number"),
                "fingerprint": spec.fingerprint,
                "published_by": status.get("role"),
                "queued": False,
                "note": "draft is NOT queued; queueing needs the separate #135 gate"}

    # ------------------------------------------------------------------
    # read model for the TUI / tests
    # ------------------------------------------------------------------
    def context_health(self) -> dict:
        session = self.current_session()
        if not session or not self.owner():
            return {"state": "UNKNOWN", "tokens": 0, "available": self._budget.available,
                    "percent": 0, "mode": "UNKNOWN", "compactions": 0,
                    "last_checkpoint": "UNKNOWN", "stale": 0}
        try:
            memory = self._open_memory()
            envelope = memory.context(session, provider=self._thread_meta(session).get("provider"))
            current = memory.get_session(session)
        except ChatError as exc:
            return {"state": "ERROR", "tokens": 0, "available": self._budget.available,
                    "percent": 0, "mode": "ERROR", "compactions": 0,
                    "last_checkpoint": "ERROR", "stale": 0, "error": str(exc)[:120]}
        tokens = planning_memory.estimate_tokens(_canonical(envelope))
        available = max(1, int(self._budget.available))
        return {
            "state": "HEALTHY" if tokens < available * 0.8 else "NEAR LIMIT",
            "tokens": tokens,
            "available": available,
            "percent": min(100, round(100 * tokens / available)),
            "mode": str(envelope.get("mode") or "UNKNOWN"),
            "compactions": int(current.get("compaction_count") or 0),
            "summaries": len(envelope.get("summaries") or []),
            "summaries_omitted": int(envelope.get("summaries_omitted") or 0),
            "last_checkpoint": (current.get("checkpoint_id") or "NONE")[:16],
            "last_checkpoint_at": _iso(current.get("updated_at")),
            "checkpoints": self.checkpoint_count(),
            "stale": len((envelope.get("ledger") or {}).get("stale_items") or []),
            "refreshed": self._context_refreshed,
        }

    def checkpoint_count(self) -> int:
        session = self.current_session()
        if not session:
            return 0
        if self._ckpt_count is None:
            try:
                self._ckpt_count = len(self._open_memory().checkpoints(session))
            except ChatError:
                return 0
        return self._ckpt_count

    def transcript(self) -> list[dict]:
        session = self.current_session()
        if not session or not self.owner():
            return []
        try:
            rows = self._open_memory().turns(session, limit=1000)
        except ChatError:
            return []
        return [{"seq": row["seq"], "role": row["role"], "provider": row["provider"],
                 "text": row["text"], "untrusted": row["untrusted"],
                 "compacted": bool(row.get("compacted"))}
                for row in rows[-MAX_TRANSCRIPT_VIEW:]]

    def state(self) -> dict:
        allowed = self.owner()
        if not allowed:
            return {"mode": "help", "allowed": False, "role": self._role["role"],
                    "role_reason": self._role["reason"], "threads": [], "thread": None,
                    "contributor_help": list(CONTRIBUTOR_HELP), "transcript": [],
                    "events": [], "proposals": [], "autosave": "N/A (read-only)",
                    "privacy": "Owner planning memory is not opened for this account.",
                    "error": None, "context_refreshed": False}
        session = self.current_session()
        meta = self._thread_meta(session) if session else {}
        context = self.context_health()
        return {
            "mode": "owner",
            "allowed": True,
            "role": self._role["role"],
            "role_reason": self._role["reason"],
            "threads": self.thread_rows(),
            "thread": ({"session_id": session,
                        "title": meta.get("title", "Untitled plan"),
                        "provider": meta.get("provider", DEFAULT_PROVIDER),
                        "model": meta.get("model",
                                          self._provider_default_model(
                                              meta.get("provider", DEFAULT_PROVIDER))),
                        "provider_state": provider_state(
                            meta.get("provider", DEFAULT_PROVIDER), self.adapters)}
                       if session else None),
            "objective": self.objective() if session else "Not set",
            "next_actions": self.next_actions() if session else [],
            "open_questions": self.open_questions() if session else [],
            "context": context,
            "context_refreshed": self._context_refreshed,
            "autosave": self._autosave,
            "privacy": ("PRIVATE: local per-user ledger; nothing is uploaded automatically. "
                        "No transcript or secret enters the public repository."),
            "transcript": self.transcript(),
            "events": list(self._events[-12:]),
            "proposals": self.proposals(),
            "checkpoints": self.checkpoint_count(),
            "streaming": self._streaming,
            "can_retry": bool(self._last_prompt),
            "error": self._error or (("LEDGER UNAVAILABLE: " + self._memory_error)
                                     if self._memory_error else None),
            "ledger_error": self._memory_error,
        }

    def summary_line(self) -> str:
        state = self.state()
        if state["mode"] != "owner":
            return "PLAN & CHAT — owner/admin only; contributor help surface"
        thread = state.get("thread")
        if not thread:
            return "PLAN & CHAT — no thread; T starts a private plan"
        context = state["context"]
        return (f"{thread['title']} · {thread['provider']}/{thread['model']} · "
                f"context {context['percent']}% · {state['autosave']}")
