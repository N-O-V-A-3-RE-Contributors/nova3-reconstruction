"""Optional solo-worker CLIs. Probes never submit prompts or initiate login.

Capability evidence comes from the installed vendor's help, not a guessed
minimum version. Unknown permission semantics fail closed. No provider is
installed, authenticated, or contacted by importing this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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
    # Empty for adapters with a documented bounded headless profile. Non-empty
    # only when official vendor documentation shows no such profile exists;
    # the text cites the documented behaviour, never a guess.
    unsupported_reason: str = ''


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
        docs_url='https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html',
        install_url='https://github.com/MoonshotAI/kimi-cli#installation',
        login_guidance='Use the official Kimi Code CLI interactive login flow locally.',
        help_args=('--help',), required_help=(),
        unsupported_reason=(
            'UNSUPPORTED: official print-mode docs state that --print implicitly '
            'enables --afk, and that in AFK/YOLO mode all tool calls including '
            'shell commands are auto-approved. kimi-cli exposes no per-tool '
            'permission profile, so no bounded headless edit session can be '
            'built without a blanket approval flag. Docs: '
            'https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html '
            'and https://moonshotai.github.io/kimi-cli/en/reference/kimi-command.html '
            '#approval-control. No command is launched and no flag is guessed.'),
    ),
    ProviderSpec(
        id='qwen', display_name='Qwen Code', executable='qwen',
        docs_url='https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/',
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
        docs_url='https://opencode.ai/docs/cli/',
        install_url='https://opencode.ai/docs/',
        login_guidance='Run opencode auth login locally using the official vendor flow.',
        help_args=('run', '--help'), required_help=(),
        unsupported_reason=(
            'UNSUPPORTED: `opencode run` is documented as non-interactive, but the '
            'bounded edit profile is expressed only as opencode.json `permission` '
            'rules; the CLI argument `--auto` auto-approves every request that is '
            'not explicitly denied, which is a blanket approval this worker '
            'prohibits. No per-run permission flag exists and this adapter does '
            'not write vendor configuration files, so no verified bounded argv can '
            'be produced. Docs: https://opencode.ai/docs/cli/ and '
            'https://opencode.ai/docs/permissions/. No fallback is launched.'),
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

# Stable, ordered, read-only API for the subsequent account manager.
PROVIDERS: Mapping[str, ProviderSpec] = MappingProxyType({s.id: s for s in _SPECS})


def provider_ids() -> tuple[str, ...]:
    return tuple(PROVIDERS)


def get_provider(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise ProviderUnavailable('Unknown provider; choose a registered provider ID.') from None


def executable_stem(path: str) -> str:
    return ntpath.basename(path).lower().removesuffix('.exe')


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


def build_command(provider_id: str, prompt: str, *, codex_json: bool = False,
                  status: ProviderStatus | None = None) -> list[str]:
    """Build argv without shell interpolation; new adapters require probe evidence.

    Legacy builders retain their exact argv. Worker startup probes all providers
    before claiming work. These CLIs have their own permission boundaries; the
    worker independently validates issue scope and binary/source guards after it.
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
    if status is None and provider_id not in ('codex', 'claude', 'gemini'):
        status = probe_provider(provider_id)
    if status is not None and (status.provider_id != spec.id or not status.available):
        # Do not interpolate externally supplied status.reason into exceptions.
        raise ProviderUnavailable('Provider is unavailable or incompatible; run the local provider probe.')
    executable = status.executable_path if status else spec.executable
    if not executable:
        raise ProviderUnavailable('Provider executable is unavailable.')
    if provider_id not in ('codex', 'claude', 'gemini') and status is not None:
        # New adapters must carry the probe's help-token evidence. A status
        # object alone is not proof that the documented flags exist.
        if not set(spec.required_help) <= set(status.capabilities):
            raise ProviderUnavailable(
                'Provider capability evidence is incomplete; run the local provider probe.')
    if provider_id == 'codex':
        return [executable, 'exec', '--ephemeral', '--sandbox', 'workspace-write',
                *(['--json'] if codex_json else []), prompt]
    if provider_id == 'claude':
        return [executable, '-p', prompt, '--max-turns', '12', '--allowedTools',
                'Read', 'Edit', 'Write', 'Glob', 'Grep', '--output-format', 'text']
    if provider_id == 'gemini':
        return [executable, '-p', prompt, '--approval-mode', 'auto_edit', '--output-format', 'text']
    if provider_id == 'qwen':
        return [executable, '-p', prompt, '--approval-mode', 'auto-edit', '--output-format', 'text']
    if provider_id == 'copilot':
        # Explicit deny overrides saved shell approvals. No --allow-all-tools,
        # --allow-all-paths, --allow-all-urls or unattended login is requested.
        return [executable, '-p', prompt, '--allow-tool', 'write', '--deny-tool', 'shell']
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
