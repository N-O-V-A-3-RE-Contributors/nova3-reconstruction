"""Conservative cross-platform resource guard for NOVA3 local Ghidra discovery.

This module is deliberately independent of GitHub and the reconstruction worker.
It does not collect process lists or send local memory data to GitHub. Linux adds
cgroup hard limits through discovery_setup; other OSes get heap sizing and an
active headroom supervisor. A deferred scan is never counted as a failed source.
"""
from __future__ import annotations

import contextlib
import ctypes
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

MARKER = 'NOVA3_RESOURCE_GUARD_V1'
MIB = 1024 * 1024
GIB = 1024 * MIB

class ResourcePressure(RuntimeError):
    """Not a code-analysis error; retry when the host has resources."""


def _proc_memory() -> tuple[int, int, int, int] | None:
    try:
        fields = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            k, _, val = line.partition(':')
            if k in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
                fields[k] = int(val.strip().split()[0]) * 1024
        if all(k in fields for k in ('MemTotal', 'MemAvailable')):
            return (fields['MemTotal'], fields['MemAvailable'],
                    fields.get('SwapTotal', 0), fields.get('SwapFree', 0))
    except (OSError, ValueError, IndexError):
        pass
    return None


def _windows_memory() -> tuple[int, int, int, int] | None:
    if sys.platform != 'win32':
        return None
    try:
        class M(ctypes.Structure):
            _fields_ = [('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong),
                ('ullTotalPhys',ctypes.c_ulonglong),('ullAvailPhys',ctypes.c_ulonglong),
                ('ullTotalPageFile',ctypes.c_ulonglong),('ullAvailPageFile',ctypes.c_ulonglong),
                ('ullTotalVirtual',ctypes.c_ulonglong),('ullAvailVirtual',ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual',ctypes.c_ulonglong)]
        data=M(); data.dwLength=ctypes.sizeof(data)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
            # PageFile totals include physical memory on Windows, so ignore
            # them for swap-pressure assessment rather than misrepresenting.
            return (data.ullTotalPhys, data.ullAvailPhys, 0, 0)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _mac_memory() -> tuple[int, int, int, int] | None:
    if sys.platform != 'darwin':
        return None
    try:
        total=int(subprocess.check_output(['sysctl','-n','hw.memsize'],text=True,timeout=5).strip())
        page=int(subprocess.check_output(['sysctl','-n','hw.pagesize'],text=True,timeout=5).strip())
        vm=subprocess.check_output(['vm_stat'],text=True,timeout=5)
        fields={}
        for line in vm.splitlines():
            k,_,v=line.partition(':')
            if v:
                try: fields[k.strip()]=int(v.strip().rstrip('.').replace('.',''))
                except ValueError: pass
        # Conservative: count free/inactive/speculative as potentially usable.
        available=page*sum(fields.get(k,0) for k in
                ('Pages free','Pages inactive','Pages speculative'))
        return (total, min(total,available), 0, 0)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def memory_snapshot() -> tuple[int,int,int,int] | None:
    if sys.platform.startswith('linux'):
        return _proc_memory()
    return _windows_memory() or _mac_memory()


def heap_mb(total_bytes: int | None = None) -> int:
    """Small machines do not inherit Ghidra's default 2 GiB Java heap."""
    if total_bytes is None:
        snap=memory_snapshot()
        total_bytes=snap[0] if snap else 4 * GIB
    total=total_bytes / GIB
    if total < 6: return 512
    if total < 12: return 768
    if total < 24: return 1280
    return 2048


def heap_setting() -> str:
    return f'{heap_mb()}M'


# NOVA3_SOLO_DISCOVERY_PIPELINE_V1
# Swap occupancy is not itself evidence of current swapping/thrashing: Linux
# can leave cold pages swapped out for days. RAM availability, PSI pressure,
# and service cgroup hard limits all participate in the safety decision.
def _memory_psi_avg10() -> float | None:
    if not sys.platform.startswith('linux'):
        return None
    try:
        line=next(x for x in Path('/proc/pressure/memory').read_text().splitlines()
                  if x.startswith('some '))
        return float(next(x.split('=',1)[1] for x in line.split()
                          if x.startswith('avg10=')))
    except (OSError,StopIteration,ValueError,IndexError):
        return None


def headroom(snapshot: tuple[int,int,int,int] | None = None,
             *, running: bool = False) -> tuple[bool,str]:
    """Gate optional analysis on real usable RAM and active memory pressure.

    Existing hard systemd memory/swap caps remain in force. This method is
    intentionally conservative, but permits 8GB devices with stale swap usage
    to perform one carefully limited scan when RAM is actually available.
    """
    if snapshot is None:
        snapshot=memory_snapshot()
    if not snapshot:
        return False,'Memory availability unavailable; discovery deferred'
    total,avail,swap_total,swap_free=snapshot
    heap=heap_mb(total)
    available=avail//MIB
    floor=max(850 if running else 2050,
              int(heap*(.65 if running else 1.0))+(450 if running else 1200))
    if available<floor:
        return False,(f'Available memory {available} MiB < safe {floor} MiB '
                      f'(Ghidra heap {heap} MiB)')
    psi=_memory_psi_avg10()
    # Active memory stalls are much more useful than swap occupancy alone.
    if psi is not None and psi>(5.0 if running else 2.0):
        return False,f'Active memory pressure {psi:.2f}% over 10 seconds; discovery deferred'
    if swap_total>256*MIB:
        fraction=(swap_total-swap_free)/swap_total
        if fraction>=.80 and psi is None and available<4096:
            # Unknown active-pressure state: don't assume cold swap is safe.
            return False,(f'Swap {fraction:.0%} full, memory-pressure sensor unavailable; '
                          'discovery deferred')
    return True,(f'RAM available {available} MiB, headless heap {heap} MiB; '
                 f'PSI={psi if psi is not None else "unavailable"}')


def preflight(workspace: Path | None = None) -> tuple[bool,str]:
    okay,detail=headroom()
    if not okay:
        return False,detail
    try:
        free=shutil.disk_usage(workspace or Path(tempfile.gettempdir())).free
        if free < GIB:
            return False,f'Temporary filesystem has only {free//MIB} MiB free (need 1024 MiB)'
    except (OSError, ValueError):
        return False,'Cannot check temp filesystem free space; deferring discovery'
    return True,detail


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == 'posix':
        try: os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError: return
    elif sys.platform == 'win32':
        try:
            subprocess.run(['taskkill','/T','/F','/PID',str(proc.pid)],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
        except (OSError,subprocess.SubprocessError): proc.terminate()
    else:
        proc.terminate()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name=='posix':
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        else:proc.kill()
        proc.wait(timeout=5)


def supervised_run(args: list[str], *, output, env: dict[str,str],
                   timeout: int, poll: float = 3.0) -> subprocess.CompletedProcess:
    """Supervise Ghidra even on Windows/macOS where cgroups do not exist."""
    proc=subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,env=env,
                          start_new_session=(os.name=='posix'))
    started=time.monotonic()
    unsafe_checks=0
    try:
        while True:
            try:
                code=proc.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic()-started > timeout:
                    raise ResourcePressure('Ghidra timed out; analysis deferred without publishing partial output')
                ok,why=headroom(running=True)
                unsafe_checks=0 if ok else unsafe_checks+1
                if unsafe_checks >= 3:
                    raise ResourcePressure('Ghidra stopped to protect host memory: '+why)
        if code in (-signal.SIGKILL, 137):
            raise ResourcePressure('Ghidra was killed by the OS (likely memory limit); deferring this library')
        return subprocess.CompletedProcess(args,code)
    except BaseException:
        _terminate_tree(proc)
        raise


@contextlib.contextmanager
def discovery_lock(cache_dir:Path):
    """On Linux/macOS, do not run a manual and scheduled scan simultaneously."""
    lockfile=cache_dir/'resource-guard.lock'
    lockfile.parent.mkdir(parents=True,exist_ok=True)
    with lockfile.open('a+b') as f:
        if os.name=='posix':
            import fcntl
            try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ResourcePressure('Another discovery scan is running; skip this cycle')
        elif os.name=='nt':
            import msvcrt
            if f.seek(0,os.SEEK_END)==0:
                f.write(b'\x00');f.flush()
            f.seek(0)
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ResourcePressure('Another discovery scan is running; skip this cycle')
        try:yield
        finally:
            if os.name=='posix':fcntl.flock(f.fileno(),fcntl.LOCK_UN)
            elif os.name=='nt':
                f.seek(0)
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
