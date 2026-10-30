"""Guided Accounts screen model for the NOVA3 Control Center.

The module is deliberately boring: it renders evidence and asks the operator
before doing anything that leaves the TUI.  It never collects a password or
token, never starts an interactive vendor login by itself, and never calls a
model.  Provider metadata is owned by :mod:`nova3_team.provider_runtime`; this
module only projects that verified registry into readable rows.

GitHub identity uses the already-installed ``gh`` CLI.  Only status, account
name and token scopes are shown; token values are never read or formatted.
Owner/admin decisions are re-derived at the action boundary and fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import urllib.parse

# Observatory installs are standalone directories.  Load a sibling runtime if
# present (install.sh copies it), otherwise fall back to the repository package.
# provider_runtime uses package-relative imports (e.g. ``from . import
# hermes_runtime``), so a bare spec-from-file load breaks it.  Register a
# synthetic package rooted at the runtime's directory instead.
_RUNTIME_PACKAGE = 'nova3_private_provider_runtime'


def _load_runtime():
    # Prefer the file candidate even when `nova3_team` happens to be importable:
    # importing the real package here would shadow the fixture packages used by
    # standalone Observatory tests.
    if _RUNTIME_PACKAGE in sys.modules:
        return sys.modules[_RUNTIME_PACKAGE + '.provider_runtime']
    candidates = (Path(__file__).resolve().parent / 'provider_runtime.py',
                  Path(__file__).resolve().parents[2] / 'nova3_team' / 'provider_runtime.py')
    for candidate in candidates:
        if not candidate.is_file():
            continue
        package = types.ModuleType(_RUNTIME_PACKAGE)
        package.__path__ = [str(candidate.parent)]
        sys.modules[_RUNTIME_PACKAGE] = package
        spec = importlib.util.spec_from_file_location(
            _RUNTIME_PACKAGE + '.provider_runtime', candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            sys.modules.pop(_RUNTIME_PACKAGE, None)
            continue
        return module
    from nova3_team import provider_runtime as module  # installed package fallback
    return module


provider_runtime = _load_runtime()

MAX_GH_BYTES = 64 * 1024
GH_TIMEOUT = 4.0
ROLE_VALUES = frozenset({'ADMIN', 'MAINTAIN', 'WRITE', 'TRIAGE', 'READ', 'UNKNOWN'})
# Repository ADMIN (and verified organization owners, resolved server-side by
# operator_ops) may administer. MAINTAIN/WRITE/TRIAGE/READ are contributor
# levels and must never unlock owner actions (#134/#135).
OWNER_ROLES = frozenset({'ADMIN'})
_REPO_RE = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
_LOGIN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}')
_ACCOUNT_RE = re.compile(r'account\s+([A-Za-z0-9][A-Za-z0-9_.-]{0,62})', re.I)
_SCOPE_RE = re.compile(r'Token scopes:\s*(.+)', re.I)
_SECRET_LINE_RE = re.compile(r'(?i)\b(token|password|secret|api[_-]?key)\b\s*[:=]\s*[^\s,;]+')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class InstallState(str, Enum):
    INSTALLED = 'INSTALLED'
    NOT_INSTALLED = 'NOT INSTALLED'
    INCOMPATIBLE = 'INCOMPATIBLE'
    PROBE_FAILED = 'PROBE FAILED'
    UNKNOWN = 'UNKNOWN'


class AccountState(str, Enum):
    AUTHENTICATED = 'LOGGED IN'
    UNAUTHENTICATED = 'LOGGED OUT'
    UNKNOWN = 'LOGIN UNKNOWN'
    UNAVAILABLE = 'NOT APPLICABLE'
    NOT_LOADED = 'NOT LOADED'


def redact(value: object, limit: int = 300) -> str:
    """Make a bounded, control-character-free diagnostic safe to display.

    The runtime redactor masks known environment secrets and common token
    shapes.  This wrapper also removes ``token=...`` style assignments, so a
    fake secret supplied by a vendor probe cannot reach the screen.
    """
    text = provider_runtime.redact_diagnostic(value)
    text = _CONTROL_RE.sub(' ', text)
    text = _SECRET_LINE_RE.sub(lambda m: m.group(1) + '=[REDACTED]', text)
    text = ' '.join(text.split())
    return text[:limit]


def safe_url(url: object) -> bool:
    """Only official HTTPS destinations may reach the browser helper.

    Parsed with urlsplit so userinfo tricks (``https://github.com:443@evil``)
    cannot pass the allowlist: the username/password must be empty and the
    hostname itself must be allowlisted.
    """
    if not isinstance(url, str) or len(url) > 300:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if (parts.scheme != 'https' or parts.username is not None
            or parts.password is not None or not parts.hostname):
        return False
    host = parts.hostname.lower()
    return host in {'github.com', 'www.github.com', 'developers.openai.com',
                    'code.claude.com', 'geminicli.com', 'www.kimi-cli.com',
                    'qwenlm.github.io', 'opencode.ai', 'docs.github.com'}


def _map_install(status) -> InstallState:
    state = getattr(status, 'state', None)
    value = getattr(state, 'value', state)
    return {
        'available': InstallState.INSTALLED,
        'missing': InstallState.NOT_INSTALLED,
        'incompatible': InstallState.INCOMPATIBLE,
        'probe_failed': InstallState.PROBE_FAILED,
    }.get(str(value), InstallState.UNKNOWN)


def _map_account(state) -> AccountState:
    value = getattr(state, 'value', state)
    return {
        'authenticated': AccountState.AUTHENTICATED,
        'unauthenticated': AccountState.UNAUTHENTICATED,
        'unknown': AccountState.UNKNOWN,
        'unavailable': AccountState.UNAVAILABLE,
    }.get(str(value), AccountState.UNKNOWN)


@dataclass(frozen=True)
class ProviderAccount:
    provider_id: str
    display_name: str
    install_state: InstallState
    account_state: AccountState
    enabled: bool
    detail: str
    guidance: str
    install_url: str
    docs_url: str

    @property
    def ready(self) -> bool:
        return (self.enabled and self.install_state is InstallState.INSTALLED
                and self.account_state is AccountState.AUTHENTICATED)

    @property
    def label(self) -> str:
        if not self.enabled:
            return 'DISABLED'
        if self.install_state is not InstallState.INSTALLED:
            return self.install_state.value
        if self.account_state is AccountState.AUTHENTICATED:
            return 'READY'
        return self.account_state.value


@dataclass(frozen=True)
class ConnectionReport:
    provider_id: str
    ok: bool
    summary: str
    detail: str


@dataclass(frozen=True)
class GitHubIdentity:
    state: AccountState = AccountState.NOT_LOADED
    account: str = ''
    scopes: tuple[str, ...] = ()
    detail: str = 'GitHub status has not been queried; press R.'
    role: str = 'UNKNOWN'

    @property
    def label(self) -> str:
        if self.state is AccountState.AUTHENTICATED:
            return 'LOGGED IN'
        return self.state.value

    @property
    def scope_label(self) -> str:
        return ', '.join(self.scopes) if self.scopes else 'not reported'


@dataclass(frozen=True)
class Member:
    login: str
    role: str


@dataclass(frozen=True)
class MemberView:
    state: str = 'NOT LOADED'
    members: tuple[Member, ...] = ()
    detail: str = 'Read-only view; press M to load collaborators.'


def _bounded_gh(argv: list[str], timeout: float = GH_TIMEOUT) -> tuple[int | None, str]:
    """Run one fixed gh command without a shell, pipes, pager or unbounded RAM."""
    env = dict(os.environ)
    env.update({'GH_PAGER': 'cat', 'PAGER': 'cat', 'NO_COLOR': '1', 'CLICOLOR': '0'})
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout, shell=False, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, ''
    data = (proc.stdout or '') + (proc.stderr or '')
    if len(data) > MAX_GH_BYTES:
        data = data[:MAX_GH_BYTES]
    return proc.returncode, data


class AccountsController:
    """State and action boundary for the Accounts page.

    ``probe``, ``auth``, ``identity_probe``, ``role_resolver`` and ``opener``
    are injectable so every unit test stays offline.  None of the defaults
    submits a prompt or starts a vendor login session.
    """

    def __init__(self, repo: str, state_path: str | os.PathLike[str] | None = None, *,
                 probe=None, auth=None, identity_probe=None, role_resolver=None,
                 opener=None, login_launcher=None):
        if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
            raise ValueError('Repository must be OWNER/REPOSITORY.')
        self.repo = repo
        self.state_path = Path(state_path) if state_path else None
        self._probe = probe or provider_runtime.probe_provider
        self._auth = auth or provider_runtime.check_auth
        self._identity_probe = identity_probe or self._probe_github_identity
        self._role_resolver = role_resolver or self._probe_repo_role
        self._opener = opener
        # #134/#135 seam: a future verified login adapter can replace this
        # callable.  It must return a bounded result and must never hand the
        # TUI a password/token prompt.  The default only returns guidance.
        self._login_launcher = login_launcher or (lambda provider_id: None)
        self.ids = tuple(provider_runtime.provider_ids())
        self._enabled = {pid: True for pid in self.ids}
        self._install = {pid: InstallState.UNKNOWN for pid in self.ids}
        self._account = {pid: AccountState.UNKNOWN for pid in self.ids}
        self._detail = {pid: 'Not probed. Press R to inspect installed CLIs.'
                        for pid in self.ids}
        self.identity = GitHubIdentity()
        self.members = MemberView()
        self.selected = 0
        self.last_test: ConnectionReport | None = None
        self._load_enabled()

    # -- view -----------------------------------------------------------
    def rows(self) -> tuple[ProviderAccount, ...]:
        result = []
        for pid in self.ids:
            spec = provider_runtime.PROVIDERS[pid]
            result.append(ProviderAccount(
                pid, spec.display_name, self._install[pid], self._account[pid],
                self._enabled[pid], self._detail[pid], spec.login_guidance,
                spec.install_url, spec.docs_url))
        return tuple(result)

    def selected_row(self) -> ProviderAccount | None:
        rows = self.rows()
        if not rows:
            return None
        self.selected = max(0, min(self.selected, len(rows) - 1))
        return rows[self.selected]

    def move(self, delta: int) -> ProviderAccount | None:
        if self.ids:
            self.selected = (self.selected + delta) % len(self.ids)
        return self.selected_row()

    @property
    def invitation_url(self) -> str:
        """Official GitHub page for invitations and collaborator changes."""
        return f'https://github.com/{self.repo}/settings/access'

    # -- readiness ------------------------------------------------------
    def refresh_providers(self) -> tuple[ProviderAccount, ...]:
        for pid in self.ids:
            try:
                status = self._probe(pid)
                install = _map_install(status)
                self._install[pid] = install
                detail = redact(getattr(status, 'reason', '') or 'Local probe complete.')
                if install is InstallState.INSTALLED:
                    try:
                        evidence = self._auth(pid)
                        self._account[pid] = _map_account(getattr(evidence, 'state', None))
                        extra = redact(getattr(evidence, 'reason', ''))
                        if extra:
                            detail = (detail + ' ' + extra).strip()
                    except Exception:
                        self._account[pid] = AccountState.UNKNOWN
                        detail = (detail + ' Authentication status unavailable.').strip()
                else:
                    self._account[pid] = (AccountState.UNAVAILABLE
                                          if install is InstallState.NOT_INSTALLED
                                          else AccountState.UNKNOWN)
            except Exception:
                self._install[pid] = InstallState.UNKNOWN
                self._account[pid] = AccountState.UNKNOWN
                self._detail[pid] = 'Provider probe failed; no command output retained.'
                continue
            self._detail[pid] = detail
        return self.rows()

    def test_connection(self, provider_id: str) -> ConnectionReport:
        """Bounded local readiness test; never submits a model prompt."""
        try:
            spec = provider_runtime.get_provider(provider_id)
        except Exception:
            return ConnectionReport('', False, 'UNKNOWN PROVIDER',
                                    'Unknown provider ID; no command was run.')
        try:
            status = self._probe(spec.id)
            install = _map_install(status)
            evidence = self._auth(spec.id) if install is InstallState.INSTALLED else None
            account = _map_account(getattr(evidence, 'state', None)) if evidence else (
                AccountState.UNAVAILABLE if install is InstallState.NOT_INSTALLED
                else AccountState.UNKNOWN)
            self._install[spec.id], self._account[spec.id] = install, account
            detail = ' '.join(filter(None, (redact(getattr(status, 'reason', '')),
                                            redact(getattr(evidence, 'reason', '')) if evidence else '')))
            detail = detail or 'No diagnostic was returned.'
            self._detail[spec.id] = detail
            ok = install is InstallState.INSTALLED and account is AccountState.AUTHENTICATED
            summary = f'{spec.display_name}: {install.value} / {account.value}'
            report = ConnectionReport(spec.id, ok, summary, detail)
            self.last_test = report
            return report
        except Exception:
            report = ConnectionReport(spec.id, False, f'{spec.display_name}: TEST FAILED',
                                      'Local readiness test failed; no output retained.')
            self.last_test = report
            return report

    # -- local preference ----------------------------------------------
    def set_enabled(self, provider_id: str, enabled: bool) -> bool:
        if provider_id not in self._enabled or type(enabled) is not bool:
            return False
        self._enabled[provider_id] = enabled
        self._save_enabled()
        return True

    def toggle_enabled(self, provider_id: str) -> bool:
        if provider_id not in self._enabled:
            return False
        return self.set_enabled(provider_id, not self._enabled[provider_id])

    def _load_enabled(self) -> None:
        if self.state_path is None:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            stored = data.get('enabled') if isinstance(data, dict) else None
            if isinstance(stored, dict):
                for pid in self.ids:
                    if type(stored.get(pid)) is bool:
                        self._enabled[pid] = stored[pid]
        except (OSError, ValueError, TypeError):
            pass

    def _save_enabled(self) -> None:
        if self.state_path is None:
            return
        descriptor = None
        temporary = None
        try:
            parent = self.state_path.parent
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='accounts-', dir=parent)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                descriptor = None
                json.dump({'schema_version': 1, 'enabled': self._enabled}, handle,
                          sort_keys=True, separators=(',', ':'))
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            temporary = None
        except (OSError, ValueError, TypeError):
            pass
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    # -- GitHub identity / members -------------------------------------
    def _probe_github_identity(self) -> GitHubIdentity:
        path = provider_runtime.shutil.which('gh')
        if not path:
            return GitHubIdentity(AccountState.UNAVAILABLE, detail=(
                'GitHub CLI is not installed. Install gh, then run gh auth login locally.'))
        if provider_runtime.ntpath.splitext(path)[1].lower() in ('.bat', '.cmd'):
            return GitHubIdentity(AccountState.UNAVAILABLE,
                                  detail='GitHub CLI must be a native executable.')
        code, output = _bounded_gh([path, 'auth', 'status', '--hostname', 'github.com'])
        if code is None:
            return GitHubIdentity(AccountState.UNKNOWN,
                                  detail='GitHub status probe failed; no output retained.')
        clean = redact(output, 2000)
        account_match = _ACCOUNT_RE.search(clean)
        account = account_match.group(1) if account_match else ''
        if not _LOGIN_RE.fullmatch(account):
            account = ''
        if code != 0:
            detail = ('GitHub login is not confirmed. Run gh auth login locally.'
                      if 'not logged' in clean.lower()
                      else 'GitHub status probe was unsuccessful; identity is UNKNOWN.')
            return GitHubIdentity(AccountState.UNAUTHENTICATED if 'not logged' in clean.lower()
                                  else AccountState.UNKNOWN,
                                  account=account, detail=detail)
        scopes: tuple[str, ...] = ()
        scope_match = _SCOPE_RE.search(clean)
        if scope_match:
            raw = re.sub(r"['\"]", '', scope_match.group(1))
            values = [item.strip() for item in re.split(r'[,\s]+', raw) if item.strip()]
            scopes = tuple(value for value in values
                           if re.fullmatch(r'[A-Za-z0-9:_-]{1,40}', value))[:16]
        return GitHubIdentity(AccountState.AUTHENTICATED, account=account, scopes=scopes,
                              detail='gh auth status succeeded; provider identities are separate.')

    def refresh_identity(self) -> GitHubIdentity:
        try:
            identity = self._identity_probe()
        except Exception:
            identity = GitHubIdentity(AccountState.UNKNOWN,
                                      detail='GitHub identity probe failed; no output retained.')
        role = self._resolve_role()
        self.identity = GitHubIdentity(identity.state, identity.account,
                                       tuple(identity.scopes), redact(identity.detail), role)
        return self.identity

    def _probe_repo_role(self) -> str:
        path = provider_runtime.shutil.which('gh')
        if not path:
            return 'UNKNOWN'
        code, output = _bounded_gh([path, 'repo', 'view', self.repo,
                                    '--json', 'viewerPermission'])
        if code != 0:
            return 'UNKNOWN'
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            return 'UNKNOWN'
        role = data.get('viewerPermission') if isinstance(data, dict) else None
        return role if role in ROLE_VALUES else 'UNKNOWN'

    def _resolve_role(self) -> str:
        try:
            role = self._role_resolver()
        except Exception:
            return 'UNKNOWN'
        return role if role in ROLE_VALUES else 'UNKNOWN'

    def authorize_owner(self) -> tuple[bool, str]:
        """Re-derive the role from the server at every privileged boundary."""
        role = self._resolve_role()
        self.identity = GitHubIdentity(self.identity.state, self.identity.account,
                                       self.identity.scopes, self.identity.detail, role)
        return role in OWNER_ROLES, role

    def owner_member_url(self) -> str | None:
        allowed, _ = self.authorize_owner()
        return self.invitation_url if allowed else None

    def refresh_members(self) -> MemberView:
        """Read-only collaborator list; no invitation, removal or merge."""
        path = provider_runtime.shutil.which('gh')
        if not path:
            self.members = MemberView('UNAVAILABLE', detail='GitHub CLI is not installed.')
            return self.members
        code, output = _bounded_gh([path, 'api', '--hostname', 'github.com',
                                    f'repos/{self.repo}/collaborators?per_page=50'])
        if code != 0:
            self.members = MemberView('UNAVAILABLE',
                                      detail='Collaborator list unavailable; nothing was changed.')
            return self.members
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            data = None
        if not isinstance(data, list):
            self.members = MemberView('UNAVAILABLE', detail='Malformed collaborator list; nothing was changed.')
            return self.members
        members = []
        for item in data[:50]:
            login = item.get('login') if isinstance(item, dict) else None
            if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login):
                continue
            permissions = item.get('permissions') if isinstance(item, dict) else None
            role = item.get('role_name') if isinstance(item, dict) else None
            if not isinstance(role, str):
                role = ('ADMIN' if isinstance(permissions, dict) and permissions.get('admin')
                        else 'WRITE' if isinstance(permissions, dict) and permissions.get('push')
                        else 'READ')
            members.append(Member(login, role.upper()[:12]))
        self.members = MemberView('READ ONLY', tuple(members),
                                  'Loaded read-only; use the official GitHub page for changes.')
        return self.members

    # -- login guidance -------------------------------------------------
    def login_guidance(self, provider_id: str) -> str:
        try:
            spec = provider_runtime.get_provider(provider_id)
        except Exception:
            return 'Unknown provider; no guidance is available.'
        return f'{spec.login_guidance} Official docs: {spec.docs_url}'

    def vendor_login_url(self, provider_id: str) -> str | None:
        """Official vendor page, never a command line or a credential prompt."""
        try:
            spec = provider_runtime.get_provider(provider_id)
        except Exception:
            return None
        launched = None
        try:
            launched = self._login_launcher(spec.id)
            if isinstance(launched, str) and safe_url(launched):
                return launched
        except Exception:
            pass
        return spec.docs_url if safe_url(spec.docs_url) else None

    def open_url(self, url: object) -> bool:
        if not safe_url(url) or self._opener is None:
            return False
        try:
            return bool(self._opener(url))
        except Exception:
            return False
