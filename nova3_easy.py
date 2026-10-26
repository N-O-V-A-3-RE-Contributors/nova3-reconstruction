#!/usr/bin/env python3
"""One-launch cross-platform bootstrap for the private NOVA3 contributor worker.

A packaged PyInstaller binary embeds Python to run this bootstrap, while the
reconstruction worker and its tests run using an ordinary Python installation.
No credentials or reference game binaries are bundled or uploaded.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from nova3_team import dashboard  # NOVA3-LIVE-DASHBOARD-v1

OLD_REPO = 'crjase/nova3-reconstruction'
PACKAGES = {'codex': '@openai/codex', 'claude': '@anthropic-ai/claude-code', 'gemini': '@google/gemini-cli'}


def msg(s):
    print('\n' + s, flush=True)


def command(cmd, *, cwd=None, check=False, capture=False, timeout=None):
    cp = subprocess.run(cmd, cwd=cwd, check=False, capture_output=capture, text=True, timeout=timeout)
    if check and cp.returncode:
        detail = ((cp.stderr or '') + '\n' + (cp.stdout or ''))[-1300:] if capture else ''
        raise RuntimeError(f"Command failed: {cmd[0]} ({cp.returncode}) {detail}")
    return cp


def exe(name):
    result = shutil.which(name)
    if result:
        return result
    if sys.platform == 'win32':
        locations = {
            'gh': [r'C:\Program Files\GitHub CLI\gh.exe'],
            'git': [r'C:\Program Files\Git\cmd\git.exe', r'C:\Program Files\Git\bin\git.exe'],
            'node': [r'C:\Program Files\nodejs\node.exe'],
            'npm': [r'C:\Program Files\nodejs\npm.cmd'],
            'python': [str(Path.home() / 'AppData' / 'Local' / 'Programs' / 'Python' / 'Python312' / 'python.exe')],
        }
        for candidate in locations.get(name, []):
            if Path(candidate).exists():
                return candidate
    if sys.platform == 'darwin':
        for candidate in (f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}'):
            if Path(candidate).exists():
                return candidate
    return None


def config_path():
    if os.name == 'nt':
        base = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming')))
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return base / 'nova3-team' / 'easy.json'


def load_config():
    try:
        result = json.loads(config_path().read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_config(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def ask(msg_, default=False):
    try:
        value = input(msg_).strip().lower()
    except EOFError:
        raise RuntimeError('Please run this launcher in an interactive terminal.') from None
    return default if not value else value in ('y','yes')


def package_commands(items):
    """Return supported OS-package-manager commands, never sudo npm."""
    if sys.platform == 'win32':
        names = {'gh': 'GitHub.cli','git': 'Git.Git','node': 'OpenJS.NodeJS.LTS',
                 'python': 'Python.Python.3.12', 'cmake':'Kitware.CMake','ninja':'Ninja-build.Ninja'}
        if not exe('winget'):
            raise RuntimeError('Windows App Installer/winget is unavailable. Install App Installer from Microsoft, then reopen the launcher.')
        return [[exe('winget'), 'install','--id',names[x],'-e',
                 '--accept-package-agreements','--accept-source-agreements'] for x in items]
    if sys.platform == 'darwin':
        brew = exe('brew')
        if not brew:
            raise RuntimeError('Homebrew is required to automatically install macOS dependencies. Install it from https://brew.sh and double-click again.')
        names = {'python': 'python','node':'node','gh':'gh','git':'git','cmake':'cmake','ninja':'ninja'}
        return [[brew, 'install', *[names[x] for x in items]]]
    if exe('pacman'):
        names = {'python':'python','node':'nodejs','gh':'github-cli','git':'git','cmake':'cmake','ninja':'ninja'}
        return [['sudo','pacman','-S','--needed','--noconfirm',*[names[x] for x in items]]]
    if exe('apt-get'):
        names = {'python':'python3','node':'nodejs','gh':'gh','git':'git','cmake':'cmake','ninja':'ninja-build'}
        return [['sudo','apt-get','update'],['sudo','apt-get','install','-y',*[names[x] for x in items]]]
    if exe('dnf'):
        names = {'python':'python3','node':'nodejs','gh':'gh','git':'git','cmake':'cmake','ninja':'ninja-build'}
        return [['sudo','dnf','install','-y',*[names[x] for x in items]]]
    raise RuntimeError('No supported package manager found. Install git, gh, Python 3.10+, Node.js and npm manually.')


def python_command():
    opts = [['python3'],['python'],['py','-3']]
    if sys.platform == 'win32' and exe('python'):
        opts.insert(0,[exe('python')])
    for cmd in opts:
        if not exe(cmd[0]):
            continue
        try:
            cp=command(cmd+['-c','import sys; print(sys.version_info[:2])'],capture=True,timeout=8)
            if cp.returncode == 0 and sys.version_info: # only probe success here, exact check next
                vers=command(cmd+['-c','import sys; exit(0 if sys.version_info >= (3,10) else 1)'],timeout=8)
                if vers.returncode == 0: return cmd
        except (OSError,subprocess.TimeoutExpired):
            continue
    return None


def ensure_dependencies():
    needed=[]
    for name in ('git','gh','node'):
        if not exe(name): needed.append(name)
    if not python_command(): needed.append('python')
    # CMake and Ninja are needed for local C++ build/tests; install too.
    for name in ('cmake','ninja'):
        if not exe(name): needed.append(name)
    if not needed: return
    msg('The following development tools are missing: ' + ', '.join(needed))
    if not ask('Install missing tools using your OS package manager? [Y/n] ', True):
        raise RuntimeError('Cannot continue until prerequisites are installed.')
    for cmd in package_commands(needed):
        command(cmd,check=True)
    # Package manager installers may modify PATH only for new login sessions.
    if sys.platform == 'win32':
        path = os.environ.get('PATH','')
        for extra in (r'C:\Program Files\GitHub CLI',r'C:\Program Files\Git\cmd',r'C:\Program Files\nodejs'):
            if Path(extra).exists(): path = extra + os.pathsep + path
        os.environ['PATH']=path
    failed=[name for name in ('git','gh','node','cmake','ninja') if not exe(name)]
    if not python_command():failed.append('python')
    if failed:
        raise RuntimeError('Installation finished, but these tools were not found: '+', '.join(failed)+'. Close and reopen the launcher.')


def github_login():
    gh=exe('gh') or 'gh'
    if command([gh,'auth','status'],capture=True).returncode:
        msg('GitHub sign-in: follow the browser prompts for your own account.')
        command([gh,'auth','login','--web'],check=True)
    command([gh,'auth','setup-git'],check=True)
    return gh


def resolve_repo(gh, desired):
    cp=command([gh,'repo','view',desired,'--json','nameWithOwner','--jq','.nameWithOwner'],capture=True)
    if cp.returncode:
        raise RuntimeError('Cannot access the private repository. Ask the maintainer for a READ invitation, accept it on GitHub, and run this launcher again.\n'+(cp.stderr or '')[-450:])
    return cp.stdout.strip()


def distribution_repo():
    """Read owner/repo from the authenticated release archive, not user-provided URLs."""
    try:
        base=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
        repo=(base/'NOVA3-REPO.txt').read_text(encoding='utf-8').strip()
        parts=repo.split('/')
        if len(parts)==2 and all(part.replace('-','').replace('_','').replace('.','').isalnum() for part in parts):
            return repo
    except (OSError,UnicodeError):
        pass
    return None


def workdir(cfg, repo):
    override=os.environ.get('NOVA3_WORKDIR')
    if override:
        return Path(override).expanduser().resolve()
    familiar=Path.home()/'Projects'/'nova3_re'
    if (familiar/'.git').exists() and (familiar/'nova3_team'/'worker.py').exists():
        return familiar
    stored=cfg.get('workdir')
    if stored and (Path(stored)/'.git').is_dir():
        return Path(stored)
    return Path.home()/'NOVA3-Work'


def prepare_checkout(gh, repo, directory):
    git=exe('git') or 'git'
    if not directory.exists():
        directory.parent.mkdir(parents=True,exist_ok=True)
        msg('Downloading reconstruction source into '+str(directory))
        command([gh,'repo','clone',repo,str(directory),'--','--quiet'],check=True)
    if not (directory/'.git').exists():
        raise RuntimeError(f'{directory} exists but is not a Git checkout. Will not overwrite it.')
    cp=command([gh,'repo','view','--json','nameWithOwner','--jq','.nameWithOwner'],cwd=directory,capture=True)
    if cp.returncode or cp.stdout.strip()!=repo:
        raise RuntimeError(f'{directory} belongs to a different repository. Set NOVA3_WORKDIR to a clean directory.')
    # Only fast-forward a pristine tree. Never discard user's local work.
    dirty=command([git,'status','--porcelain'],cwd=directory,capture=True)
    if dirty.returncode==0 and not dirty.stdout.strip():
        result=command([git,'pull','--ff-only','origin','main'],cwd=directory,capture=True,timeout=90)
        if result.returncode:
            msg('Source update skipped: '+(result.stderr or '')[-250:])
    else:
        msg('Local uncommitted work detected; skipping Git pull (nothing discarded).')
    return directory


def local_identity(gh, directory):
    git=exe('git') or 'git'
    name=command([git,'config','user.name'],cwd=directory,capture=True).stdout.strip()
    email=command([git,'config','user.email'],cwd=directory,capture=True).stdout.strip()
    if name and email:return
    cp=command([gh,'api','user'],capture=True,check=True)
    data=json.loads(cp.stdout)
    if not name:command([git,'config','--local','user.name',data['login']],cwd=directory,check=True)
    if not email:command([git,'config','--local','user.email',f"{data['id']}+{data['login']}@users.noreply.github.com"],cwd=directory,check=True)


def add_private_npm_path():
    prefix=Path.home()/'.nova3-team'/'npm'
    bin_dir=prefix if os.name=='nt' else prefix/'bin'
    os.environ['PATH']=str(bin_dir)+os.pathsep+os.environ.get('PATH','')


def install_provider(provider):
    global_packages=PACKAGES
    if exe(provider): return
    npm=exe('npm')
    if not npm:raise RuntimeError('npm not found after Node.js installation. Reopen the launcher.')
    prefix=Path.home()/'.nova3-team'/'npm'
    prefix.mkdir(parents=True,exist_ok=True)
    npm_env=os.environ.copy()
    npm_env['NPM_CONFIG_PREFIX']=str(prefix)
    msg(f'Installing {provider} CLI into {prefix} (no administrator npm).')
    cmd=[npm,'install','--global',global_packages[provider]]
    cp=subprocess.run(cmd,env=npm_env)
    if cp.returncode:raise RuntimeError('AI CLI installation failed.')
    add_private_npm_path()
    if not exe(provider):raise RuntimeError(f'Installed {provider} but cannot locate it. Reopen the launcher.')


def provider_login(provider, cfg):
    if provider=='codex':
        cp=command([exe('codex'),'login','status'],capture=True)
        detail=(cp.stderr or '')+'\n'+(cp.stdout or '')
        if cp.returncode and ('Permission denied' in detail or 'os error 13' in detail):
            raise RuntimeError(
                'Codex cannot read ~/.codex (possibly left root-owned by an old sudo session). '
                'On Linux, run: sudo find "$HOME/.codex" -xdev -user root '
                '-exec chown -h "$(id -u):$(id -g)" {} + ; '
                'then run: codex login status . Do not run NOVA3 with sudo.')
        if cp.returncode:
            msg('Sign into Codex using your own ChatGPT/Codex account.')
            command([exe('codex'),'login'],check=True)
    elif cfg.get('provider_authorized')!=provider:
        msg(f'Sign into {provider} in the CLI window. Exit that session afterwards.')
        try: command([exe(provider)],check=False)
        except KeyboardInterrupt:pass
        if not ask(f'Have you completed the {provider} login? [y/N] ',False):
            raise RuntimeError('AI provider login is required.')
    cfg['provider_authorized']=provider


def select_provider(cfg, selected):
    if selected:return selected
    if cfg.get('provider') in PACKAGES:return cfg['provider']
    msg('Choose your AI provider: 1 Codex, 2 Claude Code, 3 Gemini CLI.')
    choices={'1':'codex','2':'claude','3':'gemini'}
    while True:
        value=input('Select 1 / 2 / 3: ').strip()
        if value in choices:return choices[value]
        print('Please enter 1, 2, or 3.')


def main(argv=None):
    ap=argparse.ArgumentParser(description='NOVA3 one-click contributor launcher')
    ap.add_argument('--repo',help='Override shared repository (OWNER/REPO)')
    ap.add_argument('--provider',choices=tuple(PACKAGES))
    ap.add_argument('--choose-provider',action='store_true')
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--status',action='store_true')
    ap.add_argument('--self-test',action='store_true')
    a=ap.parse_args(argv)
    if a.self_test:
        assert 'codex' in PACKAGES and OLD_REPO and config_path()
        print('NOVA3 launcher self-test OK')
        return 0
    cfg=load_config()
    add_private_npm_path()
    if sys.platform=='darwin':
        os.environ['PATH']='/opt/homebrew/bin:/usr/local/bin:'+os.environ.get('PATH','')
    msg('NOVA3 distributed reconstruction — first-run setup and worker')
    print('Private GitHub access and one AI account required. No game binaries downloaded.')
    ensure_dependencies()
    gh=github_login()
    default=a.repo or os.environ.get('NOVA3_REPO') or distribution_repo()
    if not default:
        familiar=Path.home()/'Projects'/'nova3_re'
        if (familiar/'.git').is_dir():
            local=command([gh,'repo','view','--json','nameWithOwner','--jq','.nameWithOwner'],cwd=familiar,capture=True)
            if local.returncode==0: default=local.stdout.strip()
    repo=resolve_repo(gh,default or cfg.get('repo') or OLD_REPO)
    directory=workdir(cfg,repo)
    prepare_checkout(gh,repo,directory)
    local_identity(gh,directory)
    cfg['repo']=repo
    cfg['workdir']=str(directory)
    if a.status:
        py=python_command()
        return command(py+['-m','nova3_team.worker','status','--repo',repo],cwd=directory).returncode
    provider=select_provider({} if a.choose_provider else cfg,a.provider)
    if not exe(provider):
        if not ask(f'Install the {provider} coding CLI using your own Node.js setup? [Y/n] ',True):
            raise RuntimeError('AI CLI installation was declined.')
        install_provider(provider)
    provider_login(provider,cfg)
    cfg['provider']=provider
    save_config(cfg)
    py=python_command()
    if not py:raise RuntimeError('Python 3.10+ unavailable after setup.')
    msg(f'Working: {repo} | provider={provider} | source={directory}')
    print('Leave this window open. Ctrl+C stops it. Relaunch to resume saved work.')
    command_line=py+['-m','nova3_team.worker','run','--repo',repo,'--provider',provider]
    # NOVA3-LOCAL-REFERENCE-AUTOSELECT-v1
    # On the owner's machine, analysis/ contains legally held local references.
    # On contributor machines, this remains opt-in via NOVA3_REFERENCE_ROOT.
    local_ref=os.environ.get('NOVA3_REFERENCE_ROOT', '').strip()
    if not local_ref and (directory/'analysis').is_dir():
        local_ref=str((directory/'analysis').resolve())
    if local_ref and Path(local_ref).expanduser().is_dir():
        command_line+=['--binary-root',str(Path(local_ref).expanduser().resolve())]
    if a.once: command_line+=['--once']
    try:return dashboard.run(command_line,cwd=directory,repo=repo,provider=provider)
    except KeyboardInterrupt:
        msg('Stopped by user; next launch can continue from the local checkpoint.')
        return 130


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print('\nSETUP ERROR:',exc,file=sys.stderr)
        if sys.stdin.isatty():
            try:input('Press Enter to close this window...')
            except EOFError:pass
        sys.exit(1)
