"""DeepSeek Harness (DSH) as the worker's native execution provider.

This module is the single place that knows how NOVA3 runs an unattended
reconstruction session through DSH. It is deliberately a *supervisor-side*
module: it never imports DSH internals, never parses a model's prose to decide
what happened, and never touches a credential value.

Contract (verified against the installed runtime, DSH 0.2.1-rc.5):

* Launch shape: ``dsh --profile <profile> --patch <overlay> headless --json
  [--session-id <id>] -`` with the task text on stdin. ``headless`` is the
  *application* selected by the positional argument (``dsh [--profile] <name>``);
  it is not repeated as a subcommand. The ``-`` task marker is
  the documented stdin form, so a prompt never appears in argv or the
  environment, and the process inherits no shell.
* The shipped ``headless`` profile disables the managed credential store, so
  keys saved in the DSH Web UI are invisible to it. The overlay re-enables the
  store (``credentials``, ``authorization``, ``deepseek-account``) and declares
  the model route + default model. The key is referenced by *name*
  (``apiKeyEnv``); its value lives only in DSH's own 0600 credential file.
* ``--json`` emits newline-delimited events: ``session``, ``status`` (turn /
  step boundaries, final usage), ``text``, ``thinking``, ``tool_call``,
  ``tool_result``, ``error`` and a terminal ``final``. An in-turn failure still
  ends with ``final`` (often empty), so the authoritative failure signal is the
  ``turn_end`` reason ``kind`` -- ``completed`` is the only success kind.
* Exit status is 0 for a completed turn and 1 for anything else; ``error``
  events carry a stable ``code`` (``MISSING_CREDENTIAL``, ``QUOTA``,
  ``RATE_LIMIT``, ``AUTH``, ...). Classification routes on those codes, never
  on message text.

Resource and failure policy (``EXECUTION_FAILURES``): a session is bounded by
wall time and by the number of *model steps* seen in the event stream, so a
model that keeps calling tools forever is stopped deterministically rather
than by token spend. Retries are bounded and only for transient/unknown
causes; a terminal cause (credits, auth, provider outage) short-circuits the
bounded retry loop instead of repeating an identical attempt.

No function here writes to GitHub, git, or another worker's state.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

KILL_GRACE_SECONDS = 10
DEADLINE_POLL_S = 0.5      # watchdog granularity for a silent session
DEFAULT_STREAM_CAP = 16 * 1024 * 1024      # one private log, never in Git
DEFAULT_EVENT_CAP = 4000                   # parsed events retained per session

# Model route name in the generated overlay. The OpenRouter catalog id is the
# actual model; the fingerprint default mirrors the Web UI's own route so both
# surfaces spend from the same account and the same allowance.
DEFAULT_ROUTE = 'openrouter'
DEFAULT_MODEL = 'deepseek/deepseek-v4.1-flash-1017'
DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
DEFAULT_KEY_REF = 'OPENROUTER_API_KEY'

# Failure taxonomy. Everything the supervisor does next is derived from these
# classes only; they are also the durable state names.
RATE_LIMIT = 'RATE_LIMIT'
WAITING_CREDITS = 'WAITING_CREDITS'
AUTH = 'AUTH'
PROVIDER_OUTAGE = 'PROVIDER_OUTAGE'
SESSION_TIMEOUT = 'SESSION_TIMEOUT'
STEP_LIMIT = 'STEP_LIMIT'
EXECUTION_ERROR = 'EXECUTION_ERROR'
CANCELLED = 'CANCELLED'
COMPLETED = 'COMPLETED'

# Classes whose remedy is time (or a different model), never a changed task.
TRANSIENT = frozenset({RATE_LIMIT, PROVIDER_OUTAGE, EXECUTION_ERROR, SESSION_TIMEOUT})
TERMINAL = frozenset({WAITING_CREDITS, AUTH, CANCELLED})
# Provider-level failures that an owner-approved alternative route may survive.
FAILOVER_STATUSES = frozenset({WAITING_CREDITS, AUTH, RATE_LIMIT, PROVIDER_OUTAGE})

EXACT_CODES = {
    'MISSING_CREDENTIAL': WAITING_CREDITS,
    'QUOTA': WAITING_CREDITS,
    'ACCOUNT_QUOTA': WAITING_CREDITS,
    'INVALID_CREDENTIAL': AUTH,
    'AUTH': AUTH,
    'UNAUTHORIZED': AUTH,
    'RATE_LIMIT': RATE_LIMIT,
    'NO_ADAPTER': PROVIDER_OUTAGE,
    'UNKNOWN_MODEL': PROVIDER_OUTAGE,
}
# Only used when DSH supplied no stable code at all.
PHRASE_CODES = (
    (re.compile(r'insufficient[\s_-]+(?:quota|balance|credits?)|quota[\s_-]+exceeded', re.I),
     WAITING_CREDITS),
    # Provider adapters surface an account cap as a plain provider error code
    # (observed live: PI_AI_ERROR / "Codex error: The usage limit has been
    # reached"). That is a provider-level limit, not a task verdict: it must be
    # eligible for approved-route failover instead of quarantining the task.
    (re.compile(r'usage limit (?:has been )?reached|billing hard limit'
                r'|credit balance|out of credits', re.I), WAITING_CREDITS),
    (re.compile(r'rate[\s_-]?limit|too many requests|\b429\b', re.I), RATE_LIMIT),
    (re.compile(r'\b401\b|unauthori[sz]ed|invalid api key|user not found', re.I), AUTH),
    (re.compile(r'ECONNREFUSED|ENOTFOUND|ETIMEDOUT|socket hang up|fetch failed|\b50[234]\b', re.I),
     PROVIDER_OUTAGE),
)

# The event kinds a run can end with. `turn_end.reason.kind` is parsed by DSH
# and is the only accepted success signal.
COMPLETED_KINDS = frozenset({'completed'})


class DshUnavailable(RuntimeError):
    """No usable DSH runtime/profile. Never contains a path or a secret."""


class DshLaunchError(RuntimeError):
    """A session could not be started (configuration, not a model failure)."""


@dataclass(frozen=True)
class DshEndpoint:
    """Everything needed to launch one DSH session, all non-secret."""

    binary: str
    home: pathlib.Path
    profile: str = 'headless'
    patch: pathlib.Path | None = None
    route: str = DEFAULT_ROUTE
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    key_ref: str = DEFAULT_KEY_REF
    max_steps: int = 120
    # Trusted per-role enforcement overlay, carried through configured routing.
    role_patch: pathlib.Path | None = None

    def with_route(self, provider: str, model: str) -> 'DshEndpoint':
        return replace(self, route=provider, model=model or self.model)

    def command(self, *, session_id: str | None, json_events: bool = True) -> list[str]:
        argv = [self.binary, '--profile', self.profile]
        if self.patch is not None:
            argv += ['--patch', str(self.patch)]
        if json_events:
            argv.append('--json')
        if session_id:
            argv += ['--session-id', session_id]
        argv.append('-')          # documented: read the task from stdin
        return argv

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """The DSH home is the only addition; no credential is exported."""
        env = dict(os.environ if base is None else base)
        env['DSH_HOME'] = str(self.home)
        env.setdefault('PYTHONUNBUFFERED', '1')
        return env


@dataclass(frozen=True)
class SessionOutcome:
    """Objective result of ONE DSH process; no model prose is interpreted."""

    status: str                       # one of the class constants
    exit_code: int | None
    session_id: str | None
    turn_reason: str | None
    error_code: str | None
    error_message: str                # redacted diagnostic, may be empty
    steps: int
    tool_calls: int
    elapsed_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    final_text_bytes: int = 0
    log_path: str | None = None
    truncated: bool = False
    events: tuple[dict, ...] = field(default=(), repr=False)

    @property
    def ok(self) -> bool:
        return self.status == COMPLETED

    @property
    def retryable(self) -> bool:
        return self.status in TRANSIENT

    def summary(self) -> dict:
        """Sanitized, JSON-safe, GitHub-safe: codes and counts only."""
        return {
            'status': self.status,
            'exit_code': self.exit_code,
            'session_id': self.session_id,
            'turn_reason': self.turn_reason,
            'error_code': self.error_code,
            'error_message': _redact(self.error_message)[:400],
            'steps': self.steps,
            'tool_calls': self.tool_calls,
            'elapsed_s': round(self.elapsed_s, 1),
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'final_text_bytes': self.final_text_bytes,
            'truncated': self.truncated,
            'log': pathlib.Path(self.log_path).name if self.log_path else None,
        }


# ---------------------------------------------------------------------------
# Local discovery: run the installed runtime, never guess a path
# ---------------------------------------------------------------------------

DRIVER_RELATIVE = pathlib.Path('runtime') / 'node_modules' / '.bin' / 'dsh'
RUNTIME_RELATIVE = (pathlib.Path('runtime') / 'node_modules' / '@deepseek-ai'
                    / 'dsh' / 'lib' / 'bin.js')


def discover_binary(project: pathlib.Path, *, env: dict[str, str] | None = None,
                    home: pathlib.Path | None = None) -> str | None:
    """Resolve the dsh driver, in a fixed documented order.

    1. ``NOVA3_DSH_BIN`` (an explicit operator override);
    2. ``$DSH_HOME/../runtime`` -- the layout the NOVA3 launcher installs
       (``~/.local/share/nova3-dsh/{data,runtime}``, wrapper ``nova3-dsh``);
    3. the checkout-local runtime (``<project>/.local/dsh`` for per-worker
       checkouts);
    4. ``PATH`` (a system-wide install).
    """
    env = os.environ if env is None else env
    override = (env.get('NOVA3_DSH_BIN') or '').strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    candidates = []
    if home is not None:
        candidates.append(pathlib.Path(home).expanduser().parent)
    candidates.append(pathlib.Path(project) / '.local' / 'dsh')
    for root in candidates:
        driver = root / DRIVER_RELATIVE
        if driver.is_file() and os.access(driver, os.X_OK):
            return str(driver)
    return shutil.which('dsh')


def discover_home(*, env: dict[str, str] | None = None) -> pathlib.Path:
    env = os.environ if env is None else env
    for key in ('NOVA3_DSH_HOME', 'DSH_HOME'):
        value = (env.get(key) or '').strip()
        if value:
            return pathlib.Path(value).expanduser()
    return pathlib.Path.home() / '.local' / 'share' / 'nova3-dsh' / 'data'


def _probe(argv: list[str], *, timeout: float = 20.0, env: dict[str, str] | None = None):
    try:
        cp = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=timeout, shell=False, env=env)
        return cp.returncode, (cp.stdout or '') + (cp.stderr or '')
    except (OSError, subprocess.SubprocessError):
        return None, ''


def probe_endpoint(endpoint: DshEndpoint) -> dict:
    """Bounded local checks. No model call, no login, no billing.

    Returns a record with ``ok`` plus fixed reason codes, suitable for
    ``python -m nova3_team.worker providers --check-auth`` and for the durable
    supervisor state file.
    """
    record = {'ok': False, 'reason': '', 'version': None, 'profile': endpoint.profile,
              'route': endpoint.route, 'model': endpoint.model,
              'key_ref': endpoint.key_ref, 'home': str(endpoint.home)}
    code, out = _probe([endpoint.binary, '--version'], env=endpoint.environment())
    if code is None:
        record['reason'] = 'dsh_binary_not_executable'
        return record
    match = re.search(r'(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)', out)
    record['version'] = match.group(1) if match else None
    argv = [endpoint.binary, '--profile', endpoint.profile]
    if endpoint.patch is not None:
        argv += ['--patch', str(endpoint.patch)]
    argv += ['--help']
    code, out = _probe(argv, env=endpoint.environment())
    if code != 0 or '--json' not in out or '--session-id' not in out:
        record['reason'] = 'headless_contract_missing'
        return record
    # The overlay must actually compose: --dump-config mounts the whole tree
    # and prints it, which is also how a broken patch is caught before a
    # session ever reaches the model endpoint.
    argv = [endpoint.binary, '--profile', endpoint.profile]
    if endpoint.patch is not None:
        argv += ['--patch', str(endpoint.patch)]
    argv.append('--dump-config')
    code, out = _probe(argv, timeout=60.0, env=endpoint.environment())
    if code != 0:
        record['reason'] = 'profile_composition_failed'
        return record
    if endpoint.model not in out:
        record['reason'] = 'model_route_not_composed'
        return record
    if endpoint.key_ref not in out:
        record['reason'] = 'credential_reference_not_composed'
        return record
    record['ok'] = True
    record['reason'] = 'local_contract_ok'
    return record


def credential_present(home: pathlib.Path, ref: str) -> bool:
    """Whether the DSH store names this reference. Never returns the value.

    Reads only the *keys* of the ``refs`` map. The file is DSH's own 0600
    credential store; this function exists so the supervisor can say
    ``WAITING_CREDITS``/``AUTH`` BEFORE spending a session attempt when the
    key was removed, and it never logs or returns any value.
    """
    path = pathlib.Path(home) / '.credentials.yaml'
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            match = re.match(rf'^\s{{2}}{re.escape(ref)}\s*:\s*(.*)$', line)
            if match:
                # An empty value means "no key" and is rejected by DSH itself;
                # an explicitly empty quoted value is empty too.
                value = match.group(1).strip()
                return bool(value) and value not in ("''", '""')
    except OSError:
        return False
    return False


# ---------------------------------------------------------------------------
# Overlay generation (no secret ever enters the file)
# ---------------------------------------------------------------------------

OVERLAY_HEADER = """\
# Generated by nova3_team.dsh_runtime -- safe to regenerate; do not edit.
# Re-enables DSH's managed credential store for unattended sessions (the
# shipped headless profile disables it, so Web-UI logins are invisible to it)
# and declares the NOVA3 model route. The API key is referenced by NAME; its
# value lives only in $DSH_HOME/.credentials.yaml (0700 dir, 0600 file).
"""


def _yaml_quote(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_./:@-]+', value):
        raise DshLaunchError('Refusing a non-identifier route/model value.')
    return value


@dataclass(frozen=True)
class FallbackRoute:
    """One owner-approved alternative route for the same credential."""

    name: str
    api: str = 'openai-completions'
    model: str = ''
    base_url: str = ''

    def final_model(self, primary: DshEndpoint) -> str:
        return self.model or primary.model

    def final_base_url(self, primary: DshEndpoint) -> str:
        return self.base_url or primary.base_url


def parse_fallback_routes(value: str) -> tuple[FallbackRoute, ...]:
    """Parse NOVA3_DSH_FALLBACK like ``name[:model[:base_url]][,name...]``.

    Nothing is guessed: an empty value means "no approved alternative" and the
    supervisor then pauses correctly instead of inventing a provider.
    """
    out = []
    for item in (value or '').split(','):
        item = item.strip()
        if not item:
            continue
        if '//' in item:
            continue                      # a URL is not a route name
        parts = item.split(':')
        if not parts[0] or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,39}', parts[0]):
            continue
        model = parts[1].strip() if len(parts) > 1 and parts[1].strip() else ''
        base = ':'.join(parts[2:]).strip() if len(parts) > 2 else ''
        if base and not base.startswith('https://'):
            base = ''
        out.append(FallbackRoute(parts[0], model=model, base_url=base))
    return tuple(out[:3])


def overlay_text(endpoint: DshEndpoint, fallbacks: tuple[FallbackRoute, ...] = ()) -> str:
    route = _yaml_quote(endpoint.route)
    model = _yaml_quote(endpoint.model)
    base = endpoint.base_url
    if not re.fullmatch(r'https://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+', base):
        raise DshLaunchError('Refusing a non-https model endpoint.')
    key = _yaml_quote(endpoint.key_ref)
    return (
        OVERLAY_HEADER
        + '- id: credentials\n  disabled: false\n'
        + '- id: authorization\n  disabled: false\n'
        + '- id: deepseek-account\n  disabled: false\n'
        + '- id: llm-pi-ai\n  config:\n    providers:\n'
        + f'      {route}:\n        baseURL: {base}\n        apiKeyEnv: {key}\n'
        + '        models:\n'
        + f'          - id: {model}\n            name: "NOVA3 {model}"\n'
        + '            contextWindow: 1048576\n            maxTokens: 65536\n'
        + '            input: [text]\n'
        + ''.join(
            # Each alternative is a separate pi-ai route over the same
            # credential reference, so switching never needs a new secret.
            f'      {_yaml_quote(f.name)}:\n        api: {_yaml_quote(f.api)}\n'
            f'        baseURL: {f.final_base_url(endpoint)}\n        apiKeyEnv: {key}\n'
            '        models:\n'
            f'          - id: {_yaml_quote(f.final_model(endpoint))}\n'
            f'            name: "NOVA3 fallback {f.name}"\n'
            '            contextWindow: 1048576\n            maxTokens: 65536\n'
            '            input: [text]\n'
            for f in fallbacks)
        + '- id: agent-default-model\n  config:\n'
        + f'    provider: {route}\n    model: {model}\n'
    )


def write_overlay(path: pathlib.Path, endpoint: DshEndpoint, *,
                  fallbacks: tuple[FallbackRoute, ...] = ()) -> pathlib.Path:
    """Atomically write the overlay into a private directory (0700/0600)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        path.parent.chmod(0o700)
    tmp = path.with_name(path.name + '.tmp')
    descriptor = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(overlay_text(endpoint, fallbacks))
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Event stream parsing and failure classification
# ---------------------------------------------------------------------------

def _redact(text: object) -> str:
    try:
        from . import provider_runtime
        return provider_runtime.redact_diagnostic(text)
    except Exception:                       # never fail on a diagnostic
        return str(text)


def classify(code: str | None, message: str | None) -> str:
    """Map a DSH error to a supervisor failure class.

    Stable codes win. Text is consulted only when the adapter produced no
    code at all, and then only for unambiguous provider phrases.
    """
    if code:
        if code in EXACT_CODES:
            return EXACT_CODES[code]
        if code == 'ABORTED':
            return CANCELLED
        if code.startswith('CONTEXT_WINDOW'):
            return STEP_LIMIT            # the context is full: start a fresh session
    for pattern, status in PHRASE_CODES:
        if message and pattern.search(message):
            return status
    return EXECUTION_ERROR


def classify_exit(exit_code: int | None) -> str:
    """Exit status is a fallback only; turn_end reason is authoritative."""
    if exit_code == 0:
        return COMPLETED
    if exit_code in (130, -signal.SIGINT, -signal.SIGTERM, -signal.SIGKILL):
        return CANCELLED
    return EXECUTION_ERROR


@dataclass
class _Accumulator:
    session_id: str | None = None
    steps: int = 0
    tool_calls: int = 0
    turn_kind: str | None = None
    error_code: str | None = None
    error_message: str = ''
    input_tokens: int = 0
    output_tokens: int = 0
    final_text_bytes: int = 0
    saw_final: bool = False
    events: list = field(default_factory=list)
    truncated: bool = False


def _observe(acc: _Accumulator, event: dict) -> None:
    """Fold one event into counters. No string content is retained."""
    kind = event.get('type')
    if kind == 'session':
        acc.session_id = str(event.get('sessionId') or '') or None
    elif kind == 'status':
        phase = event.get('phase')
        if phase == 'step_end':
            acc.steps += 1
            usage = event.get('usage') or {}
            acc.input_tokens += int(usage.get('inputTokens') or 0)
            acc.output_tokens += int(usage.get('outputTokens') or 0)
        elif phase == 'turn_end':
            reason = event.get('reason') or {}
            acc.turn_kind = str(reason.get('kind') or '') or None
            error = reason.get('error') or {}
            if error:
                acc.error_code = str(error.get('code') or '') or acc.error_code
                acc.error_message = str(error.get('message') or '')[:2000]
    elif kind == 'tool_call':
        acc.tool_calls += 1
    elif kind == 'error':
        if not acc.error_code:
            acc.error_code = str(event.get('code') or '') or None
        acc.error_message = acc.error_message or str(event.get('message') or '')[:2000]
    elif kind in ('tool_result', 'text', 'thinking'):
        pass
    elif kind == 'final':
        acc.saw_final = True
        acc.final_text_bytes = len(str(event.get('text') or '').encode('utf-8'))


def run_session(endpoint: DshEndpoint, prompt: str, *, cwd: pathlib.Path, log_path: pathlib.Path,
                timeout_s: int, session_id: str | None = None,
                cancel: Callable[[], bool] | None = None,
                stream_cap: int = DEFAULT_STREAM_CAP) -> SessionOutcome:
    """Run ONE bounded DSH headless session and reduce it to an objective outcome.

    The child gets its own process group so cancellation and timeouts stop the
    whole tree (DSH spawns tool subprocesses). Output goes only to the private
    ``log_path``; only parsed counters leave this function.
    """
    if not isinstance(prompt, str) or not prompt.strip() or '\0' in prompt:
        raise DshLaunchError('A nonempty prompt without NUL bytes is required.')
    if not 1 <= int(timeout_s):
        raise DshLaunchError('A positive session timeout is required.')
    log_path = pathlib.Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    argv = endpoint.command(session_id=session_id, json_events=True)
    acc = _Accumulator()
    started = time.monotonic()
    exit_code: int | None = None
    timed_out = False
    cancelled = False
    over_cap = False
    proc = None
    descriptor = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as sink:
            sink.write(('$ dsh --profile %s headless --json %s-\n'
                        % (endpoint.profile, f'--session-id {session_id} ' if session_id else '')
                        ).encode('utf-8'))
            sink.flush()
            proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    shell=False, start_new_session=(os.name == 'posix'),
                                    env=endpoint.environment())
            deadline = started + timeout_s
            written = 0
            killed = {}          # filled by the stop paths; surface, not a guess
            assert proc.stdout is not None
            # NOVA3_DSH_DEADLINE_V1: the deadline must not depend on output.
            # `for raw in proc.stdout` blocks forever on a silent process (a
            # hung tool, a stalled provider handshake, or time before the first
            # token), so the bound is enforced by a watchdog thread that kills
            # the process group on the same deadline, while the reader loop only
            # decides WHY it stopped.
            stop_reason = {'why': ''}

            def watchdog():
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        return
                    if cancel is not None and cancel():
                        stop_reason['why'] = 'cancel'
                        return _stop(proc)
                    time.sleep(min(DEADLINE_POLL_S, max(0.05, deadline - time.monotonic())))
                if proc.poll() is None:
                    stop_reason['why'] = 'timeout'
                    _stop(proc)

            guard = threading.Thread(target=watchdog, name='nova3-dsh-deadline', daemon=True)
            guard.start()
            # A cold/hung driver may never read stdin. Start the deadline BEFORE
            # writing a prompt larger than the pipe buffer, so that write is
            # bounded too (the watchdog closes the child tree's pipe on kill).
            try:
                proc.stdin.write(prompt.encode('utf-8'))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            for raw in proc.stdout:
                now = time.monotonic()
                written += len(raw)
                if written <= stream_cap:
                    sink.write(raw)
                elif not over_cap:
                    over_cap = True
                    sink.write(b'\n[nova3: private log cap reached; later events summarised only]\n')
                sink.flush()
                line = raw.decode('utf-8', errors='replace').strip()
                if not line.startswith('{'):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if len(acc.events) < DEFAULT_EVENT_CAP:
                    acc.events.append(event)
                else:
                    acc.truncated = True
                _observe(acc, event)
                if acc.saw_final and acc.turn_kind is not None:
                    # Some provider plugins retain transport handles after the
                    # headless driver has emitted its authoritative terminal
                    # turn_end + final pair. Do not wait until the TASK timeout
                    # and misclassify completed work as SESSION_TIMEOUT. Allow
                    # normal teardown briefly, then clean up this process group;
                    # the terminal reason still decides success versus failure.
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        killed = _stop(proc)
                    break
                if acc.steps >= endpoint.max_steps:
                    killed = _stop(proc)
                    break
                if now > deadline or stop_reason['why'] == 'timeout':
                    timed_out = True
                    killed = _stop(proc)
                    break
                if stop_reason['why'] == 'cancel' or (cancel is not None and cancel()):
                    cancelled = True
                    killed = _stop(proc)
                    break
            try:
                exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop(proc)
                try:
                    exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    exit_code = None
            if stop_reason['why'] == 'timeout' or time.monotonic() > deadline:
                # The watchdog fired while the reader was blocked: the process
                # was killed, not finished. Classifying that by its exit status
                # would attribute our own kill to the provider.
                timed_out = timed_out or stop_reason['why'] == 'timeout'
            if stop_reason['why'] == 'cancel':
                cancelled = True
            if timed_out or cancelled:
                killed = killed or {'returncode': proc.returncode}
    finally:
        if proc is not None and proc.poll() is None:
            _stop(proc)
        if proc is not None:
            for pipe in (getattr(proc, 'stdin', None), getattr(proc, 'stdout', None), getattr(proc, 'stderr', None)):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
        if os.name == 'posix':
            try:
                log_path.chmod(0o600)
            except OSError:
                pass
    elapsed = time.monotonic() - started
    if killed.get('returncode') not in (None, -signal.SIGKILL):
        # We stopped the process group ourselves; the child's own exit status
        # therefore says nothing about the turn. Keep it out of the report
        # instead of shipping a misleading 0.
        exit_code = None

    # ---- classification: turn_end reason first, exit status as fallback ----
    if cancelled:
        status = CANCELLED
    elif timed_out:
        status = SESSION_TIMEOUT
    elif acc.steps >= endpoint.max_steps:
        status = STEP_LIMIT
    elif acc.turn_kind is not None:
        status = COMPLETED if acc.turn_kind in COMPLETED_KINDS else classify(acc.error_code,
                                                                             acc.error_message)
    elif acc.saw_final:
        status = classify_exit(exit_code)
    else:
        status = classify(acc.error_code, acc.error_message) if acc.error_code else \
            classify_exit(exit_code)
    if status == COMPLETED and acc.turn_kind is None:
        # A final event with no turn boundary is not proof of a completed turn.
        status = EXECUTION_ERROR
    if not acc.saw_final and status == COMPLETED:
        status = EXECUTION_ERROR
    return SessionOutcome(
        status=status, exit_code=exit_code, session_id=acc.session_id,
        turn_reason=acc.turn_kind, error_code=acc.error_code,
        error_message=_redact(acc.error_message), steps=acc.steps,
        tool_calls=acc.tool_calls, elapsed_s=elapsed,
        input_tokens=acc.input_tokens, output_tokens=acc.output_tokens,
        final_text_bytes=acc.final_text_bytes, log_path=str(log_path),
        truncated=acc.truncated or over_cap, events=tuple(acc.events))


def _stop(proc: subprocess.Popen) -> dict:
    """Stop the whole session tree; never leave an orphan tool process.

    Returns ``{'returncode': ...}`` so the caller can tell "we killed it" apart
    from "it exited on its own" -- a killed child can still report 0.
    """
    returncode = None
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        returncode = proc.wait(timeout=KILL_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            returncode = proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            returncode = None
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
    return {'returncode': returncode}


def list_sessions(endpoint: DshEndpoint, cwd: pathlib.Path) -> dict:
    """Diagnostic only: which persisted sessions exist for this working tree.

    DSH stores one directory per resolved cwd under ``$DSH_HOME/sessions``. The
    supervisor uses ``--session-id`` only for checkpoints *it* recorded, so a
    missing or foreign session is a diagnostic, not a control input.
    """
    slug = re.sub(r'[^A-Za-z0-9]', '-', str(pathlib.Path(cwd).resolve()))
    folder = pathlib.Path(endpoint.home) / 'sessions'
    found = []
    try:
        for child in folder.iterdir():
            if child.is_dir() and slug in child.name:
                found.append(child.name)
    except OSError:
        pass
    return {'cwd_slug': slug, 'sessions_root': folder.name, 'matching_directories': found}

