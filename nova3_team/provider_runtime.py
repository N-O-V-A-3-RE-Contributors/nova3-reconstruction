"""Optional solo-worker CLIs. Probes never submit prompts or initiate login.

Capability evidence comes from the installed vendor's help, not a guessed
minimum version. Unknown permission semantics fail closed. No provider is
installed, authenticated, or contacted by importing this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import ntpath
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from types import MappingProxyType
from typing import Mapping


try:  # The Hermes backend is optional; its absence must not disable other providers.
    from . import hermes_runtime
except ImportError:  # pragma: no cover - exercised by the standalone-install test
    hermes_runtime = None

PROBE_TIMEOUT = 3.0
MAX_PROBE_BYTES = 64 * 1024


class RuntimeState(str, Enum):
    AVAILABLE = 'available'
    MISSING = 'missing'
    INCOMPATIBLE = 'incompatible'
    PROBE_FAILED = 'probe_failed'


class AuthState(str, Enum):
    AUTHENTICATED = 'authenticated'
    UNAUTHENTICATED = 'unauthenticated'
    UNKNOWN = 'unknown'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    executable: str
    docs_url: str
    install_url: str
    login_guidance: str
    help_args: tuple[str, ...]
    required_help: tuple[str, ...]
    permission_profile: str = ''
    permission_docs_url: str = ''
    platforms: tuple[str, ...] = ('Linux', 'macOS', 'Windows (native executable)')
    # Files the adapter must materialise in a private directory before launch,
    # as (filename, content). Used only where the vendor's bounded permission
    # profile is expressed as an agent/tool allowlist file, not a CLI flag.
    aux_files: tuple[tuple[str, str], ...] = ()
    # Extra environment entries for the child process only. Never secrets.
    environment: tuple[tuple[str, str], ...] = ()
    # Empty for adapters with a documented bounded headless profile. Non-empty
    # only when official vendor documentation shows no such profile exists;
    # the text cites the documented behaviour, never a guess.
    unsupported_reason: str = ''
    # False for CLIs that do not advertise a semantic version. Their readiness
    # rests entirely on documented capability tokens from `help`, never a
    # guessed minimum version.
    version_required: bool = True


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    state: RuntimeState
    executable_path: str | None = None
    version: str | None = None
    reason: str = ''
    capabilities: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.state == RuntimeState.AVAILABLE


@dataclass(frozen=True)
class AuthEvidence:
    state: AuthState
    reason: str


class ProviderUnavailable(RuntimeError):
    """Fixed diagnostic only: never include argv, prompts or vendor stderr."""


# Official vendor documentation URL -> the exact documented flag or mode used.
# `docs_url` is the CLI/headless reference; `permission_docs_url` is the
# permission/approval reference where the vendor publishes a separate page.
# Sources verified 2026-10-30; see docs/progress/PROVIDER_RUNTIME.md.
_SPECS = (
    ProviderSpec(
        id='codex', display_name='ChatGPT (Codex CLI)', executable='codex',
        docs_url='https://developers.openai.com/codex/cli/reference/',
        install_url='https://developers.openai.com/codex/cli/',
        login_guidance='Run codex login locally using the official vendor flow.',
        help_args=('exec', '--help'),
        required_help=('--ephemeral', '--sandbox'),
        permission_profile='codex exec --ephemeral --sandbox workspace-write (documented workspace sandbox)'),
    ProviderSpec(
        id='claude', display_name='Claude Code', executable='claude',
        docs_url='https://code.claude.com/docs/en/cli-reference',
        install_url='https://code.claude.com/docs/en/setup',
        login_guidance='Open claude locally and follow its official login flow.',
        help_args=('--help',),
        required_help=('--print', '--max-turns', '--allowedTools', '--output-format'),
        permission_profile='claude -p --max-turns 12 --allowedTools Read,Edit,Write,Glob,Grep'),
    ProviderSpec(
        id='gemini', display_name='Gemini CLI', executable='gemini',
        docs_url='https://geminicli.com/docs/cli/headless/',
        install_url='https://geminicli.com/docs/get-started/installation/',
        login_guidance='Open gemini locally and follow its official authentication flow.',
        help_args=('--help',),
        required_help=('--prompt', '--approval-mode', 'auto_edit', '--output-format'),
        permission_profile='gemini -p --approval-mode auto_edit (documented auto-edit approval mode)'),
    ProviderSpec(
        id='kimi', display_name='Kimi Code CLI', executable='kimi',
        docs_url='https://www.kimi-cli.com/en/customization/print-mode.html',
        install_url='https://github.com/MoonshotAI/kimi-cli#installation',
        login_guidance='Run kimi login locally using the official vendor flow.',
        help_args=('--help',),
        # --print implicitly enables --afk (all tool calls auto-approved), so the
        # bound must come from the agent's tool allowlist, not an approval flag.
        required_help=('--print', '--agent-file', '--prompt', '--output-format'),
        permission_profile='kimi --print --agent-file <ReadFile+WriteFile only> '
                           '--output-format text -p PROMPT (no Shell/SearchWeb/FetchURL)',
        permission_docs_url='https://www.kimi-cli.com/en/customization/agents.html',
        aux_files=(('kimi-agent.yaml',
                    'version: 1\n'
                    'agent:\n'
                    '  extend: default\n'
                    '  tools:\n'
                    '    - "kimi_cli.tools.file:ReadFile"\n'
                    '    - "kimi_cli.tools.file:WriteFile"\n'),),
    ),
    ProviderSpec(
        id='qwen', display_name='Qwen Code', executable='qwen',
        docs_url='https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/',
        install_url='https://github.com/QwenLM/qwen-code#installation',
        login_guidance='Open qwen locally and follow its official authentication flow.',
        help_args=('--help',),
        required_help=('--prompt', '--approval-mode', 'auto-edit', '--output-format'),
        permission_profile='qwen -p --approval-mode auto-edit --output-format text '
                           '(edits auto-approved; other tools still require approval)',
        permission_docs_url='https://qwenlm.github.io/qwen-code-docs/en/users/features/approval-mode/',
    ),
    ProviderSpec(
        id='opencode', display_name='OpenCode', executable='opencode',
        docs_url='https://opencode.ai/docs/cli/#run',
        install_url='https://opencode.ai/docs/',
        login_guidance='Run opencode auth login locally using the official vendor flow.',
        help_args=('run', '--help'),
        required_help=('--auto', '--format', '--agent'),
        permission_profile='opencode run --auto --agent nova3-bounded PROMPT with '
                           'OPENCODE_CONFIG_CONTENT deny-by-default rules',
        permission_docs_url='https://opencode.ai/docs/permissions/',
        # Inline runtime config (documented precedence source) defines a custom
        # primary agent whose permission table is deny-by-default with only
        # read/edit/glob/grep allowed. --auto approves nothing the rules deny;
        # it only prevents a non-interactive "ask" hang.
        environment=(('OPENCODE_CONFIG_CONTENT', json.dumps({
            'permission': {'*': 'deny', 'read': 'allow', 'edit': 'allow',
                           'glob': 'allow', 'grep': 'allow'},
            'agent': {'nova3-bounded': {
                'description': 'N.O.V.A. 3 bounded worker: edits only, no shell or network.',
                'mode': 'primary',
                'permission': {'*': 'deny', 'read': 'allow', 'edit': 'allow',
                               'glob': 'allow', 'grep': 'allow'}}},
        }, sort_keys=True, separators=(',', ':'))),),
    ),
    ProviderSpec(
        id='copilot', display_name='GitHub Copilot CLI', executable='copilot',
        docs_url='https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference',
        install_url='https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli',
        login_guidance='Open copilot locally and use its official /login flow; gh login is separate.',
        help_args=('--help',),
        required_help=('--prompt', '--allow-tool', '--deny-tool'),
        permission_profile='copilot -p --allow-tool write --deny-tool shell '
                           '(documented tool kinds write/shell; shell denied)',
    ),
)

# Hermes is registered only when its adapter module is present. A bundle that
# ships provider_runtime.py without hermes_runtime.py must still expose every
# other provider instead of failing the whole registry import.
if hermes_runtime is not None:
    _SPECS += (
        ProviderSpec(
            id='hermes', display_name='Hermes Agent', executable='hermes',
            docs_url=hermes_runtime.DOCS_URL,
            install_url=hermes_runtime.INSTALL_URL,
            login_guidance='Authenticate with the vendor-native Hermes flow locally; '
                           'no API key belongs in this repository.',
            # Hermes is driven through its file-based headless adapter, not an
            # inline -p prompt. Registered here for discovery/readiness; argv is
            # built only by nova3_team.hermes_runtime.
            help_args=('chat', '--help'),
            required_help=hermes_runtime.REQUIRED_CAPABILITIES,
            permission_profile='hermes chat --query-file <private prompt> --oneshot --quiet '
                               '--format stream-json --provider <pinned> --model <pinned> '
                               '--toolsets file,terminal,code_execution,todo --max-turns N '
                               '--run-budget S (no --yolo/--accept-hooks/--api-key)',
            version_required=False,
        ),
    )

# Stable, ordered, read-only API for the subsequent account manager.
PROVIDERS: Mapping[str, ProviderSpec] = MappingProxyType({s.id: s for s in _SPECS})


def provider_ids() -> tuple[str, ...]:
    return tuple(PROVIDERS)


# Providers that existed before the capability registry. Their argv is frozen
# by issue #127, so a probe result is advisory for them: a help text that omits
# a documented flag must warn, never block a previously working invocation.
LEGACY_PROVIDERS = ('codex', 'claude', 'gemini')


def is_legacy(provider_id: str) -> bool:
    return provider_id in LEGACY_PROVIDERS


def get_provider(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise ProviderUnavailable('Unknown provider; choose a registered provider ID.') from None


def executable_stem(path: str) -> str:
    return ntpath.basename(path).lower().removesuffix('.exe')


def prepare_aux_files(provider_id: str, directory: str | os.PathLike[str]) -> dict[str, str]:
    """Materialise a provider's documented allowlist file in a private directory.

    Only fixed filenames declared by the registry are written, mode 0600 inside
    a 0700 directory. Returns filename -> absolute path. Providers without aux
    files return an empty mapping and no directory is created.
    """
    spec = get_provider(provider_id)
    if not spec.aux_files:
        return {}
    folder = os.path.abspath(os.fspath(directory))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.name == 'posix':
        os.chmod(folder, 0o700)
    paths: dict[str, str] = {}
    for name, content in spec.aux_files:
        if os.path.basename(name) != name or name in ('', '.', '..'):
            raise ProviderUnavailable('Provider profile filename is invalid.')
        path = os.path.join(folder, name)
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(content)
        finally:
            if os.name == 'posix':
                os.chmod(path, 0o600)
        paths[name] = path
    return paths


def build_environment(provider_id: str) -> dict[str, str]:
    """Child-process environment entries declared by the registry.

    Contains no credentials: only vendor-documented non-secret configuration.
    """
    spec = get_provider(provider_id)
    return {name: value for name, value in spec.environment}


def redact_diagnostic(message: object) -> str:
    """Best-effort defence for worker diagnostics, never an auth/key collector.

    Known secret values are used transiently for replacement only. Arbitrary
    vendor output must still stay in private logs, never public telemetry.
    """
    text = str(message)
    for name, value in os.environ.items():
        if value and re.search(r'(?:TOKEN|SECRET|PASSWORD|API_?KEY|ACCESS_KEY)', name, re.I):
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(?i)\b(bearer\s+)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)',
                  '[REDACTED]', text)
    return text


@dataclass(frozen=True)
class _Probe:
    returncode: int | None
    output: str = ''
    failure: str = ''


def _terminate_probe(proc: subprocess.Popen) -> None:
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _probe(argv: list[str]) -> _Probe:
    """Closed stdin, no shell, finite wall time/output, no raw error propagation.

    A file avoids communicate() accumulating unbounded help output in RAM. Its
    size is polled and capped; the process group is killed on deadline/overflow.
    No command supplied here includes a model prompt or login operation.
    """
    proc = None
    try:
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.DEVNULL, shell=False,
                                    start_new_session=(os.name == 'posix'))
            deadline = time.monotonic() + PROBE_TIMEOUT
            while proc.poll() is None:
                if output.tell() > MAX_PROBE_BYTES:
                    return _Probe(None, failure='Probe output exceeded the local limit.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return _Probe(None, failure='Local CLI probe timed out.')
                try:
                    proc.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            output.seek(0)
            data = output.read(MAX_PROBE_BYTES + 1)
            if len(data) > MAX_PROBE_BYTES:
                return _Probe(None, failure='Probe output exceeded the local limit.')
            return _Probe(proc.returncode, data.decode('utf-8', errors='replace'))
    except (OSError, ValueError, subprocess.SubprocessError):
        return _Probe(None, failure='Local CLI probe could not execute.')
    finally:
        if proc is not None:
            _terminate_probe(proc)


def probe_provider(provider_id: str) -> ProviderStatus:
    spec = get_provider(provider_id)
    path = shutil.which(spec.executable)
    if not path:
        return ProviderStatus(spec.id, RuntimeState.MISSING,
                              reason=f'{spec.display_name} is optional and absent. '
                                     f'Install locally: {spec.install_url} {spec.login_guidance}')
    # Windows may implicitly execute .cmd/.bat through cmd.exe even with
    # shell=False. Never send a prompt through that interpolation boundary.
    if ntpath.splitext(path)[1].lower() in ('.cmd', '.bat'):
        return ProviderStatus(spec.id, RuntimeState.INCOMPATIBLE, path,
                              reason='Batch wrappers are unsupported; use a native executable '
                                     'or a vendor-supported non-shell entrypoint.')
    if not (os.path.isabs(path) or ntpath.isabs(path)):
        path = os.path.abspath(path)
    version = None
    number = None
    if spec.version_required:
        version = _probe([path, '--version'])
        if version.failure or version.returncode:
            return ProviderStatus(spec.id, RuntimeState.PROBE_FAILED, path,
                                  reason=version.failure or 'Local --version probe failed.')
        match = re.search(r'(?<![\w.])(\d{1,4}\.\d{1,4}\.\d{1,4})(?![\w.])', version.output)
        if not match:
            return ProviderStatus(spec.id, RuntimeState.INCOMPATIBLE, path,
                                  reason='Unrecognized CLI version; no worker session was started.')
        number = match.group(1)
    if spec.unsupported_reason:
        # Official docs show no bounded noninteractive edit profile for this
        # vendor, so neither probe help nor a session may proceed.
        return ProviderStatus(spec.id, RuntimeState.INCOMPATIBLE, path, number,
                              spec.unsupported_reason)
    help_result = _probe([path, *spec.help_args])
    if help_result.failure or help_result.returncode:
        return ProviderStatus(spec.id, RuntimeState.PROBE_FAILED, path, number,
                              help_result.failure or 'Local help probe failed.')
    # Match complete tokens: --allow-toolbox is not --allow-tool, and
    # auto_edit (Gemini) must not accidentally authorize Qwen's auto-edit.
    tokens = set(re.findall(r'--[A-Za-z][A-Za-z-]*|[A-Za-z][A-Za-z_-]*', help_result.output))
    if not set(spec.required_help) <= tokens:
        return ProviderStatus(spec.id, RuntimeState.INCOMPATIBLE, path, number,
                              'Installed CLI help lacks the required headless/edit approval '
                              f'capabilities. Review {spec.docs_url}; no fallback was launched.')
    return ProviderStatus(spec.id, RuntimeState.AVAILABLE, path, number,
                          'Installed help advertises the required flags; no model call tested.',
                          tuple(sorted(tokens & (set(spec.required_help) | {'--json'}))))


def readiness(provider_id: str, *, status: ProviderStatus | None = None,
              model: str | None = None, provider: str | None = None) -> dict:
    """Stable readiness record for one provider (Control Center, worker status).

    Hermes reuses the same capability probe and adds its role/model surface;
    the record is descriptive only. Execution still re-validates readiness, so
    a stale caller cannot start a session from a cached \"available\" string.
    """
    spec = get_provider(provider_id)
    status = status if status is not None else probe_provider(provider_id)
    record = {
        'provider_id': spec.id,
        'display_name': spec.display_name,
        'state': status.state.value,
        'version': status.version,
        'executable': status.executable_path,
        'reason': status.reason,
        'docs_url': spec.docs_url,
        'install_url': spec.install_url,
        'login_guidance': spec.login_guidance,
        'permission_profile': spec.permission_profile,
        'roles': ['worker'],
        'capabilities': list(status.capabilities),
    }
    if provider_id == 'hermes':
        if hermes_runtime is None:
            raise ProviderUnavailable('Hermes adapter is not installed')
        record.update(hermes_runtime.readiness(
            hermes_runtime.status_from_provider(status),
            model=model or hermes_runtime.DEFAULT_MODEL,
            provider=provider or hermes_runtime.DEFAULT_PROVIDER))
        record['executable'] = status.executable_path
        record['capabilities'] = list(status.capabilities)
    return record


def build_command(provider_id: str, prompt: str, *, codex_json: bool = False,
                  status: ProviderStatus | None = None,
                  aux_dir: str | os.PathLike[str] | None = None) -> list[str]:
    """Build argv without shell interpolation; new adapters require probe evidence.

    Legacy builders retain their exact argv. Worker startup probes all providers
    before claiming work. These CLIs have their own permission boundaries; the
    worker independently validates issue scope and binary/source guards after it.
    Providers with a documented agent-file allowlist require `aux_dir`, prepared
    by `prepare_aux_files`, so the bound is a real file the CLI reads.
    """
    spec = get_provider(provider_id)
    if not isinstance(prompt, str) or not prompt.strip() or '\0' in prompt:
        raise ProviderUnavailable('A nonempty text prompt without NUL bytes is required.')
    # Some CLI parsers treat a dash-prefixed -p value as a new option. The
    # worker's generated session prompt always starts with task text. Refuse
    # ambiguous direct API input while preserving legacy argv exactly.
    if prompt.lstrip().startswith('-'):
        raise ProviderUnavailable('Prompt must begin with task text, not a CLI option.')
    if spec.unsupported_reason:
        raise ProviderUnavailable(
            'No bounded noninteractive edit profile is documented for this provider; '
            'see the provider runtime notes.')
    legacy = provider_id in LEGACY_PROVIDERS
    if status is None and not legacy:
        status = probe_provider(provider_id)
    if status is not None and status.provider_id != spec.id:
        raise ProviderUnavailable('Provider status does not match the requested provider.')
    if status is not None and not status.available and not legacy:
        # New adapters need positive capability evidence before a session.
        raise ProviderUnavailable('Provider is unavailable or incompatible; run the local provider probe.')
    if not legacy and status is not None:
        # New adapters must carry the probe's help-token evidence. A status
        # object alone is not proof that the documented flags exist.
        if not set(spec.required_help) <= set(status.capabilities):
            raise ProviderUnavailable(
                'Provider capability evidence is incomplete; run the local provider probe.')
    executable = status.executable_path if status else spec.executable
    if not executable:
        raise ProviderUnavailable('Provider executable is unavailable.')
    profiles: dict[str, str] = {}
    if spec.aux_files:
        if aux_dir is None:
            raise ProviderUnavailable(
                'Provider agent profile is not prepared; no session was launched.')
        for name, _ in spec.aux_files:
            path = os.path.join(os.fspath(aux_dir), name)
            if not os.path.isfile(path):
                raise ProviderUnavailable(
                    'Provider agent profile is not prepared; no session was launched.')
            profiles[name] = path
    if provider_id == 'codex':
        return [executable, 'exec', '--ephemeral', '--sandbox', 'workspace-write',
                *(['--json'] if codex_json else []), prompt]
    if provider_id == 'claude':
        return [executable, '-p', prompt, '--max-turns', '12', '--allowedTools',
                'Read', 'Edit', 'Write', 'Glob', 'Grep', '--output-format', 'text']
    if provider_id == 'gemini':
        return [executable, '-p', prompt, '--approval-mode', 'auto_edit', '--output-format', 'text']
    if provider_id == 'kimi':
        # Print mode implies --afk; the bound is the agent file's tool list,
        # which contains only ReadFile and WriteFile (no Shell/web).
        return [executable, '--print', '--agent-file', profiles['kimi-agent.yaml'],
                '--output-format', 'text', '-p', prompt]
    if provider_id == 'qwen':
        return [executable, '-p', prompt, '--approval-mode', 'auto-edit', '--output-format', 'text']
    if provider_id == 'opencode':
        # Deny-by-default rules arrive via OPENCODE_CONFIG_CONTENT; --auto only
        # removes interactive "ask" prompts, it never overrides a deny rule.
        return [executable, 'run', '--auto', '--agent', 'nova3-bounded', prompt]
    if provider_id == 'copilot':
        # Explicit deny overrides saved shell approvals. No --allow-all-tools,
        # --allow-all-paths, --allow-all-urls or unattended login is requested.
        return [executable, '-p', prompt, '--allow-tool', 'write', '--deny-tool', 'shell']
    if provider_id == 'hermes':
        # Hermes must receive its query as a private file (--query-file), which
        # this inline-prompt builder cannot provide. Failure stays explicit so
        # no caller can silently fall back to interpolating task text into argv.
        raise ProviderUnavailable(
            'Hermes uses the file-based worker adapter; an inline prompt is not permitted.')
    raise ProviderUnavailable('No verified writable headless command is available.')


def check_auth(provider_id: str) -> AuthEvidence:
    """Status only. Absence of API keys is not evidence against vendor OAuth.

    Only Codex's documented login-status command is wired here. Other vendors
    return UNKNOWN rather than inventing an auth subcommand or reading private
    credential files. UNKNOWN never initiates login or a paid readiness prompt.
    """
    spec = get_provider(provider_id)
    path = shutil.which(spec.executable)
    if not path:
        return AuthEvidence(AuthState.UNAVAILABLE, 'Optional provider CLI is absent.')
    if provider_id != 'codex' or ntpath.splitext(path)[1].lower() in ('.bat', '.cmd'):
        return AuthEvidence(AuthState.UNKNOWN, 'No verified offline status command is configured. '
                            + spec.login_guidance)
    result = _probe([path, 'login', 'status'])
    # Codex emits status on stderr on some versions. A successful status exit
    # is sufficient; never retain output which may identify a credential.
    if result.failure:
        return AuthEvidence(AuthState.UNKNOWN, 'Authentication status probe failed; use codex login locally.')
    if result.returncode == 0:
        return AuthEvidence(AuthState.AUTHENTICATED, 'Vendor login status exited successfully; model access untested.')
    return AuthEvidence(AuthState.UNKNOWN, 'Vendor status was unsuccessful; authentication is unconfirmed. '
                        'Use codex login locally.')


def check_github_identity() -> AuthEvidence:
    """Separate repository identity; gh login never authenticates an AI provider."""
    path = shutil.which('gh')
    if not path:
        return AuthEvidence(AuthState.UNAVAILABLE, 'GitHub CLI is required separately. Install gh and run gh auth login locally.')
    if ntpath.splitext(path)[1].lower() in ('.bat', '.cmd'):
        return AuthEvidence(AuthState.UNAVAILABLE, 'GitHub CLI requires a non-shell executable.')
    result = _probe([path, 'auth', 'status', '--hostname', 'github.com'])
    if result.failure:
        return AuthEvidence(AuthState.UNKNOWN, 'GitHub identity status probe failed.')
    if result.returncode == 0:
        return AuthEvidence(AuthState.AUTHENTICATED, 'GitHub CLI status succeeded; provider identity is separate.')
    return AuthEvidence(AuthState.UNKNOWN, 'GitHub identity is unconfirmed. Run gh auth login locally.')
