"""Cross-platform single-instance fallback for systems without systemd user."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from .discovery import cache_root, safe_one_cycle, status, REPO_DEFAULT


def pid_active(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        try:
            pid=int((path/'pid').read_text())
        except (OSError,ValueError):
            pid=-1
        if pid_active(pid):
            return False
        # Stale lock only. Avoid touching other cache or project files.
        try:
            (path/'pid').unlink(missing_ok=True)
            path.rmdir()
            path.mkdir()
        except OSError:
            return False
    (path/'pid').write_text(str(os.getpid()))
    return True


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--project',type=Path,default=Path.cwd())
    p.add_argument('--repo',default=REPO_DEFAULT)
    p.add_argument('--minutes',type=int,default=30)
    p.add_argument('--once',action='store_true')
    a=p.parse_args(argv)
    lock=cache_root()/ 'discovery-loop.lock'
    if not acquire_lock(lock):
        print('Discovery loop already active; exiting.')
        return 0
    try:
        while True:
            safe_one_cycle(project=a.project,repo=a.repo,install_tools=False)
            if a.once:
                return 0
            time.sleep(max(5,a.minutes)*60)
    finally:
        try:
            if int((lock/'pid').read_text()) == os.getpid():
                (lock/'pid').unlink(missing_ok=True)
                lock.rmdir()
        except (OSError,ValueError):
            pass

if __name__=='__main__':sys.exit(main())
