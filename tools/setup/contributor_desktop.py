"""Optional local-only browser onboarding for NOVA3 contributors.

The module is a *frontend* over the account/provider/worker backends that
already exist in ``nova3_team`` and ``tools/observatory``:

* provider detection/readiness: ``nova3_team.worker.provider_candidates``
* bounded worker slots:        ``nova3_team.worker`` (selection, launch, gate)
* Control Center:              ``tools.observatory`` (opened, never embedded)
* GitHub identity:             the user's own ``gh`` login

Security model (see ``docs/progress/CONTRIBUTOR_ONBOARDING.md``):

* binds ``127.0.0.1`` only, on an ephemeral port chosen by the OS;
* a per-process CSRF secret is generated with ``secrets`` and required in the
  ``X-NOVA3-CSRF`` header of every mutating request;
* every request must carry an exact ``Host`` header and every mutating request
  an exact same-origin ``Origin`` header (DNS-rebinding and cross-site defence);
* no credentials, passwords, API keys or tokens are accepted, stored, echoed or
  placed in a URL; diagnostics are redacted and bounded;
* the GUI never installs with sudo, never pipes a remote script to a shell and
  never launches a provider without the existing owner/admin gate.

It is opt-in (``nova3_easy.py --gui``). The curses Control Center and the plain
CLI remain fully functional without it.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

LOOPBACK = '127.0.0.1'
CSRF_HEADER = 'X-NOVA3-CSRF'
MAX_BODY_BYTES = 64 * 1024
MAX_MESSAGES = 40
REQUEST_LOG_LIMIT = 500
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
PROVIDER_RE = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
SLOT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$')
DEFAULT_SLOT = 'slot-1'

# Official npm package ids. Mirrors nova3_easy.PACKAGES; kept local so the GUI
# cannot be pointed at an arbitrary package by a request payload.
NPM_PACKAGES = {
    'codex': '@openai/codex',
    'claude': '@anthropic-ai/claude-code',
    'gemini': '@google/gemini-cli',
}

CREDENTIAL_KEYS = {
    'password', 'passwd', 'passphrase', 'token', 'access_token', 'refresh_token',
    'secret', 'client_secret', 'api_key', 'apikey', 'authorization', 'cookie',
    'private_key', 'credentials',
}

MATCH_NOTE = (
    'A source match of UNKNOWN means nobody has proven that reconstructed code '
    'compiles to the same machine instructions as the original library. Host '
    'tests and readable source do not change that. UNKNOWN and NOT MATCHED stay '
    'visible on purpose; never treat a passing build as a byte match.'
)
PRIVACY_NOTE = (
    'This page runs on your own machine. It reads your existing GitHub CLI login '
    'and local AI CLI status. It never asks for or stores a password, API key or '
    'token, never copies one provider account into another, and answers only on '
    '127.0.0.1.'
)
GH_GUIDANCE = (
    'Run `gh auth login --web` yourself in a terminal, complete the browser '
    'prompt, then press "Check GitHub" here. The GUI never receives the token.'
)


class DesktopError(RuntimeError):
    """A user-facing failure with a fixed, non-secret message."""


class DesktopSecurityError(DesktopError):
    """A refusal that protects the loopback-only security model."""


# --------------------------------------------------------------------------
# Redaction and fixed diagnostics
# --------------------------------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r'gh[pousr]_[A-Za-z0-9]{10,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{10,}'),
    re.compile(r'sk-[A-Za-z0-9_-]{10,}'),
    re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}'),
    re.compile(r'(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)\S+'),
)


def redact_text(value: object, limit: int = 400) -> str:
    """Return bounded text with token-shaped substrings removed."""
    text = str(value if value is not None else '')
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else '') + '[redacted]', text)
    text = ' '.join(text.split())
    return text[:limit]


# --------------------------------------------------------------------------
# Loopback server primitives
# --------------------------------------------------------------------------

def create_server(session: 'OnboardingSession', *, host: str = LOOPBACK, port: int = 0,
                  handler_factory: Callable = None) -> ThreadingHTTPServer:
    """Create the HTTP server, refusing anything but a loopback bind.

    ``port=0`` asks the OS for an ephemeral port; the actual port is read back
    from ``server.server_address``.
    """
    if host != LOOPBACK:
        raise DesktopSecurityError(
            'The contributor GUI may only bind to 127.0.0.1; refusing host '
            + redact_text(host, 64))
    if not isinstance(port, int) or port < 0 or port > 65535:
        raise DesktopSecurityError('Invalid port; use 0 for an ephemeral port.')
    factory = handler_factory or make_handler
    server = ThreadingHTTPServer((LOOPBACK, port), factory(session))
    server.daemon_threads = True
    bound_host, bound_port = server.server_address[:2]
    if bound_host != LOOPBACK or not bound_port:
        server.server_close()
        raise DesktopSecurityError('Kernel did not bind a loopback ephemeral port.')
    session.bind_port = int(bound_port)
    return server


def expected_origin(port: int) -> str:
    return f'http://{LOOPBACK}:{int(port)}'


def make_handler(session: 'OnboardingSession'):
    """Build a request handler bound to one onboarding session."""

    class ContributorGUIHandler(BaseHTTPRequestHandler):
        server_version = 'NOVA3ContributorGUI/1'
        sys_version = ''

        # -- logging: method/path/status only, never headers, body or query ---
        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            session.record_request(self.command or '?', self.path or '/',
                                   getattr(self, '_status_code', '-'))

        def log_error(self, fmt, *args):
            self.log_message(fmt, *args)

        # -- helpers ---------------------------------------------------------
        def _send(self, status, body: bytes, content_type='application/json; charset=utf-8'):
            self._status_code = status
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header(
                'Content-Security-Policy',
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, sort_keys=True).encode('utf-8'))

        def _fail(self, status, reason, detail=''):
            payload = {'ok': False, 'reason': reason}
            if detail:
                payload['detail'] = redact_text(detail)
            self._json(status, payload)

        def _client_is_loopback(self):
            return str(self.client_address[0]) in (LOOPBACK, '::1')

        def _host_ok(self):
            port = self.server.server_address[1]
            host = self.headers.get('Host', '')
            return host == f'{LOOPBACK}:{int(port)}'

        def _origin_ok(self):
            return self.headers.get('Origin', '') == expected_origin(self.server.server_address[1])

        def _guard(self, *, mutating: bool):
            if not self._client_is_loopback():
                self._fail(HTTPStatus.FORBIDDEN, 'non_loopback_client')
                return False
            if not self._host_ok():
                self._fail(HTTPStatus.FORBIDDEN, 'invalid_host')
                return False
            if mutating:
                if not self._origin_ok():
                    self._fail(HTTPStatus.FORBIDDEN, 'invalid_origin')
                    return False
                supplied = self.headers.get(CSRF_HEADER, '')
                if not supplied or not secrets.compare_digest(supplied, session.csrf):
                    self._fail(HTTPStatus.FORBIDDEN, 'csrf_rejected')
                    return False
            return True

        def _read_json(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return None, 'invalid_length'
            if length <= 0 or length > MAX_BODY_BYTES:
                return None, 'invalid_length'
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                return None, 'invalid_json'
            if not isinstance(payload, dict):
                return None, 'invalid_json'
            lowered = {str(key).casefold() for key in payload}
            if lowered & CREDENTIAL_KEYS:
                return None, 'credentials_not_accepted'
            return payload, None

        # -- GET -------------------------------------------------------------
        def do_GET(self):  # noqa: N802 - stdlib signature
            if not self._guard(mutating=False):
                return
            path = urlsplit(self.path).path
            if path in ('/', '/index.html'):
                body = render_page(session).encode('utf-8')
                self._send(HTTPStatus.OK, body, 'text/html; charset=utf-8')
                return
            if path == '/api/state':
                self._json(HTTPStatus.OK, {'ok': True, 'state': session.snapshot()})
                return
            self._fail(HTTPStatus.NOT_FOUND, 'not_found')

        # -- POST ------------------------------------------------------------
        def do_POST(self):  # noqa: N802 - stdlib signature
            if not self._guard(mutating=True):
                return
            if urlsplit(self.path).path != '/api/action':
                self._fail(HTTPStatus.NOT_FOUND, 'not_found')
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                self._fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'json_required')
                return
            payload, error = self._read_json()
            if error:
                self._fail(HTTPStatus.BAD_REQUEST, error)
                return
            try:
                result = session.dispatch(payload)
            except DesktopSecurityError as exc:
                self._fail(HTTPStatus.FORBIDDEN, 'refused', exc)
                return
            except DesktopError as exc:
                self._fail(HTTPStatus.BAD_REQUEST, 'action_failed', exc)
                return
            except Exception as exc:  # never leak a traceback to the page
                session.add_message('error', f'Action failed ({type(exc).__name__}).')
                self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, 'internal_error',
                           type(exc).__name__)
                return
            status = HTTPStatus.OK if result.get('ok', True) else HTTPStatus.CONFLICT
            body = dict(result)
            body['state'] = session.snapshot()
            self._json(status, body)

    return ContributorGUIHandler


# --------------------------------------------------------------------------
# Backend adapters (all injectable so tests stay offline)
# --------------------------------------------------------------------------

def default_github_status() -> dict:
    """Read the user's own ``gh auth status``; never prompts or reads tokens."""
    gh = shutil.which('gh')
    if not gh:
        return {'state': 'MISSING',
                'reason': 'GitHub CLI (gh) is not installed. Install it from '
                          'https://cli.github.com/ and reopen the launcher.'}
    try:
        completed = subprocess.run([gh, 'auth', 'status'], capture_output=True,
                                   text=True, timeout=10, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return {'state': 'UNKNOWN', 'reason': 'gh auth status could not be checked (offline?).'}
    if completed.returncode == 0:
        return {'state': 'AUTHENTICATED',
                'reason': 'gh is signed in. The GUI never sees your token.'}
    return {'state': 'SIGNED_OUT',
            'reason': 'gh is installed but not signed in. ' + GH_GUIDANCE}


def default_repo_access(repo: str) -> dict:
    """Check repository visibility with the user's existing gh login."""
    if not REPO_RE.match(repo or ''):
        return {'state': 'INVALID', 'reason': 'Use the OWNER/REPO form.'}
    gh = shutil.which('gh')
    if not gh:
        return {'state': 'UNKNOWN', 'reason': 'GitHub CLI (gh) is not installed.'}
    try:
        completed = subprocess.run(
            [gh, 'repo', 'view', repo, '--json', 'nameWithOwner', '--jq', '.nameWithOwner'],
            capture_output=True, text=True, timeout=15, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return {'state': 'UNKNOWN', 'reason': 'Could not reach GitHub; check your connection.'}
    if completed.returncode:
        return {'state': 'DENIED',
                'reason': 'No access with the current gh login. Ask the maintainer for a '
                          'READ invitation, accept it, then check again.'}
    return {'state': 'ACCESSIBLE', 'reason': 'Repository is readable with your own login.'}


def default_candidates() -> list:
    """Installed/authenticated provider evidence from the shared backend."""
    from nova3_team import worker
    return worker.provider_candidates()


def default_installer(provider_id: str, *, run=subprocess.run) -> None:
    """Install one allow-listed official CLI into a private npm prefix.

    No sudo, no shell, no pipe-to-shell: a fixed argv list with ``shell=False``.
    """
    if provider_id not in NPM_PACKAGES:
        raise DesktopError('Only Codex, Claude Code and Gemini CLI can be installed here.')
    npm = shutil.which('npm')
    if not npm:
        raise DesktopError('npm is not installed. Install Node.js from https://nodejs.org/ '
                           'and reopen the launcher.')
    prefix = Path.home() / '.nova3-team' / 'npm'
    prefix.mkdir(parents=True, exist_ok=True)
    argv = [npm, 'install', '--global', '--prefix', str(prefix), NPM_PACKAGES[provider_id]]
    env = dict(os.environ)
    env['NPM_CONFIG_PREFIX'] = str(prefix)
    try:
        completed = run(argv, shell=False, capture_output=True, text=True,
                        timeout=900, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopError('Installer could not run: ' + type(exc).__name__) from None
    if getattr(completed, 'returncode', 1):
        raise DesktopError('The official CLI installer reported an error. '
                           'Nothing was installed with administrator rights.')
    bin_dir = prefix if os.name == 'nt' else prefix / 'bin'
    os.environ['PATH'] = str(bin_dir) + os.pathsep + os.environ.get('PATH', '')


def default_gate_factory(repo: str):
    """Reuse the existing pinned #135/#149 approval gate."""
    from nova3_team import worker
    return worker.build_gate(worker.GitHub(repo))


def default_issues_fetcher(repo: str) -> list:
    from nova3_team import worker
    return worker.GitHub(repo).issues()


def provider_rows(candidates, provider_ids=None) -> list:
    """Normalise backend candidates into fixed-state GUI rows."""
    by_id = {}
    for candidate in candidates or ():
        pid = str(getattr(candidate, 'provider', '') or '')
        if pid:
            by_id[pid] = candidate
    ids = list(provider_ids or by_id.keys())
    rows = []
    for pid in ids:
        candidate = by_id.get(pid)
        if candidate is None:
            rows.append({'id': pid, 'state': 'UNKNOWN', 'installed': None,
                         'authenticated': None, 'detail': 'Status could not be read.',
                         'installable': pid in NPM_PACKAGES})
            continue
        installed = bool(getattr(candidate, 'installed', False))
        authenticated = bool(getattr(candidate, 'authenticated', False))
        if installed and authenticated:
            state = 'READY'
        elif installed:
            state = 'SIGN_IN'
        elif getattr(candidate, 'detail', ''):
            state = 'MISSING'
        else:
            state = 'UNKNOWN'
        rows.append({'id': pid, 'state': state, 'installed': installed,
                     'authenticated': authenticated,
                     'detail': redact_text(getattr(candidate, 'detail', ''), 200),
                     'installable': pid in NPM_PACKAGES})
    return rows


class WorkerController:
    """Thin adapter over the existing bounded-slot worker API.

    Every start goes through ``nova3_team.worker.launch_slots`` with a gate, so
    the owner/admin approval check happens before any provider process. The
    in-memory scheduler cannot control a separate worker's claim loop; active
    process controls therefore report unavailability instead of false success.
    """

    def __init__(self, repo: str, *, cwd=None, python=None, popen=None,
                 gate_factory=None, issues_fetcher=None, root=None,
                 launcher=None, scheduler_factory=None):
        self.repo = repo
        self.cwd = str(cwd) if cwd else None
        self.python = python
        self.popen = popen
        self.gate_factory = gate_factory or default_gate_factory
        self.issues_fetcher = issues_fetcher or default_issues_fetcher
        self.root = root
        self.launcher = launcher
        self.scheduler_factory = scheduler_factory
        self.scheduler = None
        self.state = 'idle'
        self.detail = 'Not started.'
        self.provider = None
        self.processes = ()

    # -- helpers ---------------------------------------------------------
    def _scheduler(self, total=1):
        if self.scheduler is None:
            from nova3_team import worker
            factory = self.scheduler_factory or worker.SlotScheduler
            self.scheduler = factory(total_processes=total)
        return self.scheduler

    def _launch(self, **kwargs):
        if self.launcher is not None:
            return self.launcher(**kwargs)
        from nova3_team import worker
        root = self.root
        if root is None:
            root = worker.state_root(self.repo)
        popen = self.popen or subprocess.Popen
        return worker.launch_slots(
            kwargs['plan'], gate=kwargs['gate'], issues=kwargs['issues'],
            repository_root=root, account=None, consent=kwargs['consent'],
            budget=worker.resource_budget(kwargs['plan'].total),
            python=self.python, repo=self.repo, cwd=self.cwd, popen=popen)

    # -- operations ------------------------------------------------------
    def start(self, *, slots: int = 1, consent: bool = False) -> dict:
        from nova3_team import worker
        if self.state in ('running', 'paused'):
            return {'ok': False, 'reason': 'Worker is already started in this session.'}
        try:
            plan = worker.select_slot_providers(
                worker.provider_candidates(), total=int(slots), consent=bool(consent))
        except worker.SlotError as exc:
            self.detail = redact_text(exc, 200)
            return {'ok': False, 'reason': self.detail}
        try:
            gate = self.gate_factory(self.repo)
        except Exception as exc:
            self.detail = 'Approval gate unavailable: ' + type(exc).__name__
            return {'ok': False, 'reason': self.detail}
        if gate is None:
            self.detail = 'Owner/admin approval gate unavailable; no provider was started.'
            return {'ok': False, 'reason': self.detail}
        try:
            issues = self.issues_fetcher(self.repo)
        except Exception as exc:
            self.detail = 'Task list unavailable: ' + type(exc).__name__
            return {'ok': False, 'reason': self.detail}
        try:
            result = self._launch(plan=plan, gate=gate, issues=issues,
                                  consent=bool(consent))
        except Exception as exc:
            self.detail = 'Launch failed: ' + type(exc).__name__
            return {'ok': False, 'reason': self.detail}
        if not getattr(result, 'started', False):
            self.detail = redact_text(getattr(result, 'reason', 'launch_refused'), 200)
            return {'ok': False, 'reason': self.detail}
        self.provider = plan.slots[0].provider
        self.processes = tuple(getattr(result, 'processes', ()) or ())
        scheduler = self._scheduler(plan.total)
        for index, item in enumerate(plan.slots):
            if item.slot not in scheduler.slots:
                scheduler.add(item.slot, item.provider)
            scheduler.start(item.slot, process=(self.processes[index]
                            if index < len(self.processes) else None))
        self.state = 'running'
        self.detail = f'Started {plan.total} worker(s) after the owner/admin gate.'
        return {'ok': True, 'reason': self.detail}

    def pause(self) -> dict:
        if self.processes:
            return {'ok': False, 'reason': (
                'This GUI has no worker scheduling-control channel. The worker '
                'continues running; use the confirmed local worker controls.')}
        if self.state != 'running':
            return {'ok': False, 'reason': 'Only a running worker can be paused.'}
        self._scheduler().pause(DEFAULT_SLOT)
        self.state = 'paused'
        self.detail = ('Paused: no new work is scheduled. A provider process that is '
                       'already running is not signalled; use Stop to end it.')
        return {'ok': True, 'reason': self.detail}

    def resume(self) -> dict:
        if self.processes:
            return {'ok': False, 'reason': (
                'This GUI has no worker scheduling-control channel; no resume '
                'command was sent to the worker.')}
        if self.state != 'paused':
            return {'ok': False, 'reason': 'Only a paused worker can be resumed.'}
        self._scheduler().resume(DEFAULT_SLOT)
        self.state = 'running'
        self.detail = 'Resumed scheduling for the existing slot.'
        return {'ok': True, 'reason': self.detail}

    def stop(self) -> dict:
        if self.processes:
            return {'ok': False, 'reason': (
                'This GUI cannot safely stop an active worker and its provider. '
                'The worker continues running; use the confirmed local worker controls.')}
        if self.scheduler is not None and DEFAULT_SLOT in self.scheduler.slots:
            self.scheduler.stop(DEFAULT_SLOT)
        self.state = 'stopped'
        self.detail = 'Slot stopped. The worker checkpoint stays on disk.'
        return {'ok': True, 'reason': self.detail}


# --------------------------------------------------------------------------
# Wizard state
# --------------------------------------------------------------------------

STEPS = (
    {'id': 'welcome', 'title': 'Welcome and privacy', 'skippable': False},
    {'id': 'repository', 'title': 'GitHub repository', 'skippable': True},
    {'id': 'provider', 'title': 'AI provider', 'skippable': True},
    {'id': 'worker', 'title': 'Worker slots', 'skippable': False},
    {'id': 'control', 'title': 'Control Center and source match', 'skippable': True},
    {'id': 'done', 'title': 'Finish', 'skippable': False},
)
STEP_INDEX = {step['id']: index for index, step in enumerate(STEPS)}


class OnboardingSession:
    """Server-authoritative wizard state for one GUI run."""

    def __init__(self, *, repo: str = '', workdir=None, python=None,
                 csrf: str | None = None, github_status: Callable | None = None,
                 repo_access: Callable | None = None,
                 candidates: Callable | None = None,
                 installer: Callable | None = None,
                 controller_factory: Callable | None = None,
                 browser: Callable | None = None):
        self.csrf = csrf or secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.bind_port = 0
        self.step = 0
        self.repo = repo if REPO_RE.match(repo or '') else ''
        self.workdir = str(workdir) if workdir else ''
        self.python = python
        self._github_status = github_status or default_github_status
        self._repo_access = repo_access or default_repo_access
        self._candidates = candidates or default_candidates
        self._installer = installer or default_installer
        self._controller_factory = controller_factory or (
            lambda repo: WorkerController(repo, cwd=self.workdir, python=self.python))
        self.browser = browser or webbrowser.open
        self.github = {'state': 'UNKNOWN', 'reason': 'Not checked yet.'}
        self.repo_status = {'state': 'UNKNOWN', 'reason': 'Not checked yet.'}
        self.providers = []
        self.selected_provider = ''
        self.install_consent = False
        self.install = {'state': 'IDLE', 'reason': ''}
        self.slots = 1
        self.multi_consent = False
        self.controller = None
        self.worker = {'state': 'idle', 'detail': 'Not started.', 'provider': ''}
        self.messages = []
        self.request_log = []
        self.shutdown_requested = False

    # -- diagnostics -----------------------------------------------------
    def add_message(self, level: str, text: str):
        with self.lock:
            self.messages.append({'level': level, 'text': redact_text(text, 400)})
            del self.messages[:-MAX_MESSAGES]

    def record_request(self, method: str, path: str, status):
        with self.lock:
            self.request_log.append(f'{method} {urlsplit(path).path} -> {status}')
            del self.request_log[:-REQUEST_LOG_LIMIT]

    # -- reads -----------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            worker = self.controller.state if self.controller else 'idle'
            detail = self.controller.detail if self.controller else 'Not started.'
            provider = self.controller.provider if self.controller else ''
            return {
                'step': self.step,
                'step_id': STEPS[self.step]['id'],
                'steps': [dict(step) for step in STEPS],
                'repo': self.repo,
                'repo_status': dict(self.repo_status),
                'github': dict(self.github),
                'github_guidance': GH_GUIDANCE,
                'providers': [dict(row) for row in self.providers],
                'selected_provider': self.selected_provider,
                'install': dict(self.install),
                'slots': self.slots,
                'multi_consent': self.multi_consent,
                'max_slots': 4,
                'worker': {'state': worker, 'detail': redact_text(detail, 300),
                           'provider': provider},
                'messages': [dict(item) for item in self.messages[-12:]],
                'privacy': PRIVACY_NOTE,
                'match_note': MATCH_NOTE,
                'fallback_cli': ('python3 nova3_easy.py            # guided terminal setup\n'
                                 'python3 -m tools.observatory    # Control Center TUI\n'
                                 'python3 -m nova3_team.worker status --repo OWNER/REPO'),
                'shutdown': self.shutdown_requested,
            }

    # -- checks ----------------------------------------------------------
    def refresh_github(self) -> dict:
        try:
            status = self._github_status()
        except Exception as exc:
            status = {'state': 'UNKNOWN',
                      'reason': 'GitHub status unavailable (' + type(exc).__name__ + ').'}
        if not isinstance(status, dict) or status.get('state') not in (
                'AUTHENTICATED', 'SIGNED_OUT', 'MISSING', 'UNKNOWN'):
            status = {'state': 'UNKNOWN', 'reason': 'GitHub status could not be verified.'}
        self.github = {'state': status['state'],
                       'reason': redact_text(status.get('reason', ''), 300)}
        return self.github

    def refresh_repo(self) -> dict:
        if not REPO_RE.match(self.repo or ''):
            self.repo_status = {'state': 'INVALID', 'reason': 'Use the OWNER/REPO form, '
                                                              'for example N-O-V-A-3-RE-Contributors/nova3-reconstruction.'}
            return self.repo_status
        try:
            status = self._repo_access(self.repo)
        except Exception as exc:
            status = {'state': 'UNKNOWN',
                      'reason': 'Repository check failed (' + type(exc).__name__ + ').'}
        if not isinstance(status, dict) or status.get('state') not in (
                'ACCESSIBLE', 'DENIED', 'INVALID', 'UNKNOWN'):
            status = {'state': 'UNKNOWN', 'reason': 'Repository check was inconclusive.'}
        self.repo_status = {'state': status['state'],
                            'reason': redact_text(status.get('reason', ''), 300)}
        return self.repo_status

    def refresh_providers(self) -> list:
        try:
            candidates = self._candidates()
            rows = provider_rows(candidates)
        except Exception:
            rows = provider_rows([], provider_ids=sorted(NPM_PACKAGES))
            for row in rows:
                row['detail'] = 'Offline or probe failed; status is UNKNOWN, not READY.'
        self.providers = rows
        if self.selected_provider and self.selected_provider not in {
                row['id'] for row in rows}:
            self.selected_provider = ''
        return self.providers

    def _provider_state(self, provider_id, default='UNKNOWN'):
        for row in self.providers:
            if row['id'] == provider_id:
                return row['state']
        return default

    # -- actions ---------------------------------------------------------
    def dispatch(self, payload: dict) -> dict:
        action = str(payload.get('action') or '')
        with self.lock:
            handler = getattr(self, '_action_' + action, None)
            if handler is None:
                return {'ok': False, 'reason': 'Unknown action.'}
            return handler(payload)

    def _action_next(self, payload):
        if self.step < len(STEPS) - 1:
            self.step += 1
        return {'ok': True, 'reason': ''}

    def _action_back(self, payload):
        if self.step > 0:
            self.step -= 1
        return {'ok': True, 'reason': ''}

    def _action_skip(self, payload):
        if not STEPS[self.step]['skippable']:
            return {'ok': False, 'reason': 'This step cannot be skipped.'}
        return self._action_next(payload)

    def _action_set_repo(self, payload):
        repo = str(payload.get('repo') or '').strip()
        if not REPO_RE.match(repo):
            self.repo_status = {'state': 'INVALID', 'reason': 'Use the OWNER/REPO form.'}
            return {'ok': False, 'reason': 'Use the OWNER/REPO form.'}
        if repo != self.repo:
            self.repo = repo
            self.repo_status = {'state': 'UNKNOWN', 'reason': 'Not checked yet.'}
        return {'ok': True, 'reason': 'Repository saved locally.'}

    def _action_check_github(self, payload):
        status = self.refresh_github()
        return {'ok': status['state'] in ('AUTHENTICATED', 'SIGNED_OUT', 'MISSING'),
                'reason': status['reason']}

    def _action_check_repo(self, payload):
        status = self.refresh_repo()
        return {'ok': status['state'] == 'ACCESSIBLE', 'reason': status['reason']}

    def _action_scan_providers(self, payload):
        self.refresh_providers()
        return {'ok': True, 'reason': 'Provider status refreshed locally.'}

    def _action_install_provider(self, payload):
        provider = str(payload.get('provider') or '')
        if provider not in NPM_PACKAGES:
            return {'ok': False, 'reason': 'Only Codex, Claude Code and Gemini CLI are supported.'}
        self.install_consent = bool(payload.get('consent') is True)
        if not self.install_consent:
            self.install = {'state': 'DECLINED',
                            'reason': 'Installation skipped: explicit consent was not given.'}
            return {'ok': False, 'reason': self.install['reason']}
        self.install = {'state': 'INSTALLING',
                        'reason': 'Installing from the official vendor package.'}
        try:
            self._installer(provider)
        except Exception as exc:
            self.install = {'state': 'FAILED',
                            'reason': redact_text(str(exc) or type(exc).__name__, 300)}
            self.add_message('error', self.install['reason'])
            return {'ok': False, 'reason': self.install['reason']}
        self.install = {'state': 'INSTALLED',
                        'reason': 'Installed without administrator rights. Sign in with the vendor CLI.'}
        self.refresh_providers()
        return {'ok': True, 'reason': self.install['reason']}

    def _action_select_provider(self, payload):
        provider = str(payload.get('provider') or '')
        if provider not in {row['id'] for row in self.providers}:
            if provider not in NPM_PACKAGES:
                return {'ok': False, 'reason': 'Unknown provider.'}
        if self._provider_state(provider) != 'READY':
            return {'ok': False, 'reason': 'That provider is not installed and signed in yet.'}
        self.selected_provider = provider
        return {'ok': True, 'reason': 'Provider selected; one worker remains the default.'}

    def _action_set_slots(self, payload):
        try:
            slots = int(payload.get('slots', 1))
        except (TypeError, ValueError):
            return {'ok': False, 'reason': 'Slots must be a whole number.'}
        if slots < 1 or slots > 4:
            return {'ok': False, 'reason': 'Choose between 1 and 4 slots.'}
        consent = payload.get('consent') is True
        if slots > 1 and not consent:
            return {'ok': False,
                    'reason': 'More than one active AI process needs explicit consent.'}
        self.slots = slots
        self.multi_consent = consent if slots > 1 else False
        return {'ok': True, 'reason': 'One slot is the default; extra slots stay opt-in.'}

    def _ensure_controller(self):
        if self.controller is None:
            self.controller = self._controller_factory(self.repo)
        return self.controller

    def _action_start(self, payload):
        if not self.repo:
            return {'ok': False, 'reason': 'Choose a repository before starting a worker.'}
        if self.github.get('state') != 'AUTHENTICATED':
            return {'ok': False, 'reason': 'Sign in with gh first; press "Check GitHub".'}
        if self.slots > 1 and not self.multi_consent:
            return {'ok': False, 'reason': 'Multiple slots need explicit consent.'}
        controller = self._ensure_controller()
        result = controller.start(slots=self.slots, consent=self.multi_consent)
        self.worker = {'state': controller.state, 'detail': controller.detail,
                       'provider': controller.provider or ''}
        self.add_message('info' if result['ok'] else 'error', result['reason'])
        return result

    def _action_pause(self, payload):
        if self.controller is None:
            return {'ok': False, 'reason': 'Nothing is running in this session.'}
        result = self.controller.pause()
        self.worker = {'state': self.controller.state,
                       'detail': self.controller.detail,
                       'provider': self.controller.provider or ''}
        return result

    def _action_resume(self, payload):
        if self.controller is None:
            return {'ok': False, 'reason': 'Nothing is paused in this session.'}
        result = self.controller.resume()
        self.worker = {'state': self.controller.state,
                       'detail': self.controller.detail,
                       'provider': self.controller.provider or ''}
        return result

    def _action_stop(self, payload):
        if self.controller is None:
            return {'ok': False, 'reason': 'Nothing is running in this session.'}
        result = self.controller.stop()
        self.worker = {'state': self.controller.state,
                       'detail': self.controller.detail,
                       'provider': self.controller.provider or ''}
        return result

    def _action_open_control_center(self, payload):
        """Report the exact command; never spawn a second curses session."""
        return {'ok': True,
                'reason': 'Open Control Center in a terminal: python3 -m tools.observatory'}

    def _action_shutdown(self, payload):
        self.shutdown_requested = True
        return {'ok': True, 'reason': 'Shutting down the local GUI.'}


# --------------------------------------------------------------------------
# HTML (self-contained, no remote assets)
# --------------------------------------------------------------------------

def render_page(session: OnboardingSession) -> str:
    token = html.escape(session.csrf, quote=True)
    state = json.dumps(session.snapshot(), sort_keys=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="nova3-csrf" content="{token}">
<title>NOVA3 contributor setup</title>
<style>
 body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
        background: #0f1115; color: #e6e9ef; line-height: 1.5; }}
 main {{ max-width: 46rem; margin: 0 auto; padding: 1.5rem; }}
 h1 {{ font-size: 1.5rem; }}
 section[hidden] {{ display: none; }}
 nav ol {{ display: flex; gap: .4rem; flex-wrap: wrap; list-style: none; padding: 0; }}
 nav li {{ font-size: .8rem; padding: .2rem .5rem; border: 1px solid #39404d;
          border-radius: 999px; color: #9aa4b2; }}
 nav li[aria-current="step"] {{ border-color: #6ea8fe; color: #e6e9ef; }}
 label {{ display: block; margin: .75rem 0 .25rem; font-weight: 600; }}
 input[type=text], input[type=number], select {{
        width: 100%; padding: .55rem; border-radius: .4rem; border: 1px solid #39404d;
        background: #171a21; color: #e6e9ef; }}
 button {{ margin: .35rem .4rem .35rem 0; padding: .5rem .9rem; border-radius: .4rem;
          border: 1px solid #39404d; background: #1d222b; color: #e6e9ef; cursor: pointer; }}
 button.primary {{ background: #2f5fb3; border-color: #2f5fb3; }}
 button:disabled {{ opacity: .5; cursor: not-allowed; }}
 .status {{ padding: .6rem .8rem; border-radius: .4rem; background: #171a21;
           border: 1px solid #39404d; }}
 .warn {{ border-left: .25rem solid #d9a441; padding-left: .8rem; }}
 table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
 th, td {{ text-align: left; padding: .35rem; border-bottom: 1px solid #2a303a; }}
 .muted {{ color: #9aa4b2; font-size: .85rem; }}
 #messages li {{ font-size: .85rem; }}
 footer {{ margin-top: 2rem; font-size: .85rem; color: #9aa4b2; }}
 code {{ background: #171a21; padding: .1rem .3rem; border-radius: .25rem; }}
</style>
</head>
<body>
<main>
<h1>NOVA3 contributor setup</h1>
<nav aria-label="Setup progress"><ol id="progress"></ol></nav>
<p class="status" id="status" role="status" aria-live="polite">Ready.</p>

<section data-step="welcome">
  <h2>Welcome</h2>
  <p>This optional wizard prepares your own machine for NOVA3 reconstruction work.
     It uses the same account, provider and worker backends as the terminal launcher.</p>
  <p class="warn">{html.escape(PRIVACY_NOTE)}</p>
  <p class="muted">You can skip the browser entirely: close this tab and run
     <code>python3 nova3_easy.py</code> for the guided terminal flow, or
     <code>python3 -m tools.observatory</code> for the Control Center TUI.</p>
</section>

<section data-step="repository" hidden>
  <h2>GitHub repository</h2>
  <label for="repo">Repository, OWNER/REPO</label>
  <input id="repo" type="text" autocomplete="off" spellcheck="false"
         placeholder="N-O-V-A-3-RE-Contributors/nova3-reconstruction">
  <p><button id="save-repo" type="button">Save repository</button>
     <button id="check-github" type="button">Check GitHub</button>
     <button id="check-repo" type="button">Check access</button></p>
  <p class="status" id="github-status" aria-live="polite"></p>
  <p class="muted" id="github-guidance"></p>
</section>

<section data-step="provider" hidden>
  <h2>AI provider</h2>
  <p>Installed and signed-in CLIs are detected locally. Detection does not sign in
     or make a paid model call.</p>
  <table><thead><tr><th>Provider</th><th>Status</th><th>Detail</th><th></th></tr></thead>
    <tbody id="providers"></tbody></table>
  <label><input id="install-consent" type="checkbox"> I consent to installing the
     selected CLI from its official vendor package into my user directory (no sudo).</label>
  <p><button id="scan-providers" type="button">Rescan</button></p>
  <p class="muted">Alternatively run the vendor CLI login yourself in a terminal. The GUI
     never receives a token and never switches accounts.</p>
</section>

<section data-step="worker" hidden>
  <h2>Worker slots</h2>
  <p>One active AI process is the default on a 4-core/8&nbsp;GB machine.</p>
  <label for="slots">Active AI processes</label>
  <select id="slots"><option value="1">1 (recommended)</option>
    <option value="2">2</option><option value="3">3</option><option value="4">4</option></select>
  <label><input id="multi-consent" type="checkbox"> I explicitly consent to more than
     one active AI process. This may saturate a small machine.</label>
  <p><button id="set-slots" type="button">Apply</button></p>
  <p class="status" id="worker-status" aria-live="polite"></p>
  <p><button id="start" class="primary" type="button">Start</button>
     <button id="pause" type="button">Pause</button>
     <button id="resume" type="button">Resume</button>
     <button id="stop" type="button">Stop</button></p>
  <p class="muted">Start is refused unless the existing owner/admin approval gate and
     pinned repository identity checks pass. The GUI cannot bypass them.</p>
</section>

<section data-step="control" hidden>
  <h2>Control Center and source match</h2>
  <p class="warn">{html.escape(MATCH_NOTE)}</p>
  <p>Open the Control Center in a terminal with
     <code>python3 -m tools.observatory</code>; it stays the power-user interface.</p>
  <p><button id="open-control" type="button">Show the command</button></p>
</section>

<section data-step="done" hidden>
  <h2>Finish</h2>
  <p>Your choices are saved for this session only. Worker checkpoints remain on disk
     under the existing state directory.</p>
  <p><button id="shutdown" type="button">Close the local GUI</button></p>
</section>

<footer>
  <p><button id="back" type="button">Back</button>
     <button id="skip" type="button">Skip</button>
     <button id="next" class="primary" type="button">Next</button></p>
  <ul id="messages" aria-live="polite"></ul>
</footer>
</main>
<script>
const CSRF = document.querySelector('meta[name="nova3-csrf"]').content;
let STATE = {state};
let SCANNED = false;

function setStatus(text) {{ document.getElementById('status').textContent = text; }}

async function act(action, extra) {{
  setStatus('Working...');
  try {{
    const response = await fetch('/api/action', {{
      method: 'POST', headers: {{'Content-Type': 'application/json', 'X-NOVA3-CSRF': CSRF}},
      body: JSON.stringify(Object.assign({{action}}, extra || {{}}))
    }});
    const data = await response.json();
    STATE = data.state || STATE;
    render();
    setStatus((data.ok ? '' : 'Not done: ') + (data.reason || data.detail || ''));
  }} catch (error) {{
    setStatus('The local GUI is not responding. Restart with: python3 nova3_easy.py --gui');
  }}
}}

function el(tag, text, cls) {{
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}}

function render() {{
  const progress = document.getElementById('progress');
  progress.textContent = '';
  STATE.steps.forEach((step, index) => {{
    const item = el('li', step.title + (index < STATE.step ? ' done' : ''));
    if (index === STATE.step) item.setAttribute('aria-current', 'step');
    progress.appendChild(item);
  }});
  document.querySelectorAll('section[data-step]').forEach(section => {{
    section.hidden = section.dataset.step !== STATE.step_id;
  }});
  const repo = document.getElementById('repo');
  if (document.activeElement !== repo) repo.value = STATE.repo;
  document.getElementById('github-status').textContent =
    'GitHub: ' + STATE.github.state + ' — ' + STATE.github.reason;
  document.getElementById('github-guidance').textContent = STATE.github_guidance;
  const table = document.getElementById('providers');
  table.textContent = '';
  (STATE.providers || []).forEach(row => {{
    const tr = el('tr');
    tr.appendChild(el('td', row.id));
    tr.appendChild(el('td', row.state));
    tr.appendChild(el('td', row.detail || ''));
    const cell = el('td');
    if (row.installable) {{
      const button = el('button', row.state === 'READY' ? 'Select' : 'Install');
      button.type = 'button';
      button.addEventListener('click', () => {{
        if (row.state === 'READY') act('select_provider', {{provider: row.id}});
        else act('install_provider', {{provider: row.id,
          consent: document.getElementById('install-consent').checked}});
      }});
      cell.appendChild(button);
    }}
    tr.appendChild(cell);
    table.appendChild(tr);
  }});
  document.getElementById('slots').value = String(STATE.slots);
  document.getElementById('worker-status').textContent =
    'Worker: ' + STATE.worker.state + ' — ' + STATE.worker.detail;
  const messages = document.getElementById('messages');
  messages.textContent = '';
  (STATE.messages || []).forEach(item => messages.appendChild(el('li', item.text)));
  document.getElementById('skip').disabled = !STATE.steps[STATE.step].skippable;
  document.getElementById('back').disabled = STATE.step === 0;
  document.getElementById('next').disabled = STATE.step === STATE.steps.length - 1;
  if (STATE.step_id === 'provider' && !SCANNED) {{
    SCANNED = true;
    act('scan_providers');
  }}
}}

document.getElementById('next').addEventListener('click', () => act('next'));
document.getElementById('back').addEventListener('click', () => act('back'));
document.getElementById('skip').addEventListener('click', () => act('skip'));
document.getElementById('save-repo').addEventListener('click', () =>
  act('set_repo', {{repo: document.getElementById('repo').value.trim()}}));
document.getElementById('check-github').addEventListener('click', () => act('check_github'));
document.getElementById('check-repo').addEventListener('click', () => act('check_repo'));
document.getElementById('scan-providers').addEventListener('click', () => act('scan_providers'));
document.getElementById('set-slots').addEventListener('click', () => act('set_slots', {{
  slots: Number(document.getElementById('slots').value),
  consent: document.getElementById('multi-consent').checked}}));
document.getElementById('start').addEventListener('click', () => act('start'));
document.getElementById('pause').addEventListener('click', () => act('pause'));
document.getElementById('resume').addEventListener('click', () => act('resume'));
document.getElementById('stop').addEventListener('click', () => act('stop'));
document.getElementById('open-control').addEventListener('click', () => act('open_control_center'));
document.getElementById('shutdown').addEventListener('click', () => act('shutdown'));

render();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Launch / lifecycle
# --------------------------------------------------------------------------

def start_gui(session: OnboardingSession, *, host: str = LOOPBACK, port: int = 0,
              open_browser: bool = True, browser: Callable | None = None):
    """Bind, start serving in a thread, optionally open a browser.

    Returns ``(server, thread, info)``. ``info`` records the URL and whether a
    browser was opened. A browser failure is never fatal: the URL is printed so
    the user can open it, or fall back to the CLI/TUI.
    """
    server = create_server(session, host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2},
                              name='nova3-contributor-gui', daemon=True)
    thread.start()
    url = expected_origin(server.server_address[1]) + '/'
    opener = browser or session.browser
    info = {'url': url, 'opened': False, 'reason': ''}
    if open_browser:
        try:
            opened = opener(url)
        except Exception as exc:
            info['reason'] = 'Browser launch failed (' + type(exc).__name__ + ').'
        else:
            if opened is False:
                info['reason'] = 'No browser was available to open the page.'
            else:
                info['opened'] = True
    return server, thread, info


def wait_for_shutdown(session: OnboardingSession, server: ThreadingHTTPServer,
                      thread: threading.Thread, *, poll: float = 0.2) -> None:
    """Serve until the shutdown action or Ctrl+C, then close cleanly."""
    try:
        while not session.shutdown_requested:
            time.sleep(poll)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_gui(*, repo: str = '', workdir=None, python=None, port: int = 0,
            open_browser: bool = True, out=print) -> int:
    session = OnboardingSession(repo=repo, workdir=workdir, python=python)
    server, thread, info = start_gui(session, port=port, open_browser=open_browser)
    out('NOVA3 local setup GUI: ' + info['url'])
    out('Bound to 127.0.0.1 only. No remote access, no account tokens collected.')
    if not info['opened']:
        out('Browser was not opened' + ((': ' + info['reason']) if info['reason'] else '.')
            + ' Open the URL above, or use the terminal fallback: python3 nova3_easy.py')
    out('Press Ctrl+C to stop the GUI. Worker checkpoints stay on disk.')
    try:
        wait_for_shutdown(session, server, thread)
    except KeyboardInterrupt:
        session.shutdown_requested = True
        server.shutdown()
        server.server_close()
        out('GUI stopped by user.')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 optional local browser onboarding')
    parser.add_argument('--repo', default='', help='OWNER/REPO to prefill')
    parser.add_argument('--port', type=int, default=0, help='0 = ephemeral loopback port')
    parser.add_argument('--no-browser', action='store_true', help='print the URL only')
    args = parser.parse_args(argv)
    return run_gui(repo=args.repo, port=args.port, open_browser=not args.no_browser)


if __name__ == '__main__':
    raise SystemExit(main())
