#!/usr/bin/env python3
"""NOVA3 contributor launcher: standard library only; never stores API tokens."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = 'crjase/nova3-reconstruction'
PROVIDERS = {'1': 'codex', '2': 'claude', '3': 'gemini'}
PACKAGES = {'codex': '@openai/codex', 'claude': '@anthropic-ai/claude-code', 'gemini': '@google/gemini-cli'}


def config_file() -> Path:
    if os.name == 'nt':
        base = Path(os.environ.get('APPDATA') or (Path.home() / 'AppData' / 'Roaming'))
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config'))
    return base / 'nova3-team' / 'launcher.json'


def requirements_hint() -> str:
    if os.name == 'nt':
        return ('Windows PowerShell (install using winget, then reopen this launcher):\n'
                '  winget install --id Git.Git -e\n'
                '  winget install --id GitHub.cli -e\n'
                '  winget install --id Python.Python.3.12 -e\n'
                '  winget install --id Kitware.CMake -e\n'
                '  winget install --id Ninja-build.Ninja -e\n'
                '  winget install --id OpenJS.NodeJS.LTS -e\n'
                'For native C++ compilation also install Visual Studio Build Tools with Desktop development with C++.')
    if sys.platform == 'darwin':
        return ('macOS with Homebrew (https://brew.sh):\n'
                '  brew install git gh python cmake ninja node\n'
                '  xcode-select --install')
    return ('Linux:\n'
            '  Arch: sudo pacman -S --needed git github-cli python cmake ninja gcc nodejs npm\n'
            '  Debian/Ubuntu: sudo apt update && sudo apt install -y git gh python3 cmake ninja-build build-essential nodejs npm\n'
            'Other distributions: install equivalent packages.')


def execute(cmd: list[str], *, check: bool = False, quiet: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=check, text=True,
                              stdout=subprocess.DEVNULL if quiet else None,
                              stderr=subprocess.DEVNULL if quiet else None)
    except OSError as exc:
        raise RuntimeError(f'Cannot run {cmd[0]}: {exc}') from exc


def ask(question: str, default: str = '') -> str:
    try:
        answer = input(question).strip()
    except EOFError:
        raise RuntimeError('Interactive terminal required for first-time setup') from None
    return answer or default


def choose_provider(preselected: str | None, *, force_menu: bool = False, config: Path | None = None) -> str:
    if preselected:
        return preselected
    config = config or config_file()
    if config.is_file() and not force_menu:
        try:
            saved = json.loads(config.read_text(encoding='utf-8')).get('provider')
            if saved in PACKAGES:
                print(f'Using saved provider: {saved} (use --choose to change)')
                return saved
        except (OSError, ValueError):
            pass
    print('\nChoose the AI account to use (requires CLI support, not just a browser subscription):')
    for key, name in PROVIDERS.items():
        installed = 'installed' if shutil.which(name) else 'not installed'
        print(f'  {key}) {name:<7} {installed}')
    option = ask('Provider [1/2/3, default 1]: ', '1')
    if option not in PROVIDERS:
        raise RuntimeError('Invalid provider choice')
    return PROVIDERS[option]


def save_provider(provider: str, config: Path | None = None) -> None:
    config = config or config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'provider': provider}, indent=2) + '\n', encoding='utf-8')


def prepare_provider(provider: str, *, already_primed: bool = False) -> None:
    if not shutil.which(provider):
        print(f'\n{provider} CLI is missing.')
        if not shutil.which('npm'):
            raise RuntimeError('Install Node.js/npm first, then restart launcher.\n' + requirements_hint())
        print(f'Official npm package: {PACKAGES[provider]}')
        if ask(f'Install {provider} with npm now? [y/N]: ', 'n').lower() not in ('y', 'yes'):
            raise RuntimeError(f'Install with: npm install -g {PACKAGES[provider]}')
        cp = execute(['npm', 'install', '-g', PACKAGES[provider]])
        if cp.returncode:
            raise RuntimeError('npm installation failed. See output above.')
        if not shutil.which(provider):
            raise RuntimeError('Installation completed, but the CLI is not in PATH. Reopen your terminal.')

    if provider == 'codex':
        result = execute(['codex', 'login', 'status'], quiet=True)
        if result.returncode:
            print('\nCodex sign-in required. Follow the browser sign-in instructions.')
            if execute(['codex', 'login']).returncode:
                raise RuntimeError('Codex login did not succeed.')
    elif not already_primed:
        print(f'\n{provider} requires you to sign in through its own terminal CLI.')
        if ask(f'Already signed in to {provider}? [y/N]: ', 'n').lower() not in ('y', 'yes'):
            print(f'Opening {provider}. Sign in and close the session with /exit or Ctrl+C.')
            try:
                execute([provider])
            except KeyboardInterrupt:
                print('\nReturned from provider sign-in.')
            if ask('Continue to the shared worker? [Y/n]: ', 'y').lower() not in ('y', 'yes'):
                raise RuntimeError('Cancelled before worker started')


def ensure_git_identity() -> None:
    name = subprocess.run(['git', 'config', '--global', 'user.name'], capture_output=True, text=True).stdout.strip()
    email = subprocess.run(['git', 'config', '--global', 'user.email'], capture_output=True, text=True).stdout.strip()
    if name and email:
        return
    print('\nGit commit author identity is not configured.')
    if ask('Set a GitHub private/noreply author identity automatically? [Y/n]: ', 'y').lower() not in ('y', 'yes'):
        raise RuntimeError('Configure git user.name and user.email before submitting PRs.')
    profile = subprocess.run(['gh', 'api', 'user'], capture_output=True, text=True)
    if profile.returncode:
        raise RuntimeError('Cannot retrieve authenticated GitHub identity')
    info = json.loads(profile.stdout)
    login, ident = info.get('login'), info.get('id')
    if not login or not isinstance(ident, int):
        raise RuntimeError('GitHub login/id not available for private author email')
    if not name:
        execute(['git', 'config', '--global', 'user.name', login], check=True)
    if not email:
        execute(['git', 'config', '--global', 'user.email', f'{ident}+{login}@users.noreply.github.com'], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Start a NOVA3 reconstruction worker with your own AI subscription')
    parser.add_argument('--provider', choices=tuple(PACKAGES))
    parser.add_argument('--choose', action='store_true', help='Select a different AI provider')
    parser.add_argument('--once', action='store_true', help='Attempt a single task then exit')
    parser.add_argument('--status', action='store_true', help='Show shared issue status without starting AI')
    args = parser.parse_args(argv)

    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required.')
    os.chdir(Path(__file__).resolve().parent)
    for required in ('git', 'gh'):
        if not shutil.which(required):
            raise RuntimeError(f'{required} is missing.\n' + requirements_hint())
    if not (Path('nova3_team') / 'worker.py').exists():
        raise RuntimeError('Missing nova3_team/worker.py; extract or clone the whole repository.')

    if execute(['gh', 'auth', 'status'], quiet=True).returncode:
        print('\nSign in to GitHub (your own account; no shared tokens).')
        if execute(['gh', 'auth', 'login']).returncode:
            raise RuntimeError('GitHub sign-in failed.')
    if execute(['gh', 'repo', 'view', REPO, '--json', 'nameWithOwner'], quiet=True).returncode:
        raise RuntimeError(f'Cannot access {REPO}. Ask the maintainer for an invite, accept it and retry. '
                           'Private fork settings may also need to be enabled.')

    if args.status:
        return execute([sys.executable, '-m', 'nova3_team.worker', 'status', '--repo', REPO]).returncode
    if execute(['gh', 'auth', 'setup-git'], quiet=True).returncode:
        raise RuntimeError('gh auth setup-git failed. Git clone/push may not work.')
    ensure_git_identity()
    provider = choose_provider(args.provider, force_menu=args.choose)
    previous_provider = None
    cfg = config_file()
    if cfg.exists():
        try:
            previous_provider = json.loads(cfg.read_text(encoding='utf-8')).get('provider')
        except (OSError, ValueError):
            pass
    prepare_provider(provider, already_primed=(previous_provider == provider and not args.choose))
    save_provider(provider)
    print(f'\nStarting worker: {provider} | {REPO}')
    print('Tasks, fresh AI sessions and PRs are handled by the worker. Stop with Ctrl+C.')
    print('AI use may count against your subscription or API billing. No game assets are downloaded.')
    cmd = [sys.executable, '-m', 'nova3_team.worker', 'run', '--repo', REPO, '--provider', provider]
    if args.once:
        cmd.append('--once')
    try:
        return execute(cmd).returncode
    except KeyboardInterrupt:
        print('\nWorker stopped.')
        return 130


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f'\nSETUP STOPPED: {exc}', file=sys.stderr)
        raise SystemExit(1)
