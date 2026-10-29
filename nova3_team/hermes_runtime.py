"""Optional Hermes Agent planner/worker backend for NOVA3 (issue #136).

Verified against the locally installed Hermes CLI (``hermes chat --help``):

    hermes chat -q/--query-file PATH --oneshot -Q/--quiet
                --format {text,stream-json} -m/--model MODEL --provider NAME
                -t/--toolsets LIST --max-turns N --run-budget SECONDS
                --in DIR --source SOURCE [--safe-mode] [--ignore-user-config]

Guarantees this adapter makes:

* It never passes ``--yolo``, ``--accept-hooks`` or ``--api-key``; there is no
  code path that grants unrestricted approval or takes a credential argument.
* Prompts are always passed as a file path (``--query-file``), never inline,
  so arbitrary text is preserved verbatim without shell interpretation.
* A planner invocation is restricted to read-oriented toolsets and cannot use
  ``terminal``, ``code_execution``, ``delegation`` or ``cronjob``.
* Provider/model are explicit, bounded identifiers. The model defaults to the
  pinned DeepSeek Flash 1017 slot; an override is recorded, never guessed.
* Model output can only become a *validated proposal draft*. Nothing here
  publishes an issue, applies a label, approves a queue entry or starts a
  worker; those transitions go through planning_bridge + access_policy.
* Diagnostics never echo vendor stderr, environment variables or secrets.

It does not install Hermes, does not perform vendor login, and does not copy
provider credentials. Authentication stays vendor-native.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence

PROBE_TIMEOUT = 5.0
MAX_PROBE_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_PROVIDER = "openrouter"
MAX_TURNS = 40
MAX_RUN_BUDGET = 3600
MAX_PROMPT_BYTES = 512 * 1024
SESSION_TIMEOUT_DEFAULT = 1200
SESSION_TIMEOUT_MIN = 300
SESSION_TIMEOUT_MAX = 1800
CONFIG_ENV = "NOVA3_HERMES_"
PLANNER_ENV = "NOVA3_HERMES_PLANNER_"
ALLOWLIST_ENV = "NOVA3_HERMES_ALLOWED_PROVIDERS"
EXECUTABLE_ENV = "NOVA3_HERMES_EXECUTABLE"
# `hermes chat --help` documents `--provider PROVIDER` but exposes no static
# provider list, so only the verified default is trusted implicitly. Any other
# provider must be named explicitly in the operator allowlist; this module
# never guesses a vendor name from a typo.
DEFAULT_ALLOWED_PROVIDERS = frozenset({DEFAULT_PROVIDER})

DOCS_URL = "https://hermes-agent.nousresearch.com/docs"
INSTALL_URL = "https://hermes-agent.nousresearch.com/docs/getting-started/installation"

# Capability tokens the adapter depends on. Presence is proof the installed
# build supports the flag; a missing token fails closed rather than guessing.
REQUIRED_CAPABILITIES = (
    "--query-file", "--oneshot", "--format", "--provider", "--model",
    "--max-turns", "--run-budget", "--toolsets",
)
# Flags that must never appear in a generated argv.
FORBIDDEN_FLAGS = ("--yolo", "--accept-hooks", "--api-key")

# Verified toolset names from `hermes tools list`.
KNOWN_TOOLSETS = frozenset({
    "web", "browser", "terminal", "file", "code_execution", "vision", "video",
    "image_gen", "video_gen", "x_search", "tts", "stt", "skills", "todo",
    "kanban", "memory", "context_engine", "session_search", "connections",
    "clarify", "delegation", "cronjob", "computer_use",
})
PLANNER_TOOLSETS = ("file", "todo", "skills", "session_search")
WORKER_TOOLSETS = ("file", "terminal", "code_execution", "todo")
# Toolsets that grant code execution, privileges or unsupervised recursion.
FORBIDDEN_FOR_PLANNER = frozenset({
    "terminal", "code_execution", "browser", "computer_use", "delegation",
    "cronjob", "connections", "memory", "image_gen", "video_gen",
})

_SECRET_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_VERSION = re.compile(r"\bhermes[ -]?v?(\d+\.\d+\.\d+)\b", re.IGNORECASE)


class HermesError(RuntimeError):
    """Fixed diagnostic codes only; never contains prompt or vendor output."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class RuntimeState(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    PROBE_FAILED = "probe_failed"


class Role(str, Enum):
    PLANNER = "planner"
    WORKER = "worker"


class RunState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"


def redact(value: str) -> str:
    for pattern in _SECRET_RES:
        value = pattern.sub("[redacted]", value)
    return value


@dataclass(frozen=True)
class HermesStatus:
    state: RuntimeState
    executable_path: str | None = None
    version: str | None = None
    capabilities: tuple[str, ...] = ()
    reason: str = ""
    install_url: str = INSTALL_URL

    @property
    def available(self) -> bool:
        return self.state == RuntimeState.AVAILABLE


def probe(executable: str = "hermes", *, run: Callable[..., object] | None = None,
          timeout: float = PROBE_TIMEOUT,
          which: Callable[[str], str | None] | None = None) -> HermesStatus:
    """Bounded capability probe. Never logs in, never submits a prompt."""
    path = (which or shutil.which)(executable)
    if not path:
        return HermesStatus(RuntimeState.MISSING,
                            reason=f"Hermes executable not found on PATH: {executable}")
    runner = run or subprocess.run
    try:
        completed = runner([path, "chat", "--help"], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return HermesStatus(RuntimeState.PROBE_FAILED, path,
                            reason="Hermes help probe timed out")
    except OSError:
        return HermesStatus(RuntimeState.PROBE_FAILED, path,
                            reason="Hermes help probe could not start")
    if getattr(completed, "returncode", 1) != 0:
        return HermesStatus(RuntimeState.PROBE_FAILED, path,
                            reason=f"Hermes help probe exited {completed.returncode}")
    help_text = (completed.stdout or "")[:MAX_PROBE_BYTES]
    capabilities = tuple(token for token in REQUIRED_CAPABILITIES if token in help_text)
    missing = [token for token in REQUIRED_CAPABILITIES if token not in help_text]
    if missing:
        return HermesStatus(RuntimeState.INCOMPATIBLE, path, capabilities=capabilities,
                            reason="installed Hermes lacks required flags")
    version = None
    try:
        version_run = runner([path, "--version"], capture_output=True, text=True,
                             timeout=timeout)
        match = _VERSION.search((version_run.stdout or "")[:MAX_PROBE_BYTES])
        version = match.group(1) if match else None
    except (subprocess.TimeoutExpired, OSError):
        version = None  # capability probe already passed; version is advisory
    return HermesStatus(RuntimeState.AVAILABLE, path, version, capabilities)


@dataclass(frozen=True)
class Budget:
    max_turns: int = MAX_TURNS
    run_budget_seconds: int = 900
    max_cost_micro_usd: int = 2_000_000

    def validate(self) -> "Budget":
        if type(self.max_turns) is not int or not 1 <= self.max_turns <= 200:
            raise HermesError("budget", "max_turns must be 1..200")
        if type(self.run_budget_seconds) is not int or not 30 <= self.run_budget_seconds <= MAX_RUN_BUDGET:
            raise HermesError("budget", "run budget must be 30..3600 seconds")
        if type(self.max_cost_micro_usd) is not int or not 0 <= self.max_cost_micro_usd <= 100_000_000:
            raise HermesError("budget", "cost cap must be a bounded non-negative integer")
        return self

    def allows(self, estimated_micro_usd: int) -> bool:
        return type(estimated_micro_usd) is int and 0 <= estimated_micro_usd <= self.max_cost_micro_usd


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: str
    role: Role
    provider: str
    model: str
    prompt_path: str
    env: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "argv": list(self.argv), "cwd": self.cwd, "role": self.role.value,
            "provider": self.provider, "model": self.model,
            "prompt_path": self.prompt_path,
        }


def _toolsets_for(role: Role, requested: Sequence[str] | None) -> tuple[str, ...]:
    base = PLANNER_TOOLSETS if role is Role.PLANNER else WORKER_TOOLSETS
    if requested is None:
        return base
    chosen = tuple(requested)
    for name in chosen:
        if name not in KNOWN_TOOLSETS:
            raise HermesError("toolsets", f"unknown toolset: {name}")
    if role is Role.PLANNER and FORBIDDEN_FOR_PLANNER.intersection(chosen):
        raise HermesError("toolsets", "planner role may not enable execution toolsets")
    return chosen


def build_invocation(status: HermesStatus, *, role: Role, prompt_path: str,
                     cwd: str, provider: str = DEFAULT_PROVIDER,
                     model: str = DEFAULT_MODEL, budget: Budget | None = None,
                     toolsets: Sequence[str] | None = None,
                     issue_number: int | None = None) -> Invocation:
    """Build an exact argv tuple; raises rather than degrading silently."""
    if not isinstance(status, HermesStatus) or not status.available or not status.executable_path:
        raise HermesError("unavailable", "Hermes runtime is not available")
    if not isinstance(role, Role):
        raise HermesError("role", "role must be planner or worker")
    if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
        raise HermesError("provider", "invalid provider identifier")
    if not isinstance(model, str) or not _IDENTIFIER.fullmatch(model):
        raise HermesError("model", "invalid model identifier")
    if not isinstance(prompt_path, str) or not prompt_path:
        raise HermesError("prompt", "prompt path is required")
    if os.path.getsize(prompt_path) > MAX_PROMPT_BYTES:
        raise HermesError("prompt", "prompt file exceeds size limit")
    if not os.path.isdir(cwd):
        raise HermesError("cwd", "working directory does not exist")
    budget = (budget or Budget()).validate()
    chosen_toolsets = _toolsets_for(role, toolsets)
    if issue_number is not None and (type(issue_number) is not int or issue_number <= 0):
        raise HermesError("issue", "issue number must be positive")

    argv = [
        status.executable_path, "chat",
        "--query-file", prompt_path,
        "--oneshot",
        "--quiet",
        "--format", "stream-json",
        "--provider", provider,
        "--model", model,
        "--toolsets", ",".join(chosen_toolsets),
        "--max-turns", str(budget.max_turns),
        "--run-budget", str(budget.run_budget_seconds),
        "--in", cwd,
        "--source", "tool",
    ]
    if role is Role.PLANNER:
        # No AGENTS.md/memory/plugin injection: the planner sees only the
        # sanitised context file referenced by its prompt.
        argv.extend(["--safe-mode", "--ignore-user-config"])
    for flag in FORBIDDEN_FLAGS:
        if flag in argv:
            raise HermesError("permissions", "forbidden flag in generated argv")
    env = {
        "HERMES_NOVA3_ROLE": role.value,
        "HERMES_NOVA3_MODEL": model,
    }
    if issue_number is not None:
        env["HERMES_NOVA3_ISSUE"] = str(issue_number)
    return Invocation(tuple(argv), cwd, role, provider, model, prompt_path, env)


# --------------------------------------------------------------------------
# Configuration: operator-selected provider/model/role, fail-closed
# --------------------------------------------------------------------------

def status_from_provider(status: object) -> HermesStatus:
    """Adapt the provider registry's status record without losing evidence."""
    if isinstance(status, HermesStatus):
        return status
    raw_state = getattr(status, "state", None)
    value = getattr(raw_state, "value", raw_state)
    try:
        state = RuntimeState(value)
    except (TypeError, ValueError):
        raise HermesError("readiness", "unrecognized provider readiness state") from None
    path = getattr(status, "executable_path", None)
    capabilities = getattr(status, "capabilities", ()) or ()
    return HermesStatus(
        state,
        path if isinstance(path, str) and path else None,
        getattr(status, "version", None),
        tuple(str(token) for token in capabilities),
        str(getattr(status, "reason", "") or ""),
    )


def assert_ready(status: object, *, role: Role = Role.WORKER) -> HermesStatus:
    """Deny unavailable/unverified runtimes with a fixed, actionable code."""
    status = status_from_provider(status)
    if status.state is RuntimeState.MISSING:
        raise HermesError("unverified", "Hermes is not installed; install it locally: " + INSTALL_URL)
    if status.state is RuntimeState.PROBE_FAILED:
        raise HermesError("unverified", "Hermes capability probe failed; no session was started")
    if status.state is RuntimeState.INCOMPATIBLE:
        raise HermesError("capabilities", "installed Hermes lacks the required headless flags; no session was started")
    if not status.available or not status.executable_path:
        raise HermesError("unverified", "Hermes readiness is not verified; no session was started")
    missing = [token for token in REQUIRED_CAPABILITIES if token not in status.capabilities]
    if missing:
        raise HermesError("capabilities", "Hermes capability evidence is incomplete; no session was started")
    return status


def materialise_prompt(directory: str | os.PathLike[str], prompt: str) -> str:
    """Write the exact prompt to a private file; never pass task text via argv."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise HermesError("prompt", "a non-empty text prompt is required")
    if "\0" in prompt:
        raise HermesError("prompt", "prompt must not contain NUL bytes")
    data = prompt.encode("utf-8")
    if len(data) > MAX_PROMPT_BYTES:
        raise HermesError("prompt", "prompt file exceeds size limit")
    folder = os.path.abspath(os.fspath(directory))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(folder, 0o700)
    path = os.path.join(folder, "hermes-prompt.md")
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    finally:
        if os.name == "posix":
            os.chmod(path, 0o600)
    return path


def _env_value(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    return raw if isinstance(raw, str) and raw.strip() else None


def _env_int(env: Mapping[str, str], name: str, default: int,
             low: int, high: int) -> int:
    raw = _env_value(env, name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise HermesError("config", f"{name} must be an integer") from None
    if not low <= value <= high:
        raise HermesError("config", f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class HermesConfig:
    role: Role
    provider: str
    model: str
    budget: Budget
    timeout_seconds: int
    executable: str = "hermes"

    def as_dict(self) -> dict:
        """Serialisable summary; contains no prompt, argv or credentials."""
        return {
            "role": self.role.value, "provider": self.provider,
            "model": self.model, "max_turns": self.budget.max_turns,
            "run_budget_seconds": self.budget.run_budget_seconds,
            "timeout_seconds": self.timeout_seconds,
            "executable": self.executable,
        }


def _allowed_providers(env: Mapping[str, str]) -> frozenset[str]:
    raw = _env_value(env, ALLOWLIST_ENV)
    extra = set()
    if raw:
        for item in raw.split(","):
            item = item.strip()
            if item:
                if not _PROVIDER.fullmatch(item):
                    raise HermesError("config", f"{ALLOWLIST_ENV} contains an invalid provider identifier")
                extra.add(item)
    return DEFAULT_ALLOWED_PROVIDERS | extra


def load_config(role: Role, *, env: Mapping[str, str] | None = None,
                status: object | None = None, timeout_seconds: int | None = None,
                executable: str | None = None,
                tty: Callable[[], bool] | None = None) -> HermesConfig:
    """Resolve operator configuration for exactly one role.

    Planner and worker read separate environment prefixes, so a planner
    session's model/provider cannot silently leak into a coding worker. A role
    mismatch, TTY on a worker, unknown provider/model, bad integer or an
    unverified runtime all raise :class:`HermesError` with a fixed code; there
    is no partial or degraded configuration.
    """
    if not isinstance(role, Role):
        raise HermesError("role", "role must be planner or worker")
    env = os.environ if env is None else env
    declared = _env_value(env, CONFIG_ENV + "ROLE")
    if declared is not None and declared.strip().lower() != role.value:
        raise HermesError("role", "configured role does not match this caller; "
                                  "planner and worker configuration are separate")
    prefix = PLANNER_ENV if role is Role.PLANNER else CONFIG_ENV

    provider = _env_value(env, prefix + "PROVIDER") or DEFAULT_PROVIDER
    if not _PROVIDER.fullmatch(provider):
        raise HermesError("provider", "invalid provider identifier")
    if provider not in _allowed_providers(env):
        raise HermesError("provider", f"unknown provider; add it explicitly to {ALLOWLIST_ENV}")

    model = _env_value(env, prefix + "MODEL") or DEFAULT_MODEL
    if not _IDENTIFIER.fullmatch(model):
        raise HermesError("model", "invalid or unknown model identifier")

    max_turns = _env_int(env, prefix + "MAX_TURNS", MAX_TURNS, 1, 200)
    if timeout_seconds is None:
        timeout_seconds = _env_int(env, "NOVA3_AI_SESSION_TIMEOUT",
                                   SESSION_TIMEOUT_DEFAULT,
                                   SESSION_TIMEOUT_MIN, SESSION_TIMEOUT_MAX)
    if type(timeout_seconds) is not int or not \
            SESSION_TIMEOUT_MIN <= timeout_seconds <= SESSION_TIMEOUT_MAX:
        raise HermesError("config", "session timeout must be between "
                                    f"{SESSION_TIMEOUT_MIN} and {SESSION_TIMEOUT_MAX} seconds")

    name = executable or _env_value(env, EXECUTABLE_ENV) or "hermes"
    if not isinstance(name, str) or not name or len(name) > 255 or "\0" in name:
        raise HermesError("config", "invalid Hermes executable name")

    if role is Role.WORKER and tty is not None and tty():
        raise HermesError("tty", "interactive TTY present; a worker session must be headless")

    budget = Budget(max_turns=max_turns, run_budget_seconds=timeout_seconds).validate()
    config = HermesConfig(role, provider, model, budget, timeout_seconds, name)
    if status is not None:
        assert_ready(status, role=role)
    return config



@dataclass(frozen=True)
class RunResult:
    state: RunState
    exit_code: int | None
    text: str = ""
    events: tuple[dict, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state is RunState.SUCCEEDED


def _parse_stream(stdout: str) -> tuple[str, tuple[dict, ...]]:
    """Parse newline-delimited JSON events; tolerate non-JSON banner lines."""
    events: list[dict] = []
    final_parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append({key: event[key] for key in ("type", "role", "tool", "session_id")
                       if key in event and isinstance(event[key], (str, int, bool))})
        if event.get("type") in {"assistant", "message", "final", "response"}:
            content = event.get("content") or event.get("text")
            if isinstance(content, str):
                final_parts.append(content)
    return "\n".join(final_parts), tuple(events)


def run_oneshot(invocation: Invocation, *, run: Callable[..., object] | None = None,
                timeout: float | None = None,
                still_tty: Callable[[], bool] | None = None) -> RunResult:
    """Execute one bounded headless session. No TTY, no streamed interactivity."""
    if not isinstance(invocation, Invocation):
        raise HermesError("invocation", "expected an Invocation")
    if invocation.role is Role.PLANNER and "terminal" in invocation.argv:
        raise HermesError("permissions", "planner invocation referenced a terminal")
    if still_tty is not None and still_tty():
        # Interactive seeding would hang an unattended worker.
        return RunResult(RunState.FAILED, None, reason="interactive TTY detected")
    env = dict(os.environ)
    env.update(invocation.env)
    runner = run or subprocess.run
    try:
        completed = runner(list(invocation.argv), cwd=invocation.cwd, env=env,
                           capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return RunResult(RunState.TIMEOUT, None, reason="session exceeded wall-clock budget")
    except OSError:
        return RunResult(RunState.FAILED, None, reason="session could not start")
    code = int(getattr(completed, "returncode", 1))
    stdout = (completed.stdout or "")[:MAX_CAPTURE_BYTES]
    text, events = _parse_stream(stdout)
    if code != 0:
        return RunResult(RunState.FAILED, code, redact(text), events,
                         reason=f"Hermes exited {code}")
    if isinstance(getattr(completed, "stderr", ""), str) and "rate limit" in (completed.stderr or "").lower():
        return RunResult(RunState.FAILED, code, redact(text), events, reason="provider rate limited")
    return RunResult(RunState.SUCCEEDED, 0, redact(text), events)


# --------------------------------------------------------------------------
# Planner output -> proposal draft (never a queue transition)
# --------------------------------------------------------------------------

_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_proposal(text: str) -> tuple[dict | None, str]:
    """Return the first schema-valid proposal embedded in model output.

    Import is deferred so this module stays usable without the bridge.
    """
    from . import planning_bridge

    if not isinstance(text, str) or len(text) > MAX_CAPTURE_BYTES:
        return None, "no output"
    for block in _FENCED.findall(text):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        try:
            spec = planning_bridge.validate_proposal(payload)
        except planning_bridge.ProposalError as exc:
            return None, f"invalid proposal: {exc.code}"
        return spec.payload(), "ok"
    return None, "no fenced JSON proposal found"


def planner_prompt(context_path: str, *, question: str, max_chars: int = 8000) -> str:
    """Bounded prompt; the model receives a file reference, not private data."""
    if not isinstance(question, str) or not question.strip():
        raise HermesError("prompt", "question is required")
    if len(question) > max_chars:
        raise HermesError("prompt", "question exceeds limit")
    return (
        "You are the NOVA3 planning assistant. Read the sanitised GitHub context "
        f"snapshot at {context_path} and answer the owner's question.\n\n"
        "Rules: repository-relative paths only; never claim byte-level binary "
        "equivalence without evidence; mark unknown facts as unknown. Output at "
        "most one task proposal as a single fenced ```json block that follows the "
        "nova3_team.planning_bridge schema. Do not claim approval, do not apply "
        "labels, do not queue work.\n\n"
        f"Question: {question.strip()}\n"
    )


def readiness(status: HermesStatus, *, model: str = DEFAULT_MODEL,
              provider: str = DEFAULT_PROVIDER) -> dict:
    """Compact status for the Control Center/provider registry."""
    return {
        "provider_id": "hermes",
        "display_name": "Hermes Agent",
        "state": status.state.value,
        "version": status.version,
        "model": model,
        "provider": provider,
        "roles": [Role.PLANNER.value, Role.WORKER.value],
        "install_url": INSTALL_URL,
        "docs_url": DOCS_URL,
        "reason": status.reason,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m nova3_team.hermes_runtime",
                                     description="Probe and describe the optional Hermes backend.")
    parser.add_argument("command", choices=["status", "extract"])
    parser.add_argument("--json-out", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--file", help="model output file for extract")
    args = parser.parse_args(argv)
    status = probe()
    if args.command == "status":
        payload = readiness(status, model=args.model, provider=args.provider)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if status.available else 4
    if not args.file:
        print(json.dumps({"error": "file-required"}, sort_keys=True))
        return 2
    if os.path.getsize(args.file) > MAX_CAPTURE_BYTES:
        print(json.dumps({"error": "file-too-large"}, sort_keys=True))
        return 2
    with open(args.file, "r", encoding="utf-8", errors="replace") as handle:
        proposal, reason = extract_proposal(handle.read())
    print(json.dumps({"proposal": proposal, "reason": reason}, indent=2, sort_keys=True))
    return 0 if proposal else 3


if __name__ == "__main__":
    raise SystemExit(main())
