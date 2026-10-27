"""One-time and repeatable per-user local discovery scheduling.

Linux: a systemd USER timer. Windows/macOS: a detached single-instance loop.
Missing reference files do not prevent contributing to normal source tasks.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .discovery import find_elfs, ensure_ghidra, find_headless, status, cache_root, REPO_DEFAULT
from . import discovery_resources as resource_guard
# NOVA3_DISCOVERY_SAFE_RESOURCES_V1


def systemd_escape(value: str) -> str:
    # systemd unit quoting (ExecStart arguments, WorkingDirectory).
    if any(x in value for x in ('\n','\r','\x00','%')):
        raise ValueError('Unsupported character in service path')
    return '"' + value.replace('\\', '\\\\').replace('"','\\"') + '"'


def unit_text(project:Path, repo:str, python:Path) -> tuple[str,str]:
    start = (' '.join([systemd_escape(str(python)), '-m',
                       'nova3_team.discovery', 'run', '--project',
                       systemd_escape(str(project)), '--repo', repo]))
    ref=os.environ.get('NOVA3_REFERENCE_ROOT','').strip()
    envline=('Environment=NOVA3_REFERENCE_ROOT='+systemd_escape(ref)+'\n') if ref else ''
    service = f'''[Unit]
Description=NOVA3 private headless Ghidra function discovery
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={str(project)}
ExecStart={start}
Environment=PYTHONUNBUFFERED=1
{envline}TimeoutStartSec=3h
Nice=15
# Do not starve Codex, the browser or the desktop on contributor PCs.
CPUQuota=75%
CPUWeight=10
IOWeight=10
IOSchedulingClass=idle
MemoryAccounting=yes
MemoryHigh=20%
MemoryMax=25%
MemorySwapMax=0
TasksMax=128
OOMPolicy=stop
'''
    timer = '''[Unit]
Description=Discover more N.O.V.A. 3 functions every 30 minutes

[Timer]
OnCalendar=*-*-* *:00,30:00
AccuracySec=1min
RandomizedDelaySec=5min
Persistent=true
Unit=nova3-discovery.service

[Install]
WantedBy=timers.target
'''
    return service,timer


def install_user_timer(project:Path,repo:str) -> bool:
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        return False
    if subprocess.run(['systemctl','--user','is-system-running'],
                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode not in (0,1):
        return False
    path=Path.home()/'.config/systemd/user'
    path.mkdir(parents=True,exist_ok=True)
    service,timer=unit_text(project,repo,Path(sys.executable).resolve())
    (path/'nova3-discovery.service').write_text(service)
    (path/'nova3-discovery.timer').write_text(timer)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now','nova3-discovery.timer'],check=True)
    return True


def start_detached_loop(project:Path,repo:str) -> None:
    # The loop module uses a cache-owned lock so launching the contributor app
    # repeatedly does not create multiple Ghidra workers.
    logfile = cache_root()/'discovery-loop.log'
    logfile.parent.mkdir(parents=True,exist_ok=True)
    args=[sys.executable,'-m','nova3_team.discovery_loop','--project',str(project),'--repo',repo]
    opts = {'cwd':project, 'stdin':subprocess.DEVNULL,
            'stdout':logfile.open('ab'), 'stderr':subprocess.STDOUT}
    if os.name=='nt':
        opts['creationflags']=getattr(subprocess,'DETACHED_PROCESS',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    else:
        opts['start_new_session']=True
    proc=subprocess.Popen(args,**opts)
    opts['stdout'].close()
    print(f'Background discovery service started: PID {proc.pid}')


def install(project:Path, repo:str, *, auto_tools:bool=True) -> None:
    project=project.resolve()
    if not (project/'nova3_team/discovery.py').is_file():
        raise RuntimeError('Shared discovery module absent; upgrade repository first')
    ref=os.environ.get('NOVA3_REFERENCE_ROOT','').strip()
    sources=find_elfs(project,Path(ref).expanduser() if ref else None)
    if sources and not find_headless():
        enough,why=resource_guard.preflight()
        if not enough:
            auto_tools=False
            status('Optional Ghidra installation deferred to protect this device: '+why,
                   kind='deferred')
        if auto_tools and (sys.stdin.isatty() or os.environ.get('NOVA3_AUTO_INSTALL_TOOLS') == '1'):
            try:
                print(f'Found {len(sources)} locally held ELF(s); setting up Ghidra once.')
                ensure_ghidra(install=True)
            except (OSError,RuntimeError,subprocess.SubprocessError) as exc:
                # Never disable their coding worker: the separate discovery
                # timer will emit an actionable error and retry later.
                status(f'Ghidra setup needed: {exc}',kind='blocked')
        else:
            status('Found local ELF but no Ghidra. Run setup in an interactive terminal once.',kind='blocked')
    if install_user_timer(project,repo):
        print('NOVA3 discovery timer enabled. It runs independently of Codex and your terminal.')
    else:
        start_detached_loop(project,repo)


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--project',type=Path,default=Path.cwd())
    p.add_argument('--repo',default=REPO_DEFAULT)
    p.add_argument('--no-install-tools',action='store_true')
    a=p.parse_args(argv)
    install(a.project,a.repo,auto_tools=not a.no_install_tools)

if __name__=='__main__':main()
